"""Herramientas propias de Mate: ver la pantalla, abrir cosas y manejar el ratón y el teclado."""
import asyncio
import base64
import ctypes
import io
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

import mss
from PIL import Image
from claude_agent_sdk import create_sdk_mcp_server, tool

import sistema

log = logging.getLogger("mate.herramientas")

# Las herramientas que mueven el ratón o pulsan teclas: antes de la primera de cada orden se aparta el HUD.
CONTROL = {"mcp__mate__clic", "mcp__mate__escribir", "mcp__mate__teclas", "mcp__mate__desplazar",
           "mcp__mate__arrastrar"}

# El cerebro y la interfaz ponen aquí cómo saber si el HUD tapa la pantalla y cómo esconderlo,
# para que no tape lo que Mate tiene que ver.
ocultar_hud = None
hud_delante = None

# Relación entre la captura que ve Claude y la pantalla real: (origen_x, origen_y, escala).
_pantalla = (0, 0, 1.0)


def _texto(t, error=False):
    r = {"content": [{"type": "text", "text": t}]}
    if error:
        r["is_error"] = True
    return r


async def _apartar_hud():
    if ocultar_hud and (hud_delante is None or hud_delante()):
        ocultar_hud()
        await asyncio.sleep(0.4)   # lo que tarda Windows en minimizar la ventana


# --- entrada de Windows (SendInput) -----------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _enviar(*entradas):
    arr = (_INPUT * len(entradas))(*entradas)
    user32.SendInput(len(entradas), arr, ctypes.sizeof(_INPUT))


def _raton(flags, datos=0):
    i = _INPUT(type=0)
    i.u.mi = _MOUSEINPUT(0, 0, datos & 0xFFFFFFFF, flags, 0, 0)
    return i


def _tecla(vk=0, scan=0, flags=0):
    i = _INPUT(type=1)
    i.u.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


KEYUP, UNICODE, EXTENDIDA = 0x0002, 0x0004, 0x0001
BOTONES = {"izquierdo": (0x0002, 0x0004), "derecho": (0x0008, 0x0010), "central": (0x0020, 0x0040)}
TECLAS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "mayus": 0x10, "alt": 0x12, "win": 0x5B,
          "enter": 0x0D, "intro": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
          "retroceso": 0x08, "delete": 0x2E, "supr": 0x2E, "espacio": 0x20, "space": 0x20,
          "arriba": 0x26, "up": 0x26, "abajo": 0x28, "down": 0x28, "izquierda": 0x25, "left": 0x25,
          "derecha": 0x27, "right": 0x27, "inicio": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23,
          "repag": 0x21, "pageup": 0x21, "avpag": 0x22, "pagedown": 0x22, "insert": 0x2D,
          "imprpant": 0x2C, "printscreen": 0x2C, "menu": 0x5D,
          **{f"f{n}": 0x6F + n for n in range(1, 13)}}
EXTENDIDAS = {0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x2D, 0x2E, 0x5B, 0x5D}


class Detenido(Exception):
    pass


