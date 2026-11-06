"""Render hand close-ups for finger review (evidence only).

Run: blender -b --factory-startup -P tools/gate/fixtures/render-hands.py -- <in.glb|.blend> <out_prefix>
Writes <out_prefix>_right_top.png (right hand from above) and <out_prefix>_hands_front.png (both hands,
front view, side by side). Framing comes from the mesh bounds, so differently scaled copies match.
"""
import bpy
import math
import sys

import numpy as np
from mathutils import Vector

a = sys.argv[sys.argv.index("--") + 1:]
src, out = a[0], a[1]
if src.endswith(".blend"):
    bpy.ops.wm.open_mainfile(filepath=src)
else:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
co = np.concatenate([np.array([o.matrix_world @ v.co for v in o.data.vertices]) for o in meshes])
mn, mx = co.min(0), co.max(0)
H = mx[2] - mn[2]
lat = int(np.argmax(np.ptp(co[:, :2], 0)))
dep = 1 - lat
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "SINGLE"
sc.display.shading.show_cavity = True
sc.render.resolution_x = sc.render.resolution_y = 640
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.type = "ORTHO"
cam.data.clip_end = 100


def hand_centre(sign):
    rel = sign * co[:, lat]
    tip = rel.max()
    sel = rel > tip - 0.11 * H
    return co[sel].mean(0), sel


def shoot(path, centre, view, ortho):
    cam.data.ortho_scale = ortho
    d = Vector(view).normalized()
    cam.location = Vector(centre) + d * 5 * H
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


# "right" = the character's right hand = image-left in front view = -X for a -Y-facing (Blender) mesh
c, _ = hand_centre(-1)
top = Vector((0, 0, 1))
shoot(out + "_right_top.png", c, (0.0001, 0.0, 1.0), 0.16 * H)
mid = (co[:, lat].min() + co[:, lat].max()) / 2
fv = [0, 0, 0]
fv[dep] = -1
cl, _ = hand_centre(-1)
cr, _ = hand_centre(1)
for side, cc in (("R", cl), ("L", cr)):
    shoot(out + "_front_%s.png" % side, cc, fv, 0.16 * H)
print("HANDS_OK")
