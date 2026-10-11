"""
Build and render the 3D scene for an export3d.py file.

    blender -b --factory-startup -P blender/render3d.py -- song.json --out frames/ [options]

Options:
    --style FILE           a video_style JSON (default: the Classic look)
    --hands stick|rig      stick figures (check) or the skinned hands (hands_rig.py)
    --view auto|bird|pianist|side|top|close   (auto: bird's eye for a flat runway of notes)
    --quality max|high|balanced|fast|preview|flat   (default balanced; see QUALITY)
    --frames A-B           1-based frame range (default: all)
    --res WxH              (default 1920x1080)
    --still FILE           render only the last frame, to this PNG
    --blend FILE           also save the scene as a .blend
    --no-render            build (and save) only
"""

import argparse
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))           # video_style.py

import scene as sc_mod  # noqa: E402
import video_style  # noqa: E402

TIP = {1: 3, 2: 8, 3: 13, 4: 18, 5: 23}
# quality -> (samples, ray tracing, motion blur, resolution %)
QUALITY = {
    "max": (64, True, True, 100),
    "high": (48, True, True, 100),
    "balanced": (24, True, False, 100),
    "fast": (16, False, False, 100),
    "preview": (12, False, False, 50),
    "flat": (1, False, False, 100),          # Workbench: plain colours, no lighting (flat.py)
}


def parse(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="render3d")
    ap.add_argument("json")
    ap.add_argument("--out", default="frames")
    ap.add_argument("--style")
    ap.add_argument("--hands", default="rig", choices=("stick", "rig"))
    ap.add_argument("--view", default="auto", choices=("auto", "bird", "pianist", "side", "top", "close"))
    ap.add_argument("--cam", help="fixed camera for checks: x,y,z,look_x,look_y,look_z,lens (keyboard frame, x from its centre)")
    ap.add_argument("--quality", default="balanced", choices=tuple(QUALITY))
    ap.add_argument("--preview", action="store_true", help="same as --quality preview")
    ap.add_argument("--frames")
    ap.add_argument("--res", default="1920x1080")
    ap.add_argument("--still")
    ap.add_argument("--blend")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(argv)
    if args.preview:
        args.quality = "preview"
    return args


def clear():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.actions, bpy.data.armatures):
        for item in list(coll):
            coll.remove(item)


def setup_render(args, n, st):
    s = bpy.context.scene
    s.render.engine = "BLENDER_EEVEE"
    w, h = (int(v) for v in args.res.split("x"))
    samples, rt, blur, pct = QUALITY[args.quality]
    s.render.resolution_x, s.render.resolution_y = w, h
    s.render.resolution_percentage = pct
    s.render.fps = args.fps
    s.frame_start, s.frame_end = 1, n
    if args.frames:
        a, b = args.frames.split("-")
        s.frame_start, s.frame_end = int(a), int(b)
    ee = s.eevee
    ee.taa_render_samples = samples
    ee.use_raytracing = rt
    ee.use_shadows = True
    if rt:
        ee.use_fast_gi = True                 # glowing notes light what's near them
    ee.shadow_resolution_scale = 1.0
    if st["haze"] > 0:
        ee.volumetric_end = 3.0
        ee.volumetric_tile_size = "4"
    s.render.use_motion_blur = blur
    s.render.motion_blur_shutter = 0.5
    s.view_settings.view_transform = "AgX"
    s.view_settings.look = "AgX - " + st["contrast"]
    s.view_settings.exposure = st["exposure"]
    s.render.use_overwrite = False            # an interrupted render resumes
    s.render.image_settings.file_format = "PNG"
    s.render.image_settings.color_mode = "RGB"
    s.render.filepath = os.path.join(os.path.abspath(args.out), "f_")
    setup_compositor(s, st)


def setup_compositor(s, st):
    """Bloom on the glowing notes and a vignette (compositor)."""
    try:
        tree = bpy.data.node_groups.new("Composite", "CompositorNodeTree")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        N, L = tree.nodes, tree.links
        img = N.new("CompositorNodeRLayers").outputs["Image"]
        if st["bloom"] > 0:
            g = N.new("CompositorNodeGlare")
            g.inputs["Type"].default_value = "Bloom"
            g.inputs["Threshold"].default_value = st["bloom_threshold"]
            g.inputs["Strength"].default_value = st["bloom"]
            g.inputs["Size"].default_value = st["bloom_size"]
            L.new(img, g.inputs["Image"])
            img = g.outputs["Image"]
        if st["vignette"] > 0:
            ell = N.new("CompositorNodeEllipseMask")
            ell.inputs["Size"].default_value = (0.95, 0.95)
            blur = N.new("CompositorNodeBlur")
            blur.inputs["Size"].default_value = (300, 300)
            L.new(ell.outputs["Mask"], blur.inputs["Image"])
            fac = N.new("ShaderNodeMapRange")
            L.new(blur.outputs["Image"], fac.inputs["Value"])
            fac.inputs["To Min"].default_value = 1.0 - st["vignette"]
            mix = N.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            L.new(img, mix.inputs["A"])
            L.new(fac.outputs["Result"], mix.inputs["B"])
            img = mix.outputs["Result"]
        out = N.new("NodeGroupOutput")
        L.new(img, out.inputs[0])
        s.compositing_node_group = tree
        s.render.compositor_device = "GPU"        # on the CPU this took ~0.5 s a frame at 1080p
    except Exception as e:                       # the render still works without it
        print("compositor skipped:", e)


