@echo off
setlocal
call "D:\software\VisualStudio\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "D:\Program Files (x86)\FaceUnlock\cp"
cl /nologo /MT /O2 /EHsc /utf-8 lsa_test.cpp /link /OUT:lsa_test.exe secur32.lib advapi32.lib
if errorlevel 1 exit /b 1
lsa_test.exe

