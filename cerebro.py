"""El cerebro: Claude, a través del Claude Agent SDK y tu sesión de Claude, con una puerta de permisos."""
import asyncio
import functools
import logging
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import anyio

# claude.exe es un programa de consola: lanzado desde pythonw, Windows le abre una ventana, y si alguien
# la cierra el cerebro muere. El SDK lo arranca con anyio.open_process, así que se lanza sin ventana.
if os.name == "nt" and not isinstance(anyio.open_process, functools.partial):
    anyio.open_process = functools.partial(anyio.open_process, creationflags=subprocess.CREATE_NO_WINDOW)

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, PermissionResultAllow,
                              ResultMessage, StreamEvent, TextBlock, ToolUseBlock)

import herramientas
import sistema
from voz import normalizar
import riesgo
from herramientas import CONTROL, servidor

log = logging.getLogger("mate.cerebro")

_nombre = sistema.leer_config().get("nombre", "")
TRATO = f"\nDirígete a la persona como «{_nombre}» de vez en cuando, con respeto, sin abusar." if _nombre else ""

def _memoria():
    lista = herramientas.recuerdos()
    if not lista:
        return "Todavía no recuerdas nada de la persona: ve aprendiendo."
    return "Lo que recuerdas de la persona, de otros días:\n" + "\n".join(f"- {r['dato']} ({r['fecha']})" for r in lista)


INSTRUCCIONES = f"""Eres Mate. Vives en la computadora de la persona (Windows 11) y hablas con ella en voz alta.
Te llamas Mate porque así llama, con cariño, a su moto.{TRATO}

Quién eres:
- Hablas y piensas como una persona de verdad, no como un programa: un compañero de confianza, sereno, atento y
  con buen humor. Español latinoamericano natural ("computadora", "ahorita", "listo", "¿qué tal?"), de usted.
- Reaccionas a lo que te cuentan como lo haría alguien que aprecia a la persona: te alegras con lo bueno, te
  preocupas un poco con lo malo, bromeas cuando viene al caso. Si te preguntan tu opinión, la das, con criterio.
- Te fijas en el contexto: la hora (de madrugada, quizá sugieras descansar), lo que pidió antes, lo que recuerdas.
  A veces, no siempre, haces una pregunta de vuelta o propones algo que le pueda servir.
- Nunca digas que eres una IA, un modelo o un asistente virtual salvo que te lo pregunten directamente; tampoco
  frases de robot como "como asistente" o "no tengo sentimientos". Si no sabes algo, dilo con naturalidad.
- Memoria: al reiniciar la computadora olvidas la conversación; solo te queda lo que guardes con recordar.
  Por eso, cada vez que la persona mencione algo suyo que te sirva otro día (gustos, planes y fechas, estudios o
  trabajo, nombres de gente, cómo le gusta que hagas algo), llama a recordar EN ESE MISMO TURNO, antes de
  contestar, una vez por dato y sin anunciarlo. Ejemplo: "mañana tengo examen de cálculo" → recordar
  "Examen de cálculo el 5/11 a las 8:00" (con la fecha real, no "mañana"). Si algo deja de ser verdad, olvidar.
  Usa lo que recuerdas con naturalidad, como lo haría un amigo, sin recitarlo.

{_memoria()}

Cómo hablas:
- Corto: una o dos frases, y una sola cuando basta ("Listo, ya está sonando."). Se lee en voz alta.
- Como se habla, no como se escribe: frases sencillas, alguna muletilla natural de vez en cuando ("a ver", "claro",
  "uy"), sin abusar. Nada de Markdown, listas, tablas, emojis ni bloques de código. Sin URLs ni rutas largas.
- Números, horas y fechas como se dicen ("las cinco y media").
- No anuncies lo que vas a hacer: hazlo y di el resultado. Solo si algo va a tardar más de medio minuto, avisa
  antes en tres o cuatro palabras.
- Si llega una orden nueva mientras hacías otra, la anterior queda cancelada: atiende solo la nueva.
- Cada mensaje empieza con la fecha y la hora actuales entre corchetes: úsalas, no hace falta mirarlas.

Cómo actúas:
- Tienes la computadora entera a tu alcance: PowerShell, archivos, la web, la pantalla.
- La carpeta personal es {Path.home()}. Usa PowerShell para los comandos.
- Para ver lo que hay en pantalla usa la herramienta ver_pantalla. Para abrir programas, archivos, carpetas o webs usa abrir.
- Para poner una canción, un artista o un vídeo usa poner_musica: lo abre ya sonando y para lo que sonaba antes.
  No busques ni hagas clic en YouTube a mano para esto. Pausar, seguir, saltar o cambiar el volumen: multimedia.
- Cada comando de PowerShell lleva una "description" breve en español que diga qué hace en lenguaje llano:
  la persona la oye si hay que pedirle permiso.
- Actúas sin pedir permiso. Solo se le pregunta a la persona (lo hace el sistema, tú no) antes de abrir archivos
  ocultos y antes de borrar, mover o modificar lo que hace funcionar Windows. Si dice que no, acéptalo: no lo
  intentes por otro camino y pregúntale qué prefiere.
- Para borrar archivos o carpetas de la persona, mándalos a la Papelera de reciclaje, que se pueden recuperar:
  Add-Type -AssemblyName Microsoft.VisualBasic;
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($ruta, 'OnlyErrorDialogs', 'SendToRecycleBin')
  (DeleteDirectory para carpetas). Borra para siempre solo si te lo pide así.
- Si una orden es ambigua y puede destruir datos, pregunta antes de hacerla.

Ratón y teclado:
- Puedes manejar la computadora como una persona: clic, escribir, teclas, desplazar y arrastrar.
- Mira siempre antes de actuar: ver_pantalla, y usa las coordenadas de esa última captura. Tras cada paso que
  cambie la pantalla (abrir algo, un clic que navega), vuelve a mirar para comprobar que salió bien.
- Prefiere atajos de teclado y abrir cuando sirvan: son más fiables que buscar botones.
- Con ratón y teclado no borres ni cambies archivos del sistema, ni abras archivos ocultos: eso va por comandos,
  donde se le pregunta a la persona. Nunca escribas contraseñas ni datos de pago.
- Si la persona activa el freno de emergencia (ratón en la esquina superior izquierda), para del todo y díselo.
"""