def _comprobar_freno():
    """Freno de emergencia: con el ratón en la esquina superior izquierda, Mate suelta el control."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    ox, oy, _ = _pantalla
    if pt.x - ox <= 3 and pt.y - oy <= 3:
        raise Detenido()


def _a_pantalla(x, y):
    ox, oy, escala = _pantalla
    return int(ox + x * escala), int(oy + y * escala)


def _mover(x, y):
    user32.SetCursorPos(*_a_pantalla(x, y))


FRENO = ("La persona ha activado el freno de emergencia (ratón en la esquina superior izquierda). "
         "Deja de usar el ratón y el teclado y dile que lo has dejado.")


def _con_freno(fn):
    async def envoltorio(args):
        try:
            _comprobar_freno()
            resultado = fn(args)
            await asyncio.sleep(0.15)   # que la ventana reaccione antes de la siguiente acción
            return resultado
        except Detenido:
            log.info("freno de emergencia")
            return _texto(FRENO, error=True)
    return envoltorio


# --- herramientas -----------------------------------------------------------------------------
@tool("ver_pantalla",
      "Hace una captura de la pantalla principal. Las coordenadas de clic, desplazar y arrastrar son píxeles "
      "de la última captura.", {})
async def ver_pantalla(args):
    global _pantalla
    await _apartar_hud()
    with getattr(mss, "MSS", mss.mss)() as m:
        mon = m.monitors[1]
        shot = m.grab(mon)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    img.thumbnail((1366, 1366))   # se lee bien y pesa poco: la imagen va y vuelve en cada paso
    _pantalla = (mon["left"], mon["top"], shot.size[0] / img.size[0])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"content": [{"type": "text", "text": f"Captura de {img.size[0]}x{img.size[1]} píxeles."},
                        {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}]}


@tool("abrir",
      "Abre un programa, archivo, carpeta o página web, como si hicieras doble clic. "
      "Acepta un nombre de programa (notepad, calc, spotify:), una ruta o una URL.",
      {"objetivo": str})
async def abrir(args):
    objetivo = args["objetivo"].strip()
    try:
        if sistema.es_web(objetivo):
            sistema.abrir_web(objetivo if "://" in objetivo else "https://" + objetivo)   # las webs, en Chrome
        else:
            os.startfile(objetivo)
    except OSError as e:
        return _texto(f"No se pudo abrir {objetivo}: {e}", error=True)
    log.info("abierto %s", objetivo)
    return _texto(f"Abierto: {objetivo}")


@tool("clic", "Hace clic en (x, y) de la última captura. boton: izquierdo, derecho o central. veces: 1 o 2.",
      {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                        "boton": {"type": "string", "enum": list(BOTONES)},
                                        "veces": {"type": "integer", "minimum": 1, "maximum": 3}},
       "required": ["x", "y"]})
async def clic(args):
    def hacer(a):
        _mover(a["x"], a["y"])
        abajo, arriba = BOTONES[a.get("boton", "izquierdo")]
        for _ in range(a.get("veces", 1)):
            _enviar(_raton(abajo), _raton(arriba))
        return _texto(f"Clic en ({a['x']}, {a['y']}).")
    return await _con_freno(hacer)(args)


@tool("escribir", "Escribe texto con el teclado donde esté el cursor (acepta tildes, ñ y saltos de línea).",
      {"texto": str})
async def escribir(args):
    def hacer(a):
        entradas = []
        for ch in a["texto"]:
            if ch == "\n":
                entradas += [_tecla(0x0D), _tecla(0x0D, flags=KEYUP)]
                continue
            for unidad in ch.encode("utf-16-le").hex(" ", 2).split():   # caracteres fuera del BMP van en dos mitades
                cod = int.from_bytes(bytes.fromhex(unidad), "little")
                entradas += [_tecla(scan=cod, flags=UNICODE), _tecla(scan=cod, flags=UNICODE | KEYUP)]
        for i in range(0, len(entradas), 200):
            _comprobar_freno()
            _enviar(*entradas[i:i + 200])
        return _texto(f"Escrito ({len(a['texto'])} caracteres).")
    return await _con_freno(hacer)(args)


@tool("teclas", "Pulsa una tecla o combinación, por ejemplo: enter, esc, tab, ctrl+c, ctrl+shift+t, alt+f4, "
      "win+r, alt+tab, f5, abajo.", {"combinacion": str})
async def teclas(args):
    def hacer(a):
        vks = []
        for parte in a["combinacion"].lower().replace(" ", "").split("+"):
            if parte in TECLAS:
                vks.append(TECLAS[parte])
            elif len(parte) == 1 and parte.isalnum():
                vks.append(ord(parte.upper()))
            else:
                return _texto(f"No conozco la tecla «{parte}».", error=True)
        bajar = [_tecla(vk, flags=EXTENDIDA if vk in EXTENDIDAS else 0) for vk in vks]
        subir = [_tecla(vk, flags=KEYUP | (EXTENDIDA if vk in EXTENDIDAS else 0)) for vk in reversed(vks)]
        _enviar(*bajar, *subir)
        return _texto(f"Pulsado {a['combinacion']}.")
    return await _con_freno(hacer)(args)


@tool("desplazar", "Mueve la rueda del ratón sobre (x, y): cantidad positiva sube, negativa baja (3 = unas líneas).",
      {"x": float, "y": float, "cantidad": int})
async def desplazar(args):
    def hacer(a):
        _mover(a["x"], a["y"])
        _enviar(_raton(0x0800, int(a["cantidad"]) * 120))
        return _texto("Desplazado.")
    return await _con_freno(hacer)(args)


@tool("arrastrar", "Arrastra con el botón izquierdo de (x1, y1) a (x2, y2) de la última captura.",
      {"x1": float, "y1": float, "x2": float, "y2": float})
async def arrastrar(args):
    async def hacer():
        _comprobar_freno()
        _mover(args["x1"], args["y1"])
        _enviar(_raton(0x0002))
        await asyncio.sleep(0.1)
        for paso in range(1, 11):
            x = args["x1"] + (args["x2"] - args["x1"]) * paso / 10
            y = args["y1"] + (args["y2"] - args["y1"]) * paso / 10
            _mover(x, y)
            await asyncio.sleep(0.02)
        _enviar(_raton(0x0004))
        return _texto("Arrastrado.")
    try:
        return await hacer()
    except Detenido:
        _enviar(_raton(0x0004))
        return _texto(FRENO, error=True)


def _primer_video(busqueda):
    """El primer vídeo de la búsqueda de YouTube, leído de la página de resultados (sin abrir el navegador)."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(busqueda)
    peticion = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "es-ES,es"})
    html = urllib.request.urlopen(peticion, timeout=8).read().decode("utf-8", "ignore")
    m = re.search(r'"videoId":"([\w-]{11})"', html)
    return m.group(1) if m else None


