"""Mate: un asistente de voz que vive en el escritorio de Windows, con Claude como cerebro.

Arranca con iniciar.bat (sin consola) o con  .venv\\Scripts\\python mate.py  para ver los mensajes.
"""
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
# Con pythonw no hay consola: todo va a mate.log (y así las barras de descarga no rompen nada).
SIN_CONSOLA = sys.stderr is None or sys.stdout is None
if SIN_CONSOLA:
    sys.stdout = sys.stderr = open(CARPETA / "mate.log", "a", encoding="utf-8", buffering=1)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stderr)] if SIN_CONSOLA else
                    [logging.FileHandler(CARPETA / "mate.log", encoding="utf-8"), logging.StreamHandler(sys.stderr)])
log = logging.getLogger("mate")
for ruidoso in ("httpx", "httpx2", "huggingface_hub"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import herramientas
from cerebro import Cerebro
from hud import ACENTO, Burbuja, Hud
from voz import Oido, Voz, normalizar, quitar_palabra_clave

SEGUIMIENTO_S = 8.0   # tras hablar Mate, puedes seguir la conversación sin decir "Mate"

SI = {"si", "hazlo", "adelante", "vale", "ok", "okay", "claro", "confirmo", "dale", "afirmativo", "por favor", "de acuerdo"}
NO = {"no", "cancela", "cancelar", "para", "detente", "nada", "negativo", "mejor no", "dejalo", "olvidalo"}
PARAR = ("para", "callate", "detente", "silencio", "cancela", "basta", "stop")


def saludo(nombre=""):
    """Buenos días, buenas tardes o buenas noches según la hora: «Buenas noches, Ana, ¿qué vamos a hacer hoy?»."""
    hora = datetime.now().hour
    if 5 <= hora < 12:
        inicio = "Buenos días"
    elif 12 <= hora < 20:
        inicio = "Buenas tardes"
    else:
        inicio = "Buenas noches"
    if nombre:
        inicio += f", {nombre}"
    return f"{inicio}, ¿qué vamos a hacer hoy?"


class Bus(QObject):
    """Señales entre hilos: Qt las entrega siempre en el hilo de la interfaz."""
    oido = Signal(str)
    estado_oido = Signal(str)
    estado_cerebro = Signal(str)
    pensando = Signal()
    respuesta = Signal(str)      # texto completo, para la conversación en pantalla
    frase = Signal(str)          # frase lista para decir en voz alta, en cuanto llega
    accion = Signal(str)
    turno_terminado = Signal()
    pedir_permiso = Signal(str, str)
    permiso_resuelto = Signal()
    error = Signal(str)
    voz_empieza = Signal(str)
    voz_termina = Signal()
    ocultar_hud = Signal()        # Mate va a manejar ratón y teclado: que nada suyo tape la pantalla




class Controlador(QObject):
    """Decide qué hacer con lo que se oye: despertar, ordenar, confirmar o parar.

    Es un QObject para que las señales de los otros hilos lleguen a sus métodos en el hilo de la interfaz.
    """

    def __init__(self, app):
        super().__init__()
        self.bus = Bus()
        self.estado = "dormido"
        self.nivel_micro = 0.0
        self.despierto_hasta = 0.0
        self.permiso_pendiente = False
        self.manejando = False   # Mate usa ratón y teclado en esta orden: la burbuja no debe tapar nada
        self.estado_oido = "cargando"

        self.voz = Voz(self.bus.voz_empieza.emit, self.bus.voz_termina.emit)
        self.oido = Oido(self.bus.oido.emit, self.bus.estado_oido.emit, self._al_nivel)
        self.cerebro = Cerebro(self.bus)
        self.hud = Hud(self)
        self.burbuja = Burbuja(self)
        self._esconder = QTimer(self, singleShot=True, interval=5000, timeout=self._esconder_burbuja)
        self._bandeja(app)

        b = self.bus
        b.oido.connect(self.al_oir)
        b.estado_oido.connect(self._al_estado_oido)
        b.estado_cerebro.connect(self._al_estado_cerebro)
        b.pensando.connect(self._al_pensar)
        b.respuesta.connect(self._al_responder)
        b.frase.connect(self.voz.decir)
        b.accion.connect(self._al_accion)
        b.turno_terminado.connect(self._al_terminar_turno)
        b.pedir_permiso.connect(self._al_pedir_permiso)
        b.permiso_resuelto.connect(self._al_resolver_permiso)
        b.error.connect(self._al_error)
        b.voz_empieza.connect(self._al_empezar_voz)
        b.voz_termina.connect(self._al_terminar_voz)
        b.ocultar_hud.connect(self._al_manejar)
        herramientas.hud_delante = self.burbuja.isVisible   # solo lee: se puede preguntar desde el cerebro
        for origen in (self.hud, self.burbuja):
            origen.texto_enviado.connect(self.orden)
            origen.respuesta_permiso.connect(self.contestar_permiso)
        self.hud.parar.connect(self.parar)

        self.voz.start()
        self.cerebro.start()
        self.oido.start()
        self.hud.mostrar_como_escritorio()
        texto = saludo(self.hud.cfg.get("nombre", ""))
        self.hud.anotar("mate", texto)
        self.voz.decir(texto)

    def _bandeja(self, app):
        px = QPixmap(64, 64)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(ACENTO, 6))
        p.setBrush(QColor(200, 220, 239))
        p.drawEllipse(8, 8, 48, 48)
        p.end()
        app.setWindowIcon(QIcon(px))
        self.bandeja = QSystemTrayIcon(QIcon(px), app)
        self.bandeja.setToolTip("Mate · clic para escribirle")
        menu = QMenu()
        menu.addAction(QAction("Escribir a Mate", menu, triggered=self._abrir_burbuja))
        self.acc_micro = QAction("Silenciar micrófono", menu, checkable=True, toggled=self._silenciar)
        menu.addAction(self.acc_micro)
        menu.addSeparator()
        menu.addAction(QAction("Salir", menu, triggered=app.quit))
        self.bandeja.setContextMenu(menu)
        self.bandeja.activated.connect(self._al_clic_bandeja)
        self.bandeja.show()
        self._menu = menu

    def _al_clic_bandeja(self, razon):
        if razon == QSystemTrayIcon.Trigger:
            if self.burbuja.isVisible() and not self.burbuja.ocupada():
                self.burbuja.ocultar()
            else:
                self._abrir_burbuja()

    def _abrir_burbuja(self):
        self._esconder.stop()
        self.burbuja.mostrar(escribir=True)
        self._mostrar_estado()

    def _silenciar(self, si):
        (self.oido.silenciado.set if si else self.oido.silenciado.clear)()
        self._mostrar_estado()

    def _al_nivel(self, v):
        self.nivel_micro = v   # un float: escribirlo desde otro hilo es seguro

    # --- estados ---------------------------------------------------------------------------
    def _poner(self, estado):
        self.estado = estado
        self._mostrar_estado()
        if estado in ("escuchando", "pensando", "hablando", "permiso", "error"):
            self._esconder.stop()
            if not self.manejando or estado == "permiso":
                self.burbuja.mostrar()
        else:
            self._esconder.start()

    def _esconder_burbuja(self):
        if self.estado == "dormido" and not self.burbuja.ocupada():
            self.burbuja.ocultar()
        elif self.estado == "dormido":
            self._esconder.start()   # estás escribiendo: se mira otra vez más tarde

    def _mostrar_estado(self):
        textos = {"pensando": "● PENSANDO", "hablando": "● HABLANDO", "permiso": "● ESPERANDO TU PERMISO",
                  "escuchando": "● TE ESCUCHO", "error": "● ERROR"}
        if self.estado in textos:
            texto = textos[self.estado]
        elif self.oido.silenciado.is_set():
            texto = "○ MICRÓFONO SILENCIADO · ESCRIBE"
        else:
            texto = {"cargando": "○ CARGANDO RECONOCIMIENTO DE VOZ…", "sin_micro": "○ SIN MICRÓFONO · ESCRIBE",
                     "sin_modelo": "○ SIN RECONOCIMIENTO DE VOZ · ESCRIBE",
                     "transcribiendo": "○ PROCESANDO…"}.get(self.estado_oido, "○ EN ESPERA · DI «MATE»")
        self.hud.poner_estado(texto)
        self.burbuja.poner(texto)

    def _al_estado_oido(self, e):
        self.estado_oido = e
        if e == "grabando" and self.estado == "dormido" and time.monotonic() < self.despierto_hasta:
            self._poner("escuchando")
        else:
            self._mostrar_estado()

    # --- lo que se oye o se escribe ---------------------------------------------------------
    def al_oir(self, texto):
        if self.permiso_pendiente:
            self._permiso_por_voz(texto)
            return
        clave, resto = quitar_palabra_clave(texto)
        if clave and any(normalizar(resto).strip(" .,!¡") == p for p in PARAR):
            self.parar()
            return
        if clave or time.monotonic() < self.despierto_hasta:
            if not resto:
                self.despierto_hasta = time.monotonic() + SEGUIMIENTO_S
                self.voz.callar()
                self.voz.decir("¿Sí?")
                self._poner("escuchando")
                QTimer.singleShot(int(SEGUIMIENTO_S * 1000) + 100, self._dormir_si_toca)
                return
            self.orden(resto)
        elif self.estado == "escuchando":
            self._poner("dormido")

    def orden(self, texto):
        if self.permiso_pendiente:
            self._permiso_por_voz(texto)
            return
        # Una orden nueva manda: se corta lo que estuviera diciendo o haciendo y se atiende esta.
        self.voz.callar()
        if self.cerebro.ocupado:
            self.cerebro.interrumpir()
        self.despierto_hasta = 0.0
        self.hud.anotar("tú", texto)
        self.burbuja.poner("● PENSANDO", "")
        self.cerebro.submit(texto)

    def _permiso_por_voz(self, texto):
        _, resto = quitar_palabra_clave(texto)
        t = normalizar(resto).strip(" .,!¡?¿")
        palabras = set(t.split())
        if t in NO or palabras & {"no", "cancela", "cancelar", "detente"}:
            self.contestar_permiso(False)
        elif t in SI or palabras & {"si", "hazlo", "adelante", "vale", "confirmo", "dale"}:
            self.contestar_permiso(True)

    def contestar_permiso(self, si):
        if self.permiso_pendiente:
            self.cerebro.responder(si)

    def parar(self):
        self.voz.callar()
        if self.cerebro.ocupado:
            self.cerebro.interrumpir()
        self.despierto_hasta = 0.0
        self._poner("dormido")

    # --- lo que llega del cerebro y de la voz ---------------------------------------------
    def _al_estado_cerebro(self, e):
        self._mostrar_estado()

    def _al_pensar(self):
        self._poner("pensando")

    def _al_accion(self, nombre):
        self.hud.poner_estado(f"● TRABAJANDO · {nombre.upper()}")
        self.burbuja.poner(f"● TRABAJANDO · {nombre.upper()}")

    def _al_responder(self, texto):
        self.hud.anotar("mate", texto)   # la voz ya va diciéndolo frase a frase
        self.burbuja.poner(self.burbuja.lbl_estado.text(), texto)

    def _al_manejar(self):
        self.manejando = True
        if not self.permiso_pendiente:
            self.burbuja.ocultar()

    def _al_terminar_turno(self):
        self.manejando = False
        if self.estado in ("pensando", "permiso"):
            self._poner("dormido")

    def _al_pedir_permiso(self, dicho, detalle):
        self.permiso_pendiente = True
        self.hud.anotar("sistema", f"Permiso para: {dicho}\n{detalle[:300]}")
        self.hud.mostrar_permiso(True)
        self.burbuja.fila_permiso.show()
        self.burbuja.poner("● ESPERANDO TU PERMISO", f"Permiso para: {dicho}")
        self._poner("permiso")
        self.voz.decir(f"Necesito tu permiso para esto: {dicho}. ¿Lo hago? Di sí o no.")

    def _al_resolver_permiso(self):
        self.permiso_pendiente = False
        self.hud.mostrar_permiso(False)
        self.burbuja.fila_permiso.hide()
        if self.manejando:
            self.burbuja.ocultar()
        self._poner("pensando" if self.cerebro.ocupado else "dormido")

    def _al_error(self, msg):
        log.error(msg)
        self.hud.anotar("sistema", msg)
        self.burbuja.poner("● ERROR", msg)
        self._poner("error")
        QTimer.singleShot(4000, self._salir_de_error)

    def _salir_de_error(self):
        if self.estado == "error":
            self._poner("dormido")

    def _al_empezar_voz(self, texto):
        self.oido.pausado.set()
        if not self.permiso_pendiente:
            self._poner("hablando")

    def _al_terminar_voz(self):
        self.oido.pausado.clear()
        if self.permiso_pendiente:
            self._poner("permiso")
        elif self.cerebro.ocupado:
            self._poner("pensando")
        elif self.oido.silenciado.is_set() or self.estado_oido in ("cargando", "sin_micro", "sin_modelo"):
            self._poner("dormido")   # sin oído no hay conversación que seguir
        else:
            self.despierto_hasta = time.monotonic() + SEGUIMIENTO_S
            self._poner("escuchando")
            QTimer.singleShot(int(SEGUIMIENTO_S * 1000) + 100, self._dormir_si_toca)

    def _dormir_si_toca(self):
        if self.estado == "escuchando" and time.monotonic() >= self.despierto_hasta:
            self._poner("dormido")


def ya_hay_otro_mate():
    """Un mutex con nombre de Windows: si ya existe, hay otro Mate abierto."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    main.mutex = k32.CreateMutexW(None, False, "Local\\MateAsistente")   # vive mientras viva el proceso
    return ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS


def main():
    if ya_hay_otro_mate():
        log.info("ya hay un Mate en marcha; no abro otro")
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 9))
    ctrl = Controlador(app)
    log.info("Mate en marcha")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
