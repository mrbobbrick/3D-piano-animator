"""
video_style.py - How the 3D video looks: the falling notes (shape, colour,
glow, fire), the effects where they meet the keys (sparks, hit glow, light on
the hands), the scene and the finishing (bloom, vignette). Plain Python, used
by the 3D Video studio (video_studio.py) and inside Blender (blender/scene.py).

A style is a dict {setting id: value}; anything missing takes its default
(`resolve`). Bundled presets are in PRESETS; your own styles are JSON files
in the "video_styles" folder next to this file.
"""
from __future__ import annotations

import colorsys
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "video_styles")


def _pct(v): return f"{int(round(v * 100))}%"
def _x(v): return f"{v:.1f}×"
def _cm(v): return f"{v * 100:.1f} cm"
def _mm(v): return f"{v * 1000:.1f} mm"
def _s(v): return f"{v:.1f} s"
def _n(v): return f"{int(round(v))}"
def _sgn(v): return f"{v:+.1f}"
def _cmps(v): return f"{v * 100:.0f} cm/s"
def _f1(v): return f"{v:.1f}"
def _deg(v): return f"{v:+.0f}°"


# formatter -> name, for the browser studio (studio/app.js formats the same way)
FMT_NAMES = {_pct: "pct", _x: "x", _cm: "cm", _mm: "mm", _s: "s", _n: "int", _sgn: "sgn", _cmps: "cmps", _f1: "f1",
             _deg: "deg"}


GROUPS = ["Notes", "Colours", "Look", "Effects", "Line", "Keyboard", "Hands", "Camera", "Scene", "Text", "Audio",
          "Finish"]

COLOR_MODES = [("hand", "By hand"), ("fire", "Fire"), ("rainbow", "Rainbow"), ("velocity", "By loudness"),
               ("finger", "By finger"), ("single", "One colour")]

