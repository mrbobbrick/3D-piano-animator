import json

import video_style as V


def test_resolve_fills_defaults_and_drops_unknown_keys():
    st = V.resolve({"glow": 3.0, "nonsense": 1, "right_color": [1, 2, 3]})
    assert st["glow"] == 3.0
    assert st["right_color"] == (1, 2, 3)
    assert "nonsense" not in st
    assert set(st) == set(V.DEFAULTS)


def test_presets_use_known_settings_within_their_ranges():
    for name, p in V.PRESETS.items():
        for k, v in p.items():
            spec = V.SPEC[k]
            if spec["kind"] == "slider":
                assert spec["min"] <= v <= spec["max"], (name, k, v)
            elif spec["kind"] == "choice":
                assert v in [o[0] for o in spec["options"]], (name, k, v)


def test_note_colours_by_mode():
    note = {"pitch": 60, "hand": "R", "velocity": 100, "finger": 3}
    st = V.resolve({"color_mode": "finger"})
    assert V.note_color(st, note, False) == st["finger3_color"]
    st = V.resolve({"color_mode": "hand", "black_shade": 0.5})
    assert V.note_color(st, dict(note, hand="L"), True) == tuple(c * 0.5 for c in st["left_color"])
    st = V.resolve({"color_mode": "rainbow"})
    assert V.note_color(st, dict(note, pitch=21), False) != V.note_color(st, dict(note, pitch=90), False)


def test_spec_is_plain_data_for_the_browser():
    spec = V.spec_json()
    json.dumps(spec)
    js_formats = ("pct", "x", "cm", "mm", "s", "int", "sgn", "cmps", "f1", "deg")      # studio/app.js FMT
    assert all("fmt" not in s or s["fmt"] in js_formats for s in spec)


def test_saved_styles_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "FOLDER", str(tmp_path))
    V.save_style("My Fire", V.resolve({"fire": 0.7}))
    (name, st), = V.list_styles()
    assert name == "My Fire" and st["fire"] == 0.7
    V.delete_style("My Fire")
    assert V.list_styles() == []


def test_extra_hands_alternate_up_and_down():
    assert V.clone_shifts(V.resolve({"extra_hands": 3})) == [12, -12, 24]
    assert V.clone_shifts(V.resolve({"extra_hands": 2, "extra_spacing": 2})) == [24, -24]
    assert V.clone_shifts(V.resolve()) == []


def test_extra_hands_notes_turn_round_the_colour_wheel():
    st = V.resolve({"extra_hue": 90})
    note = {"pitch": 60, "hand": "R", "velocity": 80, "finger": 1}
    assert V.note_color(st, dict(note, hand="R2"), False) != V.note_color(st, note, False)
    assert V.note_color(V.resolve({"extra_hue": 0}), dict(note, hand="R2"), False) == V.note_color(st, note, False)


def test_export_plays_extra_hands_in_other_octaves():
    import export3d
    import pygame
    from conftest import midi_path
    pygame.init()
    e = export3d.Export(midi_path("demo_song.mid"), [12])
    d = e.data(30, 5.0, 5.2)
    assert set(d["hands"]) == {"R", "L", "R2", "L2"}
    main = sorted(n["pitch"] for n in d["notes"] if len(n["hand"]) == 1)
    extra = sorted(n["pitch"] for n in d["notes"] if len(n["hand"]) > 1)
    assert extra == sorted(p + 12 for p in main if p + 12 <= 108)


def test_notes_carry_how_long_the_hands_hold_them():
    # what blender/scene.py draws with played_lengths (on by default): strike to release, never
    # longer than the file's note, and shorter where a hand lets go early to reach the next notes
    import os
    import export3d
    assert V.resolve()["played_lengths"] is True
    here = os.path.dirname(os.path.abspath(__file__))
    notes = export3d.Export(os.path.join(here, "..", "MIDIs", "demo_song.mid")).data(fps=5)["notes"]
    assert all(n["start"] - 1e-6 <= n["press"] < n["release"] <= n["end"] + 1e-6 for n in notes)
    assert any(n["end"] - n["release"] > 0.1 for n in notes)


def test_shared_hands_mode():
    st = V.resolve({"extra_hands": 1, "extra_mode": "share", "extra_heard": False})
    assert V.clone_shifts(st) == [] and V.shared_pairs(st) == 2 and V.extra_heard(st)
    st = V.resolve({"extra_hands": 1})
    assert V.clone_shifts(st) == [12] and V.shared_pairs(st) == 1


