#define AppName "Face Unlock"
#define AppVersion "1.1.4"
#define AppPublisher "Voyager-08"
#define AppURL "https://github.com/Voyager-08/face-unlock"
#define Stage GetEnv("FACEUNLOCK_STAGE")
#define Work GetEnv("FACEUNLOCK_WORK")

[Setup]
AppId={{8F2C1E4A-6B3D-4A7E-9C2F-1D5B8E0A3F64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName=D:\Program Files (x86)\FaceUnlock
DisableDirPage=no
DefaultGroupName=Face Unlock
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
OutputDir={#Work}\output
OutputBaseFilename=FaceUnlockSetup-{#AppVersion}
SetupIconFile={#Work}\face.ico
UninstallDisplayIcon={app}\face.ico
UninstallDisplayName={#AppName}
VersionInfoVersion=1.1.4.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Installer
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no

[Files]
Source: "{#Stage}\src\*"; DestDir: "{app}\src"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\cp\*"; DestDir: "{app}\cp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\.venv\*"; DestDir: "{app}\.venv"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\face.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"
Name: "{app}\logs"

[Icons]
Name: "{group}\Face Unlock 状态面板"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\src\status_ui.py"""; WorkingDir: "{app}\src"; IconFilename: "{app}\face.ico"
Name: "{group}\卸载 Face Unlock"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\register.ps1"" -Root ""{app}"""; StatusMsg: "正在注册人脸解锁提供程序..."; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\first_run.ps1"" -Root ""{app}"""; Description: "立即配置账户密码并录入人脸"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\unregister.ps1"" -Root ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterFaceUnlock"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  StopScript: String;
begin
  if CurStep = ssInstall then
  begin
    StopScript := ExpandConstant('{app}\scripts\stop_agent.ps1');
    if FileExists(StopScript) then
      Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "' + StopScript + '"',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;






