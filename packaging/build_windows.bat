@echo off
rem FT-991A Companion
rem Copyright (C) 2026  Charles W. Powell, NK8O
rem
rem This program is free software: you can redistribute it and/or modify
rem it under the terms of the GNU General Public License as published by
rem the Free Software Foundation, either version 3 of the License, or
rem (at your option) any later version.
rem
rem This program is distributed in the hope that it will be useful,
rem but WITHOUT ANY WARRANTY; without even the implied warranty of
rem MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
rem GNU General Public License for more details.
rem
rem You should have received a copy of the GNU General Public License
rem along with this program.  If not, see <https://www.gnu.org/licenses/>.
rem
rem SPDX-License-Identifier: GPL-3.0-or-later

rem Build FT-991A-Panel.exe and its installer, FT-991A-Panel-Setup.exe, on Windows.
rem   dist\FT-991A-Panel-<version>.exe        the program, one file, opus.dll built in
rem   dist\FT-991A-Panel-Setup-<version>.exe  the installer (needs Inno Setup 6: https://jrsoftware.org/isinfo.php)
rem
rem You need Python 3.12 from python.org (it includes Tk). Run this from any folder.
setlocal
cd /d "%~dp0\.."
rem the version number lives in ft991a_panel.py (set PANEL_VERSION to override it)
if "%PANEL_VERSION%"=="" for /f %%v in ('python packaging\version.py') do set PANEL_VERSION=%%v
echo == Windows build, version %PANEL_VERSION%

if not exist Contributions\opus.dll (
    echo Contributions\opus.dll is missing.
    exit /b 1
)

rem 1. build tools in a private virtual environment
if not exist build mkdir build
python -m venv build\venv || exit /b 1
call build\venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip || exit /b 1
python -m pip install --quiet pyinstaller sounddevice opuslib numpy || exit /b 1

rem 2. the program
if exist dist\FT-991A-Panel.exe del dist\FT-991A-Panel.exe
pyinstaller --noconfirm --clean --distpath dist --workpath build\work packaging\ft991a_panel.spec || exit /b 1
if not exist dist\FT-991A-Panel.exe (
    echo The program was not built.
    exit /b 1
)

rem 3. check that everything really went into it: Tk, PortAudio and Opus.
rem    "start /wait" is needed because the program has no console window.
if exist build\selftest.txt del build\selftest.txt
start /wait "" dist\FT-991A-Panel.exe --selftest build\selftest.txt
if not exist build\selftest.txt (
    echo !! THE SELF-TEST DID NOT RUN: the program did not start.
    exit /b 1
)
type build\selftest.txt
findstr /B /C:"FAIL" build\selftest.txt >nul && (
    echo !! THE SELF-TEST FAILED: something is missing from the program ^(see above^).
    exit /b 1
)

rem 3b. the program must be the version this build says it is. The self-test line reads
rem     "OK   panel version: 1.0.3", and its fourth word is the version.
set BUILT=
for /f "tokens=4" %%v in ('findstr /C:"panel version:" build\selftest.txt') do set BUILT=%%v
if not "%BUILT%"=="%PANEL_VERSION%" (
    echo !! VERSION MISMATCH: this build is meant to be %PANEL_VERSION%, but the program says "%BUILT%".
    exit /b 1
)
echo == version %PANEL_VERSION% confirmed
del dist\FT-991A-Panel-*.exe >nul 2>&1
copy /Y dist\FT-991A-Panel.exe dist\FT-991A-Panel-%PANEL_VERSION%.exe >nul || exit /b 1

rem 4. the installer
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" (
    echo Inno Setup 6 was not found, so only the program was built: dist\FT-991A-Panel-%PANEL_VERSION%.exe
    exit /b 0
)
"%ISCC%" /Qp /DAppVersion=%PANEL_VERSION% packaging\ft991a-panel.iss || exit /b 1
echo == built dist\FT-991A-Panel-%PANEL_VERSION%.exe and dist\FT-991A-Panel-Setup-%PANEL_VERSION%.exe
