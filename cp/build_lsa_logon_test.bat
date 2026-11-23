@echo off
setlocal
call "D:\software\VisualStudio\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "D:\Program Files (x86)\FaceUnlock\cp"
cl /nologo /MT /O2 /EHsc /utf-8 /DUNICODE /D_UNICODE lsa_logon_test.cpp /link /OUT:lsa_logon_test.exe secur32.lib crypt32.lib advapi32.lib
echo BUILD_EXIT=%ERRORLEVEL%

