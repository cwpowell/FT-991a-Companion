# Windows program and installer

These files are built by GitHub, because a Windows program can only be built on Windows. Go to
**Actions**, choose **Build installers**, then **Run workflow**. When it finishes, the files below
are committed to this folder.

| File | What it is |
| --- | --- |
| `FT-991A-Panel-Setup.exe` | The installer. It adds a Start menu entry and an uninstaller, with an optional desktop shortcut, and needs no administrator rights unless you choose to install for all users. |
| `FT-991A-Panel.exe` | The program on its own. Copy it anywhere and run it; nothing is installed. |

Both contain Python, Tk, the sound library and the Opus library (`opus.dll`, from the
`Contributions` folder), so there is nothing else to install.

## Good to know

- **The first start:** the program is not signed, so Windows SmartScreen may say "Windows protected
  your PC". Click **More info**, then **Run anyway**.
- **A newer `opus.dll`:** the installer also puts `opus.dll` beside the program. If a copy sits
  next to `FT-991A-Panel.exe`, it is used instead of the one built in, so you can replace it with a
  newer one. No system folder is involved.
- **Virtual audio cables are not included.** To share the audio with FreeDV you need two cables,
  such as VB-Cable A and B from https://vb-audio.com/Cable/ . Without them, choose "(direct)" for the
  radio ends of the Audio tab. The manual explains which side of each cable goes where.
- Windows must allow desktop apps to use the microphone (Settings, then Privacy, then Microphone).
- Settings are kept in `C:\Users\<you>\.ft991a_panel.json`.