SETTINGS = [
    # ---- notes
    dict(id="layout", group="Notes", label="Notes come", kind="choice", default="floor",
         options=[("floor", "Down a runway (bird's eye)"), ("wall", "Down a wall (pianist's view)")]),
    dict(id="note_speed", group="Notes", label="Fall speed", kind="slider", min=0.05, max=0.3, default=0.11,
         fmt=_cmps, lo="Close together", hi="Spread out"),
    dict(id="note_window", group="Notes", label="Notes shown ahead", kind="slider", min=1.5, max=6.0, default=3.2,
         fmt=_s),
    dict(id="note_width", group="Notes", label="Width", kind="slider", min=0.4, max=1.0, default=0.84, fmt=_pct,
         lo="Thin", hi="Full key"),
    dict(id="note_depth", group="Notes", label="Thickness", kind="slider", min=0.002, max=0.016, default=0.007,
         fmt=_mm),
    dict(id="note_round", group="Notes", label="Rounded corners", kind="slider", min=0.0, max=1.0, default=0.25,
         fmt=_pct, lo="Square", hi="Pill"),
    dict(id="note_gap", group="Notes", label="Gap between notes", kind="slider", min=0.0, max=0.006,
         default=0.0008, fmt=_mm),
    dict(id="only_pressed", group="Notes", label="Only notes the hands actually press", kind="toggle",
         default=True, desc="Hide a note whose finger doesn't get its key down (the sound still plays)"),
    dict(id="played_lengths", group="Notes", label="Notes last as long as the key is held", kind="toggle",
         default=True, desc="Each note runs from the finger's strike to its release, not the MIDI file's times "
                            "(a hand lets go early to reach the next notes; the pedal still holds the sound)"),
    dict(id="finger_numbers", group="Notes", label="Finger numbers", kind="toggle", default=False),
    dict(id="note_outline", group="Notes", label="Outline only (neon tubes)", kind="toggle", default=False),
    dict(id="outline_width", group="Notes", label="Outline thickness", kind="slider", min=0.0003, max=0.003,
         default=0.0008, fmt=_mm),
    # ---- colours
    dict(id="color_mode", group="Colours", label="Colour notes", kind="choice", options=COLOR_MODES,
         default="hand"),
    dict(id="right_color", group="Colours", label="Right hand", kind="color", default=(116, 196, 64),
         modes=("hand",)),
    dict(id="left_color", group="Colours", label="Left hand", kind="color", default=(84, 152, 232),
         modes=("hand",)),
    dict(id="fire_right", group="Colours", label="Right hand flame", kind="color", default=(255, 140, 26),
         modes=("fire",)),
    dict(id="fire_left", group="Colours", label="Left hand flame", kind="color", default=(235, 60, 20),
         modes=("fire",)),
    dict(id="soft_color", group="Colours", label="Soft notes", kind="color", default=(60, 90, 220),
         modes=("velocity",)),
    dict(id="loud_color", group="Colours", label="Loud notes", kind="color", default=(255, 70, 70),
         modes=("velocity",)),
    dict(id="single_color", group="Colours", label="Notes", kind="color", default=(240, 200, 90),
         modes=("single",)),
    dict(id="finger1_color", group="Colours", label="Thumb", kind="color", default=(235, 80, 80), modes=("finger",)),
    dict(id="finger2_color", group="Colours", label="Index", kind="color", default=(245, 160, 60), modes=("finger",)),
    dict(id="finger3_color", group="Colours", label="Middle", kind="color", default=(235, 215, 70),
         modes=("finger",)),
    dict(id="finger4_color", group="Colours", label="Ring", kind="color", default=(100, 205, 100), modes=("finger",)),
    dict(id="finger5_color", group="Colours", label="Little", kind="color", default=(80, 150, 240),
         modes=("finger",)),
    dict(id="rainbow_span", group="Colours", label="Rainbow span", kind="slider", min=0.2, max=1.0, default=0.85,
         fmt=_pct, modes=("rainbow",)),
    dict(id="black_shade", group="Colours", label="Black-key notes darker", kind="slider", min=0.0, max=0.6,
         default=0.3, fmt=_pct),
    # ---- look
    dict(id="glow", group="Look", label="Glow", kind="slider", min=0.0, max=5.0, default=1.0, fmt=_x),
    dict(id="play_glow", group="Look", label="Glow while playing", kind="slider", min=0.0, max=10.0, default=2.5,
         fmt=_x),
    dict(id="play_whiten", group="Look", label="Whiten while playing", kind="slider", min=0.0, max=1.0,
         default=0.25, fmt=_pct),
    dict(id="flash", group="Look", label="Flash on strike", kind="slider", min=0.0, max=10.0, default=2.0, fmt=_x),
    dict(id="opacity", group="Look", label="Opacity", kind="slider", min=0.1, max=1.0, default=1.0, fmt=_pct,
         lo="Glassy", hi="Solid"),
    dict(id="rim", group="Look", label="Edge glow", kind="slider", min=0.0, max=5.0, default=0.5, fmt=_x),
    dict(id="gradient", group="Look", label="Bright at the bottom", kind="slider", min=-1.0, max=1.0, default=0.3,
         fmt=_sgn, lo="Top", hi="Bottom"),
    dict(id="gloss", group="Look", label="Gloss", kind="slider", min=0.0, max=1.0, default=0.7, fmt=_pct),
    dict(id="velocity_glow", group="Look", label="Loud notes brighter", kind="slider", min=0.0, max=1.0,
         default=0.3, fmt=_pct),
    dict(id="fire", group="Look", label="Flames on the notes", kind="slider", min=0.0, max=1.0, default=0.0,
         fmt=_pct, desc="Burning, flickering surface"),
    dict(id="fire_speed", group="Look", label="Flicker speed", kind="slider", min=0.1, max=3.0, default=1.0,
         fmt=_x),
    # ---- effects
    dict(id="sparks", group="Effects", label="Sparks per strike", kind="slider", min=0, max=80, default=0, fmt=_n,
         step=1),
    dict(id="spark_trickle", group="Effects", label="Sparks while held (per s)", kind="slider", min=0, max=80,
         default=0, fmt=_n, step=1),
    dict(id="spark_speed", group="Effects", label="Spark speed", kind="slider", min=0.05, max=0.8, default=0.25,
         fmt=_cmps),
    dict(id="spark_float", group="Effects", label="Sparks rise or fall", kind="slider", min=-1.0, max=1.0,
         default=-0.3, fmt=_sgn, lo="Fall", hi="Float up"),
    dict(id="spark_life", group="Effects", label="Spark lifetime", kind="slider", min=0.1, max=2.5, default=0.7,
         fmt=_s),
    dict(id="spark_size", group="Effects", label="Spark size", kind="slider", min=0.0003, max=0.004,
         default=0.0012, fmt=_mm),
    dict(id="spark_stretch", group="Effects", label="Spark streaks", kind="slider", min=0.0, max=2.5,
         default=1.0, fmt=_x, lo="Round", hi="Long streaks"),
    dict(id="spark_glow", group="Effects", label="Spark brightness", kind="slider", min=0.5, max=30.0, default=8.0,
         fmt=_x),
    dict(id="spark_whiten", group="Effects", label="Sparks whiter", kind="slider", min=0.0, max=1.0, default=0.4,
         fmt=_pct),
    dict(id="dust", group="Effects", label="Floating dust / embers in the air", kind="slider", min=0.0, max=3.0,
         default=0.0, fmt=_x),
    dict(id="dust_color", group="Effects", label="Dust colour", kind="color", default=(255, 200, 150)),
    dict(id="dust_size", group="Effects", label="Dust size", kind="slider", min=0.0003, max=0.003, default=0.0008,
         fmt=_mm),
    dict(id="dust_glow", group="Effects", label="Dust brightness", kind="slider", min=0.2, max=15.0, default=4.0,
         fmt=_x),
    dict(id="dust_rise", group="Effects", label="Dust drifts up", kind="slider", min=0.0, max=3.0, default=1.0,
         fmt=_x),
    dict(id="dust_life", group="Effects", label="Dust lifetime", kind="slider", min=1.0, max=12.0, default=5.0,
         fmt=_s),
    dict(id="hit_glow", group="Effects", label="Glow over pressed keys", kind="slider", min=0.0, max=12.0,
         default=6.0, fmt=_x),
    dict(id="hit_glow_height", group="Effects", label="Glow height", kind="slider", min=0.02, max=0.25,
         default=0.06, fmt=_cm),
    dict(id="hit_flames", group="Effects", label="Flames over pressed keys", kind="toggle", default=False),
    dict(id="note_lights", group="Effects", label="Notes light the hands", kind="slider", min=0.0, max=5.0,
         default=1.0, fmt=_x),
    dict(id="key_tint", group="Effects", label="Pressed keys coloured", kind="slider", min=0.0, max=1.0,
         default=1.0, fmt=_pct),
    # ---- the line between the keys and the notes
    dict(id="hit_line", group="Line", label="Glow line brightness", kind="slider", min=0.0, max=10.0, default=2.0,
         fmt=_x, desc="The line where the notes meet the keyboard (0: no line)"),
    dict(id="hit_gap", group="Line", label="Gap behind the keys", kind="slider", min=0.002, max=0.04,
         default=0.006, fmt=_cm, desc="Bird's-eye runway: how far behind the keys the notes end"),
    dict(id="hit_line_color", group="Line", label="Line colour", kind="color", default=(255, 225, 190)),
    dict(id="hit_line_width", group="Line", label="Thickness", kind="slider", min=0.0001, max=0.004,
         default=0.0004, fmt=_mm),
    dict(id="hit_line_notes", group="Line", label="Takes the note's colour where a key is down", kind="slider",
         min=0.0, max=1.0, default=0.0, fmt=_pct),
    dict(id="hit_line_flare", group="Line", label="Flares where a key is down", kind="slider", min=0.0, max=1.0,
         default=0.0, fmt=_pct),
    # ---- keyboard
    dict(id="white_key_color", group="Keyboard", label="White keys", kind="color", default=(236, 232, 222)),
    dict(id="black_key_color", group="Keyboard", label="Black keys", kind="color", default=(14, 14, 16)),
    dict(id="case_color", group="Keyboard", label="Piano case", kind="color", default=(8, 8, 10)),
    dict(id="case_gloss", group="Keyboard", label="Case gloss", kind="slider", min=0.0, max=1.0, default=0.85,
         fmt=_pct, lo="Matte", hi="Mirror"),
    dict(id="felt_strip", group="Keyboard", label="Felt strip behind the keys", kind="toggle", default=False),
    dict(id="felt_color", group="Keyboard", label="Felt colour", kind="color", default=(110, 14, 20)),
    # ---- hands
    dict(id="show_hands", group="Hands", label="Show the hands", kind="toggle", default=True),
    dict(id="hand_model", group="Hands", label="Hand model", kind="choice", default="sculpted",
         options=[("sculpted", "Sculpted (detailed)"), ("makehuman", "MakeHuman (textured)")]),
    dict(id="skin_tint", group="Hands", label="Skin tint", kind="color", default=(255, 255, 255),
         desc="Multiplies the skin's colour (white leaves it as it is)"),
    dict(id="hands_enter", group="Hands", label="Hands appear one at a time", kind="toggle", default=False,
         desc="Each hand slides in just before it first plays, in turn (hands that start together come in "
              "one after another during the opening)"),
    dict(id="hands_enter_gap", group="Hands", label="Time between hands appearing", kind="slider", min=0.2,
         max=2.0, default=0.6, fmt=_s),
    dict(id="extra_hands", group="Hands", label="Extra pairs of hands", kind="slider", min=0, max=3, default=0,
         fmt=_n, step=1, desc="More pianists at the keyboard: copies of the hands, or sharing the music (below)"),
    dict(id="extra_mode", group="Hands", label="Extra hands", kind="choice", default="copies",
         options=[("copies", "Copy the music in other octaves (just for fun)"),
                  ("share", "Share the music side by side (play what two hands can't)")],
         desc="Share: the music is divided among all the pairs, each pianist taking a register, so passages "
              "too wide or too fast for two hands are played as written"),
    dict(id="hands_apart", group="Hands", label="Pianists keep out of each other's way", kind="toggle",
         default=False, desc="When sharing the music. Off: neighbouring pianists' hands may reach into each "
                             "other's keys and collide"),
    dict(id="extra_spacing", group="Hands", label="Octaves between the pairs", kind="slider", min=1, max=3,
         default=1, fmt=_n, step=1),
    dict(id="extra_heard", group="Hands", label="Hear the extra hands", kind="toggle", default=True),
    dict(id="extra_hue", group="Hands", label="Extra hands' notes: colour shift", kind="slider", min=0.0,
         max=180.0, default=45.0, fmt=_deg),
    dict(id="skin_brightness", group="Hands", label="Skin brightness", kind="slider", min=0.4, max=1.6,
         default=1.0, fmt=_pct),
    # ---- camera
    dict(id="cam_follow", group="Camera", label="Camera follows the hands", kind="toggle", default=False,
         desc="Off: the camera stays still, centred on the keyboard"),
    dict(id="cam_follow_speed", group="Camera", label="Follow smoothness", kind="slider", min=0.5, max=6.0,
         default=2.5, fmt=_s, desc="How long the camera averages the hands' position over"),
    dict(id="cam_intro", group="Camera", label="Opening move", kind="choice", default="none",
         options=[("none", "None"), ("fly", "Fly down"), ("zoom", "Zoom in"), ("swing", "Swing round")]),
    dict(id="cam_intro_time", group="Camera", label="Opening move length", kind="slider", min=1.0, max=8.0,
         default=3.0, fmt=_s),
    dict(id="cam_drift", group="Camera", label="Gentle drift", kind="slider", min=0.0, max=0.06, default=0.0,
         fmt=_cm, desc="A slow floating sway"),
    dict(id="cam_distance", group="Camera", label="Distance", kind="slider", min=0.6, max=1.6, default=1.0,
         fmt=_x, lo="Closer", hi="Further"),
    dict(id="cam_zoom", group="Camera", label="Zoom", kind="slider", min=0.6, max=1.8, default=1.0, fmt=_x,
         lo="Wide", hi="Tight"),
    dict(id="cam_tilt", group="Camera", label="Tilt", kind="slider", min=-25.0, max=35.0, default=0.0, fmt=_deg,
         lo="Steeper", hi="Flatter"),
    dict(id="cam_turn", group="Camera", label="Turn", kind="slider", min=-35.0, max=35.0, default=0.0, fmt=_deg,
         lo="From the left", hi="From the right"),
    dict(id="cam_shift", group="Camera", label="Slide along the keyboard", kind="slider", min=-0.5, max=0.5,
         default=0.0, fmt=_cm),
    # ---- scene
    dict(id="wall_color", group="Scene", label="Wall behind the notes", kind="color", default=(10, 10, 14)),
    dict(id="world_color", group="Scene", label="Surroundings", kind="color", default=(24, 24, 30)),
    dict(id="ambient", group="Scene", label="Ambient light", kind="slider", min=0.0, max=1.0, default=0.15,
         fmt=_pct),
    dict(id="light_level", group="Scene", label="Lights", kind="slider", min=0.0, max=3.0, default=1.0, fmt=_x),
    dict(id="lane_lines", group="Scene", label="Lane lines (C and F)", kind="slider", min=0.0, max=1.0,
         default=0.15, fmt=_pct),
    dict(id="bar_lines", group="Scene", label="Bar lines", kind="slider", min=0.0, max=1.0, default=0.0, fmt=_pct),
    dict(id="runway_gloss", group="Scene", label="Glossy floor under the notes", kind="slider", min=0.0,
         max=1.0, default=0.0, fmt=_pct, desc="Reflects the notes (needs Balanced quality or above)"),
    dict(id="haze", group="Scene", label="Haze", kind="slider", min=0.0, max=1.0, default=0.0, fmt=_pct),
    # ---- text
    dict(id="title_text", group="Text", label="Title at the start", kind="text", default="",
         desc="Shown over the opening (empty: no title)"),
    dict(id="subtitle_text", group="Text", label="Second line", kind="text", default=""),
    dict(id="title_time", group="Text", label="Title shown for", kind="slider", min=1.0, max=12.0, default=4.0,
         fmt=_s),
    dict(id="title_color", group="Text", label="Title colour", kind="color", default=(255, 236, 210)),
    dict(id="title_glow", group="Text", label="Title glow", kind="slider", min=0.0, max=6.0, default=1.5, fmt=_x),
    dict(id="title_size", group="Text", label="Title size", kind="slider", min=0.4, max=2.5, default=1.0, fmt=_x),
    dict(id="title_height", group="Text", label="Title height on screen", kind="slider", min=-0.4, max=0.4,
         default=0.18, fmt=_sgn, lo="Lower", hi="Higher"),
    dict(id="end_text", group="Text", label="Text at the end", kind="text", default="",
         desc="Shown over the last seconds (\\n for a new line)"),
    dict(id="end_time", group="Text", label="End text shown for", kind="slider", min=1.0, max=12.0, default=5.0,
         fmt=_s),
    dict(id="end_height", group="Text", label="End text height on screen", kind="slider", min=-0.4, max=0.4,
         default=0.1, fmt=_sgn, lo="Lower", hi="Higher"),
    # ---- audio
    dict(id="audio_source", group="Audio", label="Sound", kind="choice", default="hands",
         options=[("hands", "What the hands play"), ("file", "Every note in the file")],
         desc="Every note: for music no hands could play (\"black MIDI\", Rush E), the whole piece is heard "
              "while the hands play what they can"),
    dict(id="audio_gain", group="Audio", label="Volume", kind="slider", min=0.2, max=1.5, default=0.6, fmt=_x),
    dict(id="reverb", group="Audio", label="Reverb", kind="slider", min=0.0, max=1.0, default=0.4, fmt=_pct),
    dict(id="reverb_room", group="Audio", label="Room size", kind="slider", min=0.0, max=1.0, default=0.5,
         fmt=_pct, lo="Small room", hi="Hall"),
    # ---- finish
    dict(id="bloom", group="Finish", label="Bloom", kind="slider", min=0.0, max=2.0, default=0.5, fmt=_x),
    dict(id="bloom_size", group="Finish", label="Bloom size", kind="slider", min=0.1, max=1.0, default=0.6,
         fmt=_pct),
    dict(id="bloom_threshold", group="Finish", label="Bloom from brightness", kind="slider", min=0.2, max=3.0,
         default=1.2, fmt=_f1),
    dict(id="vignette", group="Finish", label="Vignette", kind="slider", min=0.0, max=1.0, default=0.2, fmt=_pct),
    dict(id="exposure", group="Finish", label="Exposure", kind="slider", min=-2.0, max=2.0, default=0.0, fmt=_sgn),
    dict(id="contrast", group="Finish", label="Contrast", kind="choice", default="Medium High Contrast",
         options=[("Base Contrast", "Low"), ("Medium High Contrast", "Medium"), ("High Contrast", "High"),
                  ("Very High Contrast", "Very high")]),
]
SPEC = {s["id"]: s for s in SETTINGS}
DEFAULTS = {s["id"]: s["default"] for s in SETTINGS}

