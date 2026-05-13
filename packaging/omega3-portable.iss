; Minimal Inno Setup 6 script — compile: ISCC.exe packaging\omega3-portable.iss
; Adjust SourceDir to your built dist\Omega3.0-portable folder (or build output path).

#define MyAppName "Omega Runtime Studio"
#define MyAppVersion "0.1.0"
#define MyPublisher "OmegaRuntimeStudio"
#define MyExeName "Omega3.0-portable.exe"
#define MyServerExe "Omega3.0-portable-Server.exe"
; Stable Windows installer identity (new GUID = new Programs & Features row vs old string AppId).
; Operator data still lives under %LOCALAPPDATA%\Omega3Portable — not tied to this value.
#define MyAppId "{{967835F1-4A2C-4F8E-B1D3-9C6E2A5F8B0D}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename=OmegaRuntimeStudio-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; *** Set this path to your portable bundle before compiling ***
Source: "..\dist\Omega3.0-portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"
Name: "{group}\Omega Runtime Studio (Server CLI)"; Filename: "{app}\{#MyServerExe}"; Parameters: "serve --host 127.0.0.1 --port 11434"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  ; reserved — add custom uninstall if needed
end;
