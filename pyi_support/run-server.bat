@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" -m omega_studio.cli serve %*
  exit /b %ERRORLEVEL%
)
if exist "%~dp0Omega3.0-portable-Server.exe" (
  "%~dp0Omega3.0-portable-Server.exe" serve %*
  exit /b %ERRORLEVEL%
)
echo No python\python.exe or Omega3.0-portable-Server.exe next to this script.
exit /b 1
