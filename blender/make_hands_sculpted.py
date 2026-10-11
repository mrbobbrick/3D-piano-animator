"""
Build blender/assets/hands_sculpted.blend: the forearms and hands of the
"realistic male" body from Blender Studio's Human Base Meshes bundle (CC0,
sculpted detail on a multires modifier), rigged with the same bone names as
the MakeHuman asset (hands_rig.py drives either), with a procedural skin.

    blender -b --factory-startup --disable-autoexec -P blender/make_hands_sculpted.py -- BUNDLE.blend [--out FILE]

The mesh's face sets split each finger into proximal / middle / distal
segments and a nail, plus the palm and the forearm: the joints are the
borders between them, except the knuckles, which sit inside the palm behind
the web (KNUCKLE_BACK). The fingers' weights come from their joints
(finger_weights). The wrist end of the metacarpals and the thumb's root
come from the MakeHuman rig, fitted onto this hand (a similarity transform
on the knuckles, fingertips and wrist).
"""

import argparse
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
MPFB_ASSET = os.path.join(HERE, "assets", "hands.blend")
BODY = "GEO-body_male_realistic"
EXTEND = 0.22                      # m the forearm is carried on past the cut at the elbow

# face sets of the left arm (bundle v1.4.1); the right arm's are found by mirroring
LEFT = {
    "forearm": 12, "palm": 9,
    1: (80, 81, 82, 83),           # thumb: metacarpal, proximal, distal, nail
    2: (76, 77, 78, 79),           # index: proximal, middle, distal, nail
    3: (72, 73, 74, 75),
    4: (68, 69, 70, 71),
    5: (64, 65, 66, 67),
}
# How far the knuckle joints (the metacarpal heads, under the bumps on the back of the hand) sit
# behind the border between the palm's and the finger's skin regions, which is at the web: measured
# on this sculpt's dorsal profile. A joint at the web bent the fingers in the wrong place and left
# their first bone ~40% short of the animation's, so it was stretched.
KNUCKLE_BACK = {2: 0.011, 3: 0.013, 4: 0.013, 5: 0.011}
# half-widths (m) of the blend between a joint's two bones: firm segments, the bend at the joint;
# wider at the knuckles, where the skin of the back of the hand and the web follow
BLEND = {"mcp": 0.009, "pip": 0.004, "dip": 0.003, "thumb_mcp": 0.006, "thumb_ip": 0.0035}
SHARE_SIGMA = 0.007                # m: how far round a knuckle a palm vertex counts as that finger's
FINGER_RELAX = 0.3                 # Corrective Smooth on the fingers, against 1 on the palm and arm
SKIN_SRGB = (190, 132, 104)        # medium tone
NAIL_SRGB = (226, 176, 164)


def lin(c):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (f(c[0]), f(c[1]), f(c[2]), 1.0)


def face_sets(me):
    vals = [0] * len(me.polygons)
    me.attributes[".sculpt_face_set"].data.foreach_get("value", vals)
    return vals


def mirror_ids(me, fs):
    """{left face set: right face set} by mirrored centroids."""
    cents = {}
    for p, k in zip(me.polygons, fs):
        c = cents.setdefault(k, [Vector(), 0])
        c[0] += p.center
        c[1] += 1
    cents = {k: v[0] / v[1] for k, v in cents.items()}
    left = [LEFT["forearm"], LEFT["palm"]] + [i for f in range(1, 6) for i in LEFT[f]]
    out = {}
    for k in left:
        m = cents[k].copy()
        m.x = -m.x
        out[k] = min((r for r in cents if cents[r].x < 0), key=lambda r: (cents[r] - m).length)
    return out


def side_map(mirror, side):
    if side == "L":
        return LEFT
    m = {"forearm": mirror[LEFT["forearm"]], "palm": mirror[LEFT["palm"]]}
    for f in range(1, 6):
        m[f] = tuple(mirror[i] for i in LEFT[f])
    return m


def border(bm, fs_layer, a, b):
    """Centroid of the vertices shared by face sets a and b."""
    vs = [v for v in bm.verts if {f[fs_layer] for f in v.link_faces} >= {a, b}]
    if not vs:
        raise RuntimeError(f"face sets {a} and {b} don't touch")
    return sum((v.co for v in vs), Vector()) / len(vs)


def region(bm, fs_layer, ids):
    return [v for v in bm.verts if any(f[fs_layer] in ids for f in v.link_faces)]


