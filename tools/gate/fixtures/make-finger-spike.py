"""FingersExtra fixture: the reduced body with one extra spike on the left hand (never committed).

Run: blender -b --factory-startup -P tools/gate/fixtures/make-finger-spike.py -- <reduced.blend> <out.blend>

The vertex of the left hand nearest the back-of-hand centre is pulled 6 cm out along its normal
(a spike that is part of the hand surface), so the hand has one more geodesic-distance peak than the
source: G1 fingers must report FingersExtra:L:5->6.
"""
import bpy
import sys

import numpy as np
from mathutils import Vector

src, out = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.open_mainfile(filepath=src)
body = [o for o in bpy.data.objects if o.type == "MESH"][0]
co = np.array([body.matrix_world @ v.co for v in body.data.vertices])
H = float(np.ptp(co[:, 2]))
lat = int(np.argmax(np.ptp(co[:, :2], 0)))
dep = 1 - lat
cl = float((co[:, lat].min() + co[:, lat].max()) / 2)
rel = co[:, lat] - cl
tip = float(rel.max())
hand = co[rel > tip - 0.06 * H]
c = hand.mean(0)
base = Vector(c.tolist())
direction = [0.0, 0.0, 0.0]
direction[dep] = -1.0  # out of the front of the hand
d = Vector(direction)
me = body.data
mw_inv = body.matrix_world.inverted()
hand_idx = np.flatnonzero(rel > tip - 0.06 * H)
centre = co[hand_idx].mean(0)
near = hand_idx[np.argsort(np.linalg.norm(co[hand_idx] - centre, axis=1))[:200]]
vi = int(near[np.argmin(co[near, dep])])
v = me.vertices[vi]
n = (body.matrix_world.to_3x3() @ v.normal).normalized()
v.co = mw_inv @ (body.matrix_world @ v.co + n * 0.06)
me.update()
print("SPIKE vertex", vi, "normal", tuple(round(x, 3) for x in n))
bpy.ops.wm.save_as_mainfile(filepath=out, compress=False)
print("SPIKE_OK", out)
sys.stdout.flush()
import os
os._exit(0)
