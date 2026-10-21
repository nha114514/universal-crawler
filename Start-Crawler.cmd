@echo off
cd /d "%~dp0"
if exist "%~dp0Crawler.exe" (
  start "" "%~dp0Crawler.exe"
) else (
  start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0desktop.py"
)
