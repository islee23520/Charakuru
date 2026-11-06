"""Synthetic head-topology fixtures for auto-check G1/G3 (never committed outputs).

Run: blender -b --factory-startup -P tools/gate/fixtures/make-head-fixtures.py -- <reference.glb> <out_dir>

<reference.glb> is the read-only female-underwear runtime GLB (open eye sockets, open mouth, separate
eye and oral components). Writes into <out_dir>:
  head-open.blend         reference + neck_ring group + shape key "jaw_soft" (face moves, rims and neck
                          ring fixed)                                  -> G1 head pass, G3 pass
  head-baked-eye.blend    both Eye_Core meshes joined into the face mesh -> G1 BakedEyeball
  head-rim-closed.blend   head-open + shape key "blink_shut" collapsing both eye rims -> G3 RimClosed
  head-lid-push.blend     head-open + shape key "lid_push" pushing the left lid zone back into the
                          eyeball by 8 mm (rim stays open)                     -> G3 Penetration
  head-neck-moved.blend   head-open + shape key "neck_push" moving the neck ring 1 mm -> G3 NeckRingDelta
  head-eyes-shut.blend    both eye-socket holes capped (fill), mouth left open    -> G1 EyeSocketClosed only
  head-mouth-shut.blend   oral parts and cavity removed, the lip pocket filled by a boolean union with an
                          ellipsoid plug (the reference mouth is an inward pocket of the face mesh, not a
                          hole), eyes left open                                     -> G1 MouthClosed only
"""
import bpy
import bmesh
import json
import os
import sys

import numpy as np

ref, out_dir = sys.argv[sys.argv.index("--") + 1:][:2]
os.makedirs(out_dir, exist_ok=True)
FACE = "Female_SmoothDoll_Body"


def load():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=ref)
    for o in list(bpy.context.scene.objects):
        if o.type == "MESH":
            o.data = o.data.copy()
        if o.type == "MESH" and o.parent:
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    for o in bpy.context.scene.objects:
        o.data.transform(o.matrix_world)
        o.matrix_world.identity()
    return bpy.data.objects[FACE]


def weld_map(obj):
    return np.array([v.co[:] for v in obj.data.vertices])


def prepare(obj):
    co = weld_map(obj)
    z = co[:, 2]
    # neck ring: the band at the neck (narrowest region below the head)
    neck_z = 1.43
    ring = np.nonzero(np.abs(z - neck_z) < 0.004)[0]
    vg = obj.vertex_groups.new(name="neck_ring")
    vg.add([int(i) for i in ring], 1.0, "REPLACE")
    obj.shape_key_add(name="Basis", from_mix=False)
    # eye rims of the reference sit at (+-0.0388, -0.082, 1.5281), radius 0.0257 (shared-eyes.blend)
    near_rim = np.zeros(len(co), bool)
    for cx in (-0.0388, 0.0388):
        near_rim |= np.linalg.norm(co - np.array([cx, -0.082, 1.5281]), axis=1) < 0.034
    return co, ring, near_rim, int(near_rim.sum())


def key_from(obj, name, new_co):
    k = obj.shape_key_add(name=name, from_mix=False)
    k.data.foreach_set("co", new_co.reshape(-1))
    return k


def save(name, extra=None):
    p = os.path.join(out_dir, name)
    bpy.ops.wm.save_as_mainfile(filepath=p, compress=False)
    return p


manifest = {"reference": ref, "files": {}}

# head-open
face = load()
co, ring, near_rim, rim = prepare(face)
jaw = co.copy()
sel = (co[:, 2] > 1.44) & (co[:, 2] < 1.48) & (co[:, 1] < -0.07) & ~near_rim
jaw[sel, 2] -= 0.002
jaw[sel, 1] -= 0.001
key_from(face, "jaw_soft", jaw)
manifest["files"]["head-open.blend"] = {"jaw_soft_moved_verts": int(sel.sum()), "neck_ring_verts": int(len(ring)),
                                        "rim_zone_verts": rim}
