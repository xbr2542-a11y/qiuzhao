@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "NODEEXE="
set "NODEMODULES="
if not defined NODEEXE call :find_in_runtimes
if not defined NODEEXE call :find_on_path
if not defined NODEEXE goto :no_node

set "NODE_PATH=%NODEMODULES%"
echo 使用 Node: %NODEEXE%
echo 模块路径 : %NODEMODULES%
echo.
"%NODEEXE%" "%~dp0tools\probe_playwright.cjs"
echo.
pause
exit /b 0

:find_in_runtimes
for /d %%D in ("%USERPROFILE%\.cache\codex-runtimes\*") do call :check_runtime "%%~fD"
exit /b 0

:check_runtime
if defined NODEEXE exit /b 0
if exist "%~1\dependencies\node\bin\node.exe" set "NODEEXE=%~1\dependencies\node\bin\node.exe"
if exist "%~1\dependencies\node\node_modules" set "NODEMODULES=%~1\dependencies\node\node_modules"
exit /b 0

:find_on_path
where node >nul 2>nul
if not errorlevel 1 set "NODEEXE=node"
exit /b 0

:no_node
echo [错误] 没有找到 Node.js。
pause
exit /b 1
