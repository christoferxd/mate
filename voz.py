"""Oído y voz de Mate: escucha siempre por el micrófono y habla con la voz de Windows."""
import asyncio
import io
import logging
import os
import queue
import re
import threading
import time
import unicodedata
import wave

import numpy as np
import sounddevice as sd

log = logging.getLogger("mate.voz")

RATE = 16000                 # Whisper trabaja a 16 kHz
FRAME_S = 0.03               # analizamos el audio en trozos de 30 ms
SILENCIO_FIN_S = 0.6         # tanto silencio cierra una frase
FRASE_MAX_S = 20.0
PREROLL_S = 0.3              # lo que se guarda antes de que empiece la voz

# Releer los dispositivos reinicia PortAudio entero y corta lo que esté sonando:
# la búsqueda de micrófono y la reproducción no pueden coincidir.
AUDIO = threading.Lock()

# "Mate" es también una palabra corriente (el mate, "te mate"), así que solo cuenta al principio de la
# frase: "Mate, abre Chrome" u "Oye Mate, ...". Whisper lo escribe de varias formas.
PALABRA_CLAVE = re.compile(r"^[\s¡¿.,]*(oye\s+|hey\s+|ey\s+|hola\s+)?(mate|matte|mathe|mati|matt)\b[\s,.:!?¡¿]*")


def normalizar(texto):
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def quitar_palabra_clave(texto):
    """Devuelve (había_palabra_clave, resto_de_la_frase)."""
    norm = normalizar(texto)
    m = PALABRA_CLAVE.search(norm)
    if not m:
        return False, texto.strip()
    # Con texto precompuesto (el que da Whisper) normalizar() conserva la longitud, así que se corta
    # sobre el original y se mantienen las tildes; si no, sobre el normalizado.
    base = texto if len(norm) == len(texto) else norm
    resto = (base[:m.start()] + " " + base[m.end():]).strip(" ,.")
    return True, resto


class Oido(threading.Thread):
    """Escucha sin parar, corta frases por energía y las transcribe con Whisper."""

    def __init__(self, al_oir, al_estado, al_nivel):
        super().__init__(daemon=True, name="oido")
        self.al_oir = al_oir          # al_oir(texto)
        self.al_estado = al_estado    # al_estado("grabando" | "transcribiendo" | "esperando" | "sin_micro" | "cargando")
        self.al_nivel = al_nivel      # al_nivel(0..1), para animar el reactor
        self.pausado = threading.Event()   # activo mientras Mate habla, para no oírse a sí mismo
        self.silenciado = threading.Event()
        self._modelo = None

    def _cargar_modelo(self):
        from faster_whisper import WhisperModel
        nombre = os.environ.get("MATE_WHISPER", "small")
        self.al_estado("cargando")
        log.info("cargando Whisper %s (la primera vez se descarga)", nombre)
        self._modelo = WhisperModel(nombre, device="cpu", compute_type="int8",
                                    cpu_threads=min(8, os.cpu_count() or 4))

    def _elegir_micro(self):
        with AUDIO:
            sd._terminate()
            sd._initialize()   # vuelve a leer los dispositivos, por si acabas de conectar uno
            return self._buscar_micro()

    def _buscar_micro(self):
        entrada = sd.default.device[0]
        if entrada is not None and entrada >= 0:
            return entrada
        for api in ("Windows WASAPI", "MME"):
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == api:
                    return i
        return None

    def run(self):
        try:
            self._cargar_modelo()
        except Exception:
            log.exception("no se pudo cargar Whisper")
            self.al_estado("sin_modelo")
            return
        while True:
            micro = self._elegir_micro()
            if micro is None:
                self.al_estado("sin_micro")
                time.sleep(5)
                continue
            try:
                self._escuchar(micro)
            except Exception:
                log.exception("el micrófono falló; reintento")
                self.al_estado("sin_micro")
                time.sleep(3)

    def _escuchar(self, micro):
        info = sd.query_devices(micro)
        rate_dev = int(info["default_samplerate"])
        bloque = int(rate_dev * FRAME_S)
        log.info("escuchando por %s a %d Hz", info["name"], rate_dev)
        trozos = queue.Queue()

        def callback(indata, frames, t, status):
            trozos.put(indata[:, 0].copy())

        ruido = 0.005
        frase, previo = [], []
        hablando, voz_seguida, silencio = False, 0, 0.0
        self.al_estado("esperando")
        with sd.InputStream(device=micro, channels=1, samplerate=rate_dev, blocksize=bloque,
                            dtype="float32", callback=callback):
            while True:
                trozo = trozos.get()
                if self.pausado.is_set() or self.silenciado.is_set():
                    frase, hablando, voz_seguida = [], False, 0
                    self.al_nivel(0.0)
                    continue
                rms = float(np.sqrt(np.mean(trozo ** 2)))
                self.al_nivel(min(1.0, rms * 12))
                umbral = max(ruido * 3.0, 0.008)
                if not hablando:
                    ruido = 0.95 * ruido + 0.05 * rms
                    previo.append(trozo)
                    previo = previo[-int(PREROLL_S / FRAME_S):]
                    voz_seguida = voz_seguida + 1 if rms > umbral else 0
                    if voz_seguida >= 3:
                        hablando, frase, silencio = True, list(previo), 0.0
                        self.al_estado("grabando")
                    continue
                frase.append(trozo)
                silencio = silencio + FRAME_S if rms < umbral else 0.0
                if silencio >= SILENCIO_FIN_S or len(frase) * FRAME_S >= FRASE_MAX_S:
                    hablando, voz_seguida = False, 0
                    audio = np.concatenate(frase)
                    frase = []
                    self._transcribir(audio, rate_dev)
                    self.al_estado("esperando")

    def _transcribir(self, audio, rate_dev):
        if len(audio) / rate_dev < 0.4:
            return
        if rate_dev != RATE:
            n = int(len(audio) * RATE / rate_dev)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        self.al_estado("transcribiendo")
        segmentos, _ = self._modelo.transcribe(
            audio, language="es", beam_size=1, vad_filter=True,
            initial_prompt="Mate, abre el bloc de notas. Mate, ¿qué hora es?")
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        if texto:
            log.info("oído: %s", texto)
            self.al_oir(texto)