save("head-open.blend")

# head-rim-closed (from head-open state)
shut = co.copy()
for cx in (-0.0388, 0.0388):
    m = (np.abs(co[:, 0] - cx) < 0.03) & (np.abs(co[:, 2] - 1.528) < 0.03) & (co[:, 1] < -0.06)
    shut[m, 2] = 1.528
manifest["files"]["head-rim-closed.blend"] = {"blink_shut_verts": int(((shut - co) != 0).any(1).sum())}
key_from(face, "blink_shut", shut)
save("head-rim-closed.blend")
face.shape_key_remove(face.data.shape_keys.key_blocks["blink_shut"])

# head-neck-moved
push = co.copy()
push[ring, 1] += 0.001
key_from(face, "neck_push", push)
save("head-neck-moved.blend")
face.shape_key_remove(face.data.shape_keys.key_blocks["neck_push"])

# head-lid-push: lid ring around the left socket (between rim and 1.3 rim radii) pushed back 4 mm
push = co.copy()
r = np.linalg.norm(co - np.array([0.0388, -0.082, 1.5281]), axis=1)
m = (r > 0.024) & (r < 0.034) & (co[:, 1] < -0.07)
push[m, 1] += 0.008
key_from(face, "lid_push", push)
manifest["files"]["head-lid-push.blend"] = {"lid_push_verts": int(m.sum())}
save("head-lid-push.blend")
face.shape_key_remove(face.data.shape_keys.key_blocks["lid_push"])

# head-baked-eye: eyeballs fused into the face mesh, no shape keys needed for G1
face = load()
bpy.ops.object.select_all(action="DESELECT")
for n in ("Female_L_Eye_Core", "Female_R_Eye_Core"):
    bpy.data.objects[n].select_set(True)
face.select_set(True)
bpy.context.view_layer.objects.active = face
bpy.ops.object.join()
save("head-baked-eye.blend")

def cap_holes(obj, centres, radius):
    """Fill the boundary loops whose centre lies within radius of one of the centres (planar fan)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bnd = [e for e in bm.edges if e.is_boundary]
    pick = []
    for e in bnd:
        mid = (e.verts[0].co + e.verts[1].co) / 2
        if any((mid - __import__("mathutils").Vector(c)).length < radius for c in centres):
            pick.append(e)
    res = bmesh.ops.holes_fill(bm, edges=pick, sides=0)
    bmesh.ops.triangulate(bm, faces=res["faces"])
    bm.to_mesh(obj.data)
    bm.free()
    return len(res["faces"])


face = load()
for n in [o.name for o in bpy.context.scene.objects if "Eye" in o.name]:
    bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
manifest["files"]["head-eyes-shut.blend"] = {"cap_faces": cap_holes(face, [(-0.0388, -0.082, 1.5281), (0.0388, -0.082, 1.5281)], 0.03)}
save("head-eyes-shut.blend")
face = load()
for n in [o.name for o in bpy.context.scene.objects if any(t in o.name for t in ("Tooth", "Gum", "Tongue", "InnerMouth"))]:
    bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0, location=(0.0, -0.104, 1.4705))
plug = bpy.context.active_object
plug.scale = (0.026, 0.016, 0.014)
bpy.ops.object.transform_apply(scale=True)
mod = face.modifiers.new("plug", "BOOLEAN")
mod.operation = "UNION"
mod.solver = "EXACT"
mod.object = plug
bpy.context.view_layer.objects.active = face
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.data.objects.remove(plug, do_unlink=True)
manifest["files"]["head-mouth-shut.blend"] = {"plug": {"centre": [0.0, -0.104, 1.4705], "radii": [0.026, 0.016, 0.014]}}
save("head-mouth-shut.blend")

with open(os.path.join(out_dir, "head-fixtures.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print("HEAD_FIXTURES_OK " + json.dumps(sorted(os.listdir(out_dir))))
sys.stdout.flush()
os._exit(0)
