"""Cuándo Mate pide permiso. Solo en dos casos:

1. Abrir, leer o listar archivos y carpetas ocultos (atributo Oculto de Windows, como AppData o ProgramData).
2. Borrar, mover o modificar lo que hace funcionar Windows: la carpeta Windows, los programas instalados, el
   arranque, el registro del sistema, los discos, o procesos sin los que Windows se cae.

Todo lo demás lo hace sin preguntar. Son reglas de sentido común sobre el texto de cada orden, no un antivirus:
lo que Mate haga con ratón y teclado, por ejemplo, no pasa por aquí.
"""
import os
import re
import stat
from pathlib import Path

CASA = Path.home()
CARPETA_MATE = Path(__file__).resolve().parent
SISTEMA = os.environ.get("SystemDrive", "C:") + "\\"


def _env(nombre, defecto):
    return Path(os.environ.get(nombre) or defecto)


# Debajo de estas carpetas, borrar o modificar algo pide permiso.
CARPETAS_CRITICAS = [
    _env("SystemRoot", SISTEMA + "Windows"),
    _env("ProgramFiles", SISTEMA + "Program Files"),
    _env("ProgramFiles(x86)", SISTEMA + "Program Files (x86)"),
    _env("ProgramData", SISTEMA + "ProgramData"),
    Path(SISTEMA + "Boot"), Path(SISTEMA + "Recovery"), Path(SISTEMA + "System Volume Information"),
    Path(SISTEMA + "Users") / "Default", CARPETA_MATE,
]
# Estas, solo ellas mismas (lo de dentro sí se puede tocar): la raíz del disco, C:\Users y tu carpeta personal.
EXACTAS_CRITICAS = [Path(SISTEMA), Path(SISTEMA + "Users"), CASA]
ARCHIVOS_RAIZ = {"bootmgr", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootnxt"}

# Comandos que pueden dejar el equipo sin arrancar: siempre preguntan, vayan a donde vayan.
PELIGROSOS = re.compile(
    r"\b(format(-volume)?|diskpart|bcdedit|bootrec|bcdboot|clear-disk|remove-partition|initialize-disk|"
    r"set-partition|vssadmin\s+delete|wbadmin\s+delete|cipher\s+/w|takeown|icacls|cacls|"
    r"reg(\.exe)?\s+(delete|add|import)\s+hklm|remove-item(property)?\s+[^|;]*hklm:|"
    r"set-itemproperty\s+[^|;]*hklm:|new-itemproperty\s+[^|;]*hklm:|"
    r"dism\b[^|;]*/(remove|disable)|sfc\s+/(scanfile|offbootdir)|"
    r"set-executionpolicy\s+[^|;]*-scope\s+localmachine)", re.I)

# Procesos sin los que Windows se cuelga o se reinicia.
PROCESOS_VITALES = re.compile(
    r"\b(stop-process|taskkill|kill|spps)\b[^|;]*\b(csrss|wininit|winlogon|lsass|smss|services|svchost|"
    r"dwm|explorer\.exe\s*/f|system)\b", re.I)

# Verbos que borran, mueven o modifican.
DESTRUCTIVOS = re.compile(
    r"(^|[\s;|&(])(remove-item|rm|ri|del|erase|rd|rmdir|move-item|mv|move|mi|rename-item|ren|rni|"
    r"clear-content|clc|set-content|sc|out-file|add-content|ac|copy-item\s+[^|;]*-force|attrib)(\s|$)", re.I)

# Rutas escritas dentro de un comando: C:\..., "C:\...", ~\..., $env:USERPROFILE\..., $HOME\...
RUTAS = re.compile(r"""(?:"([A-Za-z]:\\[^"]*)"|'([A-Za-z]:\\[^']*)'|([A-Za-z]:\\[^\s"'|;,)]*)|"""
                   r"""((?<![\w~])(?:~|\$env:USERPROFILE|\$HOME)(?:[\\/][^\s"'|;,)]*)?(?=[\s"'|;,)]|$)))""", re.I)

LISTAR_OCULTOS = re.compile(r"\b(get-childitem|gci|dir|ls)\b[^|;]*(-force|-hidden|-attributes\s+\S*hidden)|"
                            r"\bdir\b[^|;]*/a(:?h|\b)|\battrib\b", re.I)

OCULTO = stat.FILE_ATTRIBUTE_HIDDEN if hasattr(stat, "FILE_ATTRIBUTE_HIDDEN") else 2


def _normal(ruta):
    ruta = str(ruta).strip().strip('"').strip("'")
    ruta = re.sub(r"^\$env:USERPROFILE|^\$HOME", str(CASA).replace("\\", "\\\\"), ruta, flags=re.I)
    ruta = os.path.expandvars(os.path.expanduser(ruta))
    ruta = ruta.split("*")[0].split("?")[0]   # de un patrón, la parte fija
    try:
        p = Path(ruta)
        return (p if p.is_absolute() else CASA / p).resolve(strict=False)
    except (OSError, ValueError):
        return None


def rutas_de(comando):
    for m in RUTAS.finditer(comando):
        p = _normal(next(g for g in m.groups() if g))
        if p:
            yield p


def es_oculta(ruta):
    """¿La ruta o alguna carpeta por encima tiene el atributo Oculto?"""
    p = _normal(ruta)
    while p and p != p.parent:
        try:
            if os.stat(p).st_file_attributes & OCULTO:
                return True
        except (OSError, AttributeError):
            pass
        p = p.parent
    return False


def _dentro(p, base):
    try:
        p.relative_to(base.resolve(strict=False))
        return True
    except ValueError:
        return False


def es_critica(ruta):
    p = _normal(ruta)
    if p is None:
        return False
    if any(p == b.resolve(strict=False) for b in EXACTAS_CRITICAS):
        return True
    if p.parent == Path(SISTEMA).resolve() and p.name.lower() in ARCHIVOS_RAIZ:
        return True
    return any(_dentro(p, b) for b in CARPETAS_CRITICAS)


def _ruta_de_entrada(nombre, entrada):
    if nombre in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return entrada.get("file_path") or entrada.get("notebook_path")
    if nombre in ("Glob", "Grep"):
        return entrada.get("path") or (entrada.get("pattern") if nombre == "Glob" else None)
    if nombre == "mcp__mate__abrir":
        return entrada.get("objetivo")
    return None


def motivo(nombre, entrada):
    """None si puede hacerlo sin preguntar; si no, (lo que se dice en voz alta, el detalle en pantalla)."""
    if nombre in ("Bash", "PowerShell"):
        cmd = entrada.get("command", "")
        que = entrada.get("description") or "ejecutar un comando"
        if PELIGROSOS.search(cmd) or PROCESOS_VITALES.search(cmd):
            return f"{que}. Toca partes vitales de Windows", cmd
        if DESTRUCTIVOS.search(cmd):
            criticas = [p for p in rutas_de(cmd) if es_critica(p)]
            if criticas or re.search(r"\\(windows|system32|syswow64|program files[^\\]*)\\?", cmd, re.I):
                return f"{que}. Afecta a archivos del sistema", cmd
        if LISTAR_OCULTOS.search(cmd) or any(es_oculta(p) for p in rutas_de(cmd)):
            return f"{que}. Mira archivos ocultos", cmd
        return None
    ruta = _ruta_de_entrada(nombre, entrada)
    if not ruta or str(ruta).lower().startswith(("http:", "https:", "shell:", "mailto:")) or "://" in str(ruta):
        return None
    nombre_archivo = Path(str(ruta)).name or str(ruta)
    if nombre in ("Write", "Edit", "MultiEdit", "NotebookEdit") and es_critica(ruta):
        return f"modificar {nombre_archivo}, que es un archivo del sistema", str(ruta)
    if es_oculta(ruta):
        return f"abrir {nombre_archivo}, que está oculto", str(ruta)
    return None
