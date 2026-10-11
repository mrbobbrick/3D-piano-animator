"""
The 3D scene for an export from export3d.py: keyboard, falling notes and
their effects, lights, camera, and the keys' motion, all styled by a
video_style dict. Hands are added by hands_stick.py or hands_rig.py. Run
through render3d.py.

Blender frame of reference = the export's (metres; x right, y away from the
player, z up from the white keys' tops), shifted so the keyboard is centred
on x = 0.
"""

import math
import random

import bpy
from bpy_extras import anim_utils
from mathutils import Vector

from video_style import note_color, to_linear

# keys
KEY_GAP = 0.0009             # between white keys
WHITE_H = 0.022              # white key body below its top
BLACK_H = 0.020              # black key body (top at the rise above white tops)
PIVOT_BACK = 0.25            # keys pivot this far behind their back end
# notes wall
HIT_Z = 0.034                # upright wall: height of the line where notes meet the keyboard
RUN_Z = 0.004                # flat runway: the notes' underside
# (flat runway: the line where notes meet the keys is the style's hit_gap behind the keys' back)
FLASH_T = 0.18               # a strike's flash fades over this
MAX_SPARKS = 400000


def srgb(c, k=1.0):
    return to_linear(c, k)


# --------------------------------------------------------------------------- #
# Animation helpers
# --------------------------------------------------------------------------- #
_INTERP = {e.identifier: e.value for e in bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items}


def fcurve(owner, path, index=0):
    ad = owner.animation_data or owner.animation_data_create()
    if ad.action is None:
        ad.action = bpy.data.actions.new(owner.name + "Action")
    if ad.action_slot is None:
        ad.action_slot = ad.action.slots.new(id_type=owner.id_type, name=owner.name)
    cb = anim_utils.action_ensure_channelbag_for_slot(ad.action, ad.action_slot)
    return cb.fcurves.find(path, index=index) or cb.fcurves.new(path, index=index)


def set_keys(owner, path, index, frames, values, interp="LINEAR"):
    """Write keyframes in bulk (frames and values are equal-length lists)."""
    fc = fcurve(owner, path, index)
    kp = fc.keyframe_points
    start = len(kp)
    kp.add(len(frames))
    co = [0.0] * (2 * len(kp))
    kp.foreach_get("co", co)
    for i, (f, v) in enumerate(zip(frames, values)):
        co[2 * (start + i)] = f
        co[2 * (start + i) + 1] = v
    kp.foreach_set("co", co)
    kp.foreach_set("interpolation", [_INTERP[interp]] * len(kp))
    fc.update()
    return fc