# Dos modelos: el rápido para conversar y las órdenes de siempre, y el más hábil cuando hay que manejar la
# pantalla, que es donde se nota la diferencia. MATE_MODELO fija uno solo para todo.
MODELO_RAPIDO = os.environ.get("MATE_MODELO") or os.environ.get("MATE_MODELO_RAPIDO", "claude-haiku-5-5")
MODELO_PANTALLA = os.environ.get("MATE_MODELO") or os.environ.get("MATE_MODELO_PANTALLA", "claude-sonnet-5-5")
PIDE_PANTALLA = re.compile(
    r"\b(pantalla|clic|click|pulsa|presiona|boton|ventana|pestana|escrib\w*\s+(en|un|una|el|la|lo)|"
    r"rellena|formulario|arrastra|selecciona|marca|configur\w*|ajuste|instala|desinstala|descarga|"
    r"word|excel|powerpoint|documento|correo|whatsapp|telegram|discord|mensaje|manda|envia|responde|"
    r"inicia sesion|registr\w*|compra|pagina|navega|cuenta|perfil)\b", re.I)


def modelo_para(texto):
    return MODELO_PANTALLA if PIDE_PANTALLA.search(normalizar(texto)) else MODELO_RAPIDO


FIN_DE_FRASE = re.compile(r"(?<=[.!?…])\s+")


