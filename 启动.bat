@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 秋招管家

echo ==========================================
echo    秋招管家 - 正在启动
echo ==========================================
echo.

set "PYEXE="
if exist ".runtime\python.exe" set "PYEXE=.runtime\python.exe"
if not defined PYEXE call :find_in_runtimes
if not defined PYEXE call :find_on_path
if not defined PYEXE goto :no_python

"%PYEXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.6);sys.exit(0 if s.connect_ex(('127.0.0.1',8760))==0 else 1)" >nul 2>nul
if not errorlevel 1 goto :already_running

echo 使用解释器: %PYEXE%
echo 界面地址  : http://127.0.0.1:8760
echo.
echo 启动后请保持本窗口开着；关掉本窗口就等于关闭程序。
echo.

"%PYEXE%" "%~dp0app\server.py" --port 8760 --open

echo.
echo 服务已退出。若上方有报错信息，请截图反馈。
pause
exit /b 0

:find_in_runtimes
for /d %%D in ("%USERPROFILE%\.cache\codex-runtimes\*") do call :check_runtime "%%~fD"
exit /b 0

:check_runtime
if defined PYEXE exit /b 0
if exist "%~1\dependencies\python\python.exe" set "PYEXE=%~1\dependencies\python\python.exe"
exit /b 0

:find_on_path
where python >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
exit /b 0

:no_python
echo [错误] 没有找到 Python 解释器。
echo.
echo 解决办法二选一：
echo   1) 安装 Python 3.10 或更高版本，安装时勾选 Add Python to PATH
echo   2) 把 python.exe 复制到本目录的 .runtime 文件夹里
echo.
pause
exit /b 1

:already_running
echo 服务已经在运行，正在打开界面...
start "" "http://127.0.0.1:8760"
timeout /t 2 /nobreak >nul
exit /b 0
