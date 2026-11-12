# 运行全部测试，保存原始日志（UTF-8）与 junit XML，并生成用例表报告。
# 用法（在 My-Project 目录下）：powershell -ExecutionPolicy Bypass -File scripts\run-tests.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "未找到 .venv，请先运行：uv sync"; exit 2 }
New-Item -ItemType Directory -Force docs\testing\logs | Out-Null
$env:PYTHONIOENCODING = "utf-8"
# 通过 cmd 重定向，日志按 Python 输出的 UTF-8 原样保存（PowerShell 5.1 的 Tee-Object 会写成 UTF-16）
cmd /c "$py -m pytest -v -rA --junitxml=docs\testing\logs\junit.xml > docs\testing\logs\pytest-full.log 2>&1"
$code = $LASTEXITCODE
Get-Content docs\testing\logs\pytest-full.log -Encoding UTF8 -Tail 5
& $py scripts\make_test_report.py docs\testing\logs\junit.xml docs\testing\test-report.md
Write-Host "pytest 退出码 $code；完整日志：docs\testing\logs\pytest-full.log；报告：docs\testing\test-report.md"
exit $code