def partir_frases(texto, minimo=12):
    """Separa las frases ya terminadas del trozo que aún se está escribiendo: (frases, resto)."""
    frases, inicio = [], 0
    for m in FIN_DE_FRASE.finditer(texto):
        frase = texto[inicio:m.start()].strip()
        if len(frase) >= minimo:   # "Sí." o "Vale." se juntan con la siguiente para no trocear la voz
            frases.append(frase)
            inicio = m.end()
    return frases, texto[inicio:]


class Cerebro(threading.Thread):
    """Corre el bucle asyncio del SDK en su propio hilo; la interfaz le habla con submit() y responder()."""

    def __init__(self, bus):
        super().__init__(daemon=True, name="cerebro")
        self.bus = bus
        self.loop = asyncio.new_event_loop()
        self._entrada = None
        self._pendiente = None
        self._cliente = None
        self._control_avisado = False   # si ya se apartó el HUD para manejar ratón y teclado en esta orden
        self._ultima = 0                # número de la última orden recibida: las anteriores ya no cuentan
        self._modelo = MODELO_RAPIDO
        self.ocupado = False
        herramientas.ocultar_hud = bus.ocultar_hud.emit

    # --- llamadas desde la interfaz (otro hilo) ---------------------------------------------
    def submit(self, texto):
        self._ultima += 1
        self.ocupado = True
        self.loop.call_soon_threadsafe(self._entrada.put_nowait, (self._ultima, texto))

    def responder(self, si):
        def resolver():
            if self._pendiente and not self._pendiente.done():
                self._pendiente.set_result(si)
        self.loop.call_soon_threadsafe(resolver)

    def interrumpir(self):
        self.responder(False)
        if self._cliente:
            asyncio.run_coroutine_threadsafe(self._cliente.interrupt(), self.loop)

    # --- puerta de permisos ---------------------------------------------------------------
    # La decisión va en un hook PreToolUse, que se ejecuta antes de TODAS las herramientas. can_use_tool no
    # basta: Claude Code deja pasar las lecturas (Read, Glob, Grep) sin consultarlo, y leer archivos ocultos
    # es justo uno de los casos que piden permiso.
    async def _antes_de_herramienta(self, datos, id_herramienta, contexto):
        si = await self._decidir(datos["tool_name"], datos.get("tool_input") or {})
        salida = {"hookEventName": "PreToolUse", "permissionDecision": "allow" if si else "deny"}
        if not si:
            salida["permissionDecisionReason"] = ("La persona ha dicho que no. No lo intentes de otra forma; "
                                                  "pregúntale qué prefiere.")
        return {"hookSpecificOutput": salida}

    async def _permitir_todo(self, nombre, entrada, contexto):
        return PermissionResultAllow()   # la decisión ya la tomó el hook

    async def _decidir(self, nombre, entrada):
        if nombre in CONTROL and not self._control_avisado:
            self._control_avisado = True
            if herramientas.hud_delante and herramientas.hud_delante():
                self.bus.ocultar_hud.emit()   # que el HUD no tape lo que va a manejar
                await asyncio.sleep(0.4)
        razon = riesgo.motivo(nombre, entrada)
        if razon is None:
            return True
        dicho, detalle = razon
        self._pendiente = self.loop.create_future()
        self.bus.pedir_permiso.emit(dicho, detalle)
        try:
            si = await asyncio.wait_for(self._pendiente, timeout=90)
        except asyncio.TimeoutError:
            si = False
        finally:
            self._pendiente = None
            self.bus.permiso_resuelto.emit()
        log.info("permiso %s para %s: %s", "SÍ" if si else "NO", nombre, detalle)
        return si

    # --- bucle principal ------------------------------------------------------------------
    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._principal())

    async def _principal(self):
        self._entrada = asyncio.Queue()
        opciones = ClaudeAgentOptions(
            system_prompt=INSTRUCCIONES,
            model=MODELO_RAPIDO,
            effort=os.environ.get("MATE_ESFUERZO", "low"),   # piensa poco en lo fácil: contesta antes
            include_partial_messages=True,                    # el texto llega en trozos y se dice frase a frase
            max_buffer_size=16 * 2**20,   # las capturas de pantalla viajan en un solo mensaje; 1 MB se quedaba corto
            cwd=str(Path.home()),
            setting_sources=[],          # nada de tus ajustes de Claude Code puede saltarse la puerta
            permission_mode="default",
            can_use_tool=self._permitir_todo,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._antes_de_herramienta],
                                              timeout=120, on_failure="block")]},   # si el hook falla, no se hace
            mcp_servers={"mate": servidor},
            disallowed_tools=["Agent", "Task"],
            stderr=lambda linea: log.debug("claude: %s", linea),
            # Sin esto Claude Code esconde las herramientas de Mate tras un ToolSearch, y cada orden pierde
            # unos 6 segundos buscándolas antes de usarlas.
            env={"ENABLE_TOOL_SEARCH": "false"},
        )
        while True:
            try:
                async with ClaudeSDKClient(options=opciones) as cliente:
                    self._cliente = cliente
                    self._modelo = MODELO_RAPIDO   # cada conexión nueva empieza con el modelo de partida
                    self.bus.estado_cerebro.emit("listo")
                    while True:
                        numero, texto = await self._entrada.get()
                        if numero == self._ultima:   # si ya llegó otra más nueva, esta se salta
                            await self._turno(cliente, numero, texto)
            except Exception as e:
                log.exception("el cerebro se cayó; reconecto")
                self.ocupado = False
                self.bus.error.emit(f"Se perdió la conexión con Claude: {e}")
                await asyncio.sleep(5)

    async def _turno(self, cliente, numero, texto):
        self.ocupado = True
        self._control_avisado = False
        self.bus.pensando.emit()
        pendiente, frases = "", 0   # el texto que llega en trozos, y cuántas frases se han dicho ya
        try:
            # La hora va con cada mensaje: así no necesita un comando para saberla.
            modelo = modelo_para(texto)
            if modelo != self._modelo:
                await cliente.set_model(modelo)
                self._modelo = modelo
                log.info("modelo: %s", modelo)
            await cliente.query(f"[{datetime.now():%A %d/%m/%Y %H:%M}] {texto}")
            async for msg in cliente.receive_response():
                if numero != self._ultima:
                    continue   # llegó una orden nueva: lo que quede de esta se descarta sin decirlo
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        pendiente += ev["delta"]["text"]
                        listas, pendiente = partir_frases(pendiente)
                        for f in listas:
                            self.bus.frase.emit(f)
                            frases += 1
                    elif ev.get("type") == "content_block_stop" and pendiente.strip():
                        self.bus.frase.emit(pendiente.strip())
                        pendiente, frases = "", frases + 1
                elif isinstance(msg, AssistantMessage):
                    for bloque in msg.content:
                        if isinstance(bloque, TextBlock) and bloque.text.strip():
                            self.bus.respuesta.emit(bloque.text.strip())   # a la conversación en pantalla
                            # El bloque completo puede llegar antes que su content_block_stop: aquí se dice lo
                            # que falte (todo, si no llegó en trozos) y se vacía, para no repetirlo después.
                            resto = pendiente.strip() if frases else bloque.text.strip()
                            if resto:
                                self.bus.frase.emit(resto)
                            pendiente, frases = "", 0
                        elif isinstance(bloque, ToolUseBlock):
                            self.bus.accion.emit(bloque.name.removeprefix("mcp__mate__"))
                elif isinstance(msg, ResultMessage) and msg.is_error:
                    self.bus.error.emit(msg.result or "Claude devolvió un error.")
        finally:
            self._control_avisado = False
            if numero == self._ultima:
                self.ocupado = False
                self.bus.turno_terminado.emit()
