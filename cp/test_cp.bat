@echo off
setlocal
call "D:\software\VisualStudio\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "D:\Program Files (x86)\FaceUnlock\cp"
cl /nologo /MT /O2 /EHsc /utf-8 /DUNICODE /D_UNICODE cp_test.cpp /link /OUT:cp_test.exe ole32.lib oleaut32.lib uuid.lib
if errorlevel 1 exit /b 1
cp_test.exe
echo EXIT=%ERRORLEVEL%