def test_pairs_share_music_two_hands_cannot_play():
    # four parts at once over five octaves: a bass, held chords, an arpeggio and a fast melody
    import export3d
    from midi_loader import Note
    import hand_split
    ns = []
    for bar in range(4):
        t = bar * 2.4
        ns += [Note(26, t + b * 0.6, t + b * 0.6 + 0.5, 80, 0, "R") for b in range(4)]
        ns += [Note(p, t, t + 2.35, 60, 0, "R") for p in (62, 66, 69)]
        ns += [Note(50 + (0, 4, 7, 12)[k % 4], t + k * 0.15, t + k * 0.15 + 0.13, 70, 0, "R") for k in range(16)]
        ns += [Note(86 + (0, 4, 7, 12, 7, 4)[k % 6], t + k * 0.15, t + k * 0.15 + 0.13, 90, 0, "R") for k in range(16)]
    who = hand_split.split_among(ns, 4)
    assert set(who) == {id(n) for n in ns}
    by = {i: [n for n in ns if who[id(n)] == i] for i in range(4)}
    assert all(by[i] for i in range(4))                     # every hand has a part
    # hands stay in order: each hand's notes, chord by chord, below the next hand's
    for t in sorted({n.start for n in ns}):
        tops = [max((n.pitch for n in by[i] if n.start == t), default=None) for i in range(4)]
        bots = [min((n.pitch for n in by[i] if n.start == t), default=None) for i in range(4)]
        for i in range(3):
            if tops[i] is not None and bots[i + 1] is not None:
                assert tops[i] <= bots[i + 1]
    # and nothing one hand plays at once is wider than a hand
    for i in range(4):
        for t in {n.start for n in by[i]}:
            chord = [n.pitch for n in by[i] if n.start == t]
            assert max(chord) - min(chord) <= 12


def test_shared_export_seats_each_pianist_at_their_part(tmp_path):
    import pretty_midi
    import export3d
    pm = pretty_midi.PrettyMIDI()
    ins = pretty_midi.Instrument(0)
    for k in range(8):
        for p in (36, 43, 60, 64, 67, 84, 88, 91):
            ins.notes.append(pretty_midi.Note(80, p, k * 0.5, k * 0.5 + 0.45))
    pm.instruments.append(ins)
    path = str(tmp_path / "wide.mid")
    pm.write(path)
    e = export3d.Export(path, pairs=2)
    assert set(e.hands) == {"L", "R", "L2", "R2"}
    assert e.hands["L2"].seat > e.hands["R"].seat
    assert e.hands["R"].neighbour is None                   # by default the pianists may collide
    assert not V.resolve()["hands_apart"]
    e = export3d.Export(path, pairs=2, apart=True)          # ... or keep out of each other's way
    assert e.hands["L2"].neighbour is e.hands["R"] and e.hands["R"].neighbour is e.hands["L2"]
    played = sorted(n.pitch for a in e.hands.values() for _, _, n in a.performance)
    assert played == sorted(n.pitch for n in pm.instruments[0].notes)


def test_hands_enter_one_at_a_time():
    assert V.entrances({"R": 3.0, "L": 3.0}, V.resolve()) == {}           # off by default
    st = V.resolve({"hands_enter": True, "hands_enter_gap": 0.5})
    # both hands start together: they come in one after the other, the second ready just before the music
    e = V.entrances({"R": 3.0, "L": 3.0}, st)
    (a1, b1), (a2, b2) = sorted(e.values())
    assert b2 == 3.0 - V.ENTER_READY_T and a2 >= b1 + 0.5 - 1e-9
    assert all(abs(b - a - V.ENTER_T) < 1e-9 for a, b in e.values())
    # a pianist who joins later comes in just before their first note, in turn
    e = V.entrances({"R": 3.0, "L": 3.2, "R2": 20.0, "L2": 20.0}, st)
    order = sorted(e, key=lambda h: e[h][0])
    assert order[:2] == ["R", "L"] and set(order[2:]) == {"R2", "L2"}
    assert e["L"][1] <= 3.2 - V.ENTER_READY_T and 19.0 < max(e["R2"][1], e["L2"][1]) <= 20.0


def test_opening_is_long_enough_for_every_hand_to_come_in():
    assert V.enter_lead(V.resolve()) == 3.0
    st = V.resolve({"hands_enter": True, "extra_hands": 1, "extra_mode": "share"})
    e = V.entrances({h: 10.0 for h in ("R", "L", "R2", "L2")}, st)
    assert 10.0 - V.enter_lead(st) < min(a for a, _ in e.values())


def test_black_midi_chords_load_quickly_and_share_out(tmp_path):
    # "black MIDI" (Rush E): chords of a hundred notes at once
    import time
    import hand_split
    import render_video
    from midi_loader import Note
    ns = [Note(p, k * 0.1, k * 0.1 + 0.09, 90, p % 4, "R") for k in range(40) for p in range(24, 104)]
    t = time.time()
    two = hand_split.split_hands(ns)
    assert set(two) == {id(n) for n in ns} and time.time() - t < 10
    eight = hand_split.split_among(ns, 8)                   # more notes than 8 hands can hold
    assert set(eight.values()) == set(range(8))
    # the sound can be the whole file rather than what the hands play
    assert V.resolve()["audio_source"] == "hands"
    import pretty_midi
    pm = pretty_midi.PrettyMIDI()
    ins = pretty_midi.Instrument(0)
    ins.notes = [pretty_midi.Note(80, 60 + k % 12, k * 0.5, k * 0.5 + 0.4) for k in range(20)]
    pm.instruments.append(ins)
    src, out = str(tmp_path / "in.mid"), str(tmp_path / "out.mid")
    pm.write(src)
    render_video.file_midi(src, {"t0": 2.0, "frames": 120, "fps": 30}, out)
    starts = sorted(n.start for i in pretty_midi.PrettyMIDI(out).instruments for n in i.notes)
    assert len(starts) == 8 and abs(starts[0]) < 1e-3          # the notes from 2 s to 6 s, from 0 s
