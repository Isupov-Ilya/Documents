#define AppVersion "0.2.3"
[Setup]
AppId={{7E91C7A2-4189-4DA6-A7C0-D4BB07476BD3}
AppName=Биография
AppVersion={#AppVersion}
AppPublisher=Local Biography Project
AppPublisherURL=https://github.com/Isupov-Ilya/Documents
DefaultDirName={localappdata}\Programs\Biography
DefaultGroupName=Биография
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=Biography-Setup-Windows-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Biography.exe
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "..\dist\Biography\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Биография"; Filename: "{app}\Biography.exe"
Name: "{autodesktop}\Биография"; Filename: "{app}\Biography.exe"
[Run]
Filename: "{app}\Biography.exe"; Description: "Открыть программу"; Flags: nowait postinstall skipifsilent
