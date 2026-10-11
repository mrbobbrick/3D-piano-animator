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
and "arch": per frame, how much higher than the hand solver placed them the
knuckles of fingers 2-4 are (hands.ARCH_FINGERS, metres; already in their
points), so the 3D rig can take the palm's frame from the solver's knuckles.
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
from hands import ARCH_FINGERS, KEY_TRAVEL_IN, WHITE_KEY_IN, HandAnimator, pair_hands, seat_side_by_side

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


def build(song, kb=None, seat=0):
    kb = kb or keyboard()
    hands = {h: HandAnimator(song, h) for h in (RIGHT, LEFT) if any(n.hand == h for n in song.notes)}
    for a in hands.values():
        a.black_rise_in = BLACK_RISE_IN
        a.seat = seat
    pair_hands(hands.values())
    return kb, hands


def shared(song, pairs, apart=True):
    """
    The song shared among `pairs` pairs of hands side by side (hand_split.split_among):
    [(sub-song with its notes labelled L / R, seat in semitones)], lowest pair first.
    """
    from hand_split import split_among
    who = split_among(song.notes, 2 * pairs, apart=apart)
    out = []
    for k in range(pairs):
        notes = [replace(n, hand=LEFT if who[id(n)] == 2 * k else RIGHT, finger=None)
                 for n in song.notes if who[id(n)] // 2 == k]
        s = MidiSong(notes, song.tracks, song.duration, song.bar_times, song.beat_times, song.path,
                     song.raw_controls)
        s.title = song.title
        # the pianist sits in front of the middle of what they play (usually D4)
        mid = sorted(n.pitch for n in notes)[len(notes) // 2] if notes else 62
        out.append((s, mid - 62))
    return out


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

    def __init__(self, path, clones=(), pairs=1, apart=False):
        """
        clones: semitone shifts of extra pairs of hands playing the same music
        (video_style.clone_shifts), "R2"/"L2", ... . pairs: how many pairs of
        hands share the music instead (export3d.shared), labelled the same way;
        apart: the pianists keep out of each other's way (else their hands may collide).
        """
        self.path = path
        self.lead = LEAD_IN_T                 # seconds of video before the first note
        self.song = load_song(path)
        if pairs > 1:
            self.kb, self.hands = keyboard(), {}
            seated = []
            for k, (s, seat) in enumerate(shared(self.song, pairs, apart), start=1):
                _, hs = build(s, self.kb, seat)
                seated.append(hs)
                self.hands.update({f"{h}{k if k > 1 else ''}": a for h, a in hs.items()})
            if apart:
                seat_side_by_side(seated)
        else:
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
        t0 = self.default_start() if start is None else start
        t1 = self.last + TAIL_T if end is None else end
        n = int(round((t1 - t0) * fps)) + 1
        frames = {h: [] for h in self.hands}
        arches = {h: [] for h in self.hands}
        clock = time.time()
        for i in range(n):
            t = t0 + i / fps
            for h, a in self.hands.items():
                frames[h].append(flatten(a.pose(t, kb), kb, m))
                arch = a.arch_at(t)
                arches[h].append([round(arch[f] * m, 5) for f in ARCH_FINGERS])
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
            "hands": {h: {"mirror": h[0] != RIGHT, "frames": f, "arch": arches[h]} for h, f in frames.items()},
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
        return self.first - self.lead

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


def export(path, out, fps=60, start=None, end=None, clones=(), pairs=1, lead=None, apart=False):
    e = Export(path, clones, pairs, apart)
    if lead is not None:
        e.lead = lead
    return e.save(out, fps, start, end, verbose=True)


def main():
    ap = argparse.ArgumentParser(description="Export hand animation for the 3D renderer")
    ap.add_argument("midi")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--start", type=float, help="first frame's song time (default: 3 s before the first note)")
    ap.add_argument("--end", type=float, help="last frame's song time (default: 2 s after the last release)")
    ap.add_argument("--clones", default="", help="extra pairs of hands, as semitone shifts: e.g. 12,-12")
    ap.add_argument("--pairs", type=int, default=1, help="pairs of hands sharing the music (side by side)")
    ap.add_argument("--apart", action="store_true", help="pianists sharing the music keep out of each other's way")
    ap.add_argument("--lead", type=float, help=f"seconds before the first note (default {LEAD_IN_T:g})")
    args = ap.parse_args()
    pygame.init()
    clones = [int(c) for c in args.clones.split(",") if c.strip()]
    export(args.midi, args.out, args.fps, args.start, args.end, clones, args.pairs, args.lead, args.apart)


if __name__ == "__main__":
    main()
