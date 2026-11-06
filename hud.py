"""El HUD de Mate: una pantalla completa sobria, con el reactor en el centro, el sistema a la vista y la conversación."""
import ctypes
import math
import os
import time
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import sistema

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", ctypes.c_uint)]

FUENTE = "Bahnschrift"   # la fuente técnica que trae Windows 10 y 11

# Paleta sobria: grafito, acero y un azul apagado. El ámbar y el rojo solo para avisos.
FONDO = QColor(9, 11, 14)
PANEL = QColor(17, 21, 26, 235)
BORDE = QColor(48, 57, 67)
ACENTO = QColor(91, 141, 184)
ACENTO_TENUE = QColor(46, 70, 92)
TEXTO = QColor(214, 221, 229)
TEXTO2 = QColor(124, 135, 148)
AMBAR = QColor(201, 161, 74)
ROJO = QColor(181, 83, 76)

COLORES_ESTADO = {
    "dormido": ACENTO, "escuchando": QColor(156, 195, 230), "pensando": AMBAR,
    "hablando": QColor(200, 220, 239), "permiso": QColor(208, 138, 60), "error": ROJO,
}

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def rgba(c, a=None):
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha() if a is None else a})"


def chaflan(rect, corte):
    """Rectángulo con las esquinas superior izquierda e inferior derecha cortadas en diagonal."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    p = QPainterPath()
    p.moveTo(x + corte, y)
    p.lineTo(x + w, y)
    p.lineTo(x + w, y + h - corte)
    p.lineTo(x + w - corte, y + h)
    p.lineTo(x, y + h)
    p.lineTo(x, y + corte)
    p.closeSubpath()
    return p


class Panel(QWidget):
    """Un panel con título, esquinas en chaflán y una marca de color en el borde."""

    def __init__(self, titulo, padre):
        super().__init__(padre)
        self.titulo = titulo.upper()
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        forma = chaflan(r, 14)
        p.fillPath(forma, PANEL)
        p.setPen(QPen(BORDE, 1))
        p.drawPath(forma)
        p.setPen(QPen(ACENTO, 2))
        p.drawLine(QPointF(18, 1), QPointF(70, 1))
        p.setPen(TEXTO2)
        p.setFont(QFont(FUENTE, 8, QFont.Bold))
        p.drawText(QRectF(18, 8, self.width() - 36, 16), Qt.AlignLeft | Qt.AlignVCenter, self.titulo)


class Grafica(QWidget):
    """Gráfica de los últimos 60 segundos de una serie."""

    def __init__(self, serie, maximo=100.0, color=ACENTO, padre=None):
        super().__init__(padre)
        self.serie, self.maximo, self.color = serie, maximo, color

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(BORDE.red(), BORDE.green(), BORDE.blue(), 120), 1, Qt.DotLine))
        for i in range(1, 4):
            p.drawLine(0, int(h * i / 4), w, int(h * i / 4))
        datos = list(self.serie)
        tope = self.maximo or max(1.0, max(datos) * 1.2)   # maximo 0: la escala sigue a los datos
        camino = QPainterPath()
        paso = w / (len(datos) - 1)
        for i, v in enumerate(datos):
            pt = QPointF(i * paso, h - 1 - (h - 2) * min(v / tope, 1.0))
            camino.moveTo(pt) if i == 0 else camino.lineTo(pt)
        relleno = QPainterPath(camino)
        relleno.lineTo(w, h)
        relleno.lineTo(0, h)
        grad = QLinearGradient(0, 0, 0, h)
        c1, c2 = QColor(self.color), QColor(self.color)
        c1.setAlpha(90)
        c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(relleno, QBrush(grad))
        p.setPen(QPen(self.color, 1.5))
        p.drawPath(camino)


def boton(texto, padre, acento=ACENTO, alinear="left"):
    b = QPushButton(texto, padre)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setStyleSheet(
        f"QPushButton {{ background: {rgba(PANEL, 200)}; color: {rgba(TEXTO)}; border: 1px solid {rgba(BORDE)};"
        f" border-left: 3px solid {rgba(acento)}; padding: 4px 12px; text-align: {alinear};"
        f" font-family: '{FUENTE}'; font-size: 10pt; letter-spacing: 1px; }}"
        f"QPushButton:hover {{ background: {rgba(ACENTO_TENUE, 230)}; border-color: {rgba(acento)}; color: white; }}"
        f"QPushButton:pressed {{ background: {rgba(acento, 200)}; }}")
    return b


def etiqueta(texto, padre, tam=10, color=TEXTO, negrita=False):
    l = QLabel(texto, padre)
    l.setStyleSheet(f"color: {rgba(color)}; background: transparent; font-family: '{FUENTE}'; font-size: {tam}pt;"
                    f" font-weight: {'bold' if negrita else 'normal'};")
    return l


class Hud(QWidget):
    texto_enviado = Signal(str)
    respuesta_permiso = Signal(bool)
    parar = Signal()

    def __init__(self, ctrl):
        # Tool: sin botón en la barra de tareas ni en Alt+Tab, como el escritorio.
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint)
        self.ctrl = ctrl
        self.setWindowTitle("Mate")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.fase, self.nivel = 0.0, 0.0
        self.cfg = sistema.leer_config()
        self.stats = sistema.Estadisticas()

        # --- cabecera ------------------------------------------------------------------
        self.lbl_nombre = etiqueta("M.A.T.E.", self, 26, TEXTO, True)
        self.lbl_sub = etiqueta(f"SISTEMA PERSONAL · {os.environ.get('USERNAME', '').upper()}", self, 9, TEXTO2)
        self.lbl_estado = etiqueta("INICIANDO", self, 10, ACENTO, True)
        self.lbl_hora = etiqueta("", self, 64, TEXTO, True)
        self.lbl_hora.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_fecha = etiqueta("", self, 13, TEXTO2)
        self.lbl_fecha.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # --- apps (columna izquierda) -----------------------------------------------------
        self.panel_apps = Panel("Aplicaciones", self)
        self.botones_apps = []
        self.apps = sistema.Apps(self._apps_listas)

        # --- carpetas y webs alrededor del reactor ---------------------------------------------
        self.botones_carpetas = []
        for nombre, ruta in sistema.carpetas():
            b = boton(nombre.upper(), self, ACENTO_TENUE, "center")
            b.clicked.connect(lambda _=False, r=ruta: os.startfile(str(r)))
            self.botones_carpetas.append(b)
        self.botones_webs = []
        for nombre, url in self.cfg["webs"].items():
            b = boton(nombre.upper(), self, ACENTO, "left")
            b.clicked.connect(lambda _=False, u=url: sistema.abrir_web(u))
            self.botones_webs.append(b)

        # --- conversación (derecha) ----------------------------------------------------------
        self.panel_charla = Panel("Conversación", self)
        self.historial = QTextBrowser(self.panel_charla)
        self.historial.setOpenExternalLinks(False)
        self.historial.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; color: {rgba(TEXTO)};"
                                     f" font-family: 'Segoe UI'; font-size: 11pt; }}"
                                     f"QScrollBar:vertical {{ width: 6px; background: transparent; }}"
                                     f"QScrollBar::handle:vertical {{ background: {rgba(BORDE)}; }}")
        self.fila_permiso = QWidget(self.panel_charla)
        fila = QHBoxLayout(self.fila_permiso)
        fila.setContentsMargins(0, 0, 0, 0)
        self.btn_si = boton("SÍ, HAZLO", self.fila_permiso, QColor(95, 158, 116), "center")
        self.btn_no = boton("NO", self.fila_permiso, ROJO, "center")
        self.btn_si.clicked.connect(lambda: self.respuesta_permiso.emit(True))
        self.btn_no.clicked.connect(lambda: self.respuesta_permiso.emit(False))
        fila.addWidget(self.btn_si)
        fila.addWidget(self.btn_no)
        self.fila_permiso.hide()
        self.entrada = QLineEdit(self.panel_charla)
        self.entrada.setPlaceholderText("Escribe a Mate o di «Mate, …»")
        self.entrada.setStyleSheet(f"QLineEdit {{ background: {rgba(FONDO, 220)}; color: {rgba(TEXTO)};"
                                   f" border: 1px solid {rgba(BORDE)}; border-bottom: 2px solid {rgba(ACENTO)};"
                                   f" padding: 6px 10px; font-family: 'Segoe UI'; font-size: 11pt; }}")
        self.entrada.returnPressed.connect(self._enviar)
        caja = QVBoxLayout(self.panel_charla)
        caja.setContentsMargins(16, 30, 16, 14)
        caja.addWidget(self.historial, 1)
        caja.addWidget(self.fila_permiso)
        caja.addWidget(self.entrada)

        # --- notas -------------------------------------------------------------------------
        self.panel_notas = Panel("Notas", self)
        self.notas = QPlainTextEdit(self.panel_notas)
        self.notas.setStyleSheet(f"QPlainTextEdit {{ background: transparent; border: none; color: {rgba(TEXTO2)};"
                                 f" font-family: 'Segoe UI'; font-size: 10pt; }}")
        self._archivo_notas = sistema.CARPETA / "notas.txt"
        if self._archivo_notas.exists():
            self.notas.setPlainText(self._archivo_notas.read_text(encoding="utf-8"))
        self.notas.textChanged.connect(lambda: self._guardar_notas.start(800))
        self._guardar_notas = QTimer(self, singleShot=True, timeout=self._escribir_notas)
        QVBoxLayout(self.panel_notas).setContentsMargins(16, 30, 16, 12)
        self.panel_notas.layout().addWidget(self.notas)

        # --- sistema (abajo izquierda) ----------------------------------------------------
        self.panel_sistema = Panel("Sistema", self)
        self.lbl_cpu = etiqueta("", self.panel_sistema, 10, TEXTO)
        self.lbl_ram = etiqueta("", self.panel_sistema, 10, TEXTO)
        self.lbl_red = etiqueta("", self.panel_sistema, 10, TEXTO)
        self.lbl_info = etiqueta("", self.panel_sistema, 9, TEXTO2)
        self.g_cpu = Grafica(self.stats.cpu, 100, ACENTO, self.panel_sistema)
        self.g_ram = Grafica(self.stats.ram, 100, QColor(140, 160, 180), self.panel_sistema)
        self.g_red = Grafica(self.stats.bajada, 0, QColor(120, 170, 150), self.panel_sistema)

        # --- multimedia (abajo derecha) ----------------------------------------------------
        self.panel_media = Panel("Multimedia", self)
        self.botones_media = []
        for texto, accion in (("⏮", "anterior"), ("⏯", "pausa"), ("⏭", "siguiente"),
                              ("🔇", "silencio"), ("−", "bajar"), ("+", "subir")):
            b = boton(texto, self.panel_media, ACENTO_TENUE, "center")
            b.setStyleSheet(b.styleSheet().replace(f"font-family: '{FUENTE}'; font-size: 10pt",
                                                   "font-family: 'Segoe UI Symbol'; font-size: 18pt"))
            b.clicked.connect(lambda _=False, a=accion: sistema.tecla_multimedia(a))
            self.botones_media.append(b)

        # --- energía (abajo centro) -------------------------------------------------------
        self.botones_energia = []
        for texto, accion in (("BLOQUEAR", "bloquear"), ("CERRAR SESIÓN", "cerrar_sesion"),
                              ("REINICIAR", "reiniciar"), ("APAGAR", "apagar")):
            b = boton(texto, self, ROJO if accion in ("apagar", "reiniciar") else ACENTO_TENUE, "center")
            b.clicked.connect(lambda _=False, a=accion: self._energia(a))
            self.botones_energia.append(b)

        self.reloj = QTimer(self, interval=33, timeout=self._tic)
        self.reloj.start()
        self.lento = QTimer(self, interval=1000, timeout=self._cada_segundo)
        self.lento.start()
        self._cada_segundo()

    # --- API para el controlador ---------------------------------------------------------------
    def poner_estado(self, texto):
        self.lbl_estado.setText(texto)

    def anotar(self, quien, texto):
        color = {"tú": TEXTO2, "mate": TEXTO, "sistema": AMBAR}[quien]
        nombre = {"tú": "TÚ", "mate": "MATE", "sistema": "SISTEMA"}[quien]
        hora = datetime.now().strftime("%H:%M")
        texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
        self.historial.append(f"<p style='margin:6px 0'><span style='color:{rgba(ACENTO)};font-family:{FUENTE};"
                              f"font-size:8pt'>{nombre} · {hora}</span><br>"
                              f"<span style='color:{rgba(color)}'>{texto}</span></p>")
        self.historial.verticalScrollBar().setValue(self.historial.verticalScrollBar().maximum())

    def mostrar_permiso(self, visible):
        self.fila_permiso.setVisible(visible)

    def mostrar_como_escritorio(self):
        """Ocupa la pantalla principal menos la barra de tareas y se queda detrás de todas las ventanas.

        El dueño de la ventana pasa a ser el escritorio de Windows (Progman): así Win+D («mostrar escritorio»)
        no la esconde, igual que pasa con los widgets de escritorio.
        """
        self.setGeometry(QApplication.primaryScreen().availableGeometry())
        self.show()
        hwnd = int(self.winId())
        escritorio = user32.FindWindowW("Progman", None)
        if escritorio:
            user32.SetWindowLongPtrW(hwnd, -8, escritorio)   # GWLP_HWNDPARENT: el dueño
        user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_BOTTOM, sin mover ni activar

    def nativeEvent(self, tipo, mensaje):
        # Cada vez que Windows va a cambiar su sitio en la pila de ventanas (al hacer clic en ella, por
        # ejemplo), se le dice que vaya al fondo: así nunca tapa tus aplicaciones.
        if tipo == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(mensaje))
            if msg.message == 0x0046:   # WM_WINDOWPOSCHANGING
                pos = _WINDOWPOS.from_address(msg.lParam)
                pos.hwndInsertAfter = 1   # HWND_BOTTOM
                pos.flags &= ~0x0004      # quita SWP_NOZORDER
        return False, 0

    # --- acciones -------------------------------------------------------------------------
    def _enviar(self):
        texto = self.entrada.text().strip()
        self.entrada.clear()
        if texto:
            self.texto_enviado.emit(texto)

    def _escribir_notas(self):
        self._archivo_notas.write_text(self.notas.toPlainText(), encoding="utf-8")

    def _energia(self, accion):
        if accion == "bloquear":
            sistema.bloquear()
            return
        nombre = {"apagar": "apagar el equipo", "reiniciar": "reiniciar el equipo",
                  "cerrar_sesion": "cerrar la sesión"}[accion]
        caja = QMessageBox(QMessageBox.Warning, "Mate", f"¿Seguro que quieres {nombre}?\n"
                           "Lo que no hayas guardado en otros programas se perderá.",
                           QMessageBox.Yes | QMessageBox.No, self)
        caja.setDefaultButton(QMessageBox.No)
        if caja.exec() == QMessageBox.Yes:
            sistema.energia(accion)

    def _apps_listas(self):
        # Llega desde el hilo que busca las apps: los botones se crean en el hilo de la interfaz.
        QTimer.singleShot(0, self, self._crear_botones_apps)

    def _crear_botones_apps(self):
        for nombre in self.cfg["apps"]:
            if not self.apps.existe(nombre):
                continue
            b = boton(nombre.upper(), self.panel_apps)
            b.clicked.connect(lambda _=False, n=nombre: self.apps.abrir(n))
            b.show()
            self.botones_apps.append(b)
        self._colocar()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.parar.emit()

    def mouseDoubleClickEvent(self, e):
        c, r = self._centro_reactor()
        if math.dist((e.position().x(), e.position().y()), (c.x(), c.y())) < r:
            self.parar.emit()

    # --- reloj y estadísticas -----------------------------------------------------------------
    def _cada_segundo(self):
        ahora = datetime.now()
        self.lbl_hora.setText(ahora.strftime("%H:%M"))
        self.lbl_fecha.setText(f"{DIAS[ahora.weekday()].upper()} · {ahora.day} DE {MESES[ahora.month - 1].upper()}"
                               f" DE {ahora.year}")
        self.stats.muestrear()
        s = self.stats
        self.lbl_cpu.setText(f"CPU   {s.cpu[-1]:4.0f} %")
        self.lbl_ram.setText(f"RAM   {s.ram[-1]:4.0f} %   {s.ram_usada:.1f} / {s.ram_total:.1f} GB")
        self.lbl_red.setText(f"RED   ↓ {s.bajada[-1]:7.1f} KB/s   ↑ {s.subida[-1]:7.1f} KB/s")
        self.lbl_info.setText(f"DISCO {s.disco:.0f} %   ·   IP {s.ip_local()}   ·   ENCENDIDO {s.encendido()}")
        for g in (self.g_cpu, self.g_ram, self.g_red):
            g.update()

    def _tic(self):
        estado = self.ctrl.estado
        objetivo = self.ctrl.voz.nivel() if estado == "hablando" else self.ctrl.nivel_micro
        self.nivel += (objetivo - self.nivel) * 0.3
        self.fase += {"pensando": 0.10, "permiso": 0.04}.get(estado, 0.012)
        # solo se repinta el reactor, no la pantalla entera
        c, r = self._centro_reactor()
        m = r * 1.45
        self.update(int(c.x() - m), int(c.y() - m), int(2 * m), int(2 * m))

    # --- disposición ------------------------------------------------------------------------
    def _centro_reactor(self):
        w, h = self.width(), self.height()
        return QPointF(w * 0.43, h * 0.47), min(w, h) * 0.17

    def resizeEvent(self, _):
        self._colocar()

    def _colocar(self):
        w, h = self.width(), self.height()
        s = min(w / 1920, h / 1080)
        m = int(32 * s)
        self.lbl_nombre.setGeometry(m + int(90 * s), m, int(400 * s), int(48 * s))
        self.lbl_sub.setGeometry(m + int(92 * s), m + int(46 * s), int(500 * s), int(20 * s))
        self.lbl_estado.setGeometry(m + int(92 * s), m + int(68 * s), int(600 * s), int(22 * s))
        self.lbl_hora.setGeometry(w - m - int(420 * s), m - int(8 * s), int(420 * s), int(96 * s))
        self.lbl_fecha.setGeometry(w - m - int(520 * s), m + int(86 * s), int(520 * s), int(24 * s))

        # columna de apps
        alto_boton, hueco = int(36 * s), int(8 * s)
        n = max(len(self.botones_apps), 1)
        self.panel_apps.setGeometry(m, int(150 * s), int(260 * s), int(40 * s) + n * (alto_boton + hueco))
        for i, b in enumerate(self.botones_apps):
            b.setGeometry(int(14 * s), int(34 * s) + i * (alto_boton + hueco), int(232 * s), alto_boton)

        # conversación y notas a la derecha
        x_der, ancho_der = w - m - int(560 * s), int(560 * s)
        self.panel_charla.setGeometry(x_der, int(150 * s), ancho_der, int(500 * s))
        self.panel_notas.setGeometry(x_der, int(665 * s), ancho_der, int(170 * s))

        # sistema y multimedia abajo
        self.panel_sistema.setGeometry(m, h - m - int(190 * s), int(620 * s), int(190 * s))
        ps = self.panel_sistema
        fila = int(26 * s)
        for i, (lbl, g) in enumerate(((self.lbl_cpu, self.g_cpu), (self.lbl_ram, self.g_ram),
                                      (self.lbl_red, self.g_red))):
            y = int(32 * s) + i * (fila + int(14 * s))
            lbl.setGeometry(int(16 * s), y, int(330 * s), fila)
            g.setGeometry(int(350 * s), y - int(4 * s), ps.width() - int(366 * s), fila + int(8 * s))
        self.lbl_info.setGeometry(int(16 * s), ps.height() - int(36 * s), ps.width() - int(32 * s), int(22 * s))

        self.panel_media.setGeometry(x_der, h - m - int(110 * s), ancho_der, int(110 * s))
        bw = (ancho_der - int(32 * s) - 5 * int(8 * s)) // 6
        for i, b in enumerate(self.botones_media):
            b.setGeometry(int(16 * s) + i * (bw + int(8 * s)), int(40 * s), bw, int(48 * s))

        # energía, centrada abajo
        ew, eh = int(150 * s), int(34 * s)
        total = len(self.botones_energia) * ew + (len(self.botones_energia) - 1) * int(8 * s)
        x0 = int(w * 0.43 - total / 2)
        for i, b in enumerate(self.botones_energia):
            b.setGeometry(x0 + i * (ew + int(8 * s)), h - m - eh, ew, eh)

        # carpetas en fila bajo el reactor, webs en arco arriba a la derecha
        c, r = self._centro_reactor()
        bw, bh, hueco = int(128 * s), int(34 * s), int(8 * s)
        n = len(self.botones_carpetas)
        x0 = c.x() - (n * bw + (n - 1) * hueco) / 2
        for i, b in enumerate(self.botones_carpetas):
            b.setGeometry(int(x0 + i * (bw + hueco)), int(c.y() + r * 1.55), bw, bh)
        self._anclas_webs = []
        n = len(self.botones_webs)
        for i, b in enumerate(self.botones_webs):
            a = math.radians(-70 + i * (60 / max(n - 1, 1)))
            borde = QPointF(c.x() + r * 1.12 * math.cos(a), c.y() + r * 1.12 * math.sin(a))
            x = c.x() + r * 1.5 * math.cos(a) + r * 0.25
            y = c.y() + r * 1.5 * math.sin(a) - bh / 2
            b.setGeometry(int(x), int(y), int(140 * s), bh)
            self._anclas_webs.append((borde, QPointF(x, y + bh / 2)))
        self.update()

    # --- pintura ----------------------------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(ev.rect(), FONDO)
        w, h = self.width(), self.height()
        s = min(w / 1920, h / 1080)

        # rejilla tenue y marco
        p.setPen(QPen(QColor(255, 255, 255, 8), 1))
        paso = int(48 * s)
        for x in range(0, w, paso):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, paso):
            p.drawLine(0, y, w, y)
        marco = chaflan(QRectF(10, 10, w - 20, h - 20), 40 * s)
        p.setPen(QPen(BORDE, 1))
        p.drawPath(marco)
        p.setPen(QPen(ACENTO_TENUE, 3))
        p.drawLine(QPointF(10 + 40 * s, 10), QPointF(10 + 300 * s, 10))
        p.drawLine(QPointF(w - 10 - 300 * s, h - 10), QPointF(w - 10 - 40 * s, h - 10))

        # emblema de la cabecera
        m = 32 * s
        self._anillos(p, QPointF(m + 38 * s, m + 44 * s), 34 * s, ACENTO, 0.7, simple=True)

        # líneas a las webs
        p.setPen(QPen(ACENTO_TENUE, 1))
        for a, b in getattr(self, "_anclas_webs", []):
            p.drawLine(a, b)
            p.setBrush(ACENTO)
            p.drawEllipse(a, 3, 3)

        c, r = self._centro_reactor()
        self._reactor(p, c, r)

    def _anillos(self, p, c, r, color, brillo, simple=False):
        def arcos(radio, grosor, n, hueco, giro, alfa):
            col = QColor(color)
            col.setAlphaF(max(0.0, min(1.0, alfa)))
            p.setPen(QPen(col, grosor, Qt.SolidLine, Qt.FlatCap))
            p.setBrush(Qt.NoBrush)
            caja = QRectF(c.x() - radio, c.y() - radio, 2 * radio, 2 * radio)
            tramo = 360 / n
            for i in range(n):
                p.drawArc(caja, int((i * tramo + giro) * 16), int((tramo - hueco) * 16))

        g = math.degrees(self.fase)
        arcos(r * 0.97, max(2, r * 0.04), 3, 14, g, 0.9 * brillo + 0.1)
        arcos(r * 0.82, max(3, r * 0.08), 24, 5, -g * 0.6, 0.5 * brillo + 0.15)
        if simple:
            return
        arcos(r * 0.66, max(1.5, r * 0.02), 8, 20, g * 1.7, 0.8 * brillo)
        arcos(r * 0.52, max(2, r * 0.05), 3, 50, -g * 2.2, 0.9 * brillo)

    def _reactor(self, p, c, r):
        estado = self.ctrl.estado
        color = COLORES_ESTADO.get(estado, ACENTO)
        dormido = estado == "dormido"
        latido = 0.5 + 0.5 * math.sin(time.monotonic() * (1.2 if dormido else 3))
        brillo = (0.35 + 0.2 * latido) if dormido else (0.6 + 0.4 * max(self.nivel, latido * 0.4))

        # anillo exterior con marcas, y arcos de CPU (izquierda) y RAM (derecha)
        p.setPen(QPen(BORDE, 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r * 1.32, r * 1.32)
        p.drawEllipse(c, r * 1.12, r * 1.12)
        for i in range(120):
            a = math.radians(i * 3)
            largo = r * (0.05 if i % 10 == 0 else 0.025)
            col = ACENTO if i % 10 == 0 else BORDE
            p.setPen(QPen(col, 1))
            p.drawLine(QPointF(c.x() + (r * 1.32) * math.cos(a), c.y() + (r * 1.32) * math.sin(a)),
                       QPointF(c.x() + (r * 1.32 - largo) * math.cos(a), c.y() + (r * 1.32 - largo) * math.sin(a)))
        caja = QRectF(c.x() - r * 1.22, c.y() - r * 1.22, r * 2.44, r * 2.44)
        for valor, inicio, col in ((self.stats.cpu[-1], 100, ACENTO), (self.stats.ram[-1], -80, QColor(140, 160, 180))):
            p.setPen(QPen(QColor(BORDE.red(), BORDE.green(), BORDE.blue(), 160), r * 0.07, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(caja, int(inicio * 16), int(160 * 16))
            p.setPen(QPen(col, r * 0.07, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(caja, int(inicio * 16), int(160 * 16 * valor / 100))
        p.setPen(TEXTO2)
        p.setFont(QFont(FUENTE, max(7, int(r * 0.06)), QFont.Bold))
        p.drawText(QRectF(c.x() - r * 1.5, c.y() - r * 1.5, r * 0.6, r * 0.2), Qt.AlignLeft,
                   f"CPU {self.stats.cpu[-1]:.0f}%")
        p.drawText(QRectF(c.x() + r * 0.9, c.y() + r * 1.3, r * 0.6, r * 0.2), Qt.AlignRight,
                   f"RAM {self.stats.ram[-1]:.0f}%")

        # disco y resplandor
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(5, 8, 11))
        p.drawEllipse(c, r * 1.02, r * 1.02)
        halo = QRadialGradient(c, r * (0.85 + 0.2 * self.nivel))
        nucleo, borde = QColor(color), QColor(color)
        nucleo.setAlphaF(min(1.0, brillo * 0.8))
        borde.setAlpha(0)
        halo.setColorAt(0.0, QColor(235, 242, 250, int(200 * brillo)))
        halo.setColorAt(0.15, nucleo)
        halo.setColorAt(1.0, borde)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r, r)
        self._anillos(p, c, r, color, brillo)

        # nombre en el centro
        p.setPen(QColor(235, 242, 250, int(120 + 135 * brillo)))
        p.setFont(QFont(FUENTE, max(10, int(r * 0.13)), QFont.Bold))
        p.drawText(QRectF(c.x() - r, c.y() - r * 0.12, 2 * r, r * 0.24), Qt.AlignCenter, "MATE")


class Burbuja(QWidget):
    """Una ventanita siempre encima, abajo a la derecha: Mate cuando tus aplicaciones tapan el escritorio.

    Aparece mientras escucha, piensa, habla o pide permiso, y se va sola cuando vuelve a estar en espera.
    """
    texto_enviado = Signal(str)
    respuesta_permiso = Signal(bool)
    ANCHO, ALTO = 460, 168

    def __init__(self, ctrl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.ctrl = ctrl
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # aparecer no te quita el teclado de lo que estés usando
        self.setFixedSize(self.ANCHO, self.ALTO)

        self.lbl_estado = etiqueta("", self, 9, ACENTO, True)
        self.lbl_texto = etiqueta("", self, 10, TEXTO)
        self.lbl_texto.setWordWrap(True)
        self.lbl_texto.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_texto.setStyleSheet(self.lbl_texto.styleSheet().replace(f"font-family: '{FUENTE}'",
                                                                          "font-family: 'Segoe UI'"))
        self.fila_permiso = QWidget(self)
        fila = QHBoxLayout(self.fila_permiso)
        fila.setContentsMargins(0, 0, 0, 0)
        si, no = boton("SÍ, HAZLO", self.fila_permiso, QColor(95, 158, 116), "center"), boton("NO", self.fila_permiso, ROJO, "center")
        si.clicked.connect(lambda: self.respuesta_permiso.emit(True))
        no.clicked.connect(lambda: self.respuesta_permiso.emit(False))
        fila.addWidget(si)
        fila.addWidget(no)
        self.fila_permiso.hide()
        self.entrada = QLineEdit(self)
        self.entrada.setPlaceholderText("Escribe a Mate…")
        self.entrada.setStyleSheet(f"QLineEdit {{ background: {rgba(FONDO, 230)}; color: {rgba(TEXTO)};"
                                   f" border: 1px solid {rgba(BORDE)}; border-bottom: 2px solid {rgba(ACENTO)};"
                                   f" padding: 4px 8px; font-family: 'Segoe UI'; font-size: 10pt; }}")
        self.entrada.returnPressed.connect(self._enviar)

        x0 = 96
        self.lbl_estado.setGeometry(x0, 12, self.ANCHO - x0 - 16, 18)
        self.lbl_texto.setGeometry(x0, 32, self.ANCHO - x0 - 16, 62)
        self.fila_permiso.setGeometry(x0, 96, self.ANCHO - x0 - 16, 30)
        self.entrada.setGeometry(x0, 130, self.ANCHO - x0 - 16, 28)
        self.reloj = QTimer(self, interval=40, timeout=self.update)

    def mostrar(self, escribir=False):
        zona = QApplication.primaryScreen().availableGeometry()
        self.move(zona.right() - self.ANCHO - 16, zona.bottom() - self.ALTO - 16)
        self.show()
        self.reloj.start()
        if escribir:
            self.activateWindow()
            self.entrada.setFocus()

    def ocultar(self):
        self.hide()
        self.reloj.stop()

    def ocupada(self):
        """Si estás escribiendo en ella o tiene botones de permiso: entonces no se esconde sola."""
        return self.fila_permiso.isVisible() or bool(self.entrada.text()) or self.entrada.hasFocus()

    def poner(self, estado, texto=None):
        self.lbl_estado.setText(estado)
        if texto is not None:
            self.lbl_texto.setText(texto if len(texto) < 230 else texto[:227] + "…")

    def _enviar(self):
        texto = self.entrada.text().strip()
        self.entrada.clear()
        if texto:
            self.texto_enviado.emit(texto)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        forma = chaflan(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 16)
        p.fillPath(forma, QColor(12, 15, 19, 245))
        p.setPen(QPen(BORDE, 1))
        p.drawPath(forma)
        p.setPen(QPen(ACENTO, 2))
        p.drawLine(QPointF(20, 1), QPointF(80, 1))
        # reactor pequeño
        estado = self.ctrl.estado
        color = COLORES_ESTADO.get(estado, ACENTO)
        c, r = QPointF(48, self.height() / 2), 32
        latido = 0.5 + 0.5 * math.sin(time.monotonic() * 3)
        nivel = self.ctrl.voz.nivel() if estado == "hablando" else self.ctrl.nivel_micro
        brillo = 0.55 + 0.45 * max(nivel, latido * 0.5)
        halo = QRadialGradient(c, r)
        nucleo, borde = QColor(color), QColor(color)
        nucleo.setAlphaF(brillo * 0.8)
        borde.setAlpha(0)
        halo.setColorAt(0, QColor(235, 242, 250, int(200 * brillo)))
        halo.setColorAt(0.2, nucleo)
        halo.setColorAt(1, borde)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r, r)
        giro = time.monotonic() * (240 if estado == "pensando" else 60)
        col = QColor(color)
        col.setAlphaF(0.9)
        p.setPen(QPen(col, 3, Qt.SolidLine, Qt.FlatCap))
        p.setBrush(Qt.NoBrush)
        caja = QRectF(c.x() - r + 2, c.y() - r + 2, 2 * r - 4, 2 * r - 4)
        for i in range(3):
            p.drawArc(caja, int((i * 120 + giro) * 16), int(100 * 16))
