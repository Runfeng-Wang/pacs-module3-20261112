# 打开 PyQt5 管理控制台（默认使用 runtime\demo；先运行 scripts\demo.ps1 可得到有数据的库）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-gui.ps1 [-Runtime runtime\demo] [-AutoStart]
param([string]$Runtime = "runtime\demo", [switch]$AutoStart)
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($AutoStart) { .venv\Scripts\pacs.exe --runtime $Runtime --operator $env:USERNAME gui --autostart }
else { .venv\Scripts\pacs.exe --runtime $Runtime --operator $env:USERNAME gui }
