; Inno Setup script for the Idaho Hunt Planner (Windows).
;
;   pyinstaller --clean --noconfirm packaging\windows\planner.spec
;   iscc packaging\windows\installer.iss
;
; Produces dist\installer\IdahoHuntPlanner-Setup-<version>.exe: one file the user
; double-clicks. No Python, no venv, no command line — the interpreter and every
; dependency are inside the PyInstaller build this packs up.
;
; Per-user install (PrivilegesRequired=lowest): no administrator prompt, and nothing
; is written outside the user's own profile. The hunt database lives in
; %LOCALAPPDATA%\IdahoHuntPlanner and is deliberately NOT removed on uninstall — tags,
; permits and reviewed seasons are the user's data, not ours.

#define AppName "Idaho Hunt Planner"
#define AppVersion "0.1.0"
#define AppPublisher "Josh Laney"
#define AppUrl "https://github.com/joshualaney23/Idaho-Hunt-Planner"
#define AppExeName "IdahoHuntPlanner.exe"

[Setup]
AppId={{CF989799-5042-5CF9-A078-41CEA02263EA}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=IdahoHuntPlanner-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; The app only ever talks to 127.0.0.1 and idfg.idaho.gov, so nothing here needs a
; reboot or a service.
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Everything PyInstaller's one-dir build produced.
Source: "..\..\dist\IdahoHuntPlanner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; First launch downloads and imports the current IDFG brochures, which takes a few
; minutes; the console window the app opens reports what it staged.
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent
