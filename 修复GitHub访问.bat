@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 修复 GitHub 无法访问

rem 修改 hosts 必须管理员权限，这里自动提权
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 需要管理员权限，正在弹出提权窗口，请点「是」...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -Verb RunAs -ArgumentList '/c','\"%~f0\"'"
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\fix_github_dns.ps1"