def tip_of(bm, fs_layer, ids, base, toward):
    """The fingertip's end on the finger's axis: the distal segment's farthest reach, 3 mm in."""
    axis = (toward - base).normalized()
    vs = region(bm, fs_layer, ids)
    c = sum((v.co for v in vs), Vector()) / len(vs)
    reach = max((v.co - c).dot(axis) for v in vs)
    return c + axis * (reach - 0.003)


def knuckle(bm, fs_layer, ids, web, pip, back, across):
    """
    The knuckle joint: `back` behind the web on the finger's axis, halfway through the hand's
    thickness there (the skin within 6 mm of the axis, sideways). `across`: the knuckle line.
    """
    axis = (pip - web).normalized()
    side = (across - axis * across.dot(axis)).normalized()
    up = axis.cross(side)
    at = web - axis * back
    hs = []
    for v in region(bm, fs_layer, ids):
        d = v.co - at
        if abs(d.dot(axis)) < 0.003 and abs(d.dot(side)) < 0.006:
            hs.append(d.dot(up))
    return at + up * ((max(hs) + min(hs)) / 2)


def similarity(src, dst):
    """Umeyama: scale s, rotation R, translation t with dst ~ s R src + t."""
    A, B = np.array(src), np.array(dst)
    ma, mb = A.mean(0), B.mean(0)
    A0, B0 = A - ma, B - mb
    U, S, Vt = np.linalg.svd(B0.T @ A0 / len(A))
    D = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = (S * np.diag(D)).sum() / A0.var(0).sum()
    t = mb - s * R @ ma
    return s, Matrix(R.tolist()), Vector(t.tolist())


