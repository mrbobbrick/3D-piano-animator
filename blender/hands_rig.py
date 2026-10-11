"""
The skinned hands (assets/hands_sculpted.blend from make_hands_sculpted.py,
or the MakeHuman assets/hands.blend from make_hands.py) posed on the
exported skeletons.

Each bone's rotation turns the rig's rest frame of its segment onto the
posed one (each frame built from the segment's direction and the hand's
knuckle line, or the palm's normal for the thumb). Bones are unparented, so
each one is posed on its own.

Sculpted hands (SOLVED): the palm, knuckles included, is rigid in the
sculpt's proportions round the exported wrist, and each finger keeps the
sculpt's bone lengths, bent (solve_finger) to put its tip on the exported
one; a finger held above the keys is lifted at the knuckle. The hand is
scaled by its fingers' length; the thumb keeps the sculpt's proportions, and
when its key is out of its reach the whole hand moves toward it instead
(thumb_shift).
MakeHuman hands: every bone stretches to span its two exported joints,
scaled first to the exported knuckle width. Either way fingertips land where
the animation put them (TIP_LIFTS aside).
"""

import math
import os

import bpy
from mathutils import Matrix, Vector

from scene import set_keys

ASSETS = {
    "sculpted": os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "hands_sculpted.blend"),
    "makehuman": os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "hands.blend"),
}
SUBDIV = 2
FOREARM_EXTEND = 0.22              # m added to each forearm past the asset's cut at the elbow
WRIST_BLEND = 0.45                  # share of the palm's turn (against the forearm) the forearm's lower bone takes
# The rig's finger bones end at about the fingertip pad's underside (measured on
# the deformed mesh: fingers 2-4 within 0.5 mm of the key), except the thumb,
# which plays on its side, and the little finger: their last joints are raised
# so they meet the key rather than sink into it. {point index: metres}
TIP_LIFTS = {
    "makehuman": {1: 0.0028, 2: 0.0055, 3: 0.0055, 22: 0.0014, 23: 0.0014},
    # measured on the deformed mesh (demo song, after the joint relaxing): each finger's last two
    # joints lifted by how far its tip sank into the key (its middle joint half as much)
    # the sculpted hands' fingers are solved (SOLVED), so only the tips are raised: by how far each
    # pad sank into its key (demo song, 97 notes: 2.4..3.3 mm; the thumb, on its side and thickened, ~8 mm)
    "sculpted": {3: 0.0079, 8: 0.0027, 13: 0.0027, 18: 0.0029, 23: 0.0030},
}

# (bone, data head index, data tail index, reference) - indices into the 27
# exported points plus 27 = wrist centre; reference "side" (knuckle line) or
# "normal" (back of the palm)
FINGER_BONES = [("finger1-1", 0, 1, "normal"), ("finger1-2", 1, 2, "normal"), ("finger1-3", 2, 3, "normal")]
for f in range(2, 6):
    b = 4 + 5 * (f - 2)
    FINGER_BONES += [(f"metacarpal{f - 1}", b, b + 1, "side"), (f"finger{f}-1", b + 1, b + 2, "side"),
                     (f"finger{f}-2", b + 2, b + 3, "side"), (f"finger{f}-3", b + 3, b + 4, "side")]
MCP2, MCP3, MCP5 = 5, 10, 20
PALM_ROOTS = (0, 4, 9, 14, 19)      # thumb CMC and the metacarpals' bases: placed rigidly with the palm
# Models whose fingers keep the sculpt's bone lengths: the palm (knuckles included) rigid, and each
# finger bent to reach the exported fingertip (the others stretch every bone onto the exported joints)
SOLVED = {"sculpted"}
KNUCKLES = (5, 10, 15, 20)
CHAINS = [(0, (1, 2, 3))] + [(5 + 5 * k, (6 + 5 * k, 7 + 5 * k, 8 + 5 * k)) for k in range(4)]   # (root, joints)
BEND_MAX = math.radians(125)       # the most the solver bends a finger's middle joint
THUMB_REACH = 0.98                 # share of its full length the sculpted thumb reaches before the hand moves
THUMB_SHIFT_MAX = 0.025            # m the hand moves toward the thumb's key at most (thumb_shift)
STRETCHED = {"chains": 0, "stretched": 0, "most": 1.0}   # out-of-reach fingers, for the build's report
SHIFTED = {"frames": 0, "shifted": 0, "most": 0.0}        # hand moves toward the thumb's key, likewise


