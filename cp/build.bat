@echo off
setlocal
call "D:\software\VisualStudio\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
    echo vcvars failed
    exit /b 1
)
cd /d "D:\Program Files (x86)\FaceUnlock\cp"
cl /nologo /LD /MT /O2 /EHsc /utf-8 /DUNICODE /D_UNICODE FaceUnlockCP.cpp ^
   /link /DEF:FaceUnlockCP.def /OUT:FaceUnlockCP.dll ^
   ole32.lib oleaut32.lib uuid.lib shlwapi.lib crypt32.lib secur32.lib advapi32.lib user32.lib gdi32.lib
echo EXIT=%ERRORLEVEL%

