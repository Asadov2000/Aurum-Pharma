@echo off
setlocal
title Aurum Pharma - Verification
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify-change.ps1" %*
set "AURUM_EXIT_CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %AURUM_EXIT_CODE%
