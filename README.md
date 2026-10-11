# Hand-thesia 3D

Falling-notes piano videos rendered in 3D with Blender, with realistic hands that actually play the notes.

Give it a MIDI file and it renders a video: a 3D keyboard, glowing notes falling onto the keys, a pair of
sculpted hands pressing each key with a planned fingering, and the audio of the performance.

> **Built on [Hand-thesia](https://github.com/V9011N/pygame-piano-animator) by [V9011N](https://github.com/V9011N).**
> Their 2D app does the hard musical part: it reads the MIDI, splits it between the hands, works out the
> fingering and animates the hands. This project adds a 3D renderer and Fallboard, a browser-based video studio, on top.

<!-- Demo video: on GitHub, click the pencil icon to edit this file and drag an .mp4 (under 10 MB) right here. -->

## What the 3D part adds

- **Real 3D hands.** Sculpted hands (from Blender Studio's CC0 Human Base Meshes) rigged and posed from the
  2D app's hand animation every frame. Fingertips land on the keys to within about a millimetre, black keys
  included.
- **Keys that follow the fingers.** Each key goes down as far as the finger pushes it, and lights up in that
  hand's colour.
- **Styles.** Presets (Classic, Embers, Neon, Glass, Rainbow, Minimal) plus a long list of settings: notes on a
  wall or a flat runway, glow, fire, sparks, floating dust, lights, haze, bloom, camera angle and intro moves,
  titles, and more.
- **More hands than two.** Extra pianists can either copy the music in other octaves, or **share it side by
  side**: the piece is divided among them so each hand only plays what it can reach, which lets them play
  passages that two hands can't.
- **Human touch.** Subtle, natural motion: loud notes struck from higher than soft ones, idle fingers drifting,
  neighbouring fingers dipping together, and a hand that is never quite still. Adjustable, or off.
- **Hands appear one at a time.** Optionally, each hand slides in just before it first plays.
- **Vertical and square videos.** Use 9:16 for Shorts/TikTok/Reels or 1:1. The camera moves closer and follows
  the hands.
- **Fallboard.** A clean, minimal page in your browser for editing a style, previewing single frames or
  3-second clips, and queuing full renders with a progress bar. Everything runs locally on your computer.
- **Audio.** What you hear is what the hands play: the performance is rendered to WAV with FluidSynth and
  muxed into the video with ffmpeg.

Still included from the original: the real-time 2D falling-notes player, the fingering editor and the
"Pianists & hands" editor. See the [original project](https://github.com/V9011N/pygame-piano-animator) for
those.

## Requirements

- **Windows** (the main target; other systems may work with some path tweaks)
- **Python 3.10+**
- **[Blender 5.1](https://www.blender.org/download/)**. The scripts look for it in
  `C:\Program Files\Blender Foundation\Blender 5.1\`; set the `BLENDER` environment variable if it's elsewhere.
- **[ffmpeg](https://ffmpeg.org/download.html)** on your PATH
- **[FluidSynth](https://github.com/FluidSynth/fluidsynth/releases)** and a General MIDI SoundFont (the project
  uses `MuseScore_General.sf2`)
- A decent GPU helps. On an RTX 4060 a 1080p frame takes about 1 second at "balanced" quality.

## Setup

```bash
git clone https://github.com/mrbobbrick/3D-piano-animator.git
cd 3D-piano-animator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### One-time assets (not in the repo)

These are third-party files, so they aren't committed. They all go in `blender/assets/`:

| What | Where it goes |
|---|---|
| FluidSynth | `blender/assets/fluidsynth/bin/fluidsynth.exe` (or set `FLUIDSYNTH`) |
| SoundFont | `blender/assets/MuseScore_General.sf2` (or set `SOUNDFONT`) |
| MakeHuman hands | `blender/assets/hands.blend`, built with `blender -b -P blender/make_hands.py` |
| Sculpted hands (default) | `blender/assets/hands_sculpted.blend`, built from Blender Studio's Human Base Meshes |

The hands are built in two steps:

1. **MakeHuman hands.** Install the [MPFB](https://extensions.blender.org/add-ons/mpfb/) extension in
   Blender and unzip its CC0 system assets and skins into MPFB's user data folder. Then run:
   ```bash
   blender -b -P blender/make_hands.py
   ```
   The sculpted hands borrow this rig, so you need it even if you only use the sculpted ones.
2. **Sculpted hands.** Download Blender Studio's
   Human Base Meshes bundle (v1.4.1, CC0, on mirror.blender.org), then run:
   ```bash
   blender -b --disable-autoexec -P blender/make_hands_sculpted.py -- path/to/HumanBaseMeshes.blend
   ```

More detail is in the "3D renderer" section of [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Usage

**Fallboard (easiest):**

```bash
python video_studio.py
```

This opens Fallboard in your browser. Pick a MIDI file, tweak a style, preview, then **Render**.

**From the command line:**

```bash
python render_video.py song.mid                       # -> Renders/song.mp4
python render_video.py song.mid out.mp4 --style Embers --quality high
python render_video.py song.mid --res 1080x1920       # vertical video
python render_video.py song.mid --start 30 --end 60   # just part of the song
```

| Option | Values |
|---|---|
| `--style` | a preset (`Classic`, `Embers`, `Neon`, `Glass`, `Rainbow`, `Minimal`), a style saved in Fallboard, or a JSON file |
| `--quality` | `flat` (quick check), `preview`, `fast`, `balanced` (default), `high`, `max` |
| `--view` | `auto`, `bird`, `pianist`, `side`, `top` |
| `--fps`, `--res` | default `30` and `1920x1080` |

Renders can be stopped and resumed: frames already done are kept.

**The original 2D app** still works the same way:

```bash
python main.py song.mid          # real-time falling notes with animated hands
python main.py song.mid --edit   # fingering editor
```

## How it works

1. The original Hand-thesia code splits the MIDI between the hands, plans the fingering and animates both
   hands.
2. `export3d.py` samples the hands' joints every frame, in metres, along with the keyboard and the
   performance, and writes it as JSON.
3. `render_video.py` turns the performance into audio with FluidSynth, runs Blender (`blender/render3d.py`)
   to build the scene and render the frames, then has ffmpeg combine them into an MP4.
4. `video_studio.py` (Fallboard) is a small local web server (Python standard library only) that keeps a Blender open
   for fast previews.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Credits and license

- **2D Hand-thesia** (MIDI loading, hand split, fingering planner, hand animation, editors) by
  [V9011N](https://github.com/V9011N/pygame-piano-animator). The original project doesn't include a license,
  so all rights to that code stay with its author.
- **3D renderer and Fallboard** (`export3d.py`, `render_video.py`, `video_style.py`, `video_studio.py`,
  `blender/`, `studio/`) by [mrbobbrick](https://github.com/mrbobbrick).
- Sculpted hands from Blender Studio's Human Base Meshes (CC0). MakeHuman/MPFB assets (CC0).

Made with a lot of help from Claude. It's a vibe-coded hobby project, so expect rough edges.
