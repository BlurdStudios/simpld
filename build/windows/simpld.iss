; Simpl'd — Inno Setup 6 script (Windows installer).
;
; Produces a single setup exe that installs the PyInstaller onedir build
; (dist\Simpld\Simpld.exe + support files) into Program Files with
; Start Menu / optional desktop shortcuts and a standard uninstaller
; (Add/Remove Programs + unins000.exe + Start Menu Uninstall shortcut).
;
; Build order on a native Windows PC (from the project root):
;   python -m pip install -r requirements.txt
;   python -m pip install -r build\windows\build_requirements.txt
;   python -m PyInstaller --noconfirm build\windows\simpld.spec
;   iscc build\windows\simpld.iss
;
; Output: dist\Simpld-Setup-1.0.0.exe
; Note: build on Windows only — a Linux host cannot produce a real PE
; (see DEMO_WINDOWS.md; the Wine route is documented as blocked).

#define MyAppName "Simpl'd"
; Version: local builds use this default; the release workflow passes
; /DMyAppVersion=<tag version> after verifying it against factory/version.py,
; so the installer, the About dialog and the release tag can never disagree.
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Blur'd"
#define MyAppExeName "Simpld.exe"

[Setup]
AppId={{9EDD6D53-9500-413C-9B46-6EEE2DFFDF2F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (C) Blur'd
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\Simpld
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=Simpld-Setup-{#MyAppVersion}
SetupIconFile=..\..\factory\ui\assets\simpld.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
; Per-user data lives outside the install dir (%LOCALAPPDATA%\Simpld),
; so uninstalling never deletes the factory database. No [UninstallDelete]
; entry for the data folder on purpose.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main executable first, then the whole onedir support folder.
Source: "..\..\dist\Simpld\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\Simpld\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Relaunch leg of the auto-update: the app exits, this installer replaces the
; files and must bring Simpl'd back up on its own — so NO `skipifsilent`.
; `runasoriginaluser` matters on factory PCs: after an elevated install the
; app must start as the logged-in operator (its data lives in that user's
; %LOCALAPPDATA%), never as the admin account that approved UAC.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall runasoriginaluser