def mpfb_rest():
    """The MakeHuman rig's rest bones {name: (head, tail, z axis)} (armature space)."""
    with bpy.data.libraries.load(MPFB_ASSET, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n == "HandsRig"]
    rig = dst.objects[0]
    out = {b.name: (b.head_local.copy(), b.tail_local.copy(), b.matrix_local.to_3x3().col[2].copy())
           for b in rig.data.bones}
    bpy.data.objects.remove(rig)
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--out", default=os.path.join(HERE, "assets", "hands_sculpted.blend"))
    args = ap.parse_args(argv)

    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    ref = mpfb_rest()
    with bpy.data.libraries.load(args.bundle, link=False) as (src, dst):
        dst.objects = [BODY]
    body = dst.objects[0]
    bpy.context.scene.collection.objects.link(body)
    body.location = (0, 0, 0)
    me = body.data
    fs = face_sets(me)
    mirror = mirror_ids(me, fs)
    sides = {s: side_map(mirror, s) for s in ("L", "R")}

    # keep the forearms and hands
    keep = set()
    for m in sides.values():
        keep |= {m["forearm"], m["palm"]} | {i for f in range(1, 6) for i in m[f]}
    bm = bmesh.new()
    bm.from_mesh(me)
    fs_layer = bm.faces.layers.int.get(".sculpt_face_set")
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f[fs_layer] not in keep], context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.verts.ensure_lookup_table()

    bones = {}
    webs = {}
    for side, m in sides.items():
        sx = "." + side
        palm, fore = m["palm"], m["forearm"]
        wrist = border(bm, fs_layer, fore, palm)
        # the elbow: the cut's middle (the forearm's open edge)
        cut = [v for v in region(bm, fs_layer, {fore}) if v.is_boundary and not any(
            f[fs_layer] == palm for f in v.link_faces)]
        elbow = sum((v.co for v in cut), Vector()) / len(cut)
        joints = {}
        across = border(bm, fs_layer, palm, m[2][0]) - border(bm, fs_layer, palm, m[5][0])
        for f in range(1, 6):
            a, b, c, nail = m[f]
            if f == 1:
                cmc = None                         # placed with the fitted rig below (near the wrist)
                mcp = border(bm, fs_layer, a, b)
                ip = border(bm, fs_layer, b, c)
                joints[1] = [cmc, mcp, ip, tip_of(bm, fs_layer, {c, nail}, mcp, ip)]
            else:
                web = border(bm, fs_layer, palm, a)
                pip = border(bm, fs_layer, a, b)
                dip = border(bm, fs_layer, b, c)
                mcp = knuckle(bm, fs_layer, {palm, a}, web, pip, KNUCKLE_BACK[f], across)
                joints[f] = [mcp, pip, dip, tip_of(bm, fs_layer, {c, nail}, pip, dip)]
                webs[side, f] = web
        # fit the MakeHuman hand onto this one (webs, fingertips, wrist, thumb: the correspondences
        # the first version was tuned on)
        src, dst_pts = [], []
        for f in range(2, 6):
            src += [ref[f"finger{f}-1{sx}"][0], ref[f"finger{f}-3{sx}"][1]]
            dst_pts += [webs[side, f], joints[f][3]]
        src += [ref[f"wrist{sx}"][0], ref[f"finger1-2{sx}"][0], ref[f"finger1-3{sx}"][1]]
        dst_pts += [wrist, joints[1][1], joints[1][3]]
        s, R, t = similarity([tuple(v) for v in src], [tuple(v) for v in dst_pts])
        fit = lambda v: R @ (v * s) + t
        turn = lambda v: (R @ v).normalized()
        # the thumb's root joint (the base of its metacarpal) sits deep by the wrist; the border of
        # its skin region lies out in the palm, and a root there folded the palm at the thumb
        joints[1][0] = fit(ref[f"finger1-1{sx}"][0])
        mid = elbow.lerp(wrist, 0.5)
        bones[f"lowerarm01{sx}"] = (elbow, mid, turn(ref[f"lowerarm01{sx}"][2]))
        bones[f"lowerarm02{sx}"] = (mid, wrist, turn(ref[f"lowerarm02{sx}"][2]))
        bones[f"wrist{sx}"] = (wrist, wrist.lerp(joints[3][0], 0.35), turn(ref[f"wrist{sx}"][2]))
        for f in range(2, 6):
            head = fit(ref[f"metacarpal{f - 1}{sx}"][0])
            bones[f"metacarpal{f - 1}{sx}"] = (head, joints[f][0], turn(ref[f"metacarpal{f - 1}{sx}"][2]))
        for f in range(1, 6):
            j = joints[f]
            for k in range(3):
                n = f"finger{f}-{k + 1}{sx}"
                bones[n] = (j[k], j[k + 1], turn(ref[n][2]))
        print(f"{side}: scale {s:.3f}, wrist {tuple(round(c, 3) for c in wrist)}")

    # carry the forearms on past the elbow cut
    for side in ("L", "R"):
        sx = "." + side
        elbow, mid, _ = bones[f"lowerarm01{sx}"]
        out = (elbow - mid).normalized()
        edges = [e for e in bm.edges if e.is_boundary and (e.verts[0].co.x > 0) == (side == "L")]
        old = {v for e in edges for v in e.verts}
        res = bmesh.ops.extrude_edge_only(bm, edges=edges)
        for v in (g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)):
            src_v = next(e.other_vert(v) for e in v.link_edges if e.other_vert(v) in old)
            v.co = src_v.co + out * EXTEND
        bones[f"lowerarm01{sx}"] = (elbow + out * EXTEND, mid, bones[f"lowerarm01{sx}"][2])
    # which vertices are on which part (vertex indices survive bm.to_mesh)
    bm.verts.index_update()
    ids = lambda sets: {v.index for v in region(bm, fs_layer, set(sets))}
    parts = {}
    for side, m in sides.items():
        parts[side] = {"palm": ids([m["palm"]]), "thumb": ids(m[1][1:]), "thumb_mc": ids(m[1][:1]),
                       "fingers": {f: ids(m[f]) for f in range(2, 6)}}
    nails = {m[f][3] for m in sides.values() for f in range(1, 6)}
    nail_faces = [f.index for f in bm.faces if f[fs_layer] in nails]
    bm.to_mesh(me)
    bm.free()

    # the rest shape, for textures that stay on the skin as it moves
    rest = me.attributes.new("rest", "FLOAT_VECTOR", "POINT")
    rest.data.foreach_set("vector", [c for v in me.vertices for c in v.co])

    # the rig
    arm = bpy.data.armatures.new("HandsRig")
    rig = bpy.data.objects.new("HandsRig", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for n, (h, t, z) in bones.items():
        eb = arm.edit_bones.new(n)
        eb.head, eb.tail = h, t
        eb.align_roll(z)
    bpy.ops.object.mode_set(mode="OBJECT")

    # materials: skin, nails
    me.materials.clear()
    me.materials.append(skin_material())
    me.materials.append(nail_material())
    idx = [0] * len(me.polygons)
    for i in nail_faces:
        idx[i] = 1
    me.polygons.foreach_set("material_index", idx)
    for p in me.polygons:
        p.use_smooth = True

    # weights (bone heat, on the base mesh), then the sculpt detail on top
    multires = next(m for m in body.modifiers if m.type == "MULTIRES")
    multires.levels = 0
    multires.render_levels = multires.total_levels
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    body.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    # soften the weights' borders on the palm and arm (bone heat leaves them sharp, which creased
    # the wrist), then give the fingers their own: firm segments that bend at the joints
    smooth_weights(body, factor=0.5, repeat=6)
    finger_weights(body, bones, parts)
    # deform the base mesh, relax its pinching, then lay the sculpted detail on top: the multires
    # last, so the bones move ~2000 vertices instead of ~130000
    arm_mod = next(m for m in body.modifiers if m.type == "ARMATURE")
    relax = body.vertex_groups.new(name="relax")
    on_fingers = set().union(*(p["thumb"] | set().union(*p["fingers"].values()) for p in parts.values()))
    relax.add([i for i in range(len(me.vertices)) if i not in on_fingers], 1.0, "REPLACE")
    relax.add(sorted(on_fingers), FINGER_RELAX, "REPLACE")
    cs = body.modifiers.new("Relax joints", "CORRECTIVE_SMOOTH")
    cs.factor = 0.5
    cs.iterations = 6
    cs.smooth_type = "LENGTH_WEIGHTED"
    cs.rest_source = "ORCO"
    cs.use_only_smooth = False
    cs.vertex_group = "relax"
    for i, mod in enumerate((arm_mod, cs, multires)):
        body.modifiers.move(list(body.modifiers).index(mod), i)
    groups = {g.name for g in body.vertex_groups}
    missing = [n for n in bones if n not in groups]
    empty = [g.name for g in body.vertex_groups
             if not any(gr.group == g.index and gr.weight > 0.01 for v in me.vertices for gr in v.groups)]
    print("weights:", len(groups), "groups; missing", missing, "empty", empty)
    multires.levels = 1
    body.name = me.name = "Hands"
    body["forearm_extended"] = True
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args.out, compress=True)
    print("Saved", args.out, len(me.vertices), "base vertices")


