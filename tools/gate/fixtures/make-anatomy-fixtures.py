"""Anatomy fixtures from the read-only avatar-gen reference body (outputs never committed).

Run: blender -b --factory-startup -P tools/gate/fixtures/make-anatomy-fixtures.py -- <female-underwear.glb> <out_dir>

  anat-ref.blend         Female_SmoothDoll_Body as shipped (1.651 m)              -> anatomy pass
  anat-zsquash.blend     object scale (1, 1, 0.8), not applied                    -> NonUniformScale, HeightOutOfBand
  anat-bighead.blend     vertices above the chin scaled 1.3x about the chin centre -> ProportionOutOfBand:head
  anat-longarm.blend     left arm (beyond the shoulder) stretched so its reach is 15% longer -> AsymmetryOver2pct:arm
  anat-floating.blend    moved up 5 cm                                            -> NotOnGround
"""
import bpy
import os
import sys

import numpy as np

ref, out = sys.argv[sys.argv.index("--") + 1:][:2]
os.makedirs(out, exist_ok=True)
BODY = "Female_SmoothDoll_Body"
CHIN_Z = 0.8643 * 1.6509  # measured by auto-check anatomy on the reference (chin_z_frac 0.8643)
SHOULDER_X = 0.18          # beyond the deltoid (measured shoulder half-width 0.088 H = 0.145 m)


def load():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=ref)
    body = bpy.data.objects[BODY]
    mw = body.matrix_world.copy()
    body.parent = None
    body.data = body.data.copy()
    body.data.transform(mw)
    body.matrix_world.identity()
    for o in list(bpy.context.scene.objects):
        if o != body:
            bpy.data.objects.remove(o, do_unlink=True)
    return body


def coords(o):
    co = np.empty(len(o.data.vertices) * 3)
    o.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def setc(o, co):
    o.data.vertices.foreach_set("co", co.reshape(-1))
    o.data.update()


def save(name):
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name), compress=False)


b = load()
save("anat-ref.blend")
b.scale = (1.0, 1.0, 0.8)
save("anat-zsquash.blend")
b = load()
co = coords(b)
m = co[:, 2] > CHIN_Z
c = np.array([0.0, float(np.median(co[m, 1])), CHIN_Z])
co[m] = c + (co[m] - c) * 1.3
setc(b, co)
save("anat-bighead.blend")
b = load()
co = coords(b)
tip = co[:, 0].max()
m = (co[:, 0] > SHOULDER_X) & (co[:, 2] > 1.15)  # arm only (the hips are wider than SHOULDER_X)
k = ((tip - SHOULDER_X) + 0.15 * tip) / (tip - SHOULDER_X)
co[m, 0] = SHOULDER_X + (co[m, 0] - SHOULDER_X) * k
setc(b, co)
save("anat-longarm.blend")
b = load()
b.location.z = 0.05
save("anat-floating.blend")
print("ANAT_FIXTURES_OK " + " ".join(sorted(os.listdir(out))))
sys.stdout.flush()
os._exit(0)
