@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\start-local-demo-admin.ps1"
exit /b %ERRORLEVEL%
