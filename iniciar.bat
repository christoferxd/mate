@echo off
rem Arranca Mate sin ventana de consola. Los mensajes van a mate.log.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0mate.py"
