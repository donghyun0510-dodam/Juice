$xmlPath = "C:\Users\dongh\Desktop\주식\AI agent\task_global.xml"
$xmlContent = Get-Content $xmlPath -Raw -Encoding UTF8
$logPath = "C:\Users\dongh\Desktop\주식\AI agent\register_log.txt"

try {
    Register-ScheduledTask -TaskName "StockReview_Global" -Xml $xmlContent -Force
    "SUCCESS: Task registered" | Out-File $logPath -Encoding UTF8
    Get-ScheduledTask -TaskName "StockReview_Global" | Format-List TaskName, State | Out-File $logPath -Append -Encoding UTF8
} catch {
    "FAILED: $($_.Exception.Message)" | Out-File $logPath -Encoding UTF8
}
