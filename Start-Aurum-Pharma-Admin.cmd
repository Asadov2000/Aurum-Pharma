@echo off
setlocal
title Aurum Pharma

set "PROJECT_ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\start-local-demo-admin.ps1" -PauseOnError %*
set "AURUM_EXIT_CODE=%ERRORLEVEL%"
exit /b %AURUM_EXIT_CODE%