def smooth_weights(ob, factor=0.5, repeat=6):
    """Each vertex's weights pulled toward its neighbours' average, `repeat` times; then normalized."""
    me = ob.data
    n = len(me.vertices)
    nbrs = [[] for _ in range(n)]
    for e in me.edges:
        a, b = e.vertices
        nbrs[a].append(b)
        nbrs[b].append(a)
    w = [{g.group: g.weight for g in v.groups} for v in me.vertices]
    for _ in range(repeat):
        new = []
        for i in range(n):
            if not nbrs[i]:
                new.append(w[i])
                continue
            avg = {}
            for j in nbrs[i]:
                for g, x in w[j].items():
                    avg[g] = avg.get(g, 0.0) + x / len(nbrs[i])
            keys = set(w[i]) | set(avg)
            new.append({g: (1 - factor) * w[i].get(g, 0.0) + factor * avg.get(g, 0.0) for g in keys})
        w = new
    groups = list(ob.vertex_groups)
    for i, d in enumerate(w):
        total = sum(d.values()) or 1.0
        for g in groups:
            x = d.get(g.index, 0.0) / total
            if x > 1e-4:
                g.add([i], x, "REPLACE")
            elif g.index in d:
                g.remove([i])


def step(s, w):
    """0 below -w, 1 above w, smooth between."""
    x = min(1.0, max(0.0, (s + w) / (2 * w)))
    return x * x * (3 - 2 * x)


