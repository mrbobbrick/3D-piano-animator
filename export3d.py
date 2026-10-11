"""
Export a song's hand animation for the 3D renderer (blender/): both hands'
skeletons sampled at a frame rate, the keyboard, and what the hands play.

    python export3d.py song.mid out.json [--fps 60] [--start S] [--end S]

Everything is in metres, in the keyboard's frame: x to the right from the
lowest key's left edge, y away from the player from the keys' front edge,
z up from the white keys' tops. Black keys stand BLACK_RISE_IN above them
(the fingertips are raised onto them, HandAnimator.black_rise_in).

Per frame and hand, 27 points (x, y, z):
    0-3    thumb: CMC, MCP, IP, tip
    4-23   fingers 2-5: metacarpal base, MCP, PIP, DIP, tip (5 points each)
    24-25  wrist: radius side, ulna side
    26     forearm end (toward the elbow)
"""

import argparse
import json
import os
import time
from dataclasses import replace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import pianist as pianists
from common import Keyboard, Performance
from hands import KEY_TRAVEL_IN, WHITE_KEY_IN, HandAnimator, pair_hands

PRESSED_DOWN = 0.4           # a key counts as pressed when its finger gets it this far down (share of its travel)
from midi_loader import LEFT, RIGHT, MidiSong, is_black_key, load_song

M_PER_IN = 0.0254
BLACK_RISE_IN = 0.5          # black key tops above the white ones
KB_WHITE_PX = 100            # pixels per white key for the solve (large, so key rounding is negligible)
LEAD_IN_T = 3.0              # seconds before the first note
TAIL_T = 2.0                 # ...and after the last release


def keyboard():
    return Keyboard(pygame.Rect(0, 0, 52 * KB_WHITE_PX, round(KB_WHITE_PX * 5.6)))


def transposed(song, shift):
    """The song moved by `shift` semitones (notes that would leave the keyboard dropped)."""
    notes = [replace(n, pitch=n.pitch + shift) for n in song.notes if 21 <= n.pitch + shift <= 108]
    s = MidiSong(notes, song.tracks, song.duration, song.bar_times, song.beat_times, song.path, song.raw_controls)
    s.title = song.title
    return s


def build(song, kb=None):
    kb = kb or keyboard()
    hands = {h: HandAnimator(song, h) for h in (RIGHT, LEFT) if any(n.hand == h for n in song.notes)}
    for a in hands.values():
        a.black_rise_in = BLACK_RISE_IN
    pair_hands(hands.values())
    return kb, hands


def flatten(pose, kb, m):
    """The 27 points of a pose, in metres."""
    st = pose["struct"]
    ch = st["chains"]
    pts = list(ch[1]) + [p for f in range(2, 6) for p in ch[f]] + list(st["wrist"]) + [st["arm_end"]]
    out = []
    for x, y, z in pts:
        out += [round((x - kb.rect.x) * m, 5), round(y * m, 5), round(z * m, 5)]
    return out


def keyboard_data(kb, m):
    keys = []
    for p, r in sorted(kb.key_rects.items()):
        black = is_black_key(p)
        # rects run from the keys' back (screen top) to their front
        keys.append({"pitch": p, "black": black,
                     "x0": round((r.left - kb.rect.x) * m, 5), "x1": round((r.right - kb.rect.x) * m, 5),
                     "length": round(r.h * m, 5)})
    return {"keys": keys, "width": round(kb.rect.w * m, 5), "white_length": round(kb.rect.h * m, 5),
            "travel": KEY_TRAVEL_IN * M_PER_IN, "black_rise": BLACK_RISE_IN * M_PER_IN,
            "white_key": WHITE_KEY_IN * M_PER_IN}


