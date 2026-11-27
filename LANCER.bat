@echo off
title Imprimante comics - NE PAS FERMER
cd /d "%~dp0printer_app"
start "" http://127.0.0.1:8766
python run.py
pause
