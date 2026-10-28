@echo off
rem Baut PDF2PVS.exe (einmalig, benoetigt Python 3.10 oder neuer von python.org)
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version || (echo Python wurde nicht gefunden. Bitte von https://www.python.org installieren. & pause & exit /b 1)
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || (pause & exit /b 1)
pyinstaller --noconfirm --clean --onefile --windowed --name PDF2PVS --icon daten\pdf2pvs.ico --add-data "daten;daten" --exclude-module pymupdf.layout --exclude-module onnxruntime PDF2PVS.py || (pause & exit /b 1)
echo.
echo Fertig: dist\PDF2PVS.exe
pause