PRESETS = {
    "Classic": dict(layout="wall"),
    "Embers": dict(
        color_mode="fire", fire=1.0, fire_speed=1.0, glow=1.2, play_glow=1.6, play_whiten=0.1, flash=2.0,
        rim=0.8, gradient=0.6, gloss=0.1, note_round=0.35, black_shade=0.15, velocity_glow=0.8,
        sparks=12, spark_trickle=6, spark_speed=0.22, spark_float=0.7, spark_life=1.4, spark_size=0.0011,
        spark_glow=8.0, spark_whiten=0.3, hit_glow=10.0, hit_glow_height=0.13, hit_flames=True,
        note_lights=2.2, key_tint=0.8, wall_color=(5, 4, 3), world_color=(4, 3, 3), ambient=0.02,
        light_level=0.08, note_window=5.0, hit_line=3.0, hit_line_color=(255, 140, 50),
        title_color=(255, 190, 120), reverb=0.5, reverb_room=0.7, dust=0.8, dust_color=(255, 150, 60),
        dust_rise=1.6, dust_glow=6.0,
        lane_lines=0.0, bloom=0.95, bloom_size=0.7, bloom_threshold=0.9, vignette=0.35, haze=0.0),
    "Neon": dict(
        right_color=(60, 230, 255), left_color=(255, 60, 200), glow=2.2, play_glow=6.0, play_whiten=0.4,
        flash=4.0, rim=2.5, gloss=0.9, opacity=0.85, note_round=0.5, sparks=16, spark_speed=0.3,
        spark_float=-0.2, spark_life=0.6, spark_glow=12.0, hit_glow=9.0, hit_glow_height=0.12, note_lights=2.0,
        wall_color=(6, 6, 12), world_color=(8, 8, 18), lane_lines=0.25, bar_lines=0.2, bloom=1.2,
        hit_line=4.0, hit_line_color=(200, 120, 255), title_color=(190, 230, 255), light_level=0.15,
        ambient=0.03, note_outline=True, outline_width=0.0009,
        bloom_threshold=0.8, vignette=0.3, haze=0.15),
    "Glass": dict(
        right_color=(150, 220, 255), left_color=(200, 170, 255), opacity=0.4, rim=2.5, gloss=1.0, glow=0.4,
        play_glow=4.0, play_whiten=0.5, flash=3.0, gradient=0.0, note_round=0.6, note_depth=0.01,
        hit_glow=5.0, note_lights=1.2, bloom=0.7, vignette=0.2, hit_line_color=(210, 235, 255)),
    "Rainbow": dict(color_mode="rainbow", glow=1.5, play_glow=4.0, rim=1.0, note_round=0.45, sparks=14,
                    spark_life=0.6, spark_glow=10.0, hit_glow=7.0, note_lights=1.5, bloom=0.9, lane_lines=0.1),
    "Minimal": dict(glow=0.2, play_glow=1.0, play_whiten=0.15, flash=0.0, rim=0.0, gradient=0.0, gloss=0.3,
                    note_round=0.6, hit_glow=2.0, note_lights=0.0, lane_lines=0.0, bloom=0.15, vignette=0.0,
                    hit_line=0.8),
}


