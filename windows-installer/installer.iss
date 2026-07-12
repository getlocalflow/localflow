[Setup]
AppName=LocalFlow
AppVersion=1.0.0-beta
AppPublisher=LocalFlow
DefaultDirName={autopf}\LocalFlow
DefaultGroupName=LocalFlow
UninstallDisplayIcon={app}\LocalFlow.exe
OutputBaseFilename=LocalFlow-Setup
OutputDir=Output
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
; paths are relative to this .iss file; pyinstaller runs from windows-installer/
Source: "dist\LocalFlow\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\LocalFlow"; Filename: "{app}\LocalFlow.exe"
Name: "{userstartup}\LocalFlow"; Filename: "{app}\LocalFlow.exe"; Tasks: autostart

[Tasks]
Name: "autostart"; Description: "Start LocalFlow automatically when I sign in"; GroupDescription: "Startup:"; Flags: checkedonce

[Run]
Filename: "{app}\LocalFlow.exe"; Description: "Launch LocalFlow now"; Flags: nowait postinstall skipifsilent
