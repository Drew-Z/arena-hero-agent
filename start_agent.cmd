@echo off
setlocal
title Arena Hero Agent + Dashboard
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1" %*
set "agent_exit_code=%ERRORLEVEL%"
if not "%agent_exit_code%"=="0" pause
exit /b %agent_exit_code%
