"""
Flat test renders (render3d.py --quality flat): Blender's Workbench engine,
plain colours with no lighting, glow or reflections, a few times faster
than Eevee - for checking a whole piece's timing and framing quickly.

Workbench ignores shader tricks, so here the notes are trimmed with
geometry instead (squashed onto the hit line once they reach it), each
colour of note gets a plain material, and the glow cards are hidden.
"""

import bpy

from scene import Nodes, srgb

SKIN = (0.50, 0.30, 0.21, 1.0)


def _clamp_nodes(sc):
    """Geometry nodes that hold every vertex between the hit line and the far end of the run (world space)."""
    ng = bpy.data.node_groups.new("ClipToRun", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = Nodes(ng)
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    info = n.new("GeometryNodeObjectInfo", transform_space="ORIGINAL")
    n.link(n.new("GeometryNodeSelfObject"), info.inputs["Object"])
    loc = n.xyz(info.outputs["Location"]).outputs[sc.axis]
    pos = n.xyz(n.new("GeometryNodeInputPosition"))
    world = n.math("ADD", pos.outputs[sc.axis], loc)
    clamped = n.math("SUBTRACT", n.math("MINIMUM", n.math("MAXIMUM", world, sc.hit), sc.top), loc)
    # a note that has gone past the line altogether (its far end, 'hi', too) is tucked
    # away behind it (under the back rail, or behind the wall); one still crossing it
    # is just cut there. Objects without 'hi' (bar lines) go vertex by vertex.
    hi = n.new("GeometryNodeInputNamedAttribute", data_type="FLOAT")
    hi.inputs["Name"].default_value = "hi"
    note_past = n.math("LESS_THAN", n.math("ADD", hi.outputs["Attribute"], loc), sc.hit)
    vert_past = n.math("LESS_THAN", world, sc.hit)
    has = hi.outputs["Exists"]
    past = n.math("ADD", n.math("MULTIPLY", has, note_past), n.math("MULTIPLY", n.math("SUBTRACT", 1.0, has), vert_past))
    away, sink = ("Z", -0.03) if sc.axis == "Y" else ("Y", 0.03)
    tucked = n.math("MULTIPLY_ADD", past, sink, pos.outputs[away])
    comb = n.new("ShaderNodeCombineXYZ")
    for a in "XYZ":
        n.link(clamped if a == sc.axis else tucked if a == away else pos.outputs[a], comb.inputs[a])
    setp = n.new("GeometryNodeSetPosition")
    n.link(gi.outputs[0], setp.inputs["Geometry"])
    n.link(comb, setp.inputs["Position"])
    n.link(setp, go.inputs[0])
    return ng


def _base_color(m):
    if not m.node_tree:
        return None
    for nd in m.node_tree.nodes:
        if nd.bl_idname == "ShaderNodeBsdfPrincipled":
            sock = nd.inputs["Base Color"]
            if not sock.is_linked:
                return tuple(sock.default_value)
    return None


def apply(sc):
    s = bpy.context.scene
    s.render.engine = "BLENDER_WORKBENCH"
    sh = s.display.shading
    sh.light = "FLAT"
    sh.color_type = "MATERIAL"
    sh.show_object_outline = True
    sh.object_outline_color = (0.0, 0.0, 0.0)
    s.display.render_aa = "5"
    s.render.use_compositing = False
    s.render.use_motion_blur = False
    if s.world:
        s.world.color = srgb(sc.st["world_color"])[:3]

    for m in bpy.data.materials:
        c = _base_color(m)
        if c:
            m.diffuse_color = c
    for name, c in (("KeyWhite", srgb((236, 232, 222))), ("KeyBlack", srgb((20, 20, 24)))):
        if name in bpy.data.materials:
            bpy.data.materials[name].diffuse_color = c

    # the notes: a plain material per colour, trimmed at the hit line
    notes = bpy.data.objects.get("Notes")
    if notes:
        me = notes.data
        cols = me.color_attributes["col"].data
        palette, idx = {}, []
        for poly in me.polygons:
            c = tuple(round(v, 3) for v in cols[poly.vertices[0]].color)
            if c not in palette and len(palette) < 32:
                m = bpy.data.materials.new(f"FlatNote{len(palette)}")
                m.diffuse_color = c
                palette[c] = len(me.materials)
                me.materials.append(m)
            idx.append(palette.get(c, 0))
        me.polygons.foreach_set("material_index", idx)
        ng = _clamp_nodes(sc)
        for name in ("Notes", "BarLines", "FingerNumbers"):
            ob = bpy.data.objects.get(name)
            if ob:
                ob.modifiers.new("Clip", "NODES").node_group = ng
    for name in ("HitLine", "Title"):
        if name in bpy.data.materials:
            pass                                           # their diffuse colours are set where they're made
    if "Sparks" in bpy.data.materials:
        bpy.data.materials["Sparks"].diffuse_color = (1.0, 0.7, 0.3, 1.0)
    for ob in bpy.data.objects:
        if ob.name.startswith("Glow") or ob.name == "Haze":
            ob.hide_render = True
    for hands in [ob for ob in bpy.data.objects if ob.type == "MESH" and ob.name.startswith("Hands")]:
        for mod in hands.modifiers:
            if mod.type == "SUBSURF":
                mod.render_levels = 1
        k = sc.st["skin_brightness"]
        tint = [c / 255.0 * k for c in sc.st["skin_tint"]]
        for slot in hands.material_slots:
            if slot.material:
                c = (1.0, 0.92, 0.88) if "nail" in slot.material.name else SKIN[:3]
                slot.material.diffuse_color = (c[0] * tint[0], c[1] * tint[1], c[2] * tint[2], 1.0)