def spec_json():
    """SETTINGS as plain data (formatters by name), for the browser studio."""
    out = []
    for s in SETTINGS:
        d = {k: v for k, v in s.items() if k != "fmt"}
        if "fmt" in s:
            d["fmt"] = FMT_NAMES[s["fmt"]]
        out.append(d)
    return out


def resolve(style=None):
    """A complete style: defaults overlaid with `style` (unknown keys dropped, colours as tuples)."""
    out = dict(DEFAULTS)
    for k, v in (style or {}).items():
        if k in SPEC:
            out[k] = tuple(v) if SPEC[k]["kind"] == "color" else v
    return out


def preset(name):
    return resolve(PRESETS.get(name, {}))


def to_linear(c, k=1.0):
    """sRGB 0..255 -> linear RGBA for Blender."""
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (lin(c[0]) * k, lin(c[1]) * k, lin(c[2]) * k, 1.0)


def clone_shifts(style):
    """Semitone shifts of the extra pairs of hands: +1, -1, +2, -2 ... times the spacing in octaves."""
    if style["extra_mode"] == "share":
        return []
    out = []
    for k in range(1, int(style["extra_hands"]) + 1):
        octaves = (k + 1) // 2 * int(style["extra_spacing"])
        out.append(12 * octaves * (1 if k % 2 else -1))
    return out


