# macOS installers

The disk images are built by GitHub rather than by hand, because a Mac app can only be built on a
Mac. Download them from the **Releases** page of this repository. The version is in the file name,
and the app's title bar shows it too.

| File | For |
| --- | --- |
| `ft-991a-apple-silicon-<version>.dmg` | Macs with an Apple chip (M1 and later) |
| `ft-991a-intel-<version>.dmg` | Macs with an Intel chip |

To make a new build, go to **Actions**, choose **Build installers**, then **Run workflow**, and leave
the Version box empty: the version is the one in `ft991a-panel.py`. The finished files are attached to
a *draft* release; publish it to make them public. A build stops with an error if the app it made is not
the version in `ft991a-panel.py`.

To see which you have, choose Apple menu, then **About This Mac**: it says either "Chip: Apple M..."
or "Processor: Intel...".

## Installing

1. Open the `.dmg` and drag **FT-991A Panel** onto the **Applications** shortcut.
2. Start it from Applications. Python, Tk, the sound library and the Opus library are all inside the
   app, so there is nothing else to install.

## The first start: macOS will object

The app is not signed with an Apple Developer ID (that costs $99 a year), so macOS blocks it the
first time.

- **macOS 15 and later:** try to open it, then open **System Settings**, then **Privacy & Security**.
  Scroll down to the message that "FT-991A Panel" was blocked and click **Open Anyway**.
- **macOS 14 and earlier:** Control-click (right-click) the app, choose **Open**, then **Open**.
- If macOS says the app is "damaged", run this once in Terminal:
  `xattr -dr com.apple.quarantine "/Applications/FT-991A Panel.app"`

The first time you start the audio, macOS asks whether the app may use the microphone. Allow it.

## Good to know

- **BlackHole is not included.** You only need it if you want virtual audio cables (for example to
  share the audio with FreeDV). Install it from https://existential.audio/blackhole/ . Without it,
  choose "(direct)" for the radio ends of the Audio tab.
- The images are built on macOS 14 (Apple Silicon) and macOS 15 (Intel). Older macOS versions may
  not run them.
- GitHub is retiring its Intel Mac build machines in fall 2027. After that only the Apple Silicon
  image can be rebuilt automatically.
- Settings are kept in `~/.ft991a-panel.json`.
