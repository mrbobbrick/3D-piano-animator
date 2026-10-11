"""
Render a song as a 3D video: realistic hands playing a piano, with the
falling notes, and the performance's audio.

    python render_video.py song.mid [out.mp4] [--style NAME|FILE] [--quality balanced]
                           [--view auto|bird|pianist|side|top] [--fps 30] [--res 1920x1080]
                           [--start S --end S] [--keep]

--style is a preset (video_style.PRESETS: Classic, Embers, Neon, ...), one of
your saved styles, or a JSON file. --quality: max, high, balanced, fast or
preview (blender/render3d.py QUALITY); balanced is ~1.2 s a frame at 1080p on
an RTX 4060, fast ~0.8 s; flat (plain colours, for checking) is far quicker.

Steps: export3d.py (hand animation) -> performance MIDI -> FluidSynth WAV ->
Blender (blender/render3d.py, PNG frames) -> ffmpeg (H.264 + AAC).
The frames go to a work folder next to the video, so an interrupted render
picks up where it stopped (Blender skips frames already saved).

Tools (environment variables override the defaults):
    BLENDER     C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe
    FLUIDSYNTH  blender/assets/fluidsynth/bin/fluidsynth.exe
    SOUNDFONT   blender/assets/MuseScore_General.sf2
    FFMPEG      ffmpeg (on PATH)
The skinned hands need blender/assets/hands.blend (blender/make_hands.py).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import video_style

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # no console windows popping up on Windows

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "blender", "assets")
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
FLUIDSYNTH = os.environ.get("FLUIDSYNTH", os.path.join(ASSETS, "fluidsynth", "bin", "fluidsynth.exe"))
SOUNDFONT = os.environ.get("SOUNDFONT", os.path.join(ASSETS, "MuseScore_General.sf2"))
FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or "ffmpeg")
SAMPLE_RATE = 48000


def performance_midi(data, path, extra_heard=True):
    """
    What the hands play (strike and release times, pedals), shifted so the
    video's first frame is 0 s; the extra pairs of hands only if extra_heard.
    """
    import pretty_midi
    shift = -data["t0"]
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    piano = pretty_midi.Instrument(program=0, name="Piano")
    for n in data["notes"]:
        if len(n["hand"]) > 1 and not extra_heard:
            continue
        piano.notes.append(pretty_midi.Note(velocity=int(n["velocity"]), pitch=int(n["pitch"]),
                                            start=n["press"] + shift, end=max(n["release"], n["press"] + 0.01) + shift))
    for t, c, v in data["pedals"]:
        piano.control_changes.append(pretty_midi.ControlChange(number=int(c), value=int(v), time=max(0.0, t + shift)))
    pm.instruments.append(piano)
    pm.write(path)


def find_style(name):
    """A preset, a saved style (by name) or a JSON file -> style dict."""
    if name in video_style.PRESETS:
        return video_style.preset(name)
    for n, st in video_style.list_styles():
        if n.lower() == name.lower():
            return st
    with open(name, encoding="utf-8") as fh:
        d = json.load(fh)
    return video_style.resolve(d.get("style", d))


def run(cmd, **kw):
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True, creationflags=NO_WINDOW, **kw)


def stage(*words):
    """A progress line for the browser studio (video_studio.py reads lines starting with '@')."""
    print("@" + " ".join(str(w) for w in words), flush=True)


def export(midi, out, fps, start=None, end=None, clones=()):
    run([sys.executable, os.path.join(HERE, "export3d.py"), midi, out, "--fps", str(fps),
         "--clones", ",".join(str(c) for c in clones)]
        + (["--start", str(start)] if start is not None else [])
        + (["--end", str(end)] if end is not None else []), stdout=subprocess.DEVNULL)
    with open(out) as fh:
        return json.load(fh)


def blender(anim, style_file, *extra):
    run([BLENDER, "-b", "--factory-startup", "-P", os.path.join(HERE, "blender", "render3d.py"), "--",
         anim, "--style", style_file] + list(extra), stdout=subprocess.DEVNULL)


def still(midi, t, style, out_png, view="auto", quality="fast", res="1280x720", work=None):
    """One frame of the video at song time t (the hands need a moment before it to be in place)."""
    work = work or os.path.splitext(out_png)[0] + "_work"
    os.makedirs(work, exist_ok=True)
    anim = os.path.join(work, "still.json")
    export(midi, anim, 30, t - 0.6, t)
    style_file = os.path.join(work, "style.json")
    video_style.dump(style, style_file)
    blender(anim, style_file, "--still", out_png, "--view", view, "--quality", quality, "--res", res)
    return out_png


def main():
    ap = argparse.ArgumentParser(description="Render a song as a 3D video")
    ap.add_argument("midi")
    ap.add_argument("out", nargs="?", help="video file (default: Renders/<song>.mp4)")
    ap.add_argument("--style", default="Classic")
    ap.add_argument("--quality", default="balanced", choices=("max", "high", "balanced", "fast", "preview", "flat"))
    ap.add_argument("--view", default="auto", choices=("auto", "bird", "pianist", "side", "top"))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--res", default="1920x1080")
    ap.add_argument("--start", type=float, help="song time to start at (default: 3 s before the first note)")
    ap.add_argument("--end", type=float, help="song time to end at (default: 2 s after the last note)")
    ap.add_argument("--hands", default="rig", choices=("rig", "stick"))
    ap.add_argument("--keep", action="store_true", help="keep the frames and work files")
    args = ap.parse_args()

    name = os.path.splitext(os.path.basename(args.midi))[0]
    out = os.path.abspath(args.out or os.path.join(HERE, "Renders", f"{name}.mp4"))
    work = os.path.splitext(out)[0] + "_work"
    style_name = os.path.splitext(os.path.basename(args.style))[0]
    frames = os.path.join(work, f"frames_{style_name}_{args.view}_{args.res}_{args.fps}fps_{args.quality}")
    os.makedirs(frames, exist_ok=True)
    clock = time.time()

    style_file = os.path.join(work, "style.json")
    style = find_style(args.style)
    video_style.dump(style, style_file)

    # 1. hand animation (and any extra pairs of hands)
    stage("stage", "export")
    anim = os.path.join(work, "anim.json")
    data = export(args.midi, anim, args.fps, args.start, args.end, video_style.clone_shifts(style))
    stage("frames", data["frames"], frames)

    # 2. audio of what the hands play
    stage("stage", "audio")
    mid = os.path.join(work, "performance.mid")
    wav = os.path.join(work, "performance.wav")
    performance_midi(data, mid, style["extra_heard"])
    run([FLUIDSYNTH, "-ni", "-q", "-g", f"{style['audio_gain']:.2f}", "-r", str(SAMPLE_RATE),
         "-R", "1" if style["reverb"] > 0 else "0",
         "-o", f"synth.reverb.level={style['reverb']:.2f}", "-o", f"synth.reverb.room-size={style['reverb_room']:.2f}",
         "-F", wav, SOUNDFONT, mid])

    # 3. frames
    stage("stage", "frames")
    blender(anim, style_file, "--out", frames, "--view", args.view, "--res", args.res, "--hands", args.hands,
            "--quality", args.quality)

    # 4. video
    stage("stage", "encode")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", os.path.join(frames, "f_%04d.png"),
         "-i", wav, "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", out])
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    print(f"Saved {out} ({data['frames']} frames, {(time.time() - clock) / 60:.1f} min)")
    stage("done", out)


if __name__ == "__main__":
    main()
