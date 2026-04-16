@echo off
cd /d "%~dp0"
python generuj_historii.py
start "" git_historie.html
