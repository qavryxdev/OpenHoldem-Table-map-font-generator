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

REM Hash requirements.txt — pri zmene se zavislosti preinstaluji
set "CURHASH="
for /f "tokens=*" %%H in ('certutil -hashfile requirements.txt SHA1 ^| findstr /v ":" ^| findstr /v "CertUtil"') do (
    if not defined CURHASH set "CURHASH=%%H"
)

set "STOREDHASH="
if exist ".deps_ok" set /p STOREDHASH=<.deps_ok

if not "%CURHASH%"=="%STOREDHASH%" (
    echo Instaluji/aktualizuji zavislosti ...
    python -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo [CHYBA] pip install selhal.
        pause
        exit /b 1
    )
    >.deps_ok echo %CURHASH%
)

python __main__.py %*
if errorlevel 1 pause
endlocal
