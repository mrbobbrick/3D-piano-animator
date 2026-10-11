"""
Build blender/assets/hands.blend once: a MakeHuman (MPFB) body with the
default rig and a natural skin, cut down to the forearms and hands.

Needs Blender with the MPFB extension and the MakeHuman system assets
(plus the skin's pack) installed; the result is self-contained (textures
packed), so rendering doesn't need MPFB.

    blender -b -P blender/make_hands.py -- [--skin NAME] [--out FILE]
"""

import argparse
import os
import sys

import bpy

KEEP_BONES = ("lowerarm01", "lowerarm02", "wrist", "metacarpal", "finger")


def mpfb(name):
    import importlib
    base = next(m for m in sys.modules if m.endswith(".mpfb") or m == "mpfb")
    return importlib.import_module(f"{base}.services.{name}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--skin", default="callharvey3d_midtoned_female")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "hands.blend"))
    args = ap.parse_args(argv)

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    HumanService = mpfb("humanservice").HumanService
    LocationService = mpfb("locationservice").LocationService
    TargetService = mpfb("targetservice").TargetService

    macro = TargetService.get_default_macro_info_dict()
    macro.update(gender=0.55, age=0.5, muscle=0.5, weight=0.45)
    body = HumanService.create_human(macro_detail_dict=macro)
    rig = HumanService.add_builtin_rig(body, "default")
    skin = os.path.join(LocationService.get_user_data("skins"), args.skin, args.skin + ".mhmat")
    HumanService.set_character_skin(skin, body, skin_type="ENHANCED_SSS")

    # bake the body shape and drop the helper geometry
    bpy.context.view_layer.objects.active = body
    body.select_set(True)
    if body.data.shape_keys:
        bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
    for m in list(body.modifiers):
        if m.type != "ARMATURE":
            body.modifiers.remove(m)

    # keep the forearms and hands: vertices weighted mostly to those bones
    groups = {g.index: g.name for g in body.vertex_groups}
    keep_idx = {i for i, n in groups.items() if n.split(".")[0].startswith(KEEP_BONES)}
    body_group = body.vertex_groups.get("body")
    drop = []
    for v in body.data.vertices:
        w = sum(g.weight for g in v.groups if g.group in keep_idx)
        in_body = body_group is None or any(g.group == body_group.index and g.weight > 0.5 for g in v.groups)
        if w < 0.5 or not in_body:
            drop.append(v.index)
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i in drop], context="VERTS")
    bm.to_mesh(body.data)
    bm.free()
    for g in list(body.vertex_groups):
        if not g.name.split(".")[0].startswith(KEEP_BONES):
            body.vertex_groups.remove(g)
    body.name = body.data.name = "Hands"
    rig.name = rig.data.name = "HandsRig"
    print(f"kept {len(body.data.vertices)} vertices; materials {[m.name for m in body.data.materials]}")

    bpy.ops.file.pack_all()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args.out, compress=True)
    print("Saved", args.out)


main()
