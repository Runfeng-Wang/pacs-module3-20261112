# 端到端演示：新建 runtime\demo，本机回环 + 合成数据，逐步打印证据并自检。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
# 保存日志：powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Log docs\testing\logs\demo-run.log
param([string]$Log = "")
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
if ($Log) {
    cmd /c ".venv\Scripts\pacs.exe --runtime runtime\demo demo > $Log 2>&1"
    $code = $LASTEXITCODE
    Get-Content $Log -Encoding UTF8
} else {
    .venv\Scripts\pacs.exe --runtime runtime\demo demo
    $code = $LASTEXITCODE
}
exit $code
