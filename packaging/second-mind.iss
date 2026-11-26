#define MyAppName "Second Mind"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Second Mind Contributors"

[Setup]
AppId={{C79AF758-B96B-40A0-AF5C-684274A3358B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Second Mind
DefaultGroupName=Second Mind
OutputDir=..\dist\installer
OutputBaseFilename=Second-Mind-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayIcon={app}\second-mind-desktop\second-mind-desktop.exe

[Files]
Source: "..\dist\second-mind-core\*"; DestDir: "{app}\second-mind-core"; Flags: ignoreversion recursesubdirs
Source: "..\dist\second-mind-desktop\*"; DestDir: "{app}\second-mind-desktop"; Flags: ignoreversion recursesubdirs
Source: "..\dist\second-mind-mcp\*"; DestDir: "{app}\second-mind-mcp"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Second Mind"; Filename: "{app}\second-mind-desktop\second-mind-desktop.exe"
Name: "{autodesktop}\Second Mind"; Filename: "{app}\second-mind-desktop\second-mind-desktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\second-mind-desktop\second-mind-desktop.exe"; Description: "Launch Second Mind"; Flags: nowait postinstall skipifsilent

