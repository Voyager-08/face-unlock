@echo off
schtasks /run /tn FaceUnlockAgent >nul 2>&1
start "" "%~dp0..\.venv\Scripts\pythonw.exe" "%~dp0..\src\status_ui.py"
