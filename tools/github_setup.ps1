# 首次把项目接到 GitHub：配置远端 → 推送（会弹出一次 GitHub 登录）。
#
# 为什么要你亲手跑一次：第一次推送需要登录 GitHub，浏览器登录窗口必须由你本人完成。
# 登录成功后 Windows 凭据管理器会记住它，之后的自动同步就不用再登录了。

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host ""
Write-Host "================ 把秋招管家接到你的 GitHub ================" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 没找到 git，请先安装 Git for Windows。" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

$existing = git remote get-url origin 2>$null
if ($existing) {
    Write-Host "当前已经连着的仓库：$existing"
    $answer = Read-Host "想换成别的仓库就粘贴新地址，直接回车保持不变"
    if ($answer.Trim()) { git remote set-url origin $answer.Trim() }
} else {
    Write-Host "还没有连 GitHub 仓库。请先去 github.com 新建一个仓库：" -ForegroundColor Yellow
    Write-Host "  1. 打开 https://github.com/new"
    Write-Host "  2. Repository name 填 qiuzhao"
    Write-Host "  3. 一定要选 Private（私有）——仓库里有你的姓名和求职意向" -ForegroundColor Yellow
    Write-Host "  4. 不要勾 Add a README / .gitignore / license，建成空仓库即可"
    Write-Host "  5. 建好后把页面上的仓库地址复制过来"
    Write-Host ""
    Write-Host "地址形如： https://github.com/你的用户名/qiuzhao.git"
    $url = (Read-Host "粘贴仓库地址（直接回车取消）").Trim()
    if (-not $url) {
        Write-Host "已取消，没有做任何修改。"
        Read-Host "按回车关闭"
        exit 1
    }
    git remote add origin $url 2>&1 | Write-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 这个地址加不上，请确认复制完整了。" -ForegroundColor Red
        Read-Host "按回车关闭"
        exit 1
    }
}

git branch -M main 2>&1 | Out-Null

Write-Host ""
Write-Host "开始推送。第一次会弹出 GitHub 登录窗口，登录后会自动继续……" -ForegroundColor Cyan
Write-Host ""
git push -u origin main
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "上传成功。以后想同步，双击「同步到GitHub.bat」就行。" -ForegroundColor Green
    Write-Host "如果已经装过自动同步任务，它会每 15 分钟自动帮你推一次。"
} else {
    Write-Host "推送失败（改动都还在本地，没丢）。" -ForegroundColor Red
    Write-Host "常见原因：仓库地址写错 / 没登录 / 网络连不上 github.com。"
}
Read-Host "按回车关闭"
