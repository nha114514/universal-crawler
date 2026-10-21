@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m crawler_system.mcp_server
