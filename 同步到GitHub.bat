@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 同步到 GitHub
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\github_sync.ps1"
echo.
pause