ENTER_T = 0.8          # s a hand takes to slide in (hands_enter)
ENTER_READY_T = 0.3    # ...finishing this long before its first note


def entrances(firsts, style):
    """
    {hand: (start, end)} song times over which each hand slides in, with
    hands_enter on (else {}): firsts = {hand: its first strike}. Hands come in
    in the order they first play, each done ENTER_READY_T before its first
    note, and the next starting at least hands_enter_gap after it is done
    (hands that start together come in one by one ahead of the music).
    """
    if not style["hands_enter"] or not firsts:
        return {}
    order = sorted(firsts, key=lambda h: (firsts[h], h))
    ends = {}
    nxt = None                     # when the next hand starts sliding in
    for h in reversed(order):
        e = firsts[h] - ENTER_READY_T
        if nxt is not None:
            e = min(e, nxt - style["hands_enter_gap"])
        ends[h] = e
        nxt = e - ENTER_T
    return {h: (ends[h] - ENTER_T, ends[h]) for h in order}


def enter_lead(style, lead=3.0):
    """Seconds of video before the first note: long enough for every hand to come in (hands_enter)."""
    if not style["hands_enter"]:
        return lead
    hands = 2 * (shared_pairs(style) + len(clone_shifts(style)))
    return max(lead, ENTER_READY_T + hands * ENTER_T + (hands - 1) * style["hands_enter_gap"] + 0.5)


