@echo off
setlocal
cd /d "%~dp0.."
".conda\python.exe" -X utf8 -m scripts.local_services start
pause