def rest_points(rig, s):
    """The exported points' equivalents on the rig at rest (armature space), side suffix s."""
    bones = rig.data.bones

    def head(n):
        return bones[n + s].head_local.copy()

    def tail(n):
        return bones[n + s].tail_local.copy()
    p = [None] * 28
    p[0], p[1], p[2], p[3] = head("finger1-1"), head("finger1-2"), head("finger1-3"), tail("finger1-3")
    for f in range(2, 6):
        b = 4 + 5 * (f - 2)
        p[b] = head(f"metacarpal{f - 1}")
        p[b + 1], p[b + 2], p[b + 3] = head(f"finger{f}-1"), head(f"finger{f}-2"), head(f"finger{f}-3")
        p[b + 4] = tail(f"finger{f}-3")
    p[27] = head("wrist")
    p[26] = head("lowerarm01")
    return p


def frame_of(y, ref):
    """Rotation whose Y axis is y and X axis is ref made perpendicular to it."""
    y = y.normalized()
    x = ref - y * ref.dot(y)
    if x.length < 1e-9:
        x = Vector((1, 0, 0)) - y * y.x
    x.normalize()
    z = x.cross(y)
    return Matrix((x, y, z)).transposed()


def hand_scale(p, rp):
    """The export's hand over the rig's, by the fingers' bone lengths (index to little finger)."""
    def total(pts):
        return sum((pts[r + k + 1] - pts[r + k]).length for r, _ in CHAINS[1:] for k in range(3))
    return total(p) / total(rp)


def thumb_shift(cmc, tip, length):
    """
    How far the whole hand moves so the thumb, rooted at cmc and `length` long, reaches the
    exported tip with a little bend left (THUMB_REACH): toward the tip by what it lacks, at most
    THUMB_SHIFT_MAX. The export's thumb is longer than the sculpt's, which couldn't reach its tip
    61% of the time; lengthening the sculpt's thumb to match made it look freakishly long, so the
    hand comes closer instead (as a pianist's does) and the fingers, solved from the knuckles to
    their own tips, bend a little more.
    """
    d = tip - cmc
    lack = d.length - THUMB_REACH * length
    if lack <= 0.0:
        return Vector((0.0, 0.0, 0.0))
    return d.normalized() * min(lack, THUMB_SHIFT_MAX)


def bend(a, b, c):
    """The angle (radians) a joint at b bends by, between segments a-b and b-c."""
    u, v = b - a, c - b
    if u.length < 1e-9 or v.length < 1e-9:
        return 0.0
    return u.angle(v)


def finger_chain(p, q, rp, g, root, joints, surface=None, lift=0.0):
    """
    One finger's (or the thumb's) joints after its root q[root], the sculpt's bone lengths times g,
    reaching the exported tip: bent in the plane the exported finger bends in, its last joint
    bending the same share of the middle one as in the export.

    surface(x, y): the key top under a point. A finger held above the keys keeps the curve it
    would have resting on the key below and is lifted at the knuckle, as real fingers lift (the
    export raises the tip toward the knuckle, and bending the finger to follow it curled it into
    a claw); its tip then sits a little beyond the exported one, on the same line from the knuckle.
    """
    j1, j2, tip = joints
    lengths = [(rp[b] - rp[a]).length * g for a, b in ((root, j1), (j1, j2), (j2, tip))]
    # the exported finger's bow: its middle joints' offset from the line root -> tip
    line = (p[tip] - q[root]).normalized()
    mid = (p[j1] + p[j2]) / 2 - q[root]
    bulge = mid - line * mid.dot(line)
    b_mid = bend(p[root], p[j1], p[j2])
    coupling = bend(p[j1], p[j2], p[tip]) / b_mid if b_mid > 0.15 else 0.75
    curl_r = None
    if surface is not None:
        rest = p[tip].copy()
        rest.z = min(rest.z, surface(rest.x, rest.y) + lift)       # lift: the tip's TIP_LIFTS
        curl_r = (rest - q[root]).length
    return solve_finger(q[root], p[tip], lengths, bulge, min(1.3, max(0.2, coupling)), curl_r)


