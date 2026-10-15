@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 秋招管家（后台服务，别关）

set "PYEXE="
if exist ".runtime\python.exe" set "PYEXE=.runtime\python.exe"
if not defined PYEXE call :find_in_runtimes
if not defined PYEXE call :find_on_path
if not defined PYEXE goto :no_python

rem github.com 的域名解析被污染，起一个本机转发让 git 能连上真站。
rem 先探端口：已经在跑就不再起第二个（重复起会因端口占用直接退出）
"%PYEXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.6);sys.exit(0 if s.connect_ex(('127.0.0.1',18765))==0 else 1)" >nul 2>nul
if not errorlevel 1 goto :proxy_ready
start "github-proxy" /min "%PYEXE%" "%~dp0tools\github_net_proxy.py" --port 18765
:proxy_ready

rem 已经在跑就直接退出，避免端口冲突
"%PYEXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.6);sys.exit(0 if s.connect_ex(('127.0.0.1',8760))==0 else 1)" >nul 2>nul
if not errorlevel 1 exit /b 0

rem 不打开浏览器，只把服务跑起来（供开机自动启动使用）
"%PYEXE%" "%~dp0app\server.py" --port 8760
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
pause
exit /b 1
