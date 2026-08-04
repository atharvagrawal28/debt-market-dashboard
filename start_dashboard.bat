@echo off
REM Starts the dashboard backend and opens it in your browser.
REM Leave this window open while you use the dashboard -- closing it stops
REM the server, and the fetch buttons will start failing.

title Debt Market Dashboard - keep this window open
cd /d "%~dp0"

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY (
    echo.
    echo   ERROR: python.exe not found on PATH.
    echo   Install Python, or edit this file to point at your python.exe.
    echo.
    pause
    exit /b 9009
)

REM Make sure the dependencies are present before trying to boot.
"%PY%" -c "import flask, requests, docx, openpyxl" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   ERROR: dependency install failed. See the messages above.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo   Fixed Income ^& Debt Market Dashboard
echo   ------------------------------------
echo   Closing report    http://127.0.0.1:5000/
echo   CD/CP dashboard   http://127.0.0.1:5000/cdcp
echo.
echo   Keep this window open. Press Ctrl+C to stop the server.
echo.

start "" http://127.0.0.1:5000/
"%PY%" -m app.server

echo.
echo   Server stopped.
pause