def limpiar_para_hablar(texto):
    """Quita el formato Markdown, que en voz alta no tiene sentido."""
    texto = re.sub(r"```.*?```", " ", texto, flags=re.S)
    texto = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", texto)
    texto = re.sub(r"[*_#`>|]", "", texto)
    texto = re.sub(r"https?://\S+", "el enlace", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _vaciar(cola):
    while True:
        try:
            cola.get_nowait()
        except queue.Empty:
            return


class Voz(threading.Thread):
    """Habla en cola y se puede cortar.

    La voz es Alex (neuronal de Microsoft, acento peruano, por internet). Si no hay conexión, habla Pablo, la voz
    de Windows, que funciona sin internet. Un hilo sintetiza y otro reproduce: la frase siguiente se prepara
    mientras suena la anterior.
    """

    def __init__(self, al_empezar, al_terminar):
        super().__init__(daemon=True, name="voz")
        self.al_empezar = al_empezar
        self.al_terminar = al_terminar
        self.cola = queue.Queue()      # textos por sintetizar
        self._audio = queue.Queue()    # (generación, texto, muestras, rate, envolvente) por reproducir
        self._generacion = 0           # callar() la sube: lo que se sintetizó antes se descarta
        self._pendientes = 0           # frases aceptadas que aún no han terminado de sonar
        self._sintetizando = self._sonando = False
        self._cerrojo = threading.Lock()
        self._envolvente, self._inicio = None, 0.0
        self._nombre_voz = os.environ.get("MATE_VOZ", "Pablo")   # la de Windows, de reserva
        self._voz_neuronal = os.environ.get("MATE_VOZ_NEURONAL", "es-PE-AlexNeural")   # vacía: solo Windows
        self._sin_red_hasta = 0.0   # tras un fallo de red, un rato con la voz de Windows antes de reintentar

    def decir(self, texto):
        texto = limpiar_para_hablar(texto)
        if texto:
            with self._cerrojo:
                self._pendientes += 1
            self.cola.put((self._generacion, texto))

    def callar(self):
        with self._cerrojo:
            self._generacion += 1
            _vaciar(self.cola)
            _vaciar(self._audio)
            # solo siguen pendientes la frase que se está sintetizando y la que está sonando
            self._pendientes = int(self._sintetizando) + int(self._sonando)
        sd.stop()

    def _salida(self):
        """El altavoz de mate.json («altavoz», parte de su nombre): la voz sale por ahí aunque Windows mande el
        resto del sonido a los audífonos. Si no está conectado, la salida predeterminada."""
        import sistema
        nombre = (sistema.leer_config().get("altavoz") or "").lower()
        if nombre:
            for api in ("MME", "Windows WASAPI"):   # MME acepta cualquier frecuencia de muestreo
                for i, d in enumerate(sd.query_devices()):
                    if (d["max_output_channels"] > 0 and nombre in d["name"].lower()
                            and sd.query_hostapis(d["hostapi"])["name"] == api):
                        return i
        return None

    def nivel(self):
        """Volumen de lo que suena ahora, de 0 a 1, para que el reactor lata con la voz."""
        env = self._envolvente
        if env is None:
            return 0.0
        i = int((time.monotonic() - self._inicio) / 0.05)
        return float(env[i]) if 0 <= i < len(env) else 0.0

    async def _neuronal(self, texto):
        """MP3 de la voz neuronal, decodificado a muestras: (muestras float32, frecuencia)."""
        import av
        import edge_tts
        datos = bytearray()

        async def bajar():
            async for trozo in edge_tts.Communicate(texto, self._voz_neuronal, rate="+5%").stream():
                if trozo["type"] == "audio":
                    datos.extend(trozo["data"])
        await asyncio.wait_for(bajar(), timeout=6)
        contenedor = av.open(io.BytesIO(bytes(datos)))
        remuestreo = av.AudioResampler(format="flt", layout="mono", rate=24000)
        trozos = [f.to_ndarray().ravel() for paquete in contenedor.decode(audio=0) for f in remuestreo.resample(paquete)]
        return np.concatenate(trozos).astype(np.float32), 24000

    async def _windows(self, texto):
        """WAV de la voz de Windows, decodificado: (muestras float32, frecuencia)."""
        with wave.open(io.BytesIO(await self._sintetizar(texto))) as w:
            rate = w.getframerate()
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768, rate

    async def _generar(self, texto):
        if self._voz_neuronal and time.monotonic() >= self._sin_red_hasta:
            try:
                return await self._neuronal(texto)
            except Exception as e:
                log.warning("voz neuronal no disponible (%s); hablo con la de Windows", e)
                self._sin_red_hasta = time.monotonic() + 60
        return await self._windows(texto)

    async def _sintetizar(self, texto):
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader
        s = SpeechSynthesizer()
        for v in SpeechSynthesizer.all_voices:
            if self._nombre_voz.lower() in v.display_name.lower():
                s.voice = v
                break
        flujo = await s.synthesize_text_to_stream_async(texto)
        lector = DataReader(flujo.get_input_stream_at(0))
        await lector.load_async(flujo.size)
        datos = bytearray(flujo.size)
        lector.read_bytes(datos)
        return bytes(datos)

    def run(self):
        threading.Thread(target=self._sintetizador, daemon=True, name="voz-sintesis").start()
        self._reproductor()

    def _sintetizador(self):
        bucle = asyncio.new_event_loop()
        while True:
            gen, texto = self.cola.get()
            with self._cerrojo:
                self._sintetizando = True
            item = None
            if gen == self._generacion:
                try:
                    muestras, rate = bucle.run_until_complete(self._generar(texto))
                    paso = int(rate * 0.05)
                    env = [float(np.sqrt(np.mean(muestras[i:i + paso] ** 2))) * 6 for i in range(0, len(muestras), paso)]
                    item = (gen, texto, muestras, rate, np.clip(env, 0, 1))
                except Exception:
                    log.exception("no pude sintetizar")
            with self._cerrojo:
                self._audio.put(item or (gen, None, None, None, None))   # un hueco también cuenta como terminado
                self._sintetizando = False

    def _reproductor(self):
        while True:
            gen, texto, muestras, rate, env = self._audio.get()
            with self._cerrojo:
                self._sonando = True
            try:
                if texto and gen == self._generacion:
                    self.al_empezar(texto)
                    with AUDIO:
                        self._envolvente, self._inicio = env, time.monotonic()
                        sd.play(muestras, rate, device=self._salida())
                        sd.wait()
            except Exception:
                log.exception("no pude hablar")
            finally:
                self._envolvente = None
                with self._cerrojo:
                    self._sonando = False
                    self._pendientes = max(0, self._pendientes - 1)
                    queda = self._pendientes
                if queda == 0:
                    time.sleep(0.3)   # que el eco de la última palabra no llegue al micrófono
                    self.al_terminar()
