@echo off
REM Spousteci davka pro OHLearn — dvojklik.
REM Parametr (volitelne): cesta k .tm/.tmn souboru; jinak vezme prvni v tomto adresari.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [CHYBA] Python neni v PATH. Nainstaluj Python 3.10+ a zaskrtni "Add to PATH".
    pause
    exit /b 1
)

REM Prvni spusteni — nainstaluj zavislosti
if not exist ".deps_ok" (
    echo Instaluji zavislosti ...
    python -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo [CHYBA] pip install selhal.
        pause
        exit /b 1
    )
    echo ok> .deps_ok
)

python __main__.py %*
if errorlevel 1 pause
endlocal
