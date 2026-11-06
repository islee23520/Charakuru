"""Synthetic G4/G5 fixtures (outputs are never committed).

Run: blender -b --factory-startup -P tools/gate/fixtures/make-synth-fixtures.py -- <out_dir>

G4: a skinned cylinder "body" (cast_role=body, vertex group "torso_hidden") and a sleeve garment
(cast_role=garment, hides=["torso_hidden"]) on a 2-bone armature with actions walk / sit / elbow90.
  g4-pass.blend          garment 6 mm outside the body in every pose         -> pass
  g4-penetrate.blend     garment radius 5 mm inside the body                 -> Penetration
  g4-hide-missing.blend  garment hides a group the body does not have        -> HideGroupMissing
G5: role-tagged boxes with given triangle counts and textures.
  g5-pass.blend          head 10k, body 20k, hair 6k, garments 20k, props 2k; textures 2048/1024 -> pass
  g5-over.blend          body 30k (total 66k) and a 4096 base colour texture -> TriTotal, ComponentBudget, TextureSize
"""
import bpy
import bmesh
import math
import os
import sys

out = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out, exist_ok=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cylinder(name, radius, depth, segs=48, rings=24):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=segs, radius1=radius, radius2=radius, depth=depth)
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6],
                              cuts=rings)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


def rig():
    arm_d = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", arm_d)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    a = arm_d.edit_bones.new("upper")
    a.head, a.tail = (0, 0, -0.3), (0, 0, 0)
    b = arm_d.edit_bones.new("lower")
    b.head, b.tail, b.parent = (0, 0, 0), (0, 0, 0.3), a
    bpy.ops.object.mode_set(mode="OBJECT")
    poses = {"walk": 20, "sit": 60, "elbow90": 90}
    for name, deg in poses.items():
        pb = arm.pose.bones["lower"]
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (math.radians(deg), 0, 0)
        pb.keyframe_insert("rotation_euler", frame=1)
        act = arm.animation_data.action
        act.name = name
        act.use_fake_user = True
        arm.animation_data.action = None
        pb.rotation_euler = (0, 0, 0)
    return arm


def skin(o, arm):
    up = o.vertex_groups.new(name="upper")
    lo = o.vertex_groups.new(name="lower")
    for v in o.data.vertices:
        t = min(max((v.co.z + 0.05) / 0.1, 0.0), 1.0)
        up.add([v.index], 1 - t, "REPLACE")
        lo.add([v.index], t, "REPLACE")
    m = o.modifiers.new("arm", "ARMATURE")
    m.object = arm
    o.parent = arm


def g4(name, garment_r, hides):
    reset()
    arm = rig()
    body = cylinder("body", 0.05, 0.6)
    body["cast_role"] = "body"
    hg = body.vertex_groups.new(name="torso_hidden")
    hg.add([v.index for v in body.data.vertices if v.co.z < -0.25], 1.0, "REPLACE")
    skin(body, arm)
    g = cylinder("sleeve", garment_r, 0.4)
    g["cast_role"] = "garment"
    g["hides"] = hides
    skin(g, arm)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name), compress=False)


g4("g4-pass.blend", 0.056, ["torso_hidden"])
g4("g4-penetrate.blend", 0.045, ["torso_hidden"])
g4("g4-hide-missing.blend", 0.056, ["no_such_group"])


def box(name, role, tris, tex=None):
    # a grid of quads with tris/2 faces
    faces = max(tris // 2, 1)
    n = int(math.sqrt(faces))
    m = faces // n
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=n, y_segments=m, size=0.1)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    o["cast_role"] = role
    bpy.context.scene.collection.objects.link(o)
    if tex:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next(nd for nd in nt.nodes if nd.type == "BSDF_PRINCIPLED")
        for size, socket in tex:
            img = bpy.data.images.new("%s_%s_%d" % (name, socket.replace(" ", ""), size), size, size)
            node = nt.nodes.new("ShaderNodeTexImage")
            node.image = img
            if socket == "Normal":
                nm = nt.nodes.new("ShaderNodeNormalMap")
                nt.links.new(node.outputs["Color"], nm.inputs["Color"])
                nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
            else:
                nt.links.new(node.outputs["Color"], bsdf.inputs[socket])
        me.materials.append(mat)
    return o


def g5(name, rows):
    reset()
    for r in rows:
        box(*r)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name), compress=False)


g5("g5-pass.blend", [("head", "head", 10000, [(2048, "Base Color"), (1024, "Normal")]), ("body", "body", 20000),
                     ("hair", "hair_cards", 6000), ("garment", "garments", 20000), ("prop", "props", 2000)])
g5("g5-over.blend", [("head", "head", 10000, [(4096, "Base Color"), (1024, "Normal")]), ("body", "body", 30000),
                     ("hair", "hair_cards", 6000), ("garment", "garments", 20000)])
print("SYNTH_OK " + " ".join(sorted(os.listdir(out))))
sys.stdout.flush()
os._exit(0)
