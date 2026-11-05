@echo off
setlocal
cd /d "%~dp0"
title Key Vault Desk

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo Python 3.11+ not found. Install from https://www.python.org/downloads/ and tick "Add to PATH".
    goto fail
)

where az >nul 2>nul || (
    echo Azure CLI not found. Install: winget install -e --id Microsoft.AzureCLI or Download portable version from https://aka.ms/installazurecliwindowszipx64 and add to PATH.
    goto fail
)

if not exist "config.json" (
    echo config.json missing. Copy config.example.json to config.json and set your vaults.
    goto fail
)

rem Extra args pass through, e.g. Start-KeyVaultDesk.bat --debug --port 8800
%PY% app.py --open %*
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
pause
exit /b 1
