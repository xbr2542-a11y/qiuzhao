# 注册/更新 Windows 计划任务：每 15 分钟自动把改动同步到 GitHub。
#
# 说明几句，免得误会：
#   - 这是"本地定时推送"，不是 GitHub 自己在动。GitHub 无法主动读取你电脑上的文件，
#     也没有任何云端任务能看见本地数据，所以"实时更新"只能由本机定时完成。
#   - 没有改动时脚本直接退出，不会刷出一堆空提交。
#   - 只同步代码。data/（简历、岗位库）在 .gitignore 里，永远不会被推上去。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_auto_sync.ps1
# 卸载：Unregister-ScheduledTask -TaskName "秋招管家-自动同步GitHub" -Confirm:$false

param(
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$sync = Join-Path $root "tools\github_sync.ps1"
$taskName = "秋招管家-自动同步GitHub"

if (-not (Test-Path $sync)) {
    Write-Host "[错误] 找不到 $sync" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$sync`" -Quiet"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "每 $IntervalMinutes 分钟把秋招管家的改动推送到 GitHub" `
    -Force | Out-Null

Write-Host "已注册计划任务「$taskName」，每 $IntervalMinutes 分钟自动同步一次。" -ForegroundColor Green
Write-Host "运行日志：$root\.tmp\github_sync.log"
Write-Host ""
Write-Host "查看任务：Get-ScheduledTask -TaskName '$taskName'"
Write-Host "立即跑一次：Start-ScheduledTask -TaskName '$taskName'"
Write-Host "取消自动同步：Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