@tool("poner_musica",
      "Pone una canción, artista, álbum o vídeo: lo busca en YouTube y abre directamente el primer resultado, que "
      "empieza a sonar solo. Antes para lo que estuviera sonando. Mucho más rápido que buscar y hacer clic.",
      {"busqueda": str})
async def poner_musica(args):
    busqueda = args["busqueda"].strip()
    sistema.tecla_multimedia("detener")   # que no se oigan dos canciones a la vez
    try:
        video = await asyncio.to_thread(_primer_video, busqueda)
    except Exception as e:
        log.warning("no pude buscar en YouTube: %s", e)
        video = None
    if video:
        sistema.abrir_web(f"https://www.youtube.com/watch?v={video}")
        log.info("música: %s -> %s", busqueda, video)
        return _texto(f"Sonando el primer resultado de «{busqueda}» en YouTube.")
    sistema.abrir_web("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(busqueda))
    return _texto("No encontré el vídeo directo; abrí la búsqueda de YouTube. Habría que elegir uno con clic.")


@tool("multimedia", "Controla lo que esté sonando (YouTube, Spotify…): pausa (pausa o sigue), siguiente, anterior, "
      "detener, subir, bajar o silencio. veces: cuántas pulsaciones (para el volumen, 2 % cada una).",
      {"type": "object", "properties": {"accion": {"type": "string", "enum": list(sistema.TECLAS)},
                                        "veces": {"type": "integer", "minimum": 1, "maximum": 50}},
       "required": ["accion"]})
async def multimedia(args):
    for _ in range(args.get("veces", 1)):
        sistema.tecla_multimedia(args["accion"])
    return _texto(f"Hecho: {args['accion']}.")


# --- memoria: lo que Mate sabe de la persona, de un día para otro ---------------------------------
MEMORIA = sistema.CARPETA / "memoria.json"


def recuerdos():
    try:
        return json.loads(MEMORIA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _guardar_recuerdos(lista):
    MEMORIA.write_text(json.dumps(lista, indent=2, ensure_ascii=False), encoding="utf-8")


@tool("recordar", "Guarda algo que vale la pena recordar de la persona para otros días: gustos, planes, nombres, "
      "cómo le gusta que hagas las cosas. Una frase corta y concreta.", {"dato": str})
async def recordar(args):
    dato = args["dato"].strip()
    lista = recuerdos()
    if dato and dato not in (r["dato"] for r in lista):
        lista.append({"dato": dato, "fecha": time.strftime("%Y-%m-%d")})
        _guardar_recuerdos(lista[-200:])
    return _texto("Guardado en la memoria.")


@tool("olvidar", "Borra de la memoria los recuerdos que contengan este texto (si ya no es verdad o te lo piden).",
      {"texto": str})
async def olvidar(args):
    buscado = args["texto"].strip().lower()
    lista = recuerdos()
    quedan = [r for r in lista if buscado not in r["dato"].lower()]
    _guardar_recuerdos(quedan)
    return _texto(f"Olvidados {len(lista) - len(quedan)} recuerdos.")


servidor = create_sdk_mcp_server("mate", tools=[ver_pantalla, abrir, poner_musica, multimedia, recordar, olvidar,
                                                clic, escribir, teclas, desplazar, arrastrar])
