@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 上传到 GitHub
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\github_setup.ps1"
echo.
pause
