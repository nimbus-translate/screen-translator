; Lightweight, per-user Windows installer.  The executable is built from
; build-lite.spec and must be signed before this installer is compiled for a
; tagged release.

#ifndef MyAppVersion
  #define MyAppVersion "0.2.5"
#endif
#ifndef SourceDir
  #define SourceDir "..\\dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\\release"
#endif

#define MyAppName "Screen Translator"
#define MyAppPublisher "Nimbus Translate"
#define MyAppExeName "ScreenTranslator-Lite.exe"

[Setup]
AppId={{0D4C39A6-70AA-4B1D-8CB4-A88EE0E74EA7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=ScreenTranslator-Lite-{#MyAppVersion}-Setup
SetupIconFile=..\assets\app_launch_v4.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
#ifdef EnableSigning
SignTool=ScreenTranslator
SignedUninstaller=yes
#endif

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
