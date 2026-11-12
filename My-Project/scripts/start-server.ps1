# 无界面前台运行 DICOM SCP（Ctrl+C 停止）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-server.ps1 [-Runtime runtime\main]
param([string]$Runtime = "runtime\main")
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path "$Runtime\pacs.json")) { .venv\Scripts\pacs.exe --runtime $Runtime init }
.venv\Scripts\pacs.exe --runtime $Runtime --operator $env:USERNAME serve
