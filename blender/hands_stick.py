"""
Stick-figure hands (cylinders and spheres on the exported joints): a quick
check of the motion and the key contact before the skinned hands.
"""

import bpy
from mathutils import Vector

from scene import add_obj, principled, set_keys, srgb

# segments between the 27 exported points (27 = wrist centre, added here)
SEGMENTS = ([(0, 1, 0.009), (1, 2, 0.008), (2, 3, 0.007)] +
            [(b + i, b + i + 1, r) for b in (4, 9, 14, 19) for i, r in enumerate((0.006, 0.0075, 0.0068, 0.006))] +
            [(24, 0, 0.007), (0, 4, 0.006), (4, 9, 0.006), (9, 14, 0.006), (14, 19, 0.006), (19, 25, 0.007),
             (24, 25, 0.008), (27, 26, 0.022)])


def points(frame):
    p = [Vector(frame[3 * i:3 * i + 3]) for i in range(27)]
    p.append((p[24] + p[25]) / 2)
    return p


def build(sc, data):
    root = sc.root
    mesh_cyl = bpy.data.meshes.new("cyl")
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=1, radius2=1, depth=1)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 0.5))
    bm.to_mesh(mesh_cyl)
    bm.free()
    mesh_sph = bpy.data.meshes.new("sph")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=1)
    bm.to_mesh(mesh_sph)
    bm.free()
    for p in mesh_cyl.polygons:
        p.use_smooth = True
    for p in mesh_sph.polygons:
        p.use_smooth = True
    for h, hd in data["hands"].items():
        mat = principled(f"Stick{h}", srgb((210, 160, 130)), rough=0.5)
        frames = hd["frames"]
        pts = [points(f) for f in frames]
        fr = list(range(1, len(frames) + 1))
        for si, (a, b, r) in enumerate(SEGMENTS):
            ob = add_obj(f"{h}seg{si}", mesh_cyl, root, [mat])
            ob.rotation_mode = "QUATERNION"
            locs, quats, lens = [], [], []
            prev = None
            for p in pts:
                d = p[b] - p[a]
                q = Vector((0, 0, 1)).rotation_difference(d.normalized()) if d.length > 1e-9 else prev
                if prev is not None and q.dot(prev) < 0:
                    q = -q
                prev = q
                locs.append(p[a]); quats.append(q); lens.append(d.length)
            for c in range(3):
                set_keys(ob, "location", c, fr, [v[c] for v in locs])
            for c in range(4):
                set_keys(ob, "rotation_quaternion", c, fr, [q[c] for q in quats])
            set_keys(ob, "scale", 0, [1], [r]); set_keys(ob, "scale", 1, [1], [r])
            set_keys(ob, "scale", 2, fr, lens)
        for j in list(range(24)) + [24, 25]:
            r = 0.0085 if j in (3, 8, 13, 18, 23) else 0.008
            ob = add_obj(f"{h}joint{j}", mesh_sph, root, [mat])
            ob.scale = (r * 0.8, r * 0.8, r * 0.8)
            for c in range(3):
                set_keys(ob, "location", c, fr, [p[j][c] for p in pts])