def solve_finger(base, target, lengths, bulge, coupling, curl_r=None):
    """
    Three bones from base toward target, bowing toward `bulge`: the second joint bends by b and the
    third by coupling * b, b found so the tip lands on the target (as hands.solve_chain), or, given
    curl_r, so the tip is curl_r from the base (on the line toward the target). Out of reach, the
    chain points straight at the target and stretches to it. Returns the three joints after the base.
    """
    L1, L2, L3 = lengths
    d = target - base
    r = max(d.length, 1e-6)
    shape_r = max(r, curl_r) if curl_r is not None else r
    e1 = d / r
    e2 = bulge - e1 * bulge.dot(e1)
    if e2.length < 1e-6:
        e2 = Vector((0, 0, 1)) - e1 * e1.z
    e2.normalize()
    k = 1.0 + coupling

    def reach(b):
        return (L1 + L2 * math.cos(b) + L3 * math.cos(b * k), -L2 * math.sin(b) - L3 * math.sin(b * k))

    s = 1.0
    STRETCHED["chains"] += 1
    if r >= L1 + L2 + L3:
        b, s = 0.0, r / (L1 + L2 + L3)
        STRETCHED["stretched"] += 1
        STRETCHED["most"] = max(STRETCHED["most"], s)
    elif shape_r >= L1 + L2 + L3:
        b = 0.0
    elif math.hypot(*reach(BEND_MAX)) >= shape_r:
        b = BEND_MAX
    else:
        lo, hi = 0.0, BEND_MAX
        for _ in range(30):
            b = (lo + hi) / 2
            if math.hypot(*reach(b)) > shape_r:
                lo = b
            else:
                hi = b
    x, y = reach(b)
    a = -math.atan2(y, x)

    def u(t):
        return e1 * math.cos(t) + e2 * math.sin(t)
    p1 = base + u(a) * (L1 * s)
    p2 = p1 + u(a - b) * (L2 * s)
    return [p1, p2, p2 + u(a - b * k) * (L3 * s)]


def refs(p):
    side = p[MCP5] - p[MCP2]
    fwd = p[MCP3] - p[27]
    return {"side": side, "normal": side.cross(fwd)}


def load_asset(model="sculpted"):
    """A fresh copy of the hands and their rig (loaded again for each pair, so names may get suffixes)."""
    path = ASSETS.get(model, ASSETS["sculpted"])
    if not os.path.exists(path):
        path = ASSETS["makehuman"]
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n in ("Hands", "HandsRig")]
    for ob in dst.objects:
        bpy.context.scene.collection.objects.link(ob)
    mesh = next(ob for ob in dst.objects if ob.type == "MESH")
    rig = next(ob for ob in dst.objects if ob.type == "ARMATURE")
    return mesh, rig


def build(sc, data):
    """Skinned hands for every pair in the export ("R"/"L", extra pairs "R2"/"L2", ...); returns their meshes."""
    pairs = {}
    for h, hd in data["hands"].items():
        pairs.setdefault(h[1:], {})[h[0]] = hd
    meshes = []
    for suffix in sorted(pairs, key=lambda x: int(x or 1)):
        mesh, _ = build_pair(sc, pairs[suffix], sc.st.get("hand_model", "sculpted"))
        meshes.append(mesh)
    return meshes


def extend_forearms(mesh, rig, length=FOREARM_EXTEND):
    """
    Carry each forearm on past the asset's cut at the elbow (its only open
    edges), the new skin moving rigidly with the upper forearm bone, so the
    cut stays out of wide and upright pictures without stretching the arm
    (stretching its bone also pulled the skin at the wrist into the hand).
    """
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    deform = bm.verts.layers.deform.verify()
    uv = bm.loops.layers.uv.active
    for suffix, sign in ((".L", 1), (".R", -1)):
        bone = rig.data.bones["lowerarm01" + suffix]
        out = (bone.head_local - bone.tail_local).normalized()          # toward the elbow
        group = mesh.vertex_groups["lowerarm01" + suffix].index
        edges = [e for e in bm.edges if e.is_boundary and (e.verts[0].co.x > 0) == (sign > 0)]
        if not edges:
            continue
        old = {v for e in edges for v in e.verts}
        res = bmesh.ops.extrude_edge_only(bm, edges=edges)
        for v in (g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)):
            src = next(e.other_vert(v) for e in v.link_edges if e.other_vert(v) in old)
            v.co = src.co + out * length
            v[deform].clear()
            v[deform][group] = 1.0
        # the new faces take the skin colour from where they join the arm
        for f in (g for g in res["geom"] if isinstance(g, bmesh.types.BMFace)):
            for loop in f.loops:
                v = loop.vert
                src = v if v in old else next(e.other_vert(v) for e in v.link_edges if e.other_vert(v) in old)
                ref = next((l for l in src.link_loops if l.face is not f), None)
                if ref is not None and uv is not None:
                    loop[uv].uv = ref[uv].uv
    bm.to_mesh(mesh.data)
    bm.free()


