@echo off
rem Startet PDF2PVS direkt aus dem Quellcode (ohne EXE)
cd /d "%~dp0"
if not exist .venv (py -3 -m venv .venv & call .venv\Scripts\activate.bat & pip install -r requirements.txt) else call .venv\Scripts\activate.bat
start "" pythonw PDF2PVS.py %*
