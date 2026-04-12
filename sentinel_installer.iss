; ═══════════════════════════════════════════════════════════════════════════
;  SentinelAI v2 — Inno Setup Installer Script
;
;  Builds a Windows installer (.exe) for SentinelAI
;
;  Requirements:
;    Inno Setup 6.x  →  https://jrsoftware.org/isinfo.php
;
;  Usage:
;    1. Build EXE first:  pyinstaller sentinel.spec --clean
;    2. Open this file in Inno Setup IDE → Compile
;       OR run:  ISCC.exe sentinel_installer.iss
;
;  Output:
;    installer/SentinelAI_v2_Setup.exe
; ═══════════════════════════════════════════════════════════════════════════

#define AppName      "SentinelAI"
#define AppVersion   "2.0.0"
#define AppPublisher "SentinelAI"
#define AppURL       "https://github.com/sentinel-ai/sentinel"
#define AppExeName   "SentinelAI.exe"
#define WakeExeName  "SentinelAI.exe"
#define DistDir      "dist"
#define AppDataName  "SentinelAI"

[Setup]
; App identity
AppId={{F3A2B1C4-8D5E-4F9A-B3C7-E2D1F4A5B8C9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Install location
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=no

; Output
OutputDir=installer
OutputBaseFilename=SentinelAI_v3_Setup
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; UI
WizardStyle=modern
WizardResizable=yes
; SetupIconFile=resources\icons\sentinel_icon.ico  ; Commented out to bypass EndUpdateResource failed (110) file locks
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} v{#AppVersion}

; Windows requirements (Windows 10 1809 minimum)
MinVersion=10.0.17763
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Architecture
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; Misc
ChangesEnvironment=yes
DisableWelcomePage=no
DisableReadyMemo=no
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut";      GroupDescription: "Shortcuts:"
Name: "startupentry";  Description: "Start with &Windows (wake word only)"; GroupDescription: "Startup:"; Flags: unchecked
Name: "startmenuicon"; Description: "Create Start &Menu shortcuts";    GroupDescription: "Shortcuts:"

; ─────────────────────────────────────────────────────────────────────────────
;  FILES — the main app bundle from PyInstaller dist/
; ─────────────────────────────────────────────────────────────────────────────

[Files]

; Main application bundle (folder contents from --onedir build)
Source: "{#DistDir}\{#AppName}\*";    DestDir: "{app}";     Flags: ignoreversion recursesubdirs createallsubdirs

; .env template (don't overwrite existing .env — user's API keys live here)
; .env template removed, directly deploying .env
Source: ".env";            DestDir: "{app}";     DestName: ".env";         Flags: ignoreversion onlyifdoesntexist

; Resources (icons only)
Source: "resources\icons\*";         DestDir: "{app}\resources\icons";         Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; VC++ Redistributable (required for many Python native extensions)
Source: "redist\vc_redist.x64.exe";  DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

; ─────────────────────────────────────────────────────────────────────────────
;  SHORTCUTS
; ─────────────────────────────────────────────────────────────────────────────

[Icons]
; Start Menu
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\resources\icons\sentinel_icon.ico"; Tasks: startmenuicon
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}";      Tasks: startmenuicon

; Desktop
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\resources\icons\sentinel_icon.ico"; Tasks: desktopicon

; ─────────────────────────────────────────────────────────────────────────────
;  WINDOWS STARTUP — Wake Word Only (detached, lightweight)
;
;  The wake word process (sentinel_wake.exe) is tiny (~40MB with Vosk model)
;  and runs independently. It starts with Windows, waits silently in the
;  background, and launches the full SentinelAI.exe when the wake word is
;  detected.
; ─────────────────────────────────────────────────────────────────────────────

[Registry]
; Auto-start wake word process at Windows logon (only if task selected)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; \
  ValueName: "SentinelAI_WakeWord"; \
  ValueData: """{app}\{#AppExeName}"" --background"; \
  Flags: uninsdeletevalue; \
  Tasks: startupentry

; App settings storage location hint
Root: HKCU; Subkey: "Software\{#AppPublisher}\{#AppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey

; ─────────────────────────────────────────────────────────────────────────────
;  INSTALLATION STEPS
; ─────────────────────────────────────────────────────────────────────────────

[Run]
; Install VC++ Redist if present
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Visual C++ Runtime..."; Flags: skipifdoesntexist runascurrentuser waituntilterminated

; Install Playwright Chromium browser (required for browser_agent)
Filename: "{app}\python.exe"; Parameters: "-m playwright install chromium"; WorkingDir: "{app}"; StatusMsg: "Installing Playwright browser (for browser automation)..."; Flags: skipifdoesntexist runascurrentuser waituntilterminated

; Open API key setup on first install
Filename: "{app}\.env"; StatusMsg: "Opening API key setup guide..."; Flags: runascurrentuser postinstall shellexec skipifsilent unchecked; Description: "Open .env to set up advanced API keys (Groq, AssemblyAI, etc.)"

; Launch SentinelAI after install
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall skipifsilent; Description: "Launch {#AppName} now"

; ─────────────────────────────────────────────────────────────────────────────
;  UNINSTALL CLEANUP
; ─────────────────────────────────────────────────────────────────────────────

[UninstallDelete]
; Remove app data created at runtime (logs, FAISS index, session memory)
Type: filesandordirs; Name: "{localappdata}\{#AppDataName}"
Type: filesandordirs; Name: "{userappdata}\{#AppDataName}"
; Remove sentinel_wake from startup
Type: files;          Name: "{userstartup}\SentinelAI Wake Word.lnk"

[UninstallRun]
; Kill running processes before uninstall
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExeName}  /T"; Flags: runhidden skipifdoesntexist

; ─────────────────────────────────────────────────────────────────────────────
;  WELCOME / INFO PAGES
; ─────────────────────────────────────────────────────────────────────────────

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%n%nBEFORE YOU BEGIN:%n%nYou will need free API keys for:%n  • Groq (LLM)        → console.groq.com%n  • AssemblyAI (STT)  → assemblyai.com%n%nBoth are free to sign up. After installation, open .env and add your keys.%n%nClick Next to continue.

; ─────────────────────────────────────────────────────────────────────────────
;  CUSTOM PASCAL SCRIPT — pre-install checks
; ─────────────────────────────────────────────────────────────────────────────

[Code]
var
  ApiKeyPage: TInputQueryWizardPage;
  GroqKeyEdit: TEdit;
  AssemblyKeyEdit: TEdit;

procedure InitializeWizard();
begin
  // Page: Collect API keys during install so .env is pre-filled
  ApiKeyPage := CreateInputQueryPage(
    wpSelectDir,
    'API Keys Setup',
    'Enter your free API keys (you can skip and edit .env later)',
    ''
  );
  ApiKeyPage.Add('Groq API Key (get free at console.groq.com):', False);
  ApiKeyPage.Add('AssemblyAI API Key (get free at assemblyai.com):', False);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  EnvFile: string;
  EnvContent: AnsiString;
  GroqKey: string;
  AssemblyKey: string;
begin
  Result := True;

  // After user enters API keys, write them to .env
  if CurPageID = ApiKeyPage.ID then
  begin
    GroqKey    := Trim(ApiKeyPage.Values[0]);
    AssemblyKey := Trim(ApiKeyPage.Values[1]);

    EnvFile := ExpandConstant('{app}\.env');

    if FileExists(EnvFile) then
    begin
      LoadStringFromFile(EnvFile, EnvContent);
    end
    else
    begin
      EnvContent := '';
    end;

    // Inject keys into .env
    if (GroqKey <> '') and (Pos('GROQ_API_KEY', EnvContent) = 0) then
      EnvContent := EnvContent + #13#10 + 'GROQ_API_KEY=' + GroqKey;

    if (AssemblyKey <> '') and (Pos('ASSEMBLYAI_API_KEY', EnvContent) = 0) then
      EnvContent := EnvContent + #13#10 + 'ASSEMBLYAI_API_KEY=' + AssemblyKey;

    if (GroqKey <> '') or (AssemblyKey <> '') then
      SaveStringToFile(EnvFile, EnvContent, False);
  end;
end;

function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);

  // Require Windows 10 1809+
  if (Version.Major < 10) or
     ((Version.Major = 10) and (Version.Build < 17763)) then
  begin
    MsgBox(
      'SentinelAI requires Windows 10 version 1809 or later.' + #13#10 +
      'Please update Windows and try again.',
      mbError, MB_OK
    );
    Result := False;
    Exit;
  end;

  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Create data directory in APPDATA
    ForceDirectories(ExpandConstant('{userappdata}\{#AppDataName}\data'));
    ForceDirectories(ExpandConstant('{userappdata}\{#AppDataName}\logs'));
  end;
end;