def build_pair(sc, hands, model="sculpted"):
    """One pair of hands ({"R": export hand, "L": ...}, either may be missing) on a copy of the asset."""
    mesh, rig = load_asset(model)
    lifts = TIP_LIFTS.get(model, {})
    solved = model in SOLVED
    blacks = [(k["x0"], k["x1"], sc.L - k["length"]) for k in sc.keys.values() if k["black"]]

    def surface(x, y):
        """The top of the key under (x, y): a black key's at its rise, a white key's at 0."""
        return sc.rise if any(x0 <= x <= x1 and y >= y0 for x0, x1, y0 in blacks) else 0.0
    if not mesh.get("forearm_extended"):
        extend_forearms(mesh, rig)
    rig.parent = sc.root
    rig.matrix_parent_inverse = Matrix.Identity(4)
    rig.location = (0, 0, 0)
    rig.rotation_euler = (0, 0, 0)
    rig.scale = (1, 1, 1)
    if not any(m.type == "MULTIRES" for m in mesh.modifiers):        # the sculpted asset has its own detail
        mod = mesh.modifiers.new("Subdivision", "SUBSURF")
        mod.levels = 1
        mod.render_levels = SUBDIV
    for poly in mesh.data.polygons:
        poly.use_smooth = True

    sides = {"R": ".R", "L": ".L"}
    names = [b for b, *_ in FINGER_BONES] + ["wrist", "lowerarm01", "lowerarm02"]
    # unparent the posed bones (each is placed on its own; a stretched parent would shear its children)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for s in sides.values():
        for n in names:
            eb = rig.data.edit_bones[n + s]
            eb.use_connect = False
            eb.parent = None
    bpy.ops.object.mode_set(mode="OBJECT")

    for h, hd in hands.items():
        s = sides[h]
        rp = rest_points(rig, s)
        rref = refs(rp)
        rest = {n: rig.data.bones[n + s].matrix_local.copy() for n in names}
        rest_rot = {n: m.to_3x3().normalized() for n, m in rest.items()}
        rest_inv = {n: m.inverted() for n, m in rest.items()}
        head01 = rig.data.bones["lowerarm01" + s].head_local.copy()
        head02 = rig.data.bones["lowerarm02" + s].head_local.copy()
        frames = hd["frames"]
        n_fr = len(frames)
        chans = {n: ([], [], []) for n in names}       # loc, quat, scale per frame
        g = None
        for fr in frames:
            p = [Vector(fr[3 * i:3 * i + 3]) for i in range(27)]
            for i, dz in lifts.items():
                p[i].z += dz
            p.append((p[24] + p[25]) / 2)
            if g is None:
                g = hand_scale(p, rp) if solved else (p[MCP5] - p[MCP2]).length / (rp[MCP5] - rp[MCP2]).length
            dref = refs(p)
            pose = {}
            # the palm and the forearm are rigid round the wrist, in the rig's own proportions: the
            # wrist joint at the exported one, turned with the palm; the metacarpals' and the thumb's
            # roots where the rig has them in the palm (the exported ones sit further forward and
            # higher, and dragged the wrist into a lump); the forearm turned with the arm, its bones
            # where the rig has them relative to the wrist (they don't end exactly at it)
            w0, wr = p[27], rp[27]
            palm = frame_of(p[MCP3] - w0, dref["side"]) @ frame_of(rp[MCP3] - wr, rref["side"]).transposed()
            if solved:
                thumb_len = sum((rp[k + 1] - rp[k]).length for k in range(3)) * g
                shift = thumb_shift(w0 + palm @ ((rp[0] - wr) * g), p[3], thumb_len)
                w0 = w0 + shift
                SHIFTED["frames"] += 1
                if shift.length > 0.0:
                    SHIFTED["shifted"] += 1
                    SHIFTED["most"] = max(SHIFTED["most"], shift.length)
            pose["wrist"] = (w0, palm, (g, g, g))
            q = list(p)
            for i in PALM_ROOTS:
                q[i] = w0 + palm @ ((rp[i] - wr) * g)
            if solved:
                # the knuckles too are rigid in the palm, and each finger is bent to put its tip on
                # the exported one with the sculpt's own bone lengths (see solve_finger)
                for i in KNUCKLES:
                    q[i] = w0 + palm @ ((rp[i] - wr) * g)
                for base, joints in CHAINS:
                    q[joints[0]:joints[-1] + 1] = finger_chain(p, q, rp, g, base, joints,
                                                               surface if base else None,
                                                               lifts.get(joints[-1], 0.0))
                p = q
            for n, a, b, r in FINGER_BONES:
                rot = frame_of(p[b] - q[a], dref[r]) @ frame_of(rp[b] - rp[a], rref[r]).transposed()
                stretch = (p[b] - q[a]).length / max(1e-9, (rp[b] - rp[a]).length)
                pose[n] = (q[a], rot, (g, stretch, g))
            arm = frame_of(w0 - p[26], dref["side"]) @ frame_of(wr - rp[26], rref["side"]).transposed()
            h02 = w0 + arm @ ((head02 - wr) * g)
            # the forearm's lower bone takes part of the wrist's turn, so the bend runs smoothly
            # into the hand instead of folding at one line
            lower = arm.to_quaternion().slerp(palm.to_quaternion(), WRIST_BLEND).to_matrix()
            pose["lowerarm02"] = (w0 + lower @ ((head02 - wr) * g), lower, (g, g, g))
            pose["lowerarm01"] = (h02 + arm @ ((head01 - head02) * g), arm, (g, g, g))
            for n, (head, rot, sc_) in pose.items():
                m = Matrix.Translation(head) @ (rot @ rest_rot[n]).to_4x4() @ Matrix.Diagonal((*sc_, 1.0))
                loc, q, scl = (rest_inv[n] @ m).decompose()
                L, Q, S = chans[n]
                if Q and Q[-1].dot(q) < 0:
                    q.negate()
                L.append(loc)
                Q.append(q)
                S.append(scl)
        fr_idx = list(range(1, n_fr + 1))
        for n, (L, Q, S) in chans.items():
            pb = rig.pose.bones[n + s]
            pb.rotation_mode = "QUATERNION"
            path = f'pose.bones["{n + s}"]'
            for c in range(3):
                set_keys(rig, path + ".location", c, fr_idx, [v[c] for v in L])
                set_keys(rig, path + ".scale", c, fr_idx, [v[c] for v in S])
            for c in range(4):
                set_keys(rig, path + ".rotation_quaternion", c, fr_idx, [q[c] for q in Q])
        print(f"hand {h}: scale {g:.3f}")
        if solved:
            n = max(1, STRETCHED["chains"])
            print(f"  fingers out of reach (stretched): {100 * STRETCHED['stretched'] / n:.2f}% of {n}, "
                  f"at most {STRETCHED['most']:.3f}")
            n = max(1, SHIFTED["frames"])
            print(f"  hand moved toward the thumb's key: {100 * SHIFTED['shifted'] / n:.1f}% of frames, "
                  f"at most {1000 * SHIFTED['most']:.1f} mm")
            STRETCHED.update(chains=0, stretched=0, most=1.0)
            SHIFTED.update(frames=0, shifted=0, most=0.0)

    # a hand the song doesn't use is left out of the render
    unused = [s for h, s in sides.items() if h not in hands]
    if unused:
        keep = mesh.vertex_groups.new(name="used")
        idx = {g.index for g in mesh.vertex_groups if g.name.endswith(tuple(unused))}
        used = [v.index for v in mesh.data.vertices
                if sum(g.weight for g in v.groups if g.group in idx) < 0.5]
        keep.add(used, 1.0, "REPLACE")
        mask = mesh.modifiers.new("Used hands", "MASK")
        mask.vertex_group = "used"
        bpy.ops.object.select_all(action="DESELECT")
        mesh.modifiers.move(len(mesh.modifiers) - 1, 0)
    return mesh, rig