# --------------------------------------------------------------------------- #
# Node helpers
# --------------------------------------------------------------------------- #
class Nodes:
    """Terse node building: n = Nodes(tree); m = n.math('ADD', a, 0.5); n.link(m, socket)."""

    def __init__(self, tree):
        self.t = tree

    def new(self, kind, **props):
        node = self.t.nodes.new(kind)
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def link(self, a, b):
        self.t.links.new(a.outputs[0] if hasattr(a, "outputs") else a, b)

    def _in(self, sock, v):
        if v is None:
            return
        if isinstance(v, (int, float, tuple, list)):
            sock.default_value = v
        else:
            self.link(v, sock)

    def math(self, op, a, b=None, c=None):
        m = self.new("ShaderNodeMath", operation=op)
        for sock, v in zip(m.inputs, (a, b, c)):
            self._in(sock, v)
        return m

    def vmath(self, op, a, b=None, scale=None):
        m = self.new("ShaderNodeVectorMath", operation=op)
        self._in(m.inputs[0], a)
        if b is not None:
            self._in(m.inputs[1], b)
        if scale is not None:
            self._in(m.inputs["Scale"], scale)
        return m

    def mix_rgb(self, fac, a, b, blend="MIX"):
        m = self.new("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        self._in(m.inputs["Factor"], fac)
        self._in(m.inputs["A"], a)
        self._in(m.inputs["B"], b)
        return m.outputs["Result"]

    def map_range(self, v, a, b, c=0.0, d=1.0, clamp=True):
        m = self.new("ShaderNodeMapRange", clamp=clamp)
        self._in(m.inputs["Value"], v)
        m.inputs["From Min"].default_value, m.inputs["From Max"].default_value = a, b
        m.inputs["To Min"].default_value, m.inputs["To Max"].default_value = c, d
        return m

    def xyz(self, v):
        s = self.new("ShaderNodeSeparateXYZ")
        self._in(s.inputs[0], v)
        return s

    def attr(self, name, kind="GEOMETRY"):
        a = self.new("ShaderNodeAttribute", attribute_name=name, attribute_type=kind)
        return a


def fire_ramp(n):
    """Black-body colours for a heat 0..1."""
    r = n.new("ShaderNodeValToRGB")
    els = r.color_ramp.elements
    stops = [(0.0, (0.05, 0.0, 0.0)), (0.3, (0.55, 0.04, 0.0)), (0.55, (1.0, 0.25, 0.01)),
             (0.78, (1.0, 0.62, 0.12)), (1.0, (1.0, 0.95, 0.75))]
    els[0].position, els[0].color = stops[0][0], (*stops[0][1], 1)
    els[1].position, els[1].color = stops[-1][0], (*stops[-1][1], 1)
    for pos, c in stops[1:-1]:
        e = els.new(pos)
        e.color = (*c, 1)
    return r


def time_node(n, sc):
    """A Value node keyframed to the song time in seconds (for flicker and flow)."""
    v = n.new("ShaderNodeValue", name="SongTime")
    set_keys(n.t, 'nodes["SongTime"].outputs[0].default_value', 0,
             [1, sc.n + 1], [sc.t0, sc.t0 + sc.n / sc.fps])
    return v


# --------------------------------------------------------------------------- #
# Materials
# --------------------------------------------------------------------------- #
def _mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    return m, nt, out


def principled(name, color, rough=0.4, coat=0.0, spec=0.5, emit=None, strength=0.0):
    m, nt, out = _mat(name)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Specular IOR Level"].default_value = spec
    if emit:
        b.inputs["Emission Color"].default_value = emit
        b.inputs["Emission Strength"].default_value = strength
    nt.links.new(b.outputs[0], out.inputs[0])
    return m


def key_material(name, color, rough):
    """Key surface tinted toward its object's colour by the colour's alpha (how far the key is down)."""
    m, nt, out = _mat(name)
    n = Nodes(nt)
    b = n.new("ShaderNodeBsdfPrincipled")
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = 0.6
    b.inputs["Coat Roughness"].default_value = 0.08
    info = n.new("ShaderNodeObjectInfo")
    n.link(n.mix_rgb(info.outputs["Alpha"], color, info.outputs["Color"]), b.inputs["Base Color"])
    n.link(info.outputs["Color"], b.inputs["Emission Color"])
    n.link(n.math("MULTIPLY", info.outputs["Alpha"], 0.6), b.inputs["Emission Strength"])
    n.link(b, out.inputs[0])
    return m


def clip_factor(n, sc):
    """
    1 past the hit line (fading in near the far end of the notes' run), 0
    before it; and the position along the run (world z on the upright
    wall, y on the flat runway).
    """
    z = n.xyz(n.new("ShaderNodeNewGeometry").outputs["Position"]).outputs[sc.axis]
    above = n.math("GREATER_THAN", z, sc.hit)
    fade = n.map_range(z, sc.top, sc.top - 0.15 * (sc.top - sc.hit))
    return n.math("MULTIPLY", above, fade), z


def note_material(sc, st):
    """
    The note bars: their colour (attribute 'col'), cut off below the hit
    line and faded in at the top; glow, brighter toward one end (gradient),
    while a note is being played (its bottom 'lo' has passed the hit line,
    its top 'hi' hasn't; both in the roll's frame) and in a flash as it is
    struck; an edge glow; and flames: a flowing, flickering black-body
    surface hotter toward the bottom and while playing.
    """
    m, nt, out = _mat("Notes")
    m.surface_render_method = "DITHERED"
    n = Nodes(nt)
    speed = st["note_speed"]
    hit = sc.hit
    vis, z = clip_factor(n, sc)
    roll = n.xyz(n.new("ShaderNodeObjectInfo").outputs["Location"]).outputs[sc.axis]
    lo, hi = n.attr("lo").outputs["Fac"], n.attr("hi").outputs["Fac"]
    col = n.attr("col").outputs["Color"]
    lo_w, hi_w = n.math("ADD", lo, roll), n.math("ADD", hi, roll)
    playing = n.math("MULTIPLY", n.math("LESS_THAN", lo_w, hit + 0.0005), n.math("GREATER_THAN", hi_w, hit))
    # seconds since the note reached the line, and the strike's flash
    age = n.math("DIVIDE", n.math("SUBTRACT", hit, lo_w), speed)
    flash = n.math("MULTIPLY", n.math("MULTIPLY", n.math("EXPONENT", n.math("DIVIDE", age, -FLASH_T)), playing),
                   st["flash"])
    # where along the note (0 bottom .. 1 top)
    zl = n.math("SUBTRACT", z, roll)
    rel = n.math("DIVIDE", n.math("SUBTRACT", zl, lo), n.math("MAXIMUM", n.math("SUBTRACT", hi, lo), 1e-4))
    rel = n.math("MINIMUM", n.math("MAXIMUM", rel, 0.0), 1.0)
    grad = n.math("MAXIMUM", n.math("MULTIPLY_ADD", rel, -2.0 * st["gradient"], 1.0 + st["gradient"]), 0.05)
    color = n.mix_rgb(n.math("MULTIPLY", playing, st["play_whiten"]), col, (1, 1, 1, 1))
    strength = n.math("ADD", n.math("ADD", n.math("MULTIPLY", grad, 0.5 * st["glow"]),
                                    n.math("MULTIPLY", playing, st["play_glow"])), flash)
    # loud notes brighter: x(0.4 .. 1.6) by velocity, blended in by velocity_glow
    loud = n.math("MULTIPLY_ADD", n.attr("vel").outputs["Fac"], 1.2, 0.4)
    strength = n.math("MULTIPLY", strength, n.math("ADD", 1.0 - st["velocity_glow"],
                                                   n.math("MULTIPLY", loud, st["velocity_glow"])))
    base = color
    if st["fire"] > 0:
        t = time_node(n, sc)
        pos = n.new("ShaderNodeNewGeometry").outputs["Position"]
        # flames flow up the note (the texture is fixed in place while the notes fall) and flicker
        flow = n.vmath("ADD", pos, n.new("ShaderNodeCombineXYZ"))
        comb = flow.inputs[1].links[0].from_node
        n.link(n.math("MULTIPLY", t, -0.05 * st["fire_speed"]), comb.inputs[sc.axis])
        stretch = n.vmath("MULTIPLY", flow, (70.0, 70.0, 26.0) if sc.axis == "Z" else (70.0, 26.0, 70.0))
        noise = n.new("ShaderNodeTexNoise", noise_dimensions="4D")
        n.link(stretch, noise.inputs["Vector"])
        n.link(n.math("MULTIPLY", t, 1.6 * st["fire_speed"]), noise.inputs["W"])
        noise.inputs["Scale"].default_value = 1.0
        noise.inputs["Detail"].default_value = 6.0
        noise.inputs["Roughness"].default_value = 0.6
        noise.inputs["Distortion"].default_value = 0.4
        heat = n.map_range(noise.outputs["Fac"], 0.38, 0.66)
        heat = n.math("ADD", n.math("MULTIPLY", heat, 0.75), n.math("MULTIPLY", n.math("SUBTRACT", 1.0, rel), 0.3))
        heat = n.math("ADD", heat, n.math("ADD", n.math("MULTIPLY", playing, 0.12), n.math("MULTIPLY", flash, 0.04)))
        heat = n.math("MINIMUM", heat, 1.0)
        ramp = fire_ramp(n)
        n.link(heat, ramp.inputs["Fac"])
        # the hand's flame colour tints the mid tones
        tinted = n.mix_rgb(0.6, ramp.outputs["Color"], col, blend="MULTIPLY")
        fire_col = n.mix_rgb(0.35, ramp.outputs["Color"], tinted)
        color = n.mix_rgb(st["fire"], color, fire_col)
        base = n.mix_rgb(st["fire"], base, (0.02, 0.005, 0.0, 1))
        hot = n.math("MULTIPLY_ADD", n.math("POWER", heat, 2.2), 3.2, 0.12)
        strength = n.math("MULTIPLY", strength, n.math("ADD", 1.0 - st["fire"], n.math("MULTIPLY", hot, st["fire"])))
    lw = n.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.35
    rim = n.math("MULTIPLY", n.math("POWER", lw.outputs["Facing"], 2.0), st["rim"])
    bsdf = n.new("ShaderNodeBsdfPrincipled")
    n.link(base, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0 - 0.95 * st["gloss"]
    bsdf.inputs["Coat Weight"].default_value = 0.5 * st["gloss"]
    n.link(color, bsdf.inputs["Emission Color"])
    n.link(n.math("ADD", strength, rim), bsdf.inputs["Emission Strength"])
    tr = n.new("ShaderNodeBsdfTransparent")
    mix = n.new("ShaderNodeMixShader")
    n.link(n.math("MULTIPLY", vis, st["opacity"]), mix.inputs[0])
    n.link(tr, mix.inputs[1])
    n.link(bsdf, mix.inputs[2])
    n.link(mix, out.inputs[0])
    return m


def clipped_emission(name, color, strength, sc):
    """A flat emissive surface that only shows above the hit line (bar lines, finger numbers)."""
    m, nt, out = _mat(name)
    m.surface_render_method = "DITHERED"
    n = Nodes(nt)
    vis, _ = clip_factor(n, sc)
    em = n.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = color
    em.inputs["Strength"].default_value = strength
    if strength <= 0:
        em = n.new("ShaderNodeBsdfDiffuse")
        em.inputs["Color"].default_value = color
    tr = n.new("ShaderNodeBsdfTransparent")
    mix = n.new("ShaderNodeMixShader")
    n.link(vis, mix.inputs[0])
    n.link(tr, mix.inputs[1])
    n.link(em, mix.inputs[2])
    n.link(mix, out.inputs[0])
    return m


def glow_material(sc, st):
    """
    The glow over a pressed key: the object's colour, its alpha the
    strength. As flames: a flickering tongue of fire rising from the key
    (up the wall, or along the runway: the card's generated y).
    """
    m, nt, out = _mat("Glow")
    m.surface_render_method = "DITHERED"
    n = Nodes(nt)
    info = n.new("ShaderNodeObjectInfo")
    g = n.xyz(n.new("ShaderNodeTexCoord").outputs["Generated"])
    gz = g.outputs[sc.axis]
    up = n.math("SUBTRACT", 1.0, gz)
    side = n.math("MULTIPLY", n.math("PINGPONG", g.outputs["X"], 0.5), 2.0)
    if st["hit_flames"]:
        t = time_node(n, sc)
        obj = n.new("ShaderNodeObjectInfo")
        v = n.new("ShaderNodeCombineXYZ")
        n.link(n.math("MULTIPLY", g.outputs["X"], 2.5), v.inputs["X"])
        n.link(n.math("ADD", n.math("MULTIPLY", gz, 2.0), n.math("MULTIPLY", t, -2.4 * st["fire_speed"])),
               v.inputs["Y"])
        n.link(n.math("MULTIPLY", obj.outputs["Random"], 50.0), v.inputs["Z"])
        noise = n.new("ShaderNodeTexNoise", noise_dimensions="4D")
        n.link(v, noise.inputs["Vector"])
        n.link(n.math("MULTIPLY", t, 1.3 * st["fire_speed"]), noise.inputs["W"])
        noise.inputs["Scale"].default_value = 2.2
        noise.inputs["Detail"].default_value = 4.0
        # a tongue: wide and hot at the base, narrowing upward, torn by the noise
        width = n.math("SUBTRACT", side, n.math("MULTIPLY", gz, 0.75))
        shape = n.math("MULTIPLY", n.math("POWER", n.math("MAXIMUM", up, 0.0), 1.3), n.math("MAXIMUM", width, 0.0))
        flame = n.math("MULTIPLY_ADD", n.math("SUBTRACT", noise.outputs["Fac"], 0.5), 1.2, shape)
        flame = n.math("MULTIPLY", n.math("MAXIMUM", n.math("MINIMUM", n.math("MULTIPLY", flame, 1.6), 1.0), 0.0),
                       info.outputs["Alpha"])
        ramp = fire_ramp(n)
        n.link(flame, ramp.inputs["Fac"])
        color = n.mix_rgb(0.35, ramp.outputs["Color"], info.outputs["Color"], blend="MULTIPLY")
        f = flame
    else:
        f = n.math("MULTIPLY", n.math("MULTIPLY", n.math("POWER", up, 2.5), side), info.outputs["Alpha"])
        color = info.outputs["Color"]
    em = n.new("ShaderNodeEmission")
    n.link(color, em.inputs["Color"])
    em.inputs["Strength"].default_value = st["hit_glow"]
    tr = n.new("ShaderNodeBsdfTransparent")
    add = n.new("ShaderNodeAddShader")
    n.link(em, add.inputs[0])
    n.link(tr, add.inputs[1])
    mix = n.new("ShaderNodeMixShader")
    n.link(n.math("MINIMUM", f, 1.0), mix.inputs[0])
    n.link(tr, mix.inputs[1])
    n.link(add, mix.inputs[2])
    n.link(mix, out.inputs[0])
    return m


def spark_material(st, name="Sparks", whiten=None, glow=None):
    whiten = st["spark_whiten"] if whiten is None else whiten
    glow = st["spark_glow"] if glow is None else glow
    m, nt, out = _mat(name)
    n = Nodes(nt)
    col = n.attr("col", "INSTANCER").outputs["Color"]
    fade = n.attr("fade", "INSTANCER").outputs["Fac"]
    em = n.new("ShaderNodeEmission")
    n.link(n.mix_rgb(whiten, col, (1, 0.95, 0.85, 1)), em.inputs["Color"])
    n.link(n.math("MULTIPLY", fade, glow), em.inputs["Strength"])
    n.link(em, out.inputs[0])
    return m


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def box_mesh(name, x0, x1, y0, y1, z0, z1, taper=0.0):
    """A box; `taper` narrows the top face on each side (black keys)."""
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0 + taper, y0 + taper * 2, z1), (x1 - taper, y0 + taper * 2, z1), (x1 - taper, y1, z1), (x0 + taper, y1, z1)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(v, [], f)
    return me


BOX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def boxes_mesh(name, boxes):
    """One mesh of many boxes [(x0, x1, y0, y1, z0, z1)]."""
    verts, faces = [], []
    for x0, x1, y0, y1, z0, z1 in boxes:
        b = len(verts)
        verts += [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        faces += [tuple(b + i for i in f) for f in BOX_FACES]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    return me


def add_obj(name, me, parent=None, mats=()):
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    for m in mats:
        ob.data.materials.append(m)
    if parent:
        ob.parent = parent
    return ob


def bevel(ob, width, segments=2, angle=True):
    mod = ob.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE" if angle else "NONE"
    return mod


# --------------------------------------------------------------------------- #
# Scene parts
# --------------------------------------------------------------------------- #
class Scene:
    def __init__(self, data, style):
        self.d = data
        self.st = style
        self.fps = data["fps"]
        self.t0 = data["t0"]
        self.n = data["frames"]
        kb = data["keyboard"]
        self.kb = kb
        self.L = kb["white_length"]
        self.travel = kb["travel"]
        self.rise = kb["black_rise"]
        self.keys = {k["pitch"]: k for k in kb["keys"]}
        self.root = bpy.data.objects.new("Root", None)
        bpy.context.scene.collection.objects.link(self.root)
        self.root.location.x = -kb["width"] / 2
        self.key_obj = {}
        self.speed = style["note_speed"]
        # the notes run up an upright wall behind the keys, or along a flat
        # runway toward them (bird's-eye view): `axis` is the direction they
        # run in, `hit` where they meet the keys, `top` the far end
        self.floor = style["layout"] == "floor"
        self.axis = "Y" if self.floor else "Z"
        self.hit = self.L + style["hit_gap"] if self.floor else HIT_Z
        self.top = self.hit + self.speed * style["note_window"]
        self.y_wall = self.L + 0.035
        self.colors = [note_color(style, n, self.keys[n["pitch"]]["black"]) if n["pitch"] in self.keys else
                       (255, 255, 255) for n in data["notes"]]

    def frame(self, t):
        return (t - self.t0) * self.fps + 1

    def is_shown(self, ni):
        """Whether note ni is drawn (see _decide_shown; all of them before the keys are animated)."""
        shown = getattr(self, "shown", None)
        return shown is None or shown[ni]

    def lane(self, k):
        """(x0, x1) of a key's note bar."""
        w = k["x1"] - k["x0"]
        share = min(1.0, self.st["note_width"] + 0.16) if k["black"] else self.st["note_width"]
        cx = (k["x0"] + k["x1"]) / 2
        return cx - w * share / 2, cx + w * share / 2

    def scroll(self, ob):
        """Move a roll object toward the keys with the song: at song time t it has moved t * speed."""
        t1 = self.t0 + self.n / self.fps
        set_keys(ob, "location", 1 if self.floor else 2, [self.frame(self.t0), self.frame(t1)],
                 [-self.t0 * self.speed, -t1 * self.speed])

    def run_pos(self, t):
        """Where along the run (in the roll's frame) a note starting at song time t begins."""
        return self.hit + t * self.speed

    def note_box(self, x0, x1, a, b, black):
        """A note's box from run positions a to b (black-key notes a little nearer the viewer)."""
        depth, lift = self.st["note_depth"], (0.003 if black else 0.0)
        if self.floor:
            return (x0, x1, a, b, RUN_Z + lift, RUN_Z + lift + depth)
        return (x0, x1, self.y_wall - depth - lift, self.y_wall - lift, a, b)

    def on_face(self, x, a, black, eps=0.0004):
        """A point just off a note's face (toward the viewer) at run position a."""
        depth, lift = self.st["note_depth"], (0.003 if black else 0.0)
        if self.floor:
            return (x, a, RUN_Z + lift + depth + eps)
        return (x, self.y_wall - depth - lift - eps, a)

    # ----- keyboard -----------------------------------------------------------
    def build_keyboard(self):
        L, root = self.L, self.root
        white = key_material("KeyWhite", srgb(self.st["white_key_color"]), 0.25)
        black = key_material("KeyBlack", srgb(self.st["black_key_color"]), 0.3)
        pivot_y = L + PIVOT_BACK
        for p, k in self.keys.items():
            if k["black"]:
                y0 = L - k["length"]
                me = box_mesh(f"key{p}", k["x0"], k["x1"], y0 - pivot_y, L - pivot_y,
                              self.rise - BLACK_H, self.rise, taper=0.0012)
                ob = add_obj(f"Key{p}", me, root, [black])
                bevel(ob, 0.0012)
            else:
                me = box_mesh(f"key{p}", k["x0"] + KEY_GAP / 2, k["x1"] - KEY_GAP / 2, -pivot_y, L - pivot_y,
                              -WHITE_H, 0.0)
                ob = add_obj(f"Key{p}", me, root, [white])
                bevel(ob, 0.0009)
            ob.location = (0.0, pivot_y, 0.0)
            ob.color = (1, 1, 1, 0)
            self.key_obj[p] = ob
        self._build_case()

    def _build_case(self):
        L, w, root, st = self.L, self.kb["width"], self.root, self.st
        case = principled("Case", srgb(st["case_color"]), rough=0.05 + 0.6 * (1.0 - st["case_gloss"]),
                          coat=st["case_gloss"])
        parts = [
            # keybed under the keys, key slip in front, cheeks at the ends
            ("Keybed", (-0.06, w + 0.06, -0.06, L + 0.02, -0.06, -WHITE_H - 0.004)),
            ("KeySlip", (-0.06, w + 0.06, -0.035, -0.006, -0.075, -0.012)),
            ("CheekL", (-0.06, -0.004, -0.035, L + 0.02, -0.075, 0.03)),
            ("CheekR", (w + 0.004, w + 0.06, -0.035, L + 0.02, -0.075, 0.03)),
            # behind the keys: a nameboard whose top edge the notes meet, or a
            # low back rail up to the runway
            ("Nameboard", (-0.06, w + 0.06, L + 0.004, max(self.hit, L + 0.005) if self.floor else L + 0.045, -0.02,
                           RUN_Z - 0.0015 if self.floor else HIT_Z - 0.002)),
        ]
        for name, (x0, x1, y0, y1, z0, z1) in parts:
            ob = add_obj(name, box_mesh(name, x0, x1, y0, y1, z0, z1), root, [case])
            bevel(ob, 0.003, 3)
        if st["felt_strip"]:
            felt = principled("Felt", srgb(st["felt_color"]), rough=0.95, spec=0.1)
            add_obj("FeltStrip", box_mesh("felt", 0, w, L + 0.001, L + 0.004, -0.004, 0.002), root, [felt])
        # the wall behind the notes, with faint lanes at each C and F
        wall = principled("Wall", srgb(st["wall_color"]), rough=0.7 - 0.66 * st["runway_gloss"],
                          coat=0.6 * st["runway_gloss"], spec=0.5 + 0.5 * st["runway_gloss"])
        if self.floor:
            add_obj("NoteWall", box_mesh("wall", -0.06, w + 0.06, self.hit, self.top + 0.3, RUN_Z - 0.004,
                                         RUN_Z - 0.001), root, [wall])
        else:
            add_obj("NoteWall", box_mesh("wall", -0.06, w + 0.06, L + 0.045, L + 0.05, HIT_Z - 0.002, self.top + 0.05),
                    root, [wall])
        if st["lane_lines"] > 0:
            xs = [k["x0"] for p, k in self.keys.items() if not k["black"] and p % 12 in (0, 5)]
            if self.floor:
                me = boxes_mesh("lanes", [(x - 0.0004, x + 0.0004, self.hit, self.top + 0.3, RUN_Z - 0.001,
                                           RUN_Z - 0.0008) for x in xs])
            else:
                me = boxes_mesh("lanes", [(x - 0.0004, x + 0.0004, L + 0.0446, L + 0.0449, HIT_Z, self.top + 0.05)
                                          for x in xs])
            lines = principled("LaneLines", srgb((60, 60, 75)), rough=0.8, emit=srgb((90, 90, 110)),
                               strength=st["lane_lines"] * 0.8)
            add_obj("LaneLines", me, root, [lines])

    # ----- keys going down ----------------------------------------------------
    def animate_keys(self, tips):
        """
        Each key goes down as far as the fingertip playing it pushes it
        (tips[hand][finger] -> [(x, y, z)] per frame, export coordinates), so
        key and finger stay in contact; it takes its note's colour.
        """
        L, fps, travel = self.L, self.fps, self.travel
        pivot_y = L + PIVOT_BACK
        down = {p: {} for p in self.keys}        # pitch -> {frame index: (drop fraction, note index, depth on key)}
        reached = {}                             # note index -> deepest its key went (frames inside the export)
        for ni, n in enumerate(self.d["notes"]):
            p, f, h = n["pitch"], n["finger"], n["hand"]
            k = self.keys.get(p)
            if k is None:
                continue
            top = self.rise if k["black"] else 0.0
            i0 = max(0, int((n["press"] - 0.08 - self.t0) * fps))
            i1 = min(self.n - 1, int((n["release"] + 0.2 - self.t0) * fps) + 1)
            for i in range(i0, i1 + 1):
                t = self.t0 + i / fps
                if f and h in tips:
                    x, y, z = tips[h][f][i]
                    on = k["x0"] - 0.004 < x < k["x1"] + 0.004
                    yk = y
                    dep = min(travel, max(0.0, top - z)) if on else 0.0
                    if t > n["release"] + 0.06:
                        dep = 0.0                 # never held down by a finger left resting on it
                else:
                    yk = L - 0.6 * k["length"]
                    a = min(1.0, max(0.0, (t - n["press"]) / 0.035))
                    r = min(1.0, max(0.0, (n["release"] + 0.04 - t) / 0.04))
                    dep = travel * min(a, r)
                d = dep / travel
                if f and h in tips and t <= n["release"]:
                    reached[ni] = max(reached.get(ni, 0.0), d)
                if d > down[p].get(i, (0.0,))[0]:
                    down[p][i] = (d, ni, yk)
        tint = self.st["key_tint"]
        for p, ob in self.key_obj.items():
            frames = down[p]
            if not frames:
                continue
            idx = sorted(frames)
            # pad with rest frames around each run
            fr, ang, alpha = [], [], []
            prev = None
            for i in idx:
                if prev is not None and i - prev > 1:
                    fr += [prev + 2, i - 1]
                    ang += [0.0, 0.0]
                    alpha += [0.0, 0.0]
                elif prev is None and i > 0:
                    fr.append(i - 1); ang.append(0.0); alpha.append(0.0)
                d, _, yk = frames[i]
                fr.append(i)
                ang.append(math.asin(min(1.0, d * travel / (pivot_y - yk))))
                alpha.append(min(1.0, d * 1.5))
                prev = i
            fr.append(prev + 2); ang.append(0.0); alpha.append(0.0)
            fr = [i + 1 for i in fr]
            set_keys(ob, "rotation_euler", 0, fr, ang)
            set_keys(ob, "color", 3, fr, [a * tint for a in alpha])
            # the playing note's colour, switched at each strike
            cf, cols = [], []
            last = None
            for i in idx:
                ni = frames[i][1]
                if ni != last:
                    cf.append(i + 1)
                    cols.append(srgb(self.colors[ni]))
                    last = ni
            for c in range(3):
                set_keys(ob, "color", c, cf, [col[c] for col in cols], interp="CONSTANT")
        self.down = down
        self._decide_shown(reached)

    def _decide_shown(self, reached):
        """
        Which notes are drawn (with their sparks): with only_pressed, those
        whose finger gets the key down (export3d.PRESSED_DOWN) - from the
        export's "pressed" when it has one (previews), else from the keys'
        motion here, for notes whose strike lies inside the export.
        """
        t_end = self.t0 + (self.n - 1) / self.fps
        self.shown = []
        for ni, n in enumerate(self.d["notes"]):
            ok = True
            if self.st["only_pressed"]:
                if "pressed" in n:
                    ok = n["pressed"]
                elif self.t0 <= n["press"] + 0.06 <= t_end:
                    ok = reached.get(ni, 0.0) >= 0.4
            self.shown.append(ok)
        hidden = self.shown.count(False)
        if hidden:
            print(f"notes not pressed (hidden): {hidden}")

    # ----- falling notes ------------------------------------------------------
    def build_notes(self):
        """
        All note bars in one mesh on a vertical wall behind the keys,
        scrolled toward the keys (meeting them at `hit` at
        the note's start), with the bar lines and finger numbers that fall
        with them, and the glow over pressed keys.
        """
        st, v = self.st, self.speed
        yw = self.y_wall
        depth = st["note_depth"]
        boxes, cols, lo, hi, vel = [], [], [], [], []
        for ni, n in enumerate(self.d["notes"]):
            k = self.keys.get(n["pitch"])
            if k is None or not self.is_shown(ni):
                continue
            x0, x1 = self.lane(k)
            z0 = self.run_pos(n["start"])
            z1 = max(z0 + 0.003, self.run_pos(n["end"]) - st["note_gap"])
            boxes.append(self.note_box(x0, x1, z0, z1, k["black"]))
            cols.append(srgb(self.colors[ni]))
            lo.append(z0)
            hi.append(z1)
            vel.append(n["velocity"] / 127.0)
        me = boxes_mesh("notes", boxes)
        for name, vals in (("lo", lo), ("hi", hi), ("vel", vel)):
            a = me.attributes.new(name, "FLOAT", "POINT")
            a.data.foreach_set("value", [x for x in vals for _ in range(8)])
        ca = me.color_attributes.new("col", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", [c for col in cols for _ in range(8) for c in col])
        ob = add_obj("Notes", me, self.root, [note_material(self, st)])
        ob.visible_shadow = False
        if st["note_round"] > 0:
            r = st["note_round"] * min(depth / 2, 0.006)
            mod = bevel(ob, max(0.0002, r), 3, angle=False)
            mod.use_clamp_overlap = True
        if st["note_outline"]:
            # only the edges: glowing struts round each note, hollow inside
            wire = ob.modifiers.new("Outline", "WIREFRAME")
            wire.thickness = st["outline_width"]
            wire.use_even_offset = True
            wire.use_relative_offset = False
        self.scroll(ob)
        self.notes_obj = ob
        if st["bar_lines"] > 0 and self.d.get("bars"):
            w = self.kb["width"]
            if self.floor:
                bars = boxes_mesh("bars", [(0, w, self.run_pos(b), self.run_pos(b) + 0.0008, RUN_Z - 0.0009,
                                            RUN_Z - 0.0006) for b in self.d["bars"]])
            else:
                bars = boxes_mesh("bars", [(0, w, yw + 0.001, yw + 0.0015, self.run_pos(b), self.run_pos(b) + 0.0008)
                                           for b in self.d["bars"]])
            bl = add_obj("BarLines", bars, self.root,
                         [clipped_emission("BarLine", srgb((120, 120, 140)), st["bar_lines"] * 1.5, self)])
            bl.visible_shadow = False
            self.scroll(bl)
        if st["finger_numbers"]:
            self._build_finger_numbers()
        self._build_glow()

    def _build_glow(self):
        st = self.st
        gm = glow_material(self, st)
        h = st["hit_glow_height"]
        wide = 3.4 if st["hit_flames"] else 1.6
        for p, k in self.keys.items():
            w = k["x1"] - k["x0"]
            gw = w * wide
            cx = (k["x0"] + k["x1"]) / 2
            me = bpy.data.meshes.new(f"glow{p}")
            if self.floor:
                z = RUN_Z + st["note_depth"] + 0.004
                quad = [(cx - gw / 2, self.hit - 0.004, z), (cx + gw / 2, self.hit - 0.004, z),
                        (cx + gw / 2, self.hit + h, z), (cx - gw / 2, self.hit + h, z)]
            else:
                y = self.y_wall - st["note_depth"] - 0.006
                quad = [(cx - gw / 2, y, HIT_Z - 0.004), (cx + gw / 2, y, HIT_Z - 0.004),
                        (cx + gw / 2, y, HIT_Z + h), (cx - gw / 2, y, HIT_Z + h)]
            me.from_pydata(quad, [], [(0, 1, 2, 3)])
            g = add_obj(f"Glow{p}", me, self.root, [gm])
            g.color = (1, 1, 1, 0)
            g.visible_shadow = False
            key = self.key_obj[p]
            if key.animation_data:
                # same alpha and colour curves as the key (alpha without the key tint)
                for c in range(4):
                    src = fcurve(key, "color", c)
                    pts = [kp.co[:] for kp in src.keyframe_points]
                    vals = [q[1] for q in pts]
                    if c == 3 and st["key_tint"] > 0:
                        vals = [x / st["key_tint"] for x in vals]
                    set_keys(g, "color", c, [q[0] for q in pts], vals, interp="CONSTANT" if c < 3 else "LINEAR")

    def _build_finger_numbers(self):
        """Dark finger numbers near each note's bottom (notes tall enough to hold one), instanced by geometry nodes."""
        st, v = self.st, self.speed
        digits = bpy.data.collections.new("Digits")
        mat = clipped_emission("Digit", srgb((15, 15, 20)), 0.0, self)
        for dgt in range(1, 6):
            cu = bpy.data.curves.new(f"d{dgt}", "FONT")
            cu.body = str(dgt)
            cu.align_x, cu.align_y = "CENTER", "BOTTOM"
            tmp = bpy.data.objects.new("tmp", cu)
            bpy.context.scene.collection.objects.link(tmp)
            dg = bpy.context.evaluated_depsgraph_get()
            me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
            bpy.data.objects.remove(tmp)
            if not self.floor:                         # face the viewer from the wall
                me.transform(__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
            me.materials.append(mat)
            ob = bpy.data.objects.new(f"Digit{dgt}", me)
            digits.objects.link(ob)
        pts, dig, size = [], [], []
        for ni, n in enumerate(self.d["notes"]):
            k = self.keys.get(n["pitch"])
            if k is None or not n["finger"] or not self.is_shown(ni):
                continue
            x0, x1 = self.lane(k)
            s = min(0.75 * (x1 - x0), 0.012)
            if (n["end"] - n["start"]) * v < 1.4 * s:
                continue
            pts.append(self.on_face((x0 + x1) / 2, self.run_pos(n["start"]) + 0.15 * s, k["black"]))
            dig.append(float(n["finger"] - 1))
            size.append(s)
        me = bpy.data.meshes.new("fingers")
        me.from_pydata(pts, [], [])
        for name, vals in (("digit", dig), ("size", size)):
            a = me.attributes.new(name, "FLOAT", "POINT")
            a.data.foreach_set("value", vals)
        ob = add_obj("FingerNumbers", me, self.root)
        ng = bpy.data.node_groups.new("FingerNumbers", "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        n = Nodes(ng)
        gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
        ci = n.new("GeometryNodeCollectionInfo")
        ci.inputs["Collection"].default_value = digits
        ci.inputs["Separate Children"].default_value = True
        ci.inputs["Reset Children"].default_value = True
        inst = n.new("GeometryNodeInstanceOnPoints")
        n.link(gi.outputs[0], inst.inputs["Points"])
        n.link(ci, inst.inputs["Instance"])
        inst.inputs["Pick Instance"].default_value = True
        d = n.new("GeometryNodeInputNamedAttribute", data_type="FLOAT")
        d.inputs["Name"].default_value = "digit"
        f2i = n.new("FunctionNodeFloatToInt")
        n.link(d, f2i.inputs[0])
        n.link(f2i, inst.inputs["Instance Index"])
        s = n.new("GeometryNodeInputNamedAttribute", data_type="FLOAT")
        s.inputs["Name"].default_value = "size"
        n.link(s, inst.inputs["Scale"])
        n.link(inst, go.inputs[0])
        mod = ob.modifiers.new("Numbers", "NODES")
        mod.node_group = ng
        ob.visible_shadow = False
        self.scroll(ob)

    # ----- sparks ---------------------------------------------------------------
    def build_sparks(self):
        """
        Sparks thrown up where notes meet the keys: a burst at each strike and
        a trickle while the key is held. Every spark's birth, start, velocity,
        lifetime and size are drawn here (seeded, so renders repeat); a
        geometry-nodes modifier moves the live ones each frame (drag, and a
        pull up or down) and draws each as a glowing streak along its motion.
        """
        st = self.st
        burst, trickle = st["sparks"], st["spark_trickle"]
        if burst <= 0 and trickle <= 0:
            return
        sp, life, size = st["spark_speed"], st["spark_life"], st["spark_size"]
        rng = random.Random(7)
        pts, attrs = [], {k: [] for k in ("birth", "life", "size", "phase", "vx", "vy", "vz")}
        cols = []
        t_end = self.t0 + self.n / self.fps
        for ni, n in enumerate(self.d["notes"]):
            k = self.keys.get(n["pitch"])
            if k is None or n["release"] < self.t0 - 3 or n["press"] > t_end or not self.is_shown(ni):
                continue
            x0, x1 = self.lane(k)
            loud = 0.5 + n["velocity"] / 127.0
            births = [n["press"] + rng.uniform(0.0, 0.05) for _ in range(int(round(burst * loud)))]
            held = max(0.0, n["release"] - n["press"])
            births += [n["press"] + rng.uniform(0.0, held) for _ in range(int(trickle * held))]
            col = srgb(self.colors[ni])
            for b in births:
                x = rng.uniform(x0, x1)
                s = sp * rng.uniform(0.45, 1.15) * (1.2 if b - n["press"] < 0.06 else 0.6)
                attrs["vx"].append(s * rng.uniform(-0.45, 0.45))
                if self.floor:                     # up off the runway, and along it away from the keys
                    pts.append(self.on_face(x, self.hit + rng.uniform(0.0, 0.004), k["black"], 0.002))
                    attrs["vy"].append(s * rng.uniform(0.05, 0.6))
                    attrs["vz"].append(s * rng.uniform(0.5, 1.0))
                else:                              # up the wall, and out toward the viewer
                    pts.append(self.on_face(x, HIT_Z + rng.uniform(0.0, 0.004), k["black"], 0.004))
                    attrs["vy"].append(-s * rng.uniform(0.0, 0.35))
                    attrs["vz"].append(s * rng.uniform(0.6, 1.0))
                attrs["birth"].append(b)
                attrs["life"].append(life * rng.uniform(0.45, 1.0))
                attrs["size"].append(size * rng.uniform(0.5, 1.25))
                attrs["phase"].append(rng.uniform(0.0, 6.283))
                cols.append(col)
            if len(pts) > MAX_SPARKS:
                break
        me = bpy.data.meshes.new("sparks")
        me.from_pydata(pts, [], [])
        for name, vals in attrs.items():
            a = me.attributes.new(name, "FLOAT", "POINT")
            a.data.foreach_set("value", vals)
        ca = me.color_attributes.new("col", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", [c for col in cols for c in col])
        ob = add_obj("Sparks", me, self.root)
        ob.visible_shadow = False
        mod = ob.modifiers.new("Sparks", "NODES")
        mod.node_group = self._spark_nodes(spark_material(st))
        print(f"sparks: {len(pts)}")

    def _spark_nodes(self, mat, name="Sparks", drag=2.0, pull=None, stretch=None, wander=0.012):
        """
        Geometry nodes for particles drawn once in Python (points with birth,
        life, size, phase, vx/vy/vz): the live ones each frame, moved with drag
        and a pull up or down, wandering sideways, flickering, and drawn as
        glowing streaks along their motion (stretch 0: round).
        """
        st = self.st
        pull = st["spark_float"] * 1.6 if pull is None else pull
        stretch = 14.0 * st["spark_stretch"] if stretch is None else stretch
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        n = Nodes(ng)
        gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")

        def attr(name):
            a = n.new("GeometryNodeInputNamedAttribute", data_type="FLOAT")
            a.inputs["Name"].default_value = name
            return a.outputs["Attribute"]
        frame = n.new("GeometryNodeInputSceneTime").outputs["Frame"]
        t = n.math("ADD", n.math("DIVIDE", n.math("SUBTRACT", frame, 1.0), float(self.fps)), self.t0)
        age = n.math("SUBTRACT", t, attr("birth"))
        life = attr("life")
        dead = n.math("MAXIMUM", n.math("LESS_THAN", age, 0.0), n.math("GREATER_THAN", age, life))
        dele = n.new("GeometryNodeDeleteGeometry", domain="POINT")
        n.link(gi.outputs[0], dele.inputs["Geometry"])
        n.link(dead, dele.inputs["Selection"])
        # drag k: travelled = v (1 - e^-k age) / k; pull a: 0.5 a age^2 (up for a > 0)
        k, a = max(0.05, drag), pull
        e = n.math("EXPONENT", n.math("MULTIPLY", age, -k))
        reach = n.math("DIVIDE", n.math("SUBTRACT", 1.0, e), k)
        vel = n.new("ShaderNodeCombineXYZ")
        for axis, name in zip("XYZ", ("vx", "vy", "vz")):
            n.link(attr(name), vel.inputs[axis])
        # a slow sideways wander, like embers in rising air
        wob = n.math("MULTIPLY", n.math("SINE", n.math("MULTIPLY_ADD", age, 7.0, attr("phase"))),
                     n.math("MULTIPLY", age, wander))
        off = n.vmath("SCALE", vel, scale=reach)
        drift = n.new("ShaderNodeCombineXYZ")
        n.link(wob, drift.inputs["X"])
        n.link(n.math("MULTIPLY", n.math("MULTIPLY", age, age), 0.5 * a), drift.inputs["Z"])
        setp = n.new("GeometryNodeSetPosition")
        n.link(dele, setp.inputs["Geometry"])
        n.link(n.vmath("ADD", off, drift), setp.inputs["Offset"])
        # brightness: fading with age, flickering
        fade = n.math("SUBTRACT", 1.0, n.math("DIVIDE", age, life))
        flick = n.math("MULTIPLY_ADD", n.math("SINE", n.math("MULTIPLY_ADD", age, 31.0, n.math("MULTIPLY", attr("phase"), 5.0))),
                       0.3, 0.7)
        store = n.new("GeometryNodeStoreNamedAttribute", data_type="FLOAT", domain="POINT")
        store.inputs["Name"].default_value = "fade"
        n.link(setp, store.inputs["Geometry"])
        n.link(n.math("MULTIPLY", fade, flick), store.inputs["Value"])
        # streak along the current velocity
        cur = n.vmath("ADD", n.vmath("SCALE", vel, scale=e), n.new("ShaderNodeCombineXYZ"))
        cz = cur.inputs[1].links[0].from_node
        n.link(n.math("MULTIPLY", age, a), cz.inputs["Z"])
        align = n.new("FunctionNodeAlignRotationToVector", axis="Z")
        n.link(cur, align.inputs["Vector"])
        r = n.math("MULTIPLY", attr("size"), n.math("SQRT", n.math("MAXIMUM", fade, 0.0)))
        sc = n.new("ShaderNodeCombineXYZ")
        n.link(r, sc.inputs["X"])
        n.link(r, sc.inputs["Y"])
        speed = n.vmath("LENGTH", cur)
        n.link(n.math("MULTIPLY", r, n.math("MULTIPLY_ADD", speed.outputs["Value"], stretch, 1.0)), sc.inputs["Z"])
        ico = n.new("GeometryNodeMeshIcoSphere")
        ico.inputs["Radius"].default_value = 1.0
        ico.inputs["Subdivisions"].default_value = 1
        setm = n.new("GeometryNodeSetMaterial")
        setm.inputs["Material"].default_value = mat
        n.link(ico, setm.inputs["Geometry"])
        inst = n.new("GeometryNodeInstanceOnPoints")
        n.link(store, inst.inputs["Points"])
        n.link(setm, inst.inputs["Instance"])
        n.link(align, inst.inputs["Rotation"])
        n.link(sc, inst.inputs["Scale"])
        n.link(inst, go.inputs[0])
        return ng

    def build_dust(self):
        """
        Motes floating in the air over the keys and the notes all through the
        video: born at random times and places, drifting slowly up, flickering
        (the same particle nodes as the sparks, with no drag and no streaks).
        """
        st = self.st
        amount = st["dust"]
        if amount <= 0:
            return
        rng = random.Random(11)
        w = self.kb["width"]
        t0, t1 = self.t0 - 8.0, self.t0 + self.n / self.fps
        count = int(amount * 40 * (t1 - t0))
        life = st["dust_life"]
        col = srgb(st["dust_color"])
        y0, y1 = (-0.25, self.top) if self.floor else (-0.25, self.L + 0.03)
        pts, attrs = [], {k: [] for k in ("birth", "life", "size", "phase", "vx", "vy", "vz")}
        for _ in range(min(count, MAX_SPARKS)):
            pts.append((rng.uniform(-0.1, w + 0.1), rng.uniform(y0, y1), rng.uniform(0.01, 0.25)))
            attrs["birth"].append(rng.uniform(t0, t1))
            attrs["life"].append(life * rng.uniform(0.6, 1.0))
            attrs["size"].append(st["dust_size"] * rng.uniform(0.4, 1.2))
            attrs["phase"].append(rng.uniform(0.0, 6.283))
            attrs["vx"].append(rng.uniform(-0.006, 0.006))
            attrs["vy"].append(rng.uniform(-0.006, 0.006))
            attrs["vz"].append(rng.uniform(0.004, 0.02) * st["dust_rise"])
        me = bpy.data.meshes.new("dust")
        me.from_pydata(pts, [], [])
        for name, vals in attrs.items():
            a = me.attributes.new(name, "FLOAT", "POINT")
            a.data.foreach_set("value", vals)
        ca = me.color_attributes.new("col", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", list(col) * len(pts))
        ob = add_obj("Dust", me, self.root)
        ob.visible_shadow = False
        mat = spark_material(st, "DustMat", 0.2, st["dust_glow"])
        ob.modifiers.new("Dust", "NODES").node_group = self._spark_nodes(mat, "Dust", drag=0.05, pull=0.0,
                                                                         stretch=0.0, wander=0.004)

    # ----- light from the notes on the hands -------------------------------------
    def build_note_lights(self):
        """A light per hand over the keys it holds down, in their colours, as bright as how many are down."""
        k_pow = self.st["note_lights"]
        if k_pow <= 0 or not hasattr(self, "down"):
            return
        notes = self.d["notes"]
        per = {}                                   # hand -> frame -> [weight, x*w, r*w, g*w, b*w]
        for p, frames in self.down.items():
            k = self.keys[p]
            cx = (k["x0"] + k["x1"]) / 2
            for i, (dd, ni, _) in frames.items():
                h = notes[ni]["hand"]
                c = srgb(self.colors[ni])
                acc = per.setdefault(h, {}).setdefault(i, [0.0, 0.0, 0.0, 0.0, 0.0])
                acc[0] += dd
                acc[1] += dd * cx
                for j in range(3):
                    acc[2 + j] += dd * c[j]
        self._build_runway_light()
        for h, frames in per.items():
            ld = bpy.data.lights.new(f"NoteLight{h}", "POINT")
            ld.shadow_soft_size = 0.03
            ob = bpy.data.objects.new(f"NoteLight{h}", ld)
            bpy.context.scene.collection.objects.link(ob)
            ob.parent = self.root
            ob.location = (0, self.L + 0.02, (RUN_Z if self.floor else HIT_Z) + 0.03)
            idx = list(range(self.n))
            xs, en, cs = [], [], [[], [], []]
            last_x = None
            for i in idx:
                acc = frames.get(i)
                if acc and acc[0] > 1e-3:
                    x = acc[1] / acc[0]
                    last_x = x
                    xs.append(x)
                    en.append(k_pow * 6.0 * min(4.0, acc[0]))
                    m = max(acc[2], acc[3], acc[4], 1e-6)
                    for j in range(3):
                        cs[j].append(acc[2 + j] / m)
                else:
                    xs.append(last_x if last_x is not None else self.kb["width"] / 2)
                    en.append(0.0)
                    for j in range(3):
                        cs[j].append(cs[j][-1] if cs[j] else 1.0)
            # light falls off over a few frames instead of snapping
            for i in range(1, len(en)):
                en[i] = max(en[i], en[i - 1] * 0.75)
            fr = [i + 1 for i in idx]
            set_keys(ob, "location", 0, fr, xs)
            set_keys(ld, "energy", 0, fr, en)
            for j in range(3):
                set_keys(ld, "color", j, fr, cs[j])

    def _build_runway_light(self):
        """
        The notes about to arrive glow on the hands too: a wide soft light
        just behind the keys, facing the player, as bright as how much note
        is within NEAR_T of the hit line, in those notes' colours.
        """
        NEAR_T = 0.8
        notes = self.d["notes"]
        acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(self.n)]
        for ni, n in enumerate(notes):
            if not self.is_shown(ni):
                continue
            c = srgb(self.colors[ni])
            i0 = max(0, int((n["start"] - NEAR_T - self.t0) * self.fps))
            i1 = min(self.n - 1, int((n["end"] - self.t0) * self.fps))
            for i in range(i0, i1 + 1):
                t = self.t0 + i / self.fps
                # how much of this note is in [t, t + NEAR_T], nearer counting more
                a, b = max(n["start"], t), min(n["end"], t + NEAR_T)
                if b <= a:
                    continue
                w = (b - a) / NEAR_T * (1.0 - (a - t) / NEAR_T * 0.6)
                acc[i][0] += w
                for j in range(3):
                    acc[i][1 + j] += w * c[j]
        ld = bpy.data.lights.new("RunwayLight", "AREA")
        ld.shape = "RECTANGLE"
        ld.size, ld.size_y = self.kb["width"], 0.06
        ob = bpy.data.objects.new("RunwayLight", ld)
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = self.root
        z = (RUN_Z if self.floor else HIT_Z) + 0.045
        ob.location = (self.kb["width"] / 2, self.L + 0.03, z)
        # facing the player and tipped a little up: aimed down it lit a band of the floor just past
        # the keys, which read as a translucent bar along the keyboard
        ob.rotation_euler = (math.radians(-97), 0, 0)
        ld.spread = math.radians(100)
        k = self.st["note_lights"]
        en, cs = [], [[], [], []]
        for a in acc:
            en.append(k * 0.9 * min(6.0, a[0]))
            m = max(a[1], a[2], a[3], 1e-6)
            for j in range(3):
                cs[j].append(a[1 + j] / m if a[0] > 0 else 1.0)
        fr = list(range(1, self.n + 1))
        set_keys(ld, "energy", 0, fr, en)
        for j in range(3):
            set_keys(ld, "color", j, fr, cs[j])

    # ----- the glowing line where notes meet the keys ---------------------------
    def build_hit_line(self):
        """
        A glowing line along the hit line, made of one segment per key so that
        where a key is down it can take the note's colour and flare (its object
        colour and alpha follow the key's, as the glow cards do).
        """
        st = self.st
        if st["hit_line"] <= 0:
            return
        m, nt, out = _mat("HitLine")
        n = Nodes(nt)
        info = n.new("ShaderNodeObjectInfo")
        a = info.outputs["Alpha"]
        color = n.mix_rgb(n.math("MULTIPLY", a, st["hit_line_notes"]), srgb(st["hit_line_color"]),
                          info.outputs["Color"])
        em = n.new("ShaderNodeEmission")
        n.link(color, em.inputs["Color"])
        n.link(n.math("MULTIPLY", n.math("MULTIPLY_ADD", a, 3.0 * st["hit_line_flare"], 1.0), st["hit_line"]),
               em.inputs["Strength"])
        n.link(em, out.inputs[0])
        m.diffuse_color = srgb(st["hit_line_color"])           # the flat (Workbench) look
        hw = st["hit_line_width"] / 2
        top = st["note_depth"] + 0.0035                         # over the notes (and black-key notes)
        for p, k in self.keys.items():
            x0, x1 = k["x0"], k["x1"]
            lift = 0.0002 if k["black"] else 0.0                 # black keys' segments drawn over the white ones
            if self.floor:
                box = (x0, x1, self.hit - hw, self.hit + hw, RUN_Z + top + lift, RUN_Z + top + lift + 0.0006)
            else:
                y = self.y_wall - top - lift
                box = (x0, x1, y - 0.0006, y, HIT_Z - hw, HIT_Z + hw)
            ob = add_obj(f"Line{p}", box_mesh(f"line{p}", *box), self.root, [m])
            ob.color = (1, 1, 1, 0)
            ob.visible_shadow = False
            key = self.key_obj[p]
            if key.animation_data and (st["hit_line_notes"] > 0 or st["hit_line_flare"] > 0):
                for c in range(4):
                    pts = [kp.co[:] for kp in fcurve(key, "color", c).keyframe_points]
                    vals = [q[1] for q in pts]
                    if c == 3 and st["key_tint"] > 0:
                        vals = [x / st["key_tint"] for x in vals]
                    set_keys(ob, "color", c, [q[0] for q in pts], vals, interp="CONSTANT" if c < 3 else "LINEAR")

    # ----- title -----------------------------------------------------------------
    def build_title(self, cam):
        """
        Text in front of the camera: the title (and a second line) over the
        opening, and the end text over the last seconds, each fading in and out.
        Timed in song time from the video's start and end (video_t0/video_t1
        in the export), so a preview frame shows them only where they'd be.
        """
        st = self.st
        start = self.d.get("video_t0", self.t0)
        end = self.d.get("video_t1", self.t0 + (self.n - 1) / self.fps)
        title = [t for t in (st["title_text"].strip(), st["subtitle_text"].strip()) if t]
        if title:
            self._text_card(cam, "Title", title, start, start + st["title_time"], st["title_height"])
        ending = [t for t in st["end_text"].strip().split("\\n") if t.strip()]
        if ending:
            self._text_card(cam, "End", ending, end - st["end_time"], end + 1.0, st["end_height"])

    def _text_card(self, cam, name, lines, t0, t1, height):
        st = self.st
        m, nt, out = _mat(name)
        m.surface_render_method = "DITHERED"
        n = Nodes(nt)
        fade = n.new("ShaderNodeValue", name="Fade")
        em = n.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = srgb(st["title_color"])
        em.name = "TextEmission"
        em.inputs["Strength"].default_value = 1.0 + st["title_glow"]
        tr = n.new("ShaderNodeBsdfTransparent")
        mix = n.new("ShaderNodeMixShader")
        n.link(fade, mix.inputs[0])
        n.link(tr, mix.inputs[1])
        n.link(em, mix.inputs[2])
        n.link(mix, out.inputs[0])
        m.diffuse_color = srgb(st["title_color"])
        # fade in over 0.6 s, hold, fade out over 0.8 s
        f0, f1 = self.frame(t0), self.frame(t1)
        fin, fout = 0.6 * self.fps, 0.8 * self.fps
        set_keys(nt, 'nodes["Fade"].outputs[0].default_value', 0, [f0, f0 + fin, f1 - fout, f1], [0, 1, 1, 0])
        # in front of the camera, sized for its lens and frame (the same share of the picture at any zoom)
        dist = 0.3
        aspect = getattr(self, "aspect", 16 / 9)
        long_side = dist * 36.0 / cam.data.lens                 # the 36 mm sensor spans the longer side
        frame_h = long_side / aspect if aspect >= 1 else long_side
        frame_w = long_side if aspect >= 1 else long_side * aspect
        size = min(0.11 * frame_h, 0.06 * frame_w) * st["title_size"]
        for i, text in enumerate(lines):
            cu = bpy.data.curves.new(f"{name}{i}", "FONT")
            cu.body = text
            cu.align_x, cu.align_y = "CENTER", "CENTER"
            cu.size = size * (1.0 if i == 0 else 0.5)
            ob = bpy.data.objects.new(f"{name}{i}", cu)
            bpy.context.scene.collection.objects.link(ob)
            ob.data.materials.append(m)
            ob.parent = cam
            ob.location = (0, height * frame_h - i * size * 0.95, -dist)
            ob.visible_shadow = False
            # shown only in its time (Workbench ignores the fade)
            set_keys(ob, "hide_render", 0, [f0 - 1, f0, f1 + 1], [1, 0, 1], interp="CONSTANT")

    # ----- camera, lights, world ---------------------------------------------
    def build_camera(self, centres, view="pianist", aspect=16 / 9):
        """
        centres: per frame, the x the camera follows (export coordinates).
        The style frames the view (distance, tilt, turn, zoom, slide), may
        follow the hands, drift gently, and open with an intro move.
        """
        cam_data = bpy.data.cameras.new("Camera")
        cam = bpy.data.objects.new("Camera", cam_data)
        bpy.context.scene.collection.objects.link(cam)
        bpy.context.scene.camera = cam
        cam.parent = self.root
        self.aspect = aspect
        L = self.L
        if view == "pianist":
            lens, base, look = 30, Vector((0.0, -0.42, 0.50)), Vector((0.0, L + 0.06, 0.06))
        elif view == "side":
            lens, base, look = 40, Vector((0.0, -0.55, 0.22)), Vector((0.0, 0.08, 0.02))
        elif view == "bird":
            # high above, tilted a little: the hands at the bottom, the notes coming down from the top
            lens, base, look = 30, Vector((0.0, -0.22, 1.02)), Vector((0.0, 0.19, 0.0))
        elif view == "close":
            lens, base, look = 50, Vector((0.0, -0.30, 0.22)), Vector((0.0, 0.06, 0.0))
        else:  # top
            lens, base, look = 35, Vector((0.0, -0.05, 0.85)), Vector((0.0, 0.10, 0.0))
        st = self.st
        v = (base - look) * st["cam_distance"]
        dist, elev = v.length, math.atan2(v.z, -v.y)
        elev -= math.radians(st["cam_tilt"])
        turn = math.radians(st["cam_turn"])
        lens *= st["cam_zoom"]
        if aspect < 1.0:
            # an upright frame is fitted by its height (the sensor's 36 mm spans it), so the same
            # lens sees as tall but narrower: a little closer, it shows about five octaves - both
            # hands as they usually spread - and the camera follows them across the keyboard
            dist *= 0.9
        cam_data.lens = lens
        cam_data.sensor_fit = "AUTO"
        cam_data.dof.use_dof = view in ("side", "close")
        cam_data.dof.aperture_fstop = 4.0
        cam_data.clip_start = 0.02

        # where the camera's centre goes along the keyboard: still, or following the hands
        w = self.kb["width"]
        n = len(centres)
        half_w = dist * 18.0 / lens * min(1.0, aspect)          # half the view's width at the keys
        if st["cam_follow"] or aspect < 1.0:
            half = max(1, int(st["cam_follow_speed"] * self.fps / 2))
            pre = [0.0]
            for c in centres:
                pre.append(pre[-1] + c)
            span = min(w / 2, max(0.08, half_w * 0.9))
            xs = []
            for i in range(n):
                a, b = max(0, i - half), min(n, i + half + 1)
                xs.append(min(w - span, max(span, (pre[b] - pre[a]) / (b - a))))
        else:
            xs = [w / 2] * n
        xs = [x + st["cam_shift"] for x in xs]

        # the intro: from the video's start, eased into the framing above
        intro = st["cam_intro"]
        f_start = self.frame(self.d.get("video_t0", self.t0)) - 1
        f_len = max(1, st["cam_intro_time"] * self.fps)

        def framing(i):
            """(distance, elevation, turn, lens) at frame index i."""
            u = 1.0
            if intro != "none":
                u = min(1.0, max(0.0, (i - f_start) / f_len))
                u = u * u * u * (u * (u * 6 - 15) + 10)               # smootherstep
            d, e, tn, ln = dist, elev, turn, lens
            if intro == "fly":
                d, e = dist * (1 + 1.6 * (1 - u)), elev + math.radians(25) * (1 - u)
            elif intro == "zoom":
                ln = lens * (0.5 + 0.5 * u)
            elif intro == "swing":
                tn = turn + math.radians(45) * (1 - u)
            e = min(math.radians(89.5), max(math.radians(4.0), e))
            return d, e, tn, ln

        drift = st["cam_drift"]
        step = max(1, self.fps // 6)
        idx = sorted(set(list(range(0, n, step)) + [n - 1] +
                         (list(range(max(0, int(f_start)), min(n, int(f_start + f_len) + 2), 2)) if intro != "none" else [])))
        locs, quats, lenses = [], [], []
        prev = None
        for i in idx:
            d, e, tn, ln = framing(i)
            flat = d * math.cos(e)
            off = Vector((flat * math.sin(tn), -flat * math.cos(tn), d * math.sin(e)))
            target = Vector((xs[i], look.y, look.z))
            pos = target + off
            if drift > 0:
                t = i / self.fps
                pos += Vector((math.sin(t * 2 * math.pi / 9.0) * drift, math.sin(t * 2 * math.pi / 13.0) * drift * 0.4,
                               math.cos(t * 2 * math.pi / 11.0) * drift * 0.5))
            q = (target - pos).to_track_quat("-Z", "Y")
            if prev is not None and q.dot(prev) < 0:
                q.negate()
            prev = q
            locs.append(pos)
            quats.append(q)
            lenses.append(ln)
        fr = [i + 1 for i in idx]
        cam.rotation_mode = "QUATERNION"
        for c in range(3):
            set_keys(cam, "location", c, fr, [p[c] for p in locs], interp="BEZIER")
        for c in range(4):
            set_keys(cam, "rotation_quaternion", c, fr, [q[c] for q in quats], interp="BEZIER")
        if intro == "zoom":
            set_keys(cam_data, "lens", 0, fr, lenses, interp="BEZIER")
        if cam_data.dof.use_dof:
            focus = bpy.data.objects.new("Focus", None)
            bpy.context.scene.collection.objects.link(focus)
            focus.parent = cam
            focus.location = (0, 0, -dist)
            cam_data.dof.focus_object = focus
        return cam

    def build_lights(self):
        L, root, st = self.L, self.root, self.st
        w = self.kb["width"]
        k = st["light_level"]

        def area(name, loc, rot, size, power, color=(1, 1, 1)):
            ld = bpy.data.lights.new(name, "AREA")
            ld.shape = "RECTANGLE"
            ld.size, ld.size_y = size
            ld.energy = power * k
            ld.color = color
            ob = bpy.data.objects.new(name, ld)
            bpy.context.scene.collection.objects.link(ob)
            ob.parent = root
            ob.location = loc
            ob.rotation_euler = rot
            return ob
        # soft key light above and in front, a warm fill from the left, a cool rim from behind
        area("Key", (w / 2, -0.25, 1.1), (math.radians(20), 0, 0), (1.6, 0.6), 45)
        area("Fill", (-0.4, -0.5, 0.5), (math.radians(60), 0, math.radians(-50)), (0.6, 0.6), 6, (1.0, 0.85, 0.7))
        area("Rim", (w / 2, L + 0.5, 0.6), (math.radians(-55), 0, 0), (1.4, 0.3), 25, (0.7, 0.8, 1.0))
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes["Background"]
        bg.inputs["Color"].default_value = srgb(st["world_color"])
        bg.inputs["Strength"].default_value = st["ambient"]
        # a dark floor so the case has something below it
        floor = principled("Floor", srgb((20, 20, 24)), rough=0.8)
        add_obj("Floor", box_mesh("floor", -2, w + 2, -2, L + 2, -0.75, -0.74), root, [floor])
        if st["haze"] > 0:
            m, nt, out = _mat("Haze")
            vol = nt.nodes.new("ShaderNodeVolumePrincipled")
            vol.inputs["Density"].default_value = 1.2 * st["haze"]
            vol.inputs["Color"].default_value = (0.9, 0.9, 1.0, 1)
            nt.links.new(vol.outputs[0], out.inputs["Volume"])
            add_obj("Haze", box_mesh("haze", -0.3, w + 0.3, -0.6, self.top + 0.1 if self.floor else L + 0.04, -0.1,
                                                  0.4 if self.floor else self.top + 0.2), root, [m])
