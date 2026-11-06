"""Lo que el HUD lee del sistema y lo que sus botones hacen: estadísticas, apps, multimedia y energía."""
import ctypes
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import psutil

log = logging.getLogger("mate.sistema")

CARPETA = Path(__file__).resolve().parent
CONFIG = CARPETA / "mate.json"

CONFIG_INICIAL = {
    "nombre": "",   # cómo te llama Mate al saludar; vacío = sin nombre
    "apps": ["Google Chrome", "Word", "Excel", "PowerPoint", "Visual Studio Code"],
    "webs": {"Google": "https://www.google.com", "YouTube": "https://www.youtube.com",
             "Gmail": "https://mail.google.com", "Maps": "https://maps.google.com",
             "Traductor": "https://translate.google.com"},
}


def leer_config():
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(CONFIG_INICIAL, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        datos = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("mate.json no se puede leer; uso la configuración inicial")
        datos = {}
    return {**CONFIG_INICIAL, **datos}


def _ruta_chrome():
    """chrome.exe, buscado donde lo registra Windows y en sus carpetas habituales."""
    import winreg
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(raiz, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                ruta = winreg.QueryValue(k, None)
                if ruta and Path(ruta).exists():
                    return ruta
        except OSError:
            pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        ruta = Path(base or "") / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and ruta.exists():
            return str(ruta)
    return None


CHROME = _ruta_chrome()


def abrir_web(url):
    """Abre una web en Google Chrome; si no está instalado, en el navegador predeterminado."""
    if CHROME:
        subprocess.Popen([CHROME, url])
    else:
        os.startfile(url)


def es_web(objetivo):
    return objetivo.lower().startswith(("http://", "https://", "www."))


def carpetas():
    casa = Path.home()
    return [("Escritorio", casa / "Desktop"), ("Descargas", casa / "Downloads"), ("Documentos", casa / "Documents"),
            ("Imágenes", casa / "Pictures"), ("Música", casa / "Music"), ("Vídeos", casa / "Videos"),
            ("Este equipo", "shell:MyComputerFolder")]


class Apps:
    """Busca en el menú Inicio el identificador de cada app, para abrirla aunque cambie de versión o de ruta."""

    def __init__(self, al_terminar):
        self.ids = {}
        threading.Thread(target=self._buscar, args=(al_terminar,), daemon=True, name="apps").start()

    def _buscar(self, al_terminar):
        try:
            salida = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            for app in json.loads(salida.decode("utf-8")):
                self.ids.setdefault(app["Name"].lower(), app["AppID"])
        except Exception:
            log.exception("no pude leer las apps del menú Inicio")
        al_terminar()

    def existe(self, nombre):
        return nombre.lower() in self.ids

    def abrir(self, nombre):
        app_id = self.ids.get(nombre.lower())
        if app_id:
            os.startfile(f"shell:AppsFolder\\{app_id}")


class Estadisticas:
    """Muestra el uso de CPU, RAM, disco y red una vez por segundo, con 60 segundos de historia."""

    def __init__(self):
        self.cpu = deque([0.0] * 60, maxlen=60)
        self.ram = deque([0.0] * 60, maxlen=60)
        self.bajada = deque([0.0] * 60, maxlen=60)
        self.subida = deque([0.0] * 60, maxlen=60)
        self.disco = 0.0
        self.ram_usada = self.ram_total = 0.0
        self._red = psutil.net_io_counters()
        self._t = time.monotonic()
        psutil.cpu_percent()

    def muestrear(self):
        ahora = time.monotonic()
        red = psutil.net_io_counters()
        dt = max(ahora - self._t, 0.1)
        self.bajada.append((red.bytes_recv - self._red.bytes_recv) / dt / 1024)
        self.subida.append((red.bytes_sent - self._red.bytes_sent) / dt / 1024)
        self._red, self._t = red, ahora
        self.cpu.append(psutil.cpu_percent())
        mem = psutil.virtual_memory()
        self.ram.append(mem.percent)
        self.ram_usada, self.ram_total = mem.used / 2**30, mem.total / 2**30
        try:
            self.disco = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").percent
        except OSError:
            pass

    @staticmethod
    def encendido():
        segundos = int(time.time() - psutil.boot_time())
        d, resto = divmod(segundos, 86400)
        return f"{d}d {resto // 3600}h {resto % 3600 // 60:02d}m"

    @staticmethod
    def ip_local():
        """La IP de la tarjeta que sale a internet (no la de VirtualBox ni otras redes virtuales).

        Un socket UDP "conectado" solo elige la ruta: no envía ningún paquete.
        """
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            return "sin red"


# Teclas multimedia de Windows: controlan Spotify, YouTube o lo que esté sonando.
TECLAS = {"anterior": 0xB1, "pausa": 0xB3, "siguiente": 0xB0, "detener": 0xB2, "silencio": 0xAD, "bajar": 0xAE,
          "subir": 0xAF}


def tecla_multimedia(nombre):
    vk = TECLAS[nombre]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def bloquear():
    ctypes.windll.user32.LockWorkStation()


def energia(accion):
    """apagar | reiniciar | cerrar_sesion. Se llama solo después de confirmar en pantalla."""
    args = {"apagar": ["/s", "/t", "0"], "reiniciar": ["/r", "/t", "0"], "cerrar_sesion": ["/l"]}[accion]
    subprocess.Popen(["shutdown", *args], creationflags=subprocess.CREATE_NO_WINDOW)