def finger_weights(ob, bones, parts):
    """
    The fingers' weights from their joints: across each joint (a plane halfway between its two
    bones' directions) the weight passes from one bone to the next over BLEND, so the segments
    stay firm and bend at the joint, as bones do. Round the knuckles, the palm's vertices are
    shared between the nearest fingers (by how far they are to the side of each finger's axis),
    so the webs follow both neighbours.
    """
    me = ob.data
    co = [v.co.copy() for v in me.vertices]
    names = {g.index: g.name for g in ob.vertex_groups}
    cur = [{names[g.group]: g.weight for g in v.groups} for v in me.vertices]
    new = {}

    def seg(n):
        h, t, _ = bones[n]
        return h, (t - h).normalized()

    def plane(a, b):
        """Where bone b starts after bone a, and the plane's normal there."""
        h, db = seg(b)
        return h, (seg(a)[1] + db).normalized()

    def chain(i, names_, widths):
        """Weights along bones names_ (proximal first) with a joint between each pair."""
        ups = [step((co[i] - h).dot(n), w) for (h, n), w in
               zip((plane(names_[k], names_[k + 1]) for k in range(len(names_) - 1)), widths)]
        out, left = {}, 1.0
        for k in range(len(names_) - 1, 0, -1):
            share = left * ups[k - 1]
            out[names_[k]] = share
            left -= share
        out[names_[0]] = left
        return out

    def outside(i, chain_names, d):
        """The vertex's own weights without the chain's bones, scaled to fill 1 - sum(d), plus d."""
        rest = {g: w for g, w in cur[i].items() if g not in chain_names}
        tot = sum(rest.values())
        k = (1.0 - sum(d.values())) / tot if tot > 1e-9 else 0.0
        out = {g: w * k for g, w in rest.items()}
        if tot <= 1e-9 and d:                 # nothing else here: the chain takes it all
            s = sum(d.values())
            return {g: w / s for g, w in d.items()}
        for g, w in d.items():
            out[g] = out.get(g, 0.0) + w
        return out

    for side, p in parts.items():
        sx = "." + side
        fingers = {f: [f"metacarpal{f - 1}{sx}"] + [f"finger{f}-{k}{sx}" for k in (1, 2, 3)] for f in range(2, 6)}
        all_finger_bones = {n for ns in fingers.values() for n in ns[1:]}
        widths = (BLEND["mcp"], BLEND["pip"], BLEND["dip"])
        for f, ns in fingers.items():
            for i in p["fingers"][f]:
                new[i] = chain(i, ns, widths)
        # the palm round the knuckles
        k2, k5 = seg(fingers[2][1])[0], seg(fingers[5][1])[0]
        axes = {f: seg(ns[1]) for f, ns in fingers.items()}
        normal = (k2 - k5).cross(axes[3][1]).normalized()
        taken = set().union(*p["fingers"].values()) | p["thumb"] | p["thumb_mc"]
        for i in p["palm"] - taken:
            share, dist = {}, {}
            for f, (h, d) in axes.items():
                lat = (co[i] - h) - d * (co[i] - h).dot(d)
                lat -= normal * lat.dot(normal)
                share[f] = np.exp(-(lat.length / SHARE_SIGMA) ** 2)
            tot = sum(share.values())
            if tot < 1e-6:
                near = min(axes, key=lambda f: (co[i] - axes[f][0]).length)
                share = {f: float(f == near) for f in axes}
                tot = 1.0
            d = {}
            for f, ns in fingers.items():
                if share[f] / tot < 1e-3:
                    continue
                w = share[f] / tot * step((co[i] - axes[f][0]).dot(plane(ns[0], ns[1])[1]), BLEND["mcp"])
                if w > 1e-4:
                    d[ns[1]] = w
            new[i] = outside(i, all_finger_bones, d)
        # the thumb: its first and last bones beyond the knuckle; the root (the ball of the thumb)
        # keeps its smoothed bone-heat weights, blended into the thumb's first bone at the knuckle
        th = [f"finger1-{k}{sx}" for k in (1, 2, 3)]
        for i in p["thumb"]:
            new[i] = chain(i, th, (BLEND["thumb_mcp"], BLEND["thumb_ip"]))
        mcp_plane = plane(th[0], th[1])
        for i in p["thumb_mc"] - p["thumb"]:
            up = step((co[i] - mcp_plane[0]).dot(mcp_plane[1]), BLEND["thumb_mcp"])
            new[i] = outside(i, set(th[1:]), {th[1]: up} if up > 1e-4 else {})

    groups = {g.name: g for g in ob.vertex_groups}
    for i, d in new.items():
        for g in set(cur[i]) | set(d):
            w = d.get(g, 0.0)
            if w > 1e-4:
                groups[g].add([i], w, "REPLACE")
            elif g in cur[i]:
                groups[g].remove([i])


