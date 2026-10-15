@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 开启 GitHub 自动同步
echo 需要管理员权限来注册 Windows 计划任务，请在弹窗里点「是」。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File','%~dp0tools\install_auto_sync.ps1'"
echo.
pause
