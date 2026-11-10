@echo off
setlocal
for %%I in ("%~dp0..") do set "taskRoot=%%~fI"
set "taskPython=%taskRoot%\.conda\python.exe"
if not exist "%taskPython%" exit /b 2
echo SmartTicket core backend: http://127.0.0.1:8010/docs
echo Dedicated database must be reviewed and configured. Press Ctrl+C to stop.
if exist "%taskRoot%\.env" goto with_env
"%taskPython%" -X utf8 -m uvicorn backend.app.main:app --app-dir "%taskRoot%" --host 127.0.0.1 --port 8010
exit /b %errorlevel%
:with_env
"%taskPython%" -X utf8 -m uvicorn backend.app.main:app --app-dir "%taskRoot%" --env-file "%taskRoot%\.env" --host 127.0.0.1 --port 8010
exit /b %errorlevel%