def skin_material():
    """
    Procedural skin glued to the rest shape: a medium tone with blotchy
    variation and warmer patches, pores and fine creases in the bump,
    darker in the folds (AO), soft subsurface scattering, a faint sheen.
    """
    m = bpy.data.materials.new("Skin")
    m.use_nodes = True
    nt = m.node_tree
    N, L = nt.nodes, nt.links
    b = N["Principled BSDF"]
    rest = N.new("ShaderNodeAttribute")
    rest.attribute_name = "rest"
    co = rest.outputs["Vector"]

    def noise(scale, detail=4.0, rough=0.55, dims="3D"):
        n = N.new("ShaderNodeTexNoise")
        n.noise_dimensions = dims
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = rough
        L.new(co, n.inputs["Vector"])
        return n

    def mix(fac, a, b, blend="MIX"):
        x = N.new("ShaderNodeMix")
        x.data_type, x.blend_type = "RGBA", blend
        for sock, v in ((x.inputs["Factor"], fac), (x.inputs["A"], a), (x.inputs["B"], b)):
            if isinstance(v, (int, float, tuple)):
                sock.default_value = v
            else:
                L.new(v, sock)
        return x.outputs["Result"]

    def n_mul(src, k):
        x = N.new("ShaderNodeMath")
        x.operation = "MULTIPLY"
        L.new(src, x.inputs[0])
        x.inputs[1].default_value = k
        return x.outputs[0]

    def ramp(src, lo, hi):
        r = N.new("ShaderNodeMapRange")
        L.new(src, r.inputs["Value"])
        r.inputs["From Min"].default_value, r.inputs["From Max"].default_value = lo, hi
        return r.outputs["Result"]

    base = lin(SKIN_SRGB)
    warm = lin((186, 102, 82))
    pale = lin((196, 146, 118))
    blotch = noise(28.0, 6.0, 0.6)
    tint = mix(n_mul(ramp(blotch.outputs["Fac"], 0.35, 0.75), 0.6), base, warm)
    fine = noise(160.0, 3.0, 0.5)
    tint = mix(n_mul(ramp(fine.outputs["Fac"], 0.3, 0.8), 0.5), tint, pale)
    ao = N.new("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = 0.012
    crease = ramp(ao.outputs["AO"], 0.2, 1.0)
    tint = mix(1.0, tint, mix(crease, (0.78, 0.62, 0.56, 1), (1, 1, 1, 1)), blend="MULTIPLY")
    L.new(tint, b.inputs["Base Color"])
    # pores and creases in the surface
    pores = N.new("ShaderNodeTexVoronoi")
    pores.feature = "F1"
    pores.inputs["Scale"].default_value = 1400.0
    L.new(co, pores.inputs["Vector"])
    lines = noise(420.0, 2.0, 0.5)
    bump1 = N.new("ShaderNodeBump")
    bump1.inputs["Strength"].default_value = 0.07
    bump1.inputs["Distance"].default_value = 0.0004
    L.new(pores.outputs["Distance"], bump1.inputs["Height"])
    bump2 = N.new("ShaderNodeBump")
    bump2.inputs["Strength"].default_value = 0.04
    bump2.inputs["Distance"].default_value = 0.0003
    L.new(lines.outputs["Fac"], bump2.inputs["Height"])
    L.new(bump1.outputs["Normal"], bump2.inputs["Normal"])
    L.new(bump2.outputs["Normal"], b.inputs["Normal"])
    rough = ramp(noise(90.0).outputs["Fac"], 0.0, 1.0)
    rmap = N.new("ShaderNodeMapRange")
    L.new(rough, rmap.inputs["Value"])
    rmap.inputs["To Min"].default_value, rmap.inputs["To Max"].default_value = 0.36, 0.55
    L.new(rmap.outputs["Result"], b.inputs["Roughness"])
    b.inputs["Specular IOR Level"].default_value = 0.45
    b.inputs["Subsurface Weight"].default_value = 1.0
    b.inputs["Subsurface Radius"].default_value = (1.0, 0.38, 0.22)
    b.inputs["Subsurface Scale"].default_value = 0.0045
    b.inputs["Sheen Weight"].default_value = 0.12
    b.inputs["Sheen Roughness"].default_value = 0.4
    return m


def nail_material():
    m = bpy.data.materials.new("Nails")
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = lin(NAIL_SRGB)
    b.inputs["Roughness"].default_value = 0.25
    b.inputs["Coat Weight"].default_value = 0.5
    b.inputs["Coat Roughness"].default_value = 0.15
    b.inputs["Subsurface Weight"].default_value = 0.4
    b.inputs["Subsurface Radius"].default_value = (1.0, 0.4, 0.3)
    b.inputs["Subsurface Scale"].default_value = 0.002
    return m


main()
