# ld2singe

**Put the original arcade laserdisc video into a Hypseus Singe game.**

Many Singe games play video taken from a later DVD release, not from the arcade laserdisc. The DVD
video is often altered: reframed, re-encoded, or with extra graphics drawn over it. (The Time Traveler
DVD, for example, has a wavy grid and a tunnel background over the footage.)

ld2singe takes the laserdisc video from a MAME CHD and rebuilds the game's video from it. It finds,
scene by scene, which laserdisc frames show the same picture as each frame of the game's video, then
writes a new video with **the same frame numbers**. The game's scripts are not changed at all, so the
game plays exactly as before, with laserdisc footage.

Version 1.0. Tested on one game so far: the *Hologram Time Traveler* Singe 2 port with the MAME
`timetrv` laserdisc, on Windows 11.

> **No game data is included.** You need your own copies of the laserdisc image and the Singe game.
> ld2singe only reads them; it never changes your original files.

---

## What you need

| | What | Why |
|---|---|---|
| 1 | **The game's laserdisc CHD** from a MAME set (for Time Traveler: the `timetrv` set's `.chd`) | The source of the original footage |
| 2 | **chdman** (it comes with [MAME](https://www.mamedev.org/), in the MAME folder) | Turns the CHD into a video file |
| 3 | **The working Singe game folder**: its framefile (`.txt`), its video (`.m2v` + `.ogg`), and its scripts (`.singe`/Lua files, or a Zip ROM `.zip` for Singe 2 games) | ld2singe matches against this video and copies everything else across unchanged |
| 4 | **[Hypseus Singe](https://github.com/DirtBagXon/hypseus-singe)** | To play the result |
| 5 | **Python 3.9 or newer**, with `numpy` and `Pillow` | Runs ld2singe |
| 6 | **ffmpeg** (with ffprobe) | Reads and writes the video |
| 7 | **Disk space**: the extracted laserdisc video is uncompressed and very large (tens of GB); the finished game video is typically under 1 GB | |

The game must already work in Hypseus Singe with its original video before you start.

## Download

**Windows, easiest:** from the [Releases page](https://github.com/Soarbywire/ld2singe/releases), download
`ld2singe-1.0-windows.zip` and unzip it into a folder of its own. It contains `ld2singe.exe`, so you don't
need Python. You still need **ffmpeg** (setup step 2 below), or put `ffmpeg.exe` and `ffprobe.exe` in the
same folder as `ld2singe.exe`. Then use `ld2singe.exe` wherever this guide says `py ld2singe.py`.

**Any system (Windows, Linux, macOS):** download `ld2singe.py` and follow the setup below.

## One-time setup (Windows, if you use the Python script)

1. Install Python from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"** in the installer.
2. Install ffmpeg (needed by the .exe too). In a Command Prompt or PowerShell window:
   ```
   winget install Gyan.FFmpeg
   ```
   Close the window and open a new one afterwards, so it can find ffmpeg.
3. Install the two Python packages:
   ```
   py -m pip install numpy pillow
   ```
4. Download `ld2singe.py` from this repository (or the whole repository as a zip) into a folder of its own.

On Linux or macOS, install `python3`, `ffmpeg`, `numpy` and `Pillow` with your package manager and use
`python3` wherever these instructions say `py`.

## How to use it

The examples use these locations. Change them to yours.

```
C:\mame\timetrv.chd                       the laserdisc CHD
C:\Hypseus Singe\singe\Hologram           the working game folder
C:\mame\ld2singe_work                     a new, empty work folder
```

### Step 1: extract the laserdisc video

```
cd C:\mame
chdman extractld -i timetrv.chd -o timetrv.avi
```

The white dashes at the top of this video are data lines from the disc (frame numbers). ld2singe finds
them and blacks them out.

### Step 2: match the two videos

```
py ld2singe.py --ld "C:\mame\timetrv.avi" --game "C:\Hypseus Singe\singe\Hologram" --out "C:\mame\ld2singe_work" --step match
```

The first run scans both videos, which is the slow part. Results are saved, so later runs are quick.
When it finishes it prints a summary like this:

```
summary: NO 3730 frames (6.3%), blank 2618 frames (4.4%), dark 633 frames (1.1%), ok 51574 frames (86.8%), weak 851 frames (1.4%)
```

| Status | Meaning | What the new video uses |
|---|---|---|
| `ok` | Clear match | Laserdisc |
| `weak` | Probable match, lower score | Laserdisc |
| `dark` | Too dark to match by itself; follows its neighbour | Laserdisc |
| `bridged` | Unmatched gap between two stretches of the same clip | Laserdisc |
| `blank` | Nothing in the game's video but background | Black |
| `NO MATCH` | Not found on the laserdisc (for example a title screen the port added) | The game's own video |

### Step 3: look at the check sheets

Open the `check` folder inside the work folder. Each page shows scenes as pairs of pictures: the game's
video on the left, the laserdisc frame it was matched to on the right.

- In every pair the two pictures should show the **same moment** (same pose).
- Green rows are `ok`, blue are `bridged`, yellow are `weak`. Check the yellow rows most carefully.
- `report.csv` in the work folder lists every scene with its laserdisc frame and scores.

### Step 4: build

```
py ld2singe.py --ld "C:\mame\timetrv.avi" --game "C:\Hypseus Singe\singe\Hologram" --out "C:\mame\ld2singe_work"
```

It should end with a line like:

```
check: the new video has 59406 frames, the game's video has 59406  - OK
```

The new game is in `ld2singe_work\game`:

```
game\
  Hologram.txt        new framefile (one line pointing at the new video)
  Hologram.zip        copied unchanged (the game's scripts)
  ld\
    Hologram.m2v      the new video
    Hologram.ogg      the new sound
  ...                 anything else from the original folder, except its old video folder
```

### Step 5: install and play

1. Rename the original game folder, for example `Hologram` to `Hologram_original`. Keep it as a backup.
2. Copy `ld2singe_work\game` into the `singe` folder and give it **exactly** the original name
   (`Hologram`, same capital letters).
3. Start the game the way you always did. The first start says "Parsing video file(s)" while Hypseus
   indexes the new video.

For example, for a Singe 2 Zip ROM game:

```
hypseus.exe singe vldp -framefile singe\Hologram\Hologram.txt -zlua singe\Hologram\Hologram.zip -homedir . -datadir . -fullscreen
```

Two batch files, `1_match_and_check.bat` and `2_build.bat`, run steps 2 and 4 for you. Edit the three
paths at the top of each first.

## Options

| Option | Default | What it does |
|---|---|---|
| `--ld FILE` | required | Laserdisc video from `chdman extractld` |
| `--game FOLDER` | required | The working Singe game folder |
| `--out FOLDER` | `ld2singe_work` | Work folder: saved scans, report, check sheets, and the new `game` folder |
| `--step NAME` | `build` | Stop after `scan`, `calibrate`, `match` or `build` |
| `--framefile FILE` | found automatically | The game's framefile, if it isn't found |
| `--framing game\|disc` | `game` | `game`: the picture sits exactly where the game's video had it. `disc`: the full-size laserdisc picture with no resizing (sharpest), which may sit differently from the game's on-screen graphics |
| `--bridge N` | `900` | Longest unmatched gap, in frames, to fill from the laserdisc when both sides are the same clip. `0` turns it off |
| `--clean-kept N` | `0` | Erase thin lines (a drawn-on grid) from stretches that keep the game's own video. `1`-`3` = stronger. This also thins small text |
| `--degrid N` | `2` | Ignore thin lines when comparing the videos. `0` = off. Changing it rescans |
| `--min-score X` | `0.70` | How close the pictures must be for a scene to count as `ok` |
| `--vbi-rows N` | automatic | Rows to black out at the top of the laserdisc picture |
| `--size WxH` | same as the game | Size of the new video |
| `--ffmpeg PATH` | from the PATH | Full path to ffmpeg, if it isn't on the PATH |
| `--rescan` | | Ignore the saved scans and scan again |

## How it works

1. **Scan.** Every frame of both videos becomes a small greyscale thumbnail. Thin lines are filtered
   out first, so a grid drawn over one video doesn't spoil the comparison.
2. **Calibrate.** It tries the game's video the right way up, upside-down, mirrored and rotated, and
   measures how it is scaled and shifted relative to the laserdisc. It then repeats the measurement on
   real frames of matched moments, to place the picture within a pixel or two.
3. **Match.** The game's video is split into scenes. Each scene is searched for on the laserdisc by
   picture and by movement from frame to frame. A long scene whose start and end disagree is split and
   each part matched separately.
4. **Build.** The new video is written frame by frame as one file. For each laserdisc stretch it seeks
   close to the start, then compares thumbnails with the scan to begin on exactly the right frame.
   Interlaced fields are resized separately so the picture stays sharp. The laserdisc's sound level is
   adjusted to match the game's own sound.

## Limitations

- **It is not perfect.** Check the result by playing it. Scenes that exist only in the port (title
  screens, attract-mode montages) are not on the laserdisc and keep the game's own video.
- Tested on one game and one laserdisc image. Other games may need different options, or may not match well.
- A match can be wrong where two different clips look nearly the same. The check sheets are there to catch this.
- In `game` framing the picture is resized to fit the game's layout, so it is slightly softer than the
  raw laserdisc. `--framing disc` avoids that.
- The sound is taken from the laserdisc for laserdisc stretches and from the game for kept stretches,
  so the sound character can change slightly between them.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python was not found` | Use `py` instead of `python` on Windows |
| `can't open file ld2singe.py` | `cd` into the folder that contains `ld2singe.py` first |
| `ffmpeg` not found | Open a new window after installing ffmpeg, put `ffmpeg.exe` and `ffprobe.exe` next to `ld2singe`, or pass `--ffmpeg "C:\path\to\ffmpeg.exe"` |
| Windows SmartScreen warns about `ld2singe.exe` | It isn't code-signed. Click "More info", then "Run anyway", or use the Python script instead |
| The game says a script isn't found after installing | The new folder's name must match the original exactly, including capital letters |
| `check: ... DIFFERENT` at the end of the build | The new video is the wrong length. Please open an issue with the log |
| Picture in the wrong place or wrong size | Try `--framing disc`, or check the "precise framing" lines in the log |
| A scene shows the wrong clip | Find it in `report.csv` and the check sheets. Raising `--min-score` makes matching stricter |

## Raspberry Pi and Linux

The converted game is an ordinary Singe game: one MPEG-2 video at the game's original size, one Ogg
sound file and a framefile. If the original game runs in Hypseus Singe on your Raspberry Pi, the
converted one should too. The file size doesn't matter for playback.

- **File names are case-sensitive on Linux.** Keep the folder and file names exactly as they are,
  and use the same capital letters in your launch command.
- Run the conversion on a desktop PC and copy the finished `game` folder to the Pi. The conversion needs
  the very large extracted laserdisc video, so it is slow on a Pi.

## Legal

ld2singe is a conversion tool. It contains no game code, video, sound or disc images, and this project
does not provide them. Use it only with laserdisc images and games you are entitled to use.

## Licence

MIT. See [LICENSE](LICENSE).
