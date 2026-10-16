@echo off
setlocal
where pwsh.exe >nul 2>nul
if errorlevel 1 (set "PVZ_INSTALL_SHELL=powershell.exe") else (set "PVZ_INSTALL_SHELL=pwsh.exe")
"%PVZ_INSTALL_SHELL%" -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 pause
