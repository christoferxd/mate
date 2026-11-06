# Mate

Asistente de voz a pantalla completa para Windows. El cerebro es Claude, con tu sesión de Claude
(sin clave de API). Se llama Mate

## Arrancar

Arranca solo al iniciar sesión en Windows (acceso directo `Mate.lnk` en la carpeta Inicio:
Win+R → `shell:startup`; bórralo para quitarlo). A mano: doble clic en `iniciar.bat`.
Al arrancar saluda según la hora: buenos días (5 a 12), buenas tardes (12 a 20) o buenas noches.

El icono junto al reloj trae a Mate al frente (clic) o lo silencia y lo cierra (clic derecho).
Los mensajes van a `mate.log`.

## Usarlo

- **Voz:** «Mate, …» al principio de la frase, y la orden. O di «Mate», espera el «¿Sí?» y habla.
  Después de que conteste tienes 8 segundos para seguir sin repetir «Mate».
  Cuando le hablas o pide permiso, la pantalla de Mate se pone delante de todo.
- **Texto:** escribe en la caja de la conversación y pulsa Enter.
- **Parar:** «Mate, para», tecla Esc, o doble clic en el reactor.
- **Botones:** apps, carpetas y webs se abren con un clic. Multimedia controla lo que esté sonando.
  Apagar, reiniciar y cerrar sesión piden confirmación. Ocultar minimiza la pantalla.
- **Notas:** lo que escribas en Notas se guarda solo en `notas.txt`.

## Permisos

Mate hace casi todo sin preguntar: abrir, escribir, mover y borrar tus archivos, instalar, cerrar programas,
y manejar el ratón y el teclado. Tus archivos los borra mandándolos a la Papelera de reciclaje.

Solo pide permiso (botones Sí/No, o di «sí» / «no»; sin respuesta en 90 segundos cuenta como no) para:

- abrir, leer o listar archivos y carpetas **ocultos** (AppData, ProgramData…);
- borrar, mover o modificar **lo que hace funcionar Windows**: la carpeta Windows, Archivos de programa,
  ProgramData, el arranque, el registro del sistema (HKLM), los discos (format, diskpart…), procesos vitales,
  la raíz del disco o tu carpeta personal entera.

Las reglas están en `riesgo.py`. Son reglas sobre el texto de cada orden, no un antivirus: lo que Mate haga
con ratón y teclado no pasa por ellas.

**Freno de emergencia:** lleva el ratón a la esquina superior izquierda y Mate suelta ratón y teclado.

## Personalizar

`mate.json` (se crea al primer arranque; hay un ejemplo en `mate.ejemplo.json`):

- `nombre`: cómo te llama al saludar (`"nombre": "Ana"`).
- `apps`: los nombres tal como salen en el menú Inicio. Las que no estén instaladas no aparecen.
- `webs`: nombre y dirección de cada botón web.

**Memoria:** Mate guarda lo que le cuentas que sirve para otros días (gustos, planes, nombres) en
`memoria.json`. Puedes leerlo o borrarlo; también puedes decirle «olvida que…».

**Voz:** Alex (voz neuronal de Microsoft, acento peruano), que necesita internet. Sin conexión habla Pablo,
la voz de Windows.

**Modelos:** Haiku para conversar y las órdenes normales (es el más rápido); Sonnet cuando la orden pide
manejar la pantalla (clic, escribir en una ventana, mensajes, formularios). La regla está en `cerebro.py`
(`PIDE_PANTALLA`).

Variables de entorno: `MATE_MODELO` (un solo modelo para todo), `MATE_MODELO_RAPIDO`, `MATE_MODELO_PANTALLA`,
`MATE_VOZ_NEURONAL` (`es-PE-AlexNeural`; vacía para usar solo la voz de Windows), `MATE_VOZ` (la de Windows:
`Pablo`, `Laura` o `Helena`) y `MATE_WHISPER` (`small`; `base` es más rápido, `medium` más preciso).

## Reinstalar

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
