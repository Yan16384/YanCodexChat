@echo off
setlocal EnableExtensions

rem ======= Only edit these two values. Do not edit the rest. =======
set "TELEGRAM_BOT_TOKEN=在这里填写Telegram机器人Token"
set "ALLOWED_USER_ID=在这里填写Telegram用户ID"

set "CODEX_BRIDGE_ROOT=%~dp0"
set "PYTHON_EXE="
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo 启动失败
  echo 失败阶段：Python 检查
  echo 失败原因：找不到 python.exe。
  echo 建议处理：安装 Python 3 并加入 PATH 后重新双击本文件。
  pause
  exit /b 1
)

where node.exe >nul 2>&1
if errorlevel 1 (
  echo 启动失败
  echo 失败阶段：Node.js 检查
  echo 失败原因：找不到 node.exe，Codex 桌面 app-tools 客户端无法运行。
  echo 建议处理：安装 Node.js 并加入 PATH 后重新双击本文件。
  pause
  exit /b 1
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo 启动失败
  echo 失败阶段：npm 检查
  echo 失败原因：找不到 npm.cmd。
  echo 建议处理：修复 Node.js 安装或 PATH 后重新双击本文件。
  pause
  exit /b 1
)

if "%TELEGRAM_BOT_TOKEN%"=="在这里填写Telegram机器人Token" (
  echo 启动失败
  echo 失败阶段：配置检查
  echo 失败原因：尚未填写 TELEGRAM_BOT_TOKEN。
  echo 建议处理：只修改 BAT 顶部的两个配置变量。
  pause
  exit /b 1
)
if "%ALLOWED_USER_ID%"=="在这里填写Telegram用户ID" (
  echo 启动失败
  echo 失败阶段：配置检查
  echo 失败原因：尚未填写 ALLOWED_USER_ID。
  echo 建议处理：只修改 BAT 顶部的两个配置变量。
  pause
  exit /b 1
)

if not exist "%CODEX_BRIDGE_ROOT%data" md "%CODEX_BRIDGE_ROOT%data"
if not exist "%CODEX_BRIDGE_ROOT%runtime" md "%CODEX_BRIDGE_ROOT%runtime"
if not exist "%CODEX_BRIDGE_ROOT%logs" md "%CODEX_BRIDGE_ROOT%logs"

rem Detect an existing instance using the PID written by the bridge itself.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root=$env:CODEX_BRIDGE_ROOT; foreach($f in @((Join-Path $root 'runtime\bridge.pid'),(Join-Path $root 'bridge.pid'))){ if(Test-Path -LiteralPath $f){ $raw=(Get-Content -LiteralPath $f -Raw).Trim(); if($raw -match '^\d+$' -and (Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue)){ exit 2 } } }; exit 0"
if errorlevel 2 (
  echo 服务已经在运行，未启动第二个实例。
  endlocal
  exit /b 0
)

set "PYTHONUTF8=1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root=$env:CODEX_BRIDGE_ROOT; $out=Join-Path $root 'logs\bridge.log'; $err=Join-Path $root 'logs\bridge-error.log'; $pidPath=Join-Path $root 'runtime\bridge.pid'; $p=Start-Process -FilePath $env:PYTHON_EXE -ArgumentList @('-u','bridge.py') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru; Set-Content -LiteralPath $pidPath -Value $p.Id -Encoding ASCII"
if errorlevel 1 (
  echo 启动失败
  echo 失败阶段：桥接进程启动
  echo 失败原因：无法创建后台 Python 进程。
  echo 日志文件：%CODEX_BRIDGE_ROOT%logs\bridge-error.log
  pause
  exit /b 1
)

rem Wait for Telegram getMe and Codex app-tools checks, not merely for a PID.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$path=Join-Path $env:CODEX_BRIDGE_ROOT 'runtime\health.json'; $deadline=(Get-Date).AddSeconds(30); while((Get-Date) -lt $deadline){ if(Test-Path -LiteralPath $path){ try{ $h=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json; if($h.status -eq 'ready'){ exit 0 }; if($h.status -eq 'failed'){ Write-Output ($h.reason); exit 2 } } catch{} }; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 2 (
  echo 启动失败
  echo 失败阶段：健康检查
  echo 失败原因：Telegram Bot 或 Codex 桌面接口检查失败。
  echo 日志文件：%CODEX_BRIDGE_ROOT%logs\bridge-error.log
  pause
  exit /b 1
)
if errorlevel 1 (
  echo 启动失败
  echo 失败阶段：健康检查超时
  echo 失败原因：后台进程未在 30 秒内报告 ready。
  echo 日志文件：%CODEX_BRIDGE_ROOT%logs\bridge.log
  pause
  exit /b 1
)

echo Telegram-Codex Bridge 已启动
echo 项目目录：%CODEX_BRIDGE_ROOT%
echo 日志文件：%CODEX_BRIDGE_ROOT%logs\bridge.log
echo 授权用户：已从 BAT 配置读取
echo Codex 检查：app-tools ready
echo Telegram Bot 检查：getMe ready
echo 按任意键关闭此窗口，后台服务继续运行。
pause >nul
endlocal
exit /b 0