@echo off
chcp 65001 >nul
title 关闭 GitHub 自动同步
echo 正在取消自动同步计划任务...
echo.
schtasks /delete /tn "秋招管家-自动同步GitHub" /f
echo.
echo 完成。以后想同步请双击「同步到GitHub.bat」。
pause
