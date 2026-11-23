@echo off
call "D:\software\VisualStudio\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "D:\Program Files (x86)\FaceUnlock\cp"
cl /nologo /c /EHsc /DUNICODE /D_UNICODE test.cpp
echo EXIT=%ERRORLEVEL%