def shared_pairs(style):
    """How many pairs of hands share the music (export3d.shared): 1 unless extra_mode is "share"."""
    return 1 + int(style["extra_hands"]) if style["extra_mode"] == "share" else 1


def extra_heard(style):
    """Whether the extra pairs are heard: always when they share the music (it is theirs to play)."""
    return style["extra_mode"] == "share" or style["extra_heard"]


def note_color(style, note, black):
    """
    sRGB colour (0..255) of a note: {'pitch', 'hand', 'velocity', 'finger'}
    as in the export. Hands are "R"/"L", extra pairs "R2", "L2", ... (their
    colours turned round the hue circle by extra_hue per pair).
    """
    c = _base_note_color(style, dict(note, hand=note["hand"][0]), black)
    pair = int(note["hand"][1:] or 1) - 1
    if pair and style["extra_hue"]:
        h, s, v = colorsys.rgb_to_hsv(*(x / 255.0 for x in c))
        r, g, b = colorsys.hsv_to_rgb((h + pair * style["extra_hue"] / 360.0) % 1.0, s, v)
        c = (r * 255, g * 255, b * 255)
    return c


def _base_note_color(style, note, black):
    mode = style["color_mode"]
    if mode == "fire":
        c = style["fire_right" if note["hand"] == "R" else "fire_left"]
    elif mode == "rainbow":
        span = style["rainbow_span"]
        h = (note["pitch"] - 21) / 87.0 * span
        r, g, b = colorsys.hsv_to_rgb(h, 0.75, 1.0)
        c = (r * 255, g * 255, b * 255)
    elif mode == "velocity":
        f = min(1.0, max(0.0, (note["velocity"] - 30) / 90.0))
        a, b = style["soft_color"], style["loud_color"]
        c = tuple(a[i] + (b[i] - a[i]) * f for i in range(3))
    elif mode == "finger" and note.get("finger"):
        c = style[f"finger{note['finger']}_color"]
    elif mode == "single" or mode == "finger":
        c = style["single_color"]
    else:
        c = style["right_color" if note["hand"] == "R" else "left_color"]
    k = 1.0 - style["black_shade"] if black else 1.0
    return tuple(min(255.0, x * k) for x in c)


# ----- your own styles ---------------------------------------------------------
def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "style"


def list_styles():
    """[(name, style dict)] saved in FOLDER."""
    out = []
    if os.path.isdir(FOLDER):
        for fn in sorted(os.listdir(FOLDER)):
            if fn.endswith(".json") and fn != "settings.json":
                try:
                    with open(os.path.join(FOLDER, fn), encoding="utf-8") as fh:
                        d = json.load(fh)
                    out.append((d.get("name", fn[:-5]), resolve(d.get("style"))))
                except (OSError, ValueError):
                    pass
    return out


def save_style(name, style):
    os.makedirs(FOLDER, exist_ok=True)
    path = os.path.join(FOLDER, _slug(name) + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"name": name, "style": {k: (list(v) if isinstance(v, tuple) else v)
                                           for k, v in resolve(style).items()}}, fh, indent=1)
    return path


def delete_style(name):
    path = os.path.join(FOLDER, _slug(name) + ".json")
    if os.path.exists(path):
        os.remove(path)


def dump(style, path):
    """Write a resolved style as JSON (for the renderer)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({k: (list(v) if isinstance(v, tuple) else v) for k, v in resolve(style).items()}, fh)
