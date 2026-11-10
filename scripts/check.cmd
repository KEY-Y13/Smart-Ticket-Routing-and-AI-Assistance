@echo off
setlocal
for %%I in ("%~dp0..") do set "taskRoot=%%~fI"
"%taskRoot%\.conda\python.exe" -X utf8 "%taskRoot%\scripts\validate.py"
exit /b %errorlevel%
