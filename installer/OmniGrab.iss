#define MyAppName "OmniGrab"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "OmniGrab Open Source"
#define MyAppExeName "OmniGrab.exe"

[Setup]
AppId={{B5B9F45B-BA2C-4D54-9E87-4B6B8A8A5C21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
SetupIconFile=..\omnigrab-icon.ico
DefaultDirName={autopf}\OmniGrab
DefaultGroupName=OmniGrab
OutputDir=..\Output
OutputBaseFilename=OmniGrab-Setup
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\OmniGrab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OmniGrab"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\OmniGrab"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir OmniGrab"; Flags: nowait postinstall skipifsilent
