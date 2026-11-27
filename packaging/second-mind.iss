#define MyAppName "Second Mind"
#ifndef MyAppVersion
  #define MyAppVersion "0.4.0"
#endif
#define MyAppPublisher "Second Mind Contributors"
#define MyAppExeName "second-mind-desktop.exe"

[Setup]
AppId={{C79AF758-B96B-40A0-AF5C-684274A3358B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/markKikhtenko/second-mind
AppSupportURL=https://github.com/markKikhtenko/second-mind/issues
AppUpdatesURL=https://github.com/markKikhtenko/second-mind/releases
DefaultDirName={localappdata}\Programs\Second Mind
DefaultGroupName=Second Mind
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Second-Mind-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\second-mind-desktop\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
SetupLogging=yes
SetupIconFile=app-icon.ico

[Files]
Source: "..\dist\second-mind-core\*"; DestDir: "{app}\second-mind-core"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\second-mind-desktop\*"; DestDir: "{app}\second-mind-desktop"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\second-mind-mcp\*"; DestDir: "{app}\second-mind-mcp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\second-mind-updater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "app-icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "autostart"; Description: "Запускать фоновый Core при входе в Windows"; GroupDescription: "Автозапуск:"

[Icons]
Name: "{group}\Second Mind"; Filename: "{app}\second-mind-desktop\{#MyAppExeName}"; IconFilename: "{app}\app-icon.ico"; IconIndex: 0
Name: "{group}\Удалить Second Mind"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Second Mind"; Filename: "{app}\second-mind-desktop\{#MyAppExeName}"; IconFilename: "{app}\app-icon.ico"; IconIndex: 0; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SecondMindCore"; ValueData: """{app}\second-mind-core\second-mind-core.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\second-mind-desktop\{#MyAppExeName}"; Description: "Запустить Second Mind"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\second-mind-core"
Type: filesandordirs; Name: "{app}\second-mind-desktop"
Type: filesandordirs; Name: "{app}\second-mind-mcp"
Type: files; Name: "{app}\second-mind-updater.exe"

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
end;