class Export:
    """A song solved once (fingering, hands), sampled as often as needed (the 3D studio's previews)."""

    def __init__(self, path, clones=()):
        """clones: semitone shifts of extra pairs of hands (video_style.clone_shifts), "R2"/"L2", ... ."""
        self.path = path
        self.song = load_song(path)
        self.kb, self.hands = build(self.song)
        for k, shift in enumerate(clones, start=2):
            _, extra = build(transposed(self.song, shift), self.kb)
            self.hands.update({f"{h}{k}": a for h, a in extra.items()})
        self.m = M_PER_IN / (self.kb.white_w / WHITE_KEY_IN)          # metres per pixel
        self.perf = Performance.from_animators(self.hands.values())
        self.first = min(it[0] for it in self.perf.items)
        self.last = max(it[1] for it in self.perf.items)

    def data(self, fps=60, start=None, end=None, verbose=False, video_start=None, video_end=None, pressed=None):
        """
        The export from song time start to end; video_start / video_end: when
        the video it's part of begins and ends (for its title and end text).
        """
        kb, m, song = self.kb, self.m, self.song
        t0 = self.first - LEAD_IN_T if start is None else start
        t1 = self.last + TAIL_T if end is None else end
        n = int(round((t1 - t0) * fps)) + 1
        frames = {h: [] for h in self.hands}
        clock = time.time()
        for i in range(n):
            t = t0 + i / fps
            for h, a in self.hands.items():
                frames[h].append(flatten(a.pose(t, kb), kb, m))
            if verbose and i % (fps * 10) == 0:
                print(f"  {t:7.1f} s  ({i}/{n} frames, {time.time() - clock:.0f} s)", flush=True)
        notes = []
        for label, a in self.hands.items():
            for press, release, note in a.performance:
                notes.append({"pitch": note.pitch, "hand": label, "velocity": note.velocity,
                              "start": round(note.start, 4), "end": round(note.end, 4),
                              "press": round(press, 4), "release": round(release, 4),
                              "finger": a.finger_for(note) or 0})
                if pressed is not None:
                    notes[-1]["pressed"] = pressed.get(id(note), True)
        notes.sort(key=lambda n: (n["press"], n["pitch"]))
        return {
            "song": song.title, "source": os.path.abspath(self.path), "fps": fps, "t0": t0, "frames": n,
            "video_t0": t0 if video_start is None else video_start,
            "video_t1": t1 if video_end is None else video_end,
            "pianist": pianists.active().name,
            "keyboard": keyboard_data(kb, m),
            "hands": {h: {"mirror": h[0] != RIGHT, "frames": f} for h, f in frames.items()},
            "notes": notes,
            "pedals": [[round(t, 4), c, v] for t, c, v in song.controls],
            "bars": [round(b, 4) for b in song.bar_times],
        }

    def pressed_map(self):
        """
        {id(note): whether its finger gets its key down} - each note's
        fingertip a moment after its strike (or halfway through it): on the
        key (within 15% of a white key) and at least PRESSED_DOWN of the
        key's travel down. Slow (a pose or two per note, ~40 ms each), so
        computed once and kept.
        """
        if getattr(self, "_pressed", None) is not None:
            return self._pressed
        kb = self.kb
        ppi = kb.white_w / WHITE_KEY_IN
        travel, rise = KEY_TRAVEL_IN * ppi, BLACK_RISE_IN * ppi
        out = {}
        for a in self.hands.values():
            for press, release, note in sorted(a.performance, key=lambda it: it[0]):
                f = a.finger_for(note)
                if not f:
                    out[id(note)] = False
                    continue
                r = kb.key_rects[note.pitch]
                top = rise if is_black_key(note.pitch) else 0.0

                def down_at(t):
                    tip = a.pose(t, kb)["struct"]["chains"][f][-1]
                    on = r.left - 0.15 * kb.white_w <= tip[0] <= r.right + 0.15 * kb.white_w
                    return on and tip[2] <= top - PRESSED_DOWN * travel
                # just after the strike, or failing that halfway through the note (as the render's
                # key motion, which takes the deepest the key gets while the note is held)
                out[id(note)] = down_at(min(press + 0.06, (press + release) / 2)) or down_at((press + release) / 2)
        self._pressed = out
        return out

    def default_start(self):
        return self.first - LEAD_IN_T

    def default_end(self):
        return self.last + TAIL_T

    def save(self, out, fps=60, start=None, end=None, verbose=False, video_start=None, video_end=None, pressed=None):
        clock = time.time()
        data = self.data(fps, start, end, verbose, video_start, video_end, pressed)
        with open(out, "w") as fh:
            json.dump(data, fh, separators=(",", ":"))
        if verbose:
            print(f"Saved {out}: {data['frames']} frames ({data['t0']:.2f} s on), {len(data['notes'])} notes, "
                  f"{time.time() - clock:.0f} s")
        return data


def export(path, out, fps=60, start=None, end=None, clones=()):
    return Export(path, clones).save(out, fps, start, end, verbose=True)


def main():
    ap = argparse.ArgumentParser(description="Export hand animation for the 3D renderer")
    ap.add_argument("midi")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--start", type=float, help="first frame's song time (default: 3 s before the first note)")
    ap.add_argument("--end", type=float, help="last frame's song time (default: 2 s after the last release)")
    ap.add_argument("--clones", default="", help="extra pairs of hands, as semitone shifts: e.g. 12,-12")
    args = ap.parse_args()
    pygame.init()
    clones = [int(c) for c in args.clones.split(",") if c.strip()]
    export(args.midi, args.out, args.fps, args.start, args.end, clones)


if __name__ == "__main__":
    main()
