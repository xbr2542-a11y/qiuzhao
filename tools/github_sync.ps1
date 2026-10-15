# 把本地改动提交并推送到 GitHub。
#
# 设计原则：
#   1. 没有远端时只是提示，不报错——首次使用前仓库还没接上 GitHub 是正常状态。
#   2. 没有改动时直接退出，不产生空提交（否则每 15 分钟一条垃圾提交）。
#   3. 所有输出都写进日志，因为自动同步是后台跑的，用户看不到窗口。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File tools\github_sync.ps1

param(
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$logDir = Join-Path $root ".tmp"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "github_sync.log"

function Write-Log([string]$message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] $message"
    Add-Content -Path $log -Value $line -Encoding UTF8
    if (-not $Quiet) { Write-Host $line }
}

# ---------------------------------------------------------------- DNS 绕行
# 这台电脑的网络把 github.com 解析到了假 IP，git 连不上真站。
# tools/github_net_proxy.py 在 127.0.0.1 上开一个转发，对 github.com 改用
# 写死的真实 IP 建连（TLS 仍然是端到端校验，只搬密文）。
# 这里负责"它没在跑就把它拉起来"，这样无人值守的定时任务也能正常推送。

$proxyPort = 18765
$proxyScript = Join-Path $root "tools\github_net_proxy.py"

function Test-ProxyUp {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $proxyPort)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Find-Python {
    $local = Join-Path $root ".runtime\python.exe"
    if (Test-Path $local) { return $local }
    $runtimes = Join-Path $env:USERPROFILE ".cache\codex-runtimes"
    if (Test-Path $runtimes) {
        $found = Get-ChildItem $runtimes -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "dependencies\python\python.exe" } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) { return $found }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Ensure-Proxy {
    if (Test-ProxyUp) { return $true }
    $python = Find-Python
    if (-not $python -or -not (Test-Path $proxyScript)) {
        Write-Log "找不到 Python 或转发脚本，无法建立到 github.com 的连接。"
        return $false
    }
    Start-Process -FilePath $python -WindowStyle Hidden `
        -ArgumentList @($proxyScript, "--port", "$proxyPort")
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-ProxyUp) { return $true }
    }
    Write-Log "转发启动后仍未监听 $proxyPort 端口。"
    return $false
}

# 日志超过 500 行就截断，避免无限增长
if ((Test-Path $log) -and ((Get-Content $log -ErrorAction SilentlyContinue).Count -gt 500)) {
    Get-Content $log -Tail 200 | Set-Content $log -Encoding UTF8
}

$remote = (git remote get-url origin 2>$null)
if (-not $remote) {
    Write-Log "还没接上 GitHub：请先双击「上传到GitHub.bat」完成首次连接，之后本脚本才会自动同步。"
    exit 0
}

git add -A 2>&1 | Out-Null

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Log "没有改动，跳过。"
    exit 0
}

$count = ($staged | Measure-Object).Count
$message = "自动同步 $(Get-Date -Format 'yyyy-MM-dd HH:mm')（$count 个文件）"
git commit -q -m $message 2>&1 | Out-Null

Ensure-Proxy | Out-Null

# 这条网络对 github.com 的连接时好时坏（DNS 被污染 + 中间设备重置 TLS），
# 所以失败后重启转发换一条线路再试，最多三次。
$pushed = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    $push = git push origin HEAD 2>&1
    if ($LASTEXITCODE -eq 0) {
        $pushed = $true
        break
    }
    Write-Log "第 $attempt 次推送失败：$push"
    if ($attempt -lt 3) {
        Get-NetTCPConnection -LocalPort $proxyPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Ensure-Proxy | Out-Null
        Start-Sleep -Seconds 2
    }
}

if ($pushed) {
    Write-Log "已推送：$message"
} else {
    Write-Log "推送失败（改动已提交在本地，不会丢）。稍后自动重试。"
    exit 1
}
exit 0
