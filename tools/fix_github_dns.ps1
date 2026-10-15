# 修复 github.com 的 DNS 污染：把能用的真实 IP 写进 hosts 文件。
#
# 背景：这台电脑所在的网络把 github.com 解析到了 205.251.x.x（CloudFront 段）
# 这类假地址，导致浏览器和 git 都连不上真站。真实 IP 是通的。
# 写 hosts 后：浏览器能正常打开 github.com，git 也能直接 push，
# 不再依赖本机的转发小程序。
#
# 必须用管理员权限运行（改 hosts 和刷新 DNS 都需要）。

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[错误] 需要管理员权限。请右键「修复GitHub访问.bat」→ 以管理员身份运行。" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

# GitHub 官方公布的 github.com 地址段（140.82.112.0/20）里的候选 IP
$candidates = @(
    "140.82.113.3", "140.82.112.3", "140.82.114.3", "140.82.116.4",
    "140.82.115.3", "140.82.121.3", "140.82.112.4"
)

function Test-GitHubTls([string]$ip) {
    # 必须把 TLS 握手做完：只测 TCP 会挑到"能连上但握不上手"的 IP
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($ip, 443)
        $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, { $true })
        $ssl.AuthenticateAsClient("github.com")
        $ok = $ssl.IsAuthenticated
        $ssl.Close(); $tcp.Close()
        return $ok
    } catch {
        return $false
    }
}

Write-Host "正在测试候选 IP（每个都做一次真实 TLS 握手）…" -ForegroundColor Cyan
$good = @()
foreach ($ip in $candidates) {
    $ok = Test-GitHubTls $ip
    Write-Host ("  {0,-16} {1}" -f $ip, $(if ($ok) { "可用" } else { "不通" }))
    if ($ok) { $good += $ip }
}

if ($good.Count -eq 0) {
    Write-Host "[失败] 候选 IP 全部不通。可能不只是 DNS 问题，先别改 hosts。" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$backup = "$hostsPath.bak_qiuzhao"
Copy-Item $hostsPath $backup -Force
Write-Host "已备份原 hosts 到 $backup" -ForegroundColor DarkGray

# 先清掉上次加的段落，保证脚本可以反复运行
$kept = Get-Content $hostsPath | Where-Object { $_ -notmatch 'qiuzhao-github-fix' }
$block = @("# === qiuzhao-github-fix begin ===") +
         ($good | ForEach-Object { "$_ github.com" }) +
         @("# === qiuzhao-github-fix end ===")
Set-Content -Path $hostsPath -Value ($kept + $block) -Encoding ASCII

ipconfig /flushdns | Out-Null

Write-Host ""
Write-Host "已写入 $($good.Count) 条 github.com 记录，并刷新了 DNS。" -ForegroundColor Green
Write-Host "现在浏览器和 git 都应该能直连 github.com 了。"
Write-Host ""
Write-Host "验证："
Write-Host "  ping github.com"
Write-Host ""
Write-Host "如果以后 GitHub 换了 IP 导致又打不开，重新运行本脚本即可。"
Write-Host "注意：修好之后就不需要本机转发了，可以用下面这条命令去掉转发配置："
Write-Host "  cd /d <你的项目目录> && git config --local --unset http.https://github.com.proxy"
Read-Host "按回车关闭"