def tint_skin(mesh, st):
    """Multiply the hands' skin colour by the style's tint and brightness (inside MPFB's skin node group)."""
    k = st["skin_brightness"]
    tint = [c / 255.0 * k for c in st["skin_tint"]]
    if all(abs(c - 1.0) < 1e-3 for c in tint):
        return
    trees = {s.material.node_tree for s in mesh.material_slots if s.material and s.material.node_tree}
    trees |= {nd.node_tree for t in list(trees) for nd in t.nodes if nd.bl_idname == "ShaderNodeGroup" and nd.node_tree}
    for t in trees:
        for nd in list(t.nodes):
            if nd.bl_idname != "ShaderNodeBsdfPrincipled":
                continue
            sock = nd.inputs["Base Color"]
            mix = t.nodes.new("ShaderNodeMix")
            mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs["B"].default_value = (*tint, 1.0)
            if sock.is_linked:
                t.links.new(sock.links[0].from_socket, mix.inputs["A"])
            else:
                mix.inputs["A"].default_value = sock.default_value
            t.links.new(mix.outputs["Result"], sock)
    for slot in mesh.material_slots:
        if slot.material:
            c = slot.material.diffuse_color
            slot.material.diffuse_color = (c[0] * tint[0], c[1] * tint[1], c[2] * tint[2], 1.0)


def run(argv=None):
    """Build the scene for an export (argv as on the command line) and render it."""
    args = parse(argv)
    with open(args.json) as fh:
        data = json.load(fh)
    args.fps = data["fps"]
    st = video_style.resolve()
    if args.style:
        with open(args.style) as fh:
            st = video_style.resolve(json.load(fh))
    if args.view == "auto":
        args.view = "bird" if st["layout"] == "floor" else "pianist"
    clear()
    sc = sc_mod.Scene(data, st)
    tips = {h: {f: [fr[3 * j:3 * j + 3] for fr in hd["frames"]] for f, j in TIP.items()}
            for h, hd in data["hands"].items()}
    sc.build_keyboard()
    sc.animate_keys(tips)
    sc.build_notes()
    sc.build_hit_line()
    sc.build_sparks()
    sc.build_dust()
    sc.build_note_lights()
    sc.build_lights()
    hands = list(data["hands"].values())
    if args.view == "close":
        hands = [data["hands"].get("R", hands[0])]
    centres = [sum((hd["frames"][i][72] + hd["frames"][i][75]) / 2 for hd in hands) / len(hands)
               for i in range(data["frames"])]
    w, h = (int(v) for v in args.res.split("x"))
    cam = sc.build_camera(centres, args.view, w / h)
    if args.cam:
        from mathutils import Vector
        v = [float(c) for c in args.cam.split(",")]
        cam.animation_data_clear()
        w2 = data["keyboard"]["width"] / 2
        cam.location = (v[0] + w2, v[1], v[2])
        cam.rotation_quaternion = (Vector(v[3:6]) - Vector(v[0:3])).to_track_quat("-Z", "Y")
        cam.data.lens = v[6] if len(v) > 6 else 50
        cam.data.dof.use_dof = False
    sc.build_title(cam)
    if args.hands == "stick":
        import hands_stick
        hands_stick.build(sc, data)
    else:
        import hands_rig
        for mesh in hands_rig.build(sc, data):
            mesh.hide_render = not st["show_hands"]
            tint_skin(mesh, st)
    setup_render(args, data["frames"], st)
    if args.quality == "flat":
        import flat
        flat.apply(sc)
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))
    if args.still:
        s = bpy.context.scene
        s.frame_set(data["frames"])
        s.render.filepath = os.path.abspath(args.still)
        bpy.ops.render.render(write_still=True)
    elif not args.no_render:
        os.makedirs(args.out, exist_ok=True)
        bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    run()
