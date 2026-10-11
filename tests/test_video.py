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
