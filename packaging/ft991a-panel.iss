; FT-991A Companion
; Copyright (C) 2026  Charles W. Powell, NK8O
;
; This program is free software: you can redistribute it and/or modify
; it under the terms of the GNU General Public License as published by
; the Free Software Foundation, either version 3 of the License, or
; (at your option) any later version.
;
; This program is distributed in the hope that it will be useful,
; but WITHOUT ANY WARRANTY; without even the implied warranty of
; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
; GNU General Public License for more details.
;
; You should have received a copy of the GNU General Public License
; along with this program.  If not, see <https://www.gnu.org/licenses/>.
;
; SPDX-License-Identifier: GPL-3.0-or-later

; Inno Setup 6 script for the FT-991A panel installer.
; Build with packaging\build-windows.bat, or:  ISCC /DAppVersion=1.0.0 packaging\ft991a-panel.iss
; It expects dist\FT-991A-Panel.exe to have been built already.

#ifndef AppVersion
  #define AppVersion "1.5.0"
#endif

[Setup]
AppId={{6E1F6B7B-3C52-4C7E-9F2D-5A0B8D3E41A7}
AppName=FT-991A Panel
AppVersion={#AppVersion}
AppPublisher=cwpowell
DefaultDirName={autopf}\FT-991A Panel
DefaultGroupName=FT-991A Panel
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=FT-991A-Panel-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; install for the current user without asking for administrator rights, unless the user chooses
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\FT-991A-Panel.exe
SetupIconFile=..\packaging\icons\ft991a.ico

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\FT-991A-Panel.exe"; DestDir: "{app}"; Flags: ignoreversion
; The program already has a copy of opus.dll built in. This one beside it takes priority, so a
; newer opus.dll can simply replace it (no need to touch a system folder).
Source: "..\Contributions\opus.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\FT-991A Panel"; Filename: "{app}\FT-991A-Panel.exe"
Name: "{autodesktop}\FT-991A Panel"; Filename: "{app}\FT-991A-Panel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FT-991A-Panel.exe"; Description: "Start FT-991A Panel"; Flags: nowait postinstall skipifsilent
