@echo off
REM Daily data pull for the fixed income dashboard.
REM Fetches the last 3 days so a holiday or a late F-TRAC publish still
REM gets picked up on the next run. Re-running a day is safe -- each source
REM replaces its own slice rather than appending.

cd /d "%~dp0"
if not exist "data" mkdir "data"

REM Task Scheduler does not always inherit the interactive PATH, so resolve
REM the interpreter explicitly rather than trusting a bare "python".
set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY (
    echo [%date% %time%] ERROR: could not find python.exe >> "data\ingest.log"
    exit /b 9009
)

echo. >> "data\ingest.log"
echo ======== [%date% %time%] starting ingest using "%PY%" >> "data\ingest.log"

REM 7 days is the widest single export window F-TRAC accepts, so this costs
REM exactly the same as a 1-day pull but survives a week of missed runs
REM (holidays, laptop off, VPN down). Re-fetching a stored day is a no-op.
REM
REM -u matters: Python buffers stdout when it is redirected to a file, so a
REM run that gets killed flushes nothing and the log shows only "^C" - which
REM reads like an instant crash rather than a timeout. Unbuffered output
REM leaves a trail showing exactly how far it got.
"%PY%" -u -m app.ingest --days 7 >> "data\ingest.log" 2>&1
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo [%date% %time%] INGEST FAILED with exit code %RC% >> "data\ingest.log"
    exit /b %RC%
)

echo [%date% %time%] ingest complete >> "data\ingest.log"

REM Rebuild the published site and push it, so the public link tracks the
REM database automatically. Skipped silently when no git remote is set up.
"%PY%" -u -m app.publish --quiet >> "data\ingest.log" 2>&1
if errorlevel 1 (
    echo [%date% %time%] PUBLISH FAILED >> "data\ingest.log"
    exit /b 0
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] no git repo - skipping deploy >> "data\ingest.log"
    exit /b 0
)
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] no git remote - skipping deploy >> "data\ingest.log"
    exit /b 0
)

git add public vercel.json >> "data\ingest.log" 2>&1
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Data refresh %date%" >> "data\ingest.log" 2>&1
    git push >> "data\ingest.log" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] PUSH FAILED - check credentials >> "data\ingest.log"
    ) else (
        echo [%date% %time%] pushed - Vercel will redeploy >> "data\ingest.log"
    )
) else (
    echo [%date% %time%] no data change - nothing to deploy >> "data\ingest.log"
)
exit /b 0
