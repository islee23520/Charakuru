# tools/gate/render-views.py
"""Deterministic multi-view renderer for gate vision reviews (todo 6).

Run:
    blender -b -P tools/gate/render-views.py -- <input> <out_dir> --preset <gate> \
        [--engine auto|eevee|cycles] [--samples N] [--seed N]
        [--self-test-flip-aim] [--self-test-crash]

Self-test flags (used by tools/gate/vision/verify.mjs, never in production):
  --self-test-flip-aim  negates the camera look direction -> FacingCheckFailed
  --self-test-crash     raises RuntimeError mid-run -> exit 1 (RenderFailed:...)

Contract (docs/gate/vision-review.ko.md#render-contract):
  - Fixed camera rig, fixed 3-point sun lighting, neutral gray world, 1024px RGB PNGs.
  - Output list is a pure function of (input geometry, preset):
        body-front/right/back/left/threequarter      (5)
        head-front/right/threequarter                (3)
        crop-eyes, crop-lips, crop-mouthopen, crop-ear-left/right, crop-neck,
        crop-hand-left/right, crop-elbow-left/right,
        crop-feet-left/right                         (12)
        pose-walk/sit/elbow90/elbow140/armsraised x front/threequarter (10, rigged only)
    => 30 PNGs for a rigged asset, 20 for a static mesh, plus manifest.json.
  - FACING is detected (VRM 1.0 -> glTF +Z -> -Y after import, VRM 0.x -> +Y,
    otherwise a head-protrusion heuristic) and then VERIFIED GEOMETRICALLY
    before anything is rendered: (i) the declared axis must agree with the
    face direction measured from the evaluated mesh, (ii) ray-cast visibility
    of the eye-band and mouth-band face surface points from the camera, and
    (iii) measured face direction dot camera direction > 0.5. Skin colour is
    recorded but NEVER decides. Any failure exits 1 FacingCheckFailed before
    a single frame is rendered and no manifest is written.
  - Every camera is built from an explicit look-at basis (fixed world up =
    head-up axis); the measured roll of the head-above-feet direction must
    stay under 2 degrees (RollExceeded) and is recorded per view.
  - Every frame is checked for content (per-image non-background ratio and
    luma std, colour-space agnostic); below the floor (nonbg 0.02 / std 0.01,
    justified by the measured distribution of real renders) the run fails
    BlankFrame:<file> with exit 1 and no manifest.
  - Crops are aimed at evaluated-mesh landmarks (face-band surface points,
    outermost arm/leg extents) and the measured content-bbox centre offset
    per crop is recorded; a crop whose content centre leaves the central 60%
    of the frame fails CropOffCentre:<name>.
  - Pose "sit" rests the pelvis band on a 45 cm box under the pelvis in the
    facing frame; residual penetration is measured (sit_box_contact) and
    fails above 2 cm.
  - manifest.anatomy records mesh-measured values (height excluding scaffold
    helpers, crown-to-chin head length, shoulder width, arm span, crotch),
    the rest pose (A-pose/T-pose by hand vs shoulder height), pass/fail
    against the plan's bands, `anatomy_ok` and `SourceOutOfBand`. Out-of-band
    anatomy is REPORTED, not enforced (it is a property of the source).
  - manifest.json carries sha256 of the input and of every PNG, the full
    camera/lighting/engine contract, and NO timestamps.

Exit codes: 0 ok, 1 render/verification failure (uncaught exceptions are
    wrapped to exit 1 with RenderFailed:<type>), 2 usage errors
    (InputMissing / InvalidPreset / BadArgs), 3 BlenderRequired.
Usage validation runs BEFORE bpy is imported, so the InputMissing and
InvalidPreset paths also work under plain `python` without Blender.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile

TOOL_VERSION = 5
RESOLUTION = 1024
LENS_MM = 50.0
SENSOR_MM = 36.0
BG_GRAY = 0.5           # neutral gray, sRGB intent (PNG pixel value ~128)
BG_GRAY_LINEAR = 0.214  # the same gray as a linear color for the Background node
BLANK_NONBG_MIN = 0.02  # floors justified by measured real renders:
BLANK_STD_MIN = 0.01    # min content frame nonbg=0.12/std=0.10; blank frames
                        # measure nonbg~0.000 / std~0.0016
ROLL_LIMIT_DEG = 2.0
SIT_BOX_M = 0.45
SIT_PENETRATION_MAX_M = 0.02
CROP_CENTER_MAX_OFF = 0.30  # content-bbox centre must stay in the central 60%
VALID_PRESETS = ("G1", "G2", "G3", "G4", "G5")

# plan bands for the anatomy record (reported, not enforced by this harness)
ANATOMY_BANDS = {
    "height_m": {"min": 1.55, "max": 1.90, "note": "adult range; male sub-range 1.65-1.90"},
    "head_to_height": {"min": 0.128, "max": 0.143, "note": "7-head canon, crown-to-chin"},
    "arm_span_to_height": {"min": 0.95, "max": 1.05, "note": "T-pose only; A-pose value is recorded un-banded"},
    "crotch_to_height": {"min": 0.45, "max": 0.52, "note": "leg separation point"},
}

FULL_VIEWS = [
    ("body-front", "front", "fullbody"),
    ("body-right", "right", "fullbody"),
    ("body-back", "back", "fullbody"),
    ("body-left", "left", "fullbody"),
    ("body-threequarter", "threequarter", "fullbody"),
]
HEAD_VIEWS = [
    ("head-front", "front", "head"),
    ("head-right", "right", "head"),
    ("head-threequarter", "threequarter", "head"),
]
CROP_VIEWS = [
    ("crop-eyes", "front", "eyes", 0.16),
    ("crop-lips", "front", "lips", 0.12),
    ("crop-mouthopen", "front", "lips", 0.10),
    ("crop-ear-left", "left", "ear_left", 0.14),
    ("crop-ear-right", "right", "ear_right", 0.14),
    ("crop-neck", "front", "neck", 0.14),
    ("crop-hand-left", "threequarter_l", "hand_left", 0.22),
    ("crop-hand-right", "threequarter_r", "hand_right", 0.22),
    ("crop-elbow-left", "threequarter_l", "elbow_left", 0.24),
    ("crop-elbow-right", "threequarter_r", "elbow_right", 0.24),
    ("crop-feet-left", "front_low", "foot_left", 0.26),
    ("crop-feet-right", "front_low", "foot_right", 0.26),
]
POSES = ["walk", "sit", "elbow90", "elbow140", "armsraised"]
POSE_ANGLES = ["front", "threequarter"]

AZ_EL = {
    "front": (0.0, 0.0),
    "right": (90.0, 0.0),
    "back": (180.0, 0.0),
    "left": (-90.0, 0.0),
    "threequarter": (38.0, 18.0),
    "threequarter_l": (55.0, 12.0),
    "threequarter_r": (-55.0, 12.0),
    "front_low": (18.0, -14.0),
}

EXPECTED_RIGGED = sorted(
    [n + ".png" for n, _, _ in FULL_VIEWS]
    + [n + ".png" for n, _, _ in HEAD_VIEWS]
    + [n + ".png" for n, _, _, _ in CROP_VIEWS]
    + ["pose-%s-%s.png" % (p, a) for p in POSES for a in POSE_ANGLES]
)
EXPECTED_STATIC = sorted(
    [n + ".png" for n, _, _ in FULL_VIEWS] + [n + ".png" for n, _, _ in HEAD_VIEWS] + [n + ".png" for n, _, _, _ in CROP_VIEWS]
)


def fail(code, msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    sys.exit(code)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args(argv):
    sep = None
    for i, a in enumerate(argv):
        if a == "--":
            sep = i
            break
    args = argv[sep + 1:] if sep is not None else argv[1:]
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("input")
    ap.add_argument("out_dir")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--samples", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--self-test-flip-aim", action="store_true")
    ap.add_argument("--self-test-crash", action="store_true")
    ns, _ = ap.parse_known_args(args)
    if ns.preset not in VALID_PRESETS:
        fail(2, "InvalidPreset:%s (expected one of %s)" % (ns.preset, ",".join(VALID_PRESETS)))
    if not os.path.isfile(ns.input):
        fail(2, "InputMissing:%s" % ns.input)
    ext = os.path.splitext(ns.input)[1].lower()
    if ext not in (".glb", ".gltf", ".vrm", ".blend", ".fbx"):
        fail(2, "InputUnsupported:%s" % ext)
    try:
        os.makedirs(ns.out_dir, exist_ok=True)
    except OSError as e:
        fail(2, "OutDirUnwritable:%s (%s)" % (ns.out_dir, e))
    return ns


def glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12 or data[:4] != b"glTF":
        return None
    clen, ctype = struct.unpack_from("<I4s", data, 12)
    if ctype != b"JSON":
        return None
    try:
        return json.loads(data[20: 20 + clen].decode("utf-8"))
    except Exception:
        return None


def vrm_humanoid_nodes(path):
    j = glb_json(path) if path.lower().endswith((".glb", ".vrm")) else None
    if not j:
        return None
    nodes = j.get("nodes") or []
    exts = j.get("extensions") or {}
    out = {}
    vrm1 = (exts.get("VRMC_vrm") or {}).get("humanoid", {}).get("humanBones")
    if isinstance(vrm1, dict):
        for bone, spec in vrm1.items():
            idx = spec.get("node") if isinstance(spec, dict) else None
            if isinstance(idx, int) and 0 <= idx < len(nodes):
                name = nodes[idx].get("name")
                if name:
                    out[bone] = name
        if out:
            return out
    vrm0 = (exts.get("VRM") or {}).get("humanoid", {}).get("humanBones")
    if isinstance(vrm0, list):
        for spec in vrm0:
            idx = spec.get("node")
            if isinstance(idx, int) and 0 <= idx < len(nodes):
                name = nodes[idx].get("name")
                if name:
                    out[spec.get("bone", "")] = name
        if out:
            return out
    return None


def main(ns):
    try:
        import bpy  # noqa: F401
        import mathutils
    except ImportError:
        fail(3, "BlenderRequired: rendering needs `blender -b -P`; only usage validation works under plain python")

    import bpy
    from mathutils import Vector, Matrix, Euler
    try:
        import numpy as np
    except ImportError:
        np = None

    input_abs = os.path.abspath(ns.input)
    out_abs = os.path.abspath(ns.out_dir)
    tmp_copy = None

    bpy.ops.wm.read_factory_settings(use_empty=True)

    ext = os.path.splitext(input_abs)[1].lower()
    try:
        if ext == ".blend":
            bpy.ops.wm.open_mainfile(filepath=input_abs)
            imported = list(bpy.context.scene.objects)
        else:
            src = input_abs
            if ext == ".vrm":
                fd, tmp_copy = tempfile.mkstemp(suffix=".glb")
                os.close(fd)
                shutil.copyfile(input_abs, tmp_copy)
                src = tmp_copy
            before = set(bpy.context.scene.objects)
            op = getattr(bpy.ops.import_scene, "gltf", None)
            if op is None:
                op = getattr(bpy.ops.wm, "gltf_import", None)
            if op is None:
                raise RuntimeError("NoGLTFImporter")
            if ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=src)
            else:
                op(filepath=src)
            imported = [o for o in bpy.context.scene.objects if o not in before]
    except Exception as e:
        fail(1, "ImportFailed:%s (%s)" % (input_abs, e))
    finally:
        if tmp_copy and os.path.exists(tmp_copy):
            os.remove(tmp_copy)

    meshes = [o for o in imported if o.type == "MESH"]
    if not meshes:
        fail(1, "NoMeshObjects:%s" % input_abs)

    # Hide importer scaffolding parked in glTF_not_exported (preview helpers
    # such as the VRM Icosphere); excluded from every measurement.
    hidden_scaffolding = []
    for o in imported:
        if any(c.name == "glTF_not_exported" for c in o.users_collection):
            o.hide_render = True
            o.hide_viewport = True
            hidden_scaffolding.append(o.name)
    meshes = [o for o in meshes if not o.hide_render]
    if not meshes:
        fail(1, "NoMeshObjects:%s" % input_abs)

    # --- evaluated geometry helpers -----------------------------------------
    def evaluated_world_verts(o):
        dg = bpy.context.evaluated_depsgraph_get()
        eo = o.evaluated_get(dg)
        me = eo.to_mesh()
        try:
            mw = eo.matrix_world
            return [mw @ v.co for v in me.vertices]
        finally:
            eo.to_mesh_clear()

    def evaluated_bbox(objs):
        wmin = Vector((1e9, 1e9, 1e9))
        wmax = Vector((-1e9, -1e9, -1e9))
        for o in objs:
            for w in evaluated_world_verts(o):
                wmin.x = min(wmin.x, w.x); wmin.y = min(wmin.y, w.y); wmin.z = min(wmin.z, w.z)
                wmax.x = max(wmax.x, w.x); wmax.y = max(wmax.y, w.y); wmax.z = max(wmax.z, w.z)
        if wmin.x > wmax.x:
            return None
        return wmin, wmax

    def raw_bbox(objs):
        wmin = Vector((1e9, 1e9, 1e9))
        wmax = Vector((-1e9, -1e9, -1e9))
        for o in objs:
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                wmin.x = min(wmin.x, w.x); wmin.y = min(wmin.y, w.y); wmin.z = min(wmin.z, w.z)
                wmax.x = max(wmax.x, w.x); wmax.y = max(wmax.y, w.y); wmax.z = max(wmax.z, w.z)
        if wmin.x > wmax.x:
            return None
        return wmin, wmax

    # --- normalize placement: centre on origin, feet on Z=0 -----------------
    roots = [o for o in bpy.context.scene.objects if o.parent is None]

    def shift_roots(delta):
        for o in roots:
            o.location += delta
        bpy.context.view_layer.update()

    box = evaluated_bbox(meshes) or raw_bbox(meshes)
    wmin, wmax = box
    shift_roots(Vector((-(wmin.x + wmax.x) / 2.0, -(wmin.y + wmax.y) / 2.0, -wmin.z)))
    box = evaluated_bbox(meshes) or box
    wmin, wmax = box
    shift_roots(Vector((-(wmin.x + wmax.x) / 2.0, -(wmin.y + wmax.y) / 2.0, -wmin.z)))
    bpy.context.view_layer.update()
    box = evaluated_bbox(meshes) or box
    wmin, wmax = box
    height = wmax.z - wmin.z
    if height <= 0:
        fail(1, "DegenerateGeometry:height=%f" % height)

    all_verts = []
    obj_verts = {}
    for o in meshes:
        vs = evaluated_world_verts(o)
        obj_verts[o.name] = vs
        all_verts.extend(vs)

    # --- declared facing (VRM spec only; a plain GLB has no declaration) -----
    def detect_facing():
        j = glb_json(input_abs) if ext in (".glb", ".vrm") else None
        exts = (j or {}).get("extensions") or {}
        if "VRMC_vrm" in exts:
            # VRM 1.0: face is glTF +Z; the Blender glTF importer maps glTF +Z
            # (forward) to Blender -Y with +Z up.
            return Vector((0.0, -1.0, 0.0)), "vrm1-gltf-zplus", "high"
        if "VRM" in exts:
            # VRM 0.x: face is glTF -Z -> +Y after import.
            return Vector((0.0, 1.0, 0.0)), "vrm0-gltf-zminus", "high"
        return None, None, None

    F_declared, declared_source, declared_confidence = detect_facing()
    U = Vector((0.0, 0.0, 1.0))

    # --- face measurement from the evaluated mesh ---------------------------
    # skin/face mesh: the largest-polygon mesh object (hair cards and props
    # are separate, smaller meshes); every face measurement uses it.
    skin_mesh = max(meshes, key=lambda o: len(o.data.polygons))
    skin_verts = obj_verts[skin_mesh.name]
    # horizontal centre of the skin mesh: the shared bbox centre (hair
    # included) can be offset from the body axis, which would bias every
    # "forward" measurement
    c_skin = Vector((sum(w.x for w in skin_verts) / len(skin_verts),
                     sum(w.y for w in skin_verts) / len(skin_verts), 0.0))

    def horiz(w):
        return Vector((w.x, w.y, 0.0))

    # --- independent face-direction measurement (NO declared axis involved) --
    # Two estimates from the evaluated skin mesh, both relative to the
    # horizontal centroid of the head band:
    #   A: argmax of the support function in the CHIN band (0.815-0.87 h) —
    #      only the face has a chin protruding sideways-forward at that
    #      height; the back has the recessed nape and the sides the jaw
    #      hinge, all clearly lower.
    #   B: argmax of (support in the chin+mouth band 0.815-0.90 h MINUS the
    #      support in the brow/skull band 0.90-0.99 h) — only the face keeps
    #      substantial forward support low on the head (chin/lips); the
    #      back-of-skull loses all of it below the nape.
    # Both must agree within 20 degrees; the agreed direction must then be
    # within 20 degrees of the declared axis (VRM). For a plain GLB the
    # measured direction IS the facing (source: landmark).
    skin_head = [w for w in skin_verts if w.z > height * 0.86]
    if len(skin_head) < 50:
        fail(1, "FacingCheckFailed:no-head-vertices:%d" % len(skin_head))
    head_centre = Vector((sum(w.x for w in skin_head) / len(skin_head),
                          sum(w.y for w in skin_head) / len(skin_head), 0.0))

    CHIN_LO, CHIN_HI = height * 0.815, height * 0.87
    LOW_LO, LOW_HI = height * 0.815, height * 0.90
    UP_LO, UP_HI = height * 0.90, height * 0.99

    def support(d, zlo, zhi):
        pts = [(w - head_centre).dot(d) for w in skin_verts if zlo <= w.z <= zhi]
        return max(pts) if pts else None

    def argmax_dir(score_fn):
        best_s, best_d = -1e9, None
        for k in range(72):  # 5-degree resolution
            a = math.radians(k * 5)
            d = Vector((math.sin(a), math.cos(a), 0.0))
            s = score_fn(d)
            if s is not None and s > best_s:
                best_s, best_d = s, d
        return best_d, best_s

    dA, sA = argmax_dir(lambda d: support(d, CHIN_LO, CHIN_HI))
    dB, sB = argmax_dir(lambda d: (support(d, LOW_LO, LOW_HI) or -1e9)
                        - (support(d, UP_LO, UP_HI) or 1e9))
    if dA is None or dB is None:
        fail(1, "FacingCheckFailed:no-landmarks:estimates-unavailable")
    face_dir_meas = dA.normalized()
    angle_estimates_deg = math.degrees(dA.angle(dB))
    if angle_estimates_deg >= 20.0:
        fail(1, "FacingCheckFailed:estimates-disagree:%.1fdeg" % angle_estimates_deg)

    if F_declared is None:
        F = face_dir_meas
        facing_source = "landmark"
        facing_confidence = "medium"
        angle_declared_deg = None
    else:
        angle_declared_deg = math.degrees(F_declared.angle(face_dir_meas))
        if angle_declared_deg >= 20.0:
            fail(1, "FacingCheckFailed:axis-mismatch:%.1fdeg" % angle_declared_deg)
        F = F_declared.normalized()
        facing_source = declared_source
        facing_confidence = declared_confidence

    facing_geometry = {
        "declared_axis": [round(c, 6) for c in F_declared] if F_declared is not None else None,
        "estimate_a_chin_band": [round(c, 6) for c in dA],
        "estimate_b_chin_over_face": [round(c, 6) for c in dB],
        "face_dir_measured": [round(c, 6) for c in face_dir_meas],
        "angle_estimates_deg": round(angle_estimates_deg, 2),
        "angle_declared_deg": round(angle_declared_deg, 2) if angle_declared_deg is not None else None,
    }

    S = U.cross(F).normalized()  # side axis (+X for F = -Y); character's left

    head_radius = max(math.hypot(w.x - head_centre.x, w.y - head_centre.y) for w in skin_head)

    # crown: top of the skull of the skin mesh (hair cards excluded).
    # chin: from the forward-protrusion z-profile of the skin mesh — the
    # neck is the protrusion minimum below the head, the chin is the first
    # slice whose protrusion rises well above the neck level (a plain
    # lowest-face-vertex test grabs the front of the throat instead).
    crown_z = max(w.z for w in skin_verts)

    def fwd_proj(w):
        return horiz(w - c_skin).dot(F)

    profs = []
    zscan = height * 0.78
    while zscan < height * 0.99:
        z1 = zscan + height * 0.005
        pts = [fwd_proj(w) for w in skin_verts if zscan <= w.z < z1]
        profs.append((zscan, max(pts) if pts else None))
        zscan += height * 0.005
    neck_vals = [p for zz, p in profs if height * 0.785 <= zz <= height * 0.815 and p is not None]
    chin_z = None
    if neck_vals:
        thr = 1.8 * min(neck_vals)
        for zz, p in profs:
            if zz >= height * 0.79 and p is not None and p >= thr:
                chin_z = zz
                break
    face_verts = [w for w in skin_verts
                  if (chin_z if chin_z is not None else height * 0.80) <= w.z <= crown_z
                  and fwd_proj(w) > 0.35 * head_radius]
    if not face_verts:
        fail(1, "FacingCheckFailed:no-face-vertices")
    if chin_z is None:  # fallback: lowest face-side vertex above the neck
        chin_z = min(w.z for w in face_verts)
    head_len = crown_z - chin_z
    if head_len < 0.02:
        fail(1, "FacingCheckFailed:degenerate-head-length:%f" % head_len)

    def face_surface(zlo, zhi):
        pts = [w for w in face_verts if zlo <= w.z <= zhi]
        if not pts:
            return None
        c = Vector((sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts),
                    sum(p.z for p in pts) / len(pts)))
        return c

    eyes_z = chin_z + head_len * 0.62
    mouth_z = chin_z + head_len * 0.28
    ear_z = chin_z + head_len * 0.52
    eyes_pt = face_surface(eyes_z - head_len * 0.08, eyes_z + head_len * 0.08) or Vector((0, 0, eyes_z))
    mouth_pt = face_surface(mouth_z - head_len * 0.08, mouth_z + head_len * 0.08) or Vector((0, 0, mouth_z))
    face_center = face_surface(chin_z, crown_z) or Vector((0, 0, chin_z + head_len * 0.5))

    def side_centroid(zlo, zhi, sign, min_frac):
        pts = [w for w in all_verts
               if zlo <= w.z <= zhi and sign * horiz(w).dot(S) > min_frac * head_radius]
        if not pts:
            return None
        return Vector((sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts),
                       sum(p.z for p in pts) / len(pts)))

    ear_left = side_centroid(ear_z - head_len * 0.10, ear_z + head_len * 0.10, +1, 0.70) \
        or Vector((S.x * head_radius, S.y * head_radius, ear_z))
    ear_right = side_centroid(ear_z - head_len * 0.10, ear_z + head_len * 0.10, -1, 0.70) \
        or Vector((-S.x * head_radius, -S.y * head_radius, ear_z))

    # hands/elbows: outermost lateral extents of the arm region (evaluated mesh)
    arm_band = [w for w in all_verts if height * 0.40 <= w.z <= height * 0.95]
    if arm_band:
        ext_side = max(abs(horiz(w).dot(S)) for w in arm_band)
    else:
        ext_side = head_radius
    hand_left = side_centroid(height * 0.35, height * 0.95, +1, 0.85 * ext_side / max(head_radius, 1e-6)) \
        if ext_side else None
    hand_left = hand_left or Vector((S.x * ext_side, S.y * ext_side, height * 0.55))
    hand_right = side_centroid(height * 0.35, height * 0.95, -1, 0.85 * ext_side / max(head_radius, 1e-6)) \
        if ext_side else None
    hand_right = hand_right or Vector((-S.x * ext_side, -S.y * ext_side, height * 0.55))
    elbow_left = side_centroid(height * 0.70, height * 0.85, +1, 0.80 * ext_side / max(head_radius, 1e-6)) \
        or Vector((S.x * ext_side * 0.8, S.y * ext_side * 0.8, height * 0.76))
    elbow_right = side_centroid(height * 0.70, height * 0.85, -1, 0.80 * ext_side / max(head_radius, 1e-6)) \
        or Vector((-S.x * ext_side * 0.8, -S.y * ext_side * 0.8, height * 0.76))
    foot_left = side_centroid(0.0, height * 0.10, +1, 0.30) or Vector((S.x * height * 0.06, S.y * height * 0.06, height * 0.02))
    foot_right = side_centroid(0.0, height * 0.10, -1, 0.30) or Vector((-S.x * height * 0.06, -S.y * height * 0.06, height * 0.02))

    landmark_targets = {
        "eyes": eyes_pt, "lips": mouth_pt,
        "ear_left": ear_left, "ear_right": ear_right,
        "neck": Vector((0, 0, chin_z - head_len * 0.25)),
        "hand_left": hand_left, "hand_right": hand_right,
        "elbow_left": elbow_left, "elbow_right": elbow_right,
        "foot_left": foot_left, "foot_right": foot_right,
    }
    landmark_source = "evaluated-mesh-geometry"

    # --- anatomy (mesh-measured; reported, not enforced) --------------------
    def band_width(zlo, zhi):
        pts = [horiz(w).dot(S) for w in all_verts if zlo <= w.z <= zhi]
        if not pts:
            return None
        return max(pts) - min(pts)

    shoulder_w = band_width(height * 0.78, height * 0.88)
    span_w = band_width(height * 0.40, height * 0.90)
    shoulder_z_est = height * 0.82
    hand_z_est = (hand_left.z + hand_right.z) / 2.0
    rest_pose = "T-pose" if hand_z_est > shoulder_z_est - height * 0.05 else "A-pose"

    crotch_z = None
    # crotch = the lowest z at which the evaluated BODY mesh surface still
    # CROSSES the left/right mid-plane (the inseam vertex): triangle-plane
    # intersections at x = body axis. Below the inseam the legs open on both
    # sides of the plane, so the surface never crosses it again. No bones,
    # no gap tolerance, no vertex-proximity heuristic (which slips ~3 cm
    # between the legs on this asset).

    def inseam_crossing_min_z(obj, x0):
        dg = bpy.context.evaluated_depsgraph_get()
        eo = obj.evaluated_get(dg)
        me = eo.to_mesh()
        try:
            mw = eo.matrix_world
            nv = len(me.vertices)
            nt = len(me.loop_triangles)
            if nv == 0 or nt == 0:
                return None
            varr = np.empty(nv * 3, dtype=np.float64)
            me.vertices.foreach_get("co", varr)
            tri = np.empty(nt * 3, dtype=np.int32)
            me.loop_triangles.foreach_get("vertices", tri)
            R = np.array([[mw.row[i][j] for j in range(3)] for i in range(3)])
            t = np.array([mw.row[i][3] for i in range(3)])
            P = varr.reshape(-1, 3) @ R.T + t
            xs = P[:, 0]
            T = tri.reshape(-1, 3)
            a = T[:, [0, 1, 2]]
            b = T[:, [1, 2, 0]]
            xa, xb = xs[a], xs[b]
            cross = ((xa - x0) * (xb - x0)) <= 0
            diff = xb - xa
            with np.errstate(divide="ignore", invalid="ignore"):
                frac = np.where(cross & (np.abs(diff) > 1e-12), (x0 - xa) / np.where(np.abs(diff) > 1e-12, diff, 1.0), 0.0)
            za, zb = P[a, 2], P[b, 2]
            z_cross = np.where(cross & (np.abs(diff) > 1e-12), za + frac * (zb - za), np.inf)
            zmin = z_cross.min(axis=1)
            zmin = zmin[np.isfinite(zmin)]
            zmin = zmin[(zmin >= height * 0.15) & (zmin <= height * 0.65)]
            return float(zmin.min()) if len(zmin) else None
        finally:
            eo.to_mesh_clear()

    if np is not None:
        crotch_z = inseam_crossing_min_z(skin_mesh, c_skin.x)

    central = [w.z for w in all_verts
               if height * 0.22 <= w.z <= height * 0.65
               and abs(horiz(w - c_skin).dot(S)) < height * 0.02]
    lowest_central_vertex = min(central) if central else None

    # torso facing: direction of the torso's front-most chest protrusion,
    # estimated like the head (support-function argmax on the chest band).
    # RECORDED ONLY: a head/torso mismatch is a property of the source asset
    # (user decision, plan IS-level), never a render failure.
    chest_band_all = [w for w in skin_verts if height * 0.58 <= w.z <= height * 0.80]
    chest_band = [w for w in chest_band_all if abs(horiz(w - c_skin).dot(S)) < height * 0.25]  # exclude the spread arms
    torso_facing = None
    if len(chest_band) > 50:
        torso_centre = Vector((sum(w.x for w in chest_band) / len(chest_band),
                               sum(w.y for w in chest_band) / len(chest_band), 0.0))
        best_s, torso_dir = -1e9, None
        for k in range(72):
            a = math.radians(k * 5)
            d = Vector((math.sin(a), math.cos(a), 0.0))
            pts = [(w - torso_centre).dot(d) for w in chest_band]
            if pts and max(pts) > best_s:
                best_s, torso_dir = max(pts), d
        if torso_dir is not None:
            torso_facing = {
                "axis": [round(c, 6) for c in torso_dir],
                "method": "chest-band support argmax (recorded, not enforced)",
                "head_torso_angle_deg": round(math.degrees(F.angle(torso_dir)), 2),
            }

    anatomy_measured = {
        "height_m": round(height, 6),
        "head_to_height": round(head_len / height, 4),
        "crown_chin_m": round(head_len, 6),
    }
    if shoulder_w is not None:
        anatomy_measured["shoulder_to_height"] = round(shoulder_w / height, 4)
    if span_w is not None:
        anatomy_measured["arm_span_to_height"] = round(span_w / height, 4)
        anatomy_measured["arm_span_m"] = round(span_w, 6)
    if crotch_z is not None:
        anatomy_measured["crotch_to_height"] = round(crotch_z / height, 4)
        anatomy_measured["crotch_z_m"] = round(crotch_z, 6)
    if lowest_central_vertex is not None:
        anatomy_measured["lowest_central_vertex_m"] = round(lowest_central_vertex, 6)
    anatomy_measured["hand_z_to_height"] = round(hand_z_est / height, 4)

    anatomy_bands = {}
    out_of_band = []
    for key, band in ANATOMY_BANDS.items():
        val = anatomy_measured.get(key)
        if val is None:
            anatomy_bands[key] = "unmeasured"
            continue
        if key == "arm_span_to_height" and rest_pose == "A-pose":
            anatomy_bands[key] = "not-banded (rest pose is A-pose; T-pose band does not apply, value recorded)"
            continue
        ok = band["min"] <= val <= band["max"]
        anatomy_bands[key] = "pass" if ok else "fail(%s outside %s-%s)" % (val, band["min"], band["max"])
        if not ok:
            out_of_band.append(key)
    anatomy_ok = not out_of_band
    anatomy = {
        "measured": anatomy_measured,
        "bands": anatomy_bands,
        "rest_pose": rest_pose,
        "anatomy_ok": anatomy_ok,
        "SourceOutOfBand": out_of_band,
        "notes": ["height and all ratios exclude glTF_not_exported scaffolding",
                  "head length is skin-mesh crown-to-chin (hair cards excluded); height includes hair cards",
                  "crotch is the leg-separation height of the evaluated mesh (no bones); the lowest central torso vertex is recorded alongside",
                  "shoulder width includes A-pose arms spread beside the torso unless the rest pose is T-pose",
                  "out-of-band anatomy is reported, not enforced: it is a property of the source",
                  "crotch = lowest z where the evaluated body-mesh surface still crosses the mid-plane (inseam vertex; triangle-plane intersection, no gap tolerance)"],
    }
    if torso_facing and torso_facing["head_torso_angle_deg"] > 45.0:
        anatomy["notes"].append(
            "HeadTorsoMismatch: head faces %s but the torso chest protrusion points %s "
            "(angle %.1f deg) — a property of the source asset, reported for the user's "
            "decision, not enforced by this harness"
            % ([round(c, 2) for c in (F.x, F.y, F.z)], torso_facing["axis"],
               torso_facing["head_torso_angle_deg"]))

    # --- scene: world (node-based; non-node worlds are ignored by 5.2) ------
    scene = bpy.context.scene
    world = bpy.data.worlds.new("GateReviewWorld")
    world.use_nodes = True
    bg_node = world.node_tree.nodes.get("Background")
    if bg_node is None:
        bg_node = world.node_tree.nodes.new("ShaderNodeBackground")
        out_node = world.node_tree.nodes.get("Output")
        if out_node is None:
            out_node = world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(bg_node.outputs["Background"], out_node.inputs["Surface"])
    bg_node.inputs["Color"].default_value = (BG_GRAY_LINEAR, BG_GRAY_LINEAR, BG_GRAY_LINEAR, 1.0)
    bg_node.inputs["Strength"].default_value = 1.0
    scene.world = world

    sun_specs = [
        ("key", 45.0, 50.0, 3.0),
        ("fill", -40.0, 25.0, 1.0),
        ("rim", 195.0, 35.0, 1.6),
    ]
    for name, az, el, energy in sun_specs:
        ld = bpy.data.lights.new("sun-%s" % name, "SUN")
        ld.energy = energy
        ld.use_shadow = True
        ob = bpy.data.objects.new("sun-%s" % name, ld)
        azr, elr = math.radians(az), math.radians(el)
        ob.rotation_euler = Euler((math.radians(90) - elr, 0.0, azr), "XYZ")
        scene.collection.objects.link(ob)

    cam_data = bpy.data.cameras.new("GateReviewCam")
    cam_data.lens = LENS_MM
    cam_data.sensor_width = SENSOR_MM
    cam = bpy.data.objects.new("GateReviewCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.filter_size = 1.5

    engine = ns.engine
    avail = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if engine == "auto":
        for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
            if cand in avail:
                engine = cand
                break
    if engine in ("eevee", "cycles"):
        want = {"eevee": ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"], "cycles": ["CYCLES"]}[engine]
        engine = next((c for c in want if c in avail), None)
    eng_key = engine
    if eng_key not in avail:
        fail(2, "EngineUnavailable:%s" % engine)
    scene.render.engine = eng_key
    if eng_key.startswith("BLENDER_EEVEE"):
        ee = scene.eevee
        ee.taa_render_samples = ns.samples if ns.samples else 32
        ee.use_raytracing = False
        ee.use_shadows = True
    elif eng_key == "CYCLES":
        cy = scene.cycles
        cy.samples = ns.samples if ns.samples else 48
        cy.seed = ns.seed
        cy.use_denoising = False
        cy.device = "CPU"
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 16

    # --- camera placement: explicit look-at basis + roll assertion ----------
    fov_half = math.atan((SENSOR_MM / 2.0) / LENS_MM)
    camera_rolls = {}

    def dir_for(view):
        az, el = AZ_EL[view]
        azr, elr = math.radians(az), math.radians(el)
        off = (F * math.cos(azr) + S * math.sin(azr)) * math.cos(elr) + U * math.sin(elr)
        return off.normalized()

    def place_cam(target, frame_h, view, roll_name):
        off = dir_for(view)
        if ns.self_test_flip_aim:
            off = -off
        dist = (frame_h * 0.88 / 2.0) / math.tan(fov_half) + frame_h * 0.1
        cam.location = target + off * dist
        zc = off.normalized()
        xc = U.cross(zc).normalized()
        yc = zc.cross(xc)
        cam.rotation_euler = Matrix((xc, yc, zc)).transposed().to_euler()
        bpy.context.view_layer.update()
        R = cam.matrix_world.to_3x3()
        v = U @ R  # world up in camera coordinates
        roll = math.degrees(math.atan2(v.x, v.y))
        camera_rolls[roll_name] = round(roll, 3)
        if abs(roll) >= ROLL_LIMIT_DEG:
            fail(1, "RollExceeded:%s:%.2fdeg" % (roll_name, roll))
        return off

    head_center = Vector((0, 0, chin_z + head_len * 0.5))
    head_frame = head_len * 2.6

    # --- FACING CHECK: before any official frame is written -------------------
    # (i) two 256 px test renders (temp files, deleted immediately): the
    #     declared side must be at least as face-like as the opposite side
    #     (skin-tone share, RELATIVE comparison; no absolute skin threshold
    #     ever decides on its own),
    # (ii) the eye-band and mouth-band face surface points must be ray-visible
    #      from the head-front camera (not occluded by the skull),
    # (iii) the declared face direction dot the camera direction > 0.5.
    def ray_visible_from_cam(point, tol):
        dg = bpy.context.evaluated_depsgraph_get()
        origin = cam.matrix_world.translation.copy()
        d = point - origin
        dist = d.length
        if dist < 1e-6:
            return False
        d.normalize()
        res = scene.ray_cast(dg, origin, d)
        if not res[0]:
            return False
        return (res[1] - point).length <= tol

    def skin_share(path):
        """fraction of skin-tone pixels in a rendered frame"""
        img = bpy.data.images.load(path)
        try:
            n = len(img.pixels)
            if np is not None:
                arr = np.empty(n, dtype=np.float32)
                img.pixels.foreach_get(arr)
                rgb = arr.reshape(-1, 4)[::2, :3]
                r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
                skin = ((r > 0.20) & (r < 0.95) & (g > 0.12) & (g < 0.85)
                        & (b > 0.08) & (b < 0.75)
                        & (r >= g + 0.01) & (g >= b - 0.03) & ((r - b) < 0.50))
                return float(skin.mean())
            px = list(img.pixels)
            samples = [(px[i], px[i + 1], px[i + 2]) for i in range(0, n - 3, 8)]
            hit = 0
            for r, g, b in samples:
                if (0.20 < r < 0.95 and 0.12 < g < 0.85 and 0.08 < b < 0.75
                        and r >= g + 0.01 and g >= b - 0.03 and (r - b) < 0.50):
                    hit += 1
            return hit / len(samples)
        finally:
            bpy.data.images.remove(img)

    def face_test_render(off_dir):
        old_x, old_y = scene.render.resolution_x, scene.render.resolution_y
        scene.render.resolution_x = scene.render.resolution_y = 256
        dist = (head_frame * 0.88 / 2.0) / math.tan(fov_half) + head_frame * 0.1
        cam.location = head_center + off_dir * dist
        zc = off_dir.normalized()
        xc = U.cross(zc).normalized()
        yc = zc.cross(xc)
        cam.rotation_euler = Matrix((xc, yc, zc)).transposed().to_euler()
        bpy.context.view_layer.update()
        fd, tp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        scene.render.filepath = tp
        bpy.ops.render.render(write_still=True)
        scene.render.resolution_x, scene.render.resolution_y = old_x, old_y
        share = skin_share(tp)
        os.remove(tp)
        return share

    sk_declared = face_test_render(F.normalized())
    sk_opposite = face_test_render((-F).normalized())

    place_cam(head_center, head_frame, "front", "head-front")
    cam_pos = cam.matrix_world.translation.copy()
    cam_dir = (cam_pos - face_center).normalized()
    dot_face = F.dot(cam_dir)
    eyes_vis = ray_visible_from_cam(eyes_pt, 0.45 * head_len)
    mouth_vis = ray_visible_from_cam(mouth_pt, 0.45 * head_len)
    from bpy_extras.object_utils import world_to_camera_view
    ndc = world_to_camera_view(scene, cam, eyes_pt)
    eyes_in_frame = bool(0.05 < ndc.x < 0.95 and 0.05 < ndc.y < 0.95 and ndc.z > 0)
    # SECONDARY signals only — recorded, never deciding. The decision is the
    # geometric angle test (declared-vs-measured + estimate agreement) that
    # already ran before any render.
    facing_geometry.update({
        "test_render_skin_declared_side": round(sk_declared, 4),
        "test_render_skin_opposite_side": round(sk_opposite, 4),
        "face_dot_camera": round(dot_face, 4),
        "eyes_surface_visible": eyes_vis,
        "mouth_surface_visible": mouth_vis,
        "eyes_in_frame": eyes_in_frame,
    })

    # --- per-frame content check (blank-frame rejection) ---------------------
    BG_CANDIDATES = (BG_GRAY, BG_GRAY_LINEAR)  # 0.502 (sRGB float) / 0.214 (linear float)
    BG_TOL = 0.02

    def frame_stats(path):
        img = bpy.data.images.load(path)
        try:
            n = len(img.pixels)
            w, h = img.size
            if np is not None:
                arr = np.empty(n, dtype=np.float32)
                img.pixels.foreach_get(arr)
                rgb = arr.reshape(-1, 4)[::4, :3]
                luma = rgb.mean(axis=1)
                d0 = np.abs(luma - BG_CANDIDATES[0])
                d1 = np.abs(luma - BG_CANDIDATES[1])
                dist = np.minimum(d0, d1)
                nonbg = float((dist > BG_TOL).mean())
                std = float(luma.std())
                mask = dist > BG_TOL
                if mask.any():
                    w2 = w // 4  # the sampled grid is (h, w//4) row-major
                    idx = np.nonzero(mask)[0]
                    rows = idx // w2
                    cols = (idx % w2) * 4
                    cx = float((cols.min() + cols.max()) / 2.0) / w
                    cy = float((rows.min() + rows.max()) / 2.0) / h
                else:
                    cx = cy = None
                return nonbg, std, cx, cy
            px = list(img.pixels)
            samples = [sum((px[i], px[i + 1], px[i + 2])) / 3.0 for i in range(0, n - 3, 16)]
            dist = [min(abs(x - c) for c in BG_CANDIDATES) for x in samples]
            nonbg = sum(1 for x in dist if x > BG_TOL) / len(dist)
            m = sum(samples) / len(samples)
            std = (sum((x - m) ** 2 for x in samples) / len(samples)) ** 0.5
            return nonbg, std, None, None
        finally:
            bpy.data.images.remove(img)

    files = []
    crop_offsets = {}

    PNG_TEXT_STRIP = ("Date", "RenderTime")  # Blender tEXt chunks differ per
    # render; stripping them makes identical inputs produce identical bytes.

    def snap_pixels_2lsb(path):
        """Stabilise EEVEE's dither non-determinism (observed amplitude 1-3
        LSB on isolated pixels): snap every channel to a 4-step grid. The
        visual cost is <= 2/255 per channel; the benefit is that the same
        input renders to identical bytes (sha256)."""
        img = bpy.data.images.load(path)
        try:
            n = len(img.pixels)
            if np is not None:
                arr = np.empty(n, dtype=np.float32)
                img.pixels.foreach_get(arr)
                v8 = np.rint(arr * 255.0)
                v8s = ((v8 + 2.0) // 4.0) * 4.0
                arr2 = (v8s / 255.0).astype(np.float32)
                img.pixels.foreach_set(arr2)
            else:
                px = list(img.pixels)
                for i in range(len(px)):
                    v8 = round(px[i] * 255.0)
                    px[i] = (((v8 + 2) // 4) * 4) / 255.0
                img.pixels = px
            img.save_render(path, scene=scene)
        finally:
            bpy.data.images.remove(img)

    def strip_png_text_chunks(path):
        with open(path, "rb") as f:
            data = f.read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return
        out = [data[:8]]
        i = 8
        while i + 12 <= len(data):
            ln = struct.unpack(">I", data[i:i + 4])[0]
            ctype = data[i + 4:i + 8]
            chunk_end = i + 12 + ln
            if ctype in (b"tEXt", b"zTXt", b"iTXt") and chunk_end <= len(data):
                kw = data[i + 8:i + 40].split(b"\x00", 1)[0].decode("latin-1", "ignore")
                if kw in PNG_TEXT_STRIP:
                    i = chunk_end
                    continue
            out.append(data[i:chunk_end])
            i = chunk_end
        with open(path, "wb") as f:
            f.write(b"".join(out))

    def render_one(name, view, target, frame_h, enforce_center=False):
        def shoot(t):
            path = os.path.join(out_abs, name + ".png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            if not os.path.isfile(path):
                raise RuntimeError("RenderFailed:%s" % path)
            snap_pixels_2lsb(path)
            strip_png_text_chunks(path)
            return frame_stats(path)

        nonbg, std, cx, cy = shoot(target)
        if nonbg < BLANK_NONBG_MIN or std < BLANK_STD_MIN:
            fail(1, "BlankFrame:%s.png(nonbg=%.4f,std=%.4f,floor=%.2f/%.2f)"
                 % (name, nonbg, std, BLANK_NONBG_MIN, BLANK_STD_MIN))
        if enforce_center:
            # self-correcting crop: nudge the target toward the content centre
            # (max one correction pass), then record the residual offset
            for _ in range(2):
                dx, dy = (cx - 0.5), (cy - 0.5)
                if abs(dx) <= 0.15 and abs(dy) <= 0.15:
                    break
                R = cam.matrix_world.to_3x3()
                right = R @ Vector((1, 0, 0))   # world direction of camera +X
                up = R @ Vector((0, 1, 0))     # world direction of camera +Y
                # Blender pixel buffers are bottom-up: shift the target
                # TOWARD the content centre (visual servoing) to re-centre it
                target = target + right * (dx * frame_h) + up * (dy * frame_h)
                place_cam(target, frame_h, view, name)
                nonbg, std, cx, cy = shoot(target)
                if nonbg < BLANK_NONBG_MIN or std < BLANK_STD_MIN:
                    fail(1, "BlankFrame:%s.png(nonbg=%.4f,std=%.4f,floor=%.2f/%.2f)"
                         % (name, nonbg, std, BLANK_NONBG_MIN, BLANK_STD_MIN))
            dx, dy = (cx - 0.5), (cy - 0.5)
            crop_offsets[name] = {"cx": round(cx, 4), "cy": round(cy, 4),
                                  "dx": round(dx, 4), "dy": round(dy, 4)}
            if abs(dx) > CROP_CENTER_MAX_OFF or abs(dy) > CROP_CENTER_MAX_OFF:
                fail(1, "CropOffCentre:%s:dx=%.3f,dy=%.3f(limit=%.2f)"
                     % (name, dx, dy, CROP_CENTER_MAX_OFF))
        files.append({"name": name + ".png", "sha256": sha256_file(os.path.join(out_abs, name + ".png")),
                      "bytes": os.path.getsize(os.path.join(out_abs, name + ".png")),
                      "nonbg_ratio": round(nonbg, 5), "std_luma": round(std, 5)})
        return os.path.join(out_abs, name + ".png")

    def skin_ratio(path):
        img = bpy.data.images.load(path)
        try:
            n = len(img.pixels)
            if np is not None:
                arr = np.empty(n, dtype=np.float32)
                img.pixels.foreach_get(arr)
                rgb = arr.reshape(-1, 4)[::2, :3]
                r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
                skin = ((r > 0.20) & (r < 0.95) & (g > 0.12) & (g < 0.85)
                        & (b > 0.08) & (b < 0.75)
                        & (r >= g + 0.01) & (g >= b - 0.03) & ((r - b) < 0.50))
                return float(skin.mean())
            px = list(img.pixels)
            samples = [(px[i], px[i + 1], px[i + 2]) for i in range(0, n - 3, 8)]
            hit = 0
            for r, g, b in samples:
                if (0.20 < r < 0.95 and 0.12 < g < 0.85 and 0.08 < b < 0.75
                        and r >= g + 0.01 and g >= b - 0.03 and (r - b) < 0.50):
                    hit += 1
            return hit / len(samples)
        finally:
            bpy.data.images.remove(img)

    # head-front first (camera is already placed by the facing check)
    hf = render_one("head-front", "front", head_center, head_frame)
    facing_check = dict(facing_geometry)
    facing_check["skin_ratio_head_front"] = round(skin_ratio(hf), 5)  # informational only

    if ns.self_test_crash:
        raise RuntimeError("SelfTestCrash")

    body_target = Vector((0.0, 0.0, height * 0.5))
    for name, view, kind in FULL_VIEWS:
        place_cam(body_target, height, view, name)
        render_one(name, view, body_target, height)
    for name, view, kind in HEAD_VIEWS:
        if name == "head-front":
            continue
        place_cam(head_center, head_frame, view, name)
        render_one(name, view, head_center, head_frame)

    # crops (mesh landmarks, centre enforcement)
    def shape_keys_matching(pred):
        found = []
        for o in meshes:
            ks = o.data.shape_keys
            if not ks:
                continue
            for kb in ks.key_blocks:
                if pred(kb.name):
                    found.append((o.name, kb.name))
        return found

    def set_shape_keys(entries, value):
        for oname, kbname in entries:
            o = bpy.data.objects.get(oname)
            ks = o.data.shape_keys if o else None
            kb = ks.key_blocks.get(kbname) if ks else None
            if kb is not None:
                kb.value = value

    MOUTH_OPEN_KEYS = ("aa", "a", "jaw_open", "jawopen", "fcl_mth_a")
    mouth_open_keys = shape_keys_matching(
        lambda n: n.strip().lower() in MOUTH_OPEN_KEYS
        or ("jaw" in n.lower() and "open" in n.lower())
        or ("mouth" in n.lower() and "open" in n.lower())
    )

    for name, view, lm, fh_frac in CROP_VIEWS:
        target = landmark_targets[lm]
        if lm.startswith("foot"):
            target = Vector((target.x, target.y, target.z + height * fh_frac * 0.35))
        opened = []
        if name == "crop-mouthopen" and mouth_open_keys:
            set_shape_keys(mouth_open_keys, 1.0)
            opened = mouth_open_keys
            bpy.context.view_layer.update()
        place_cam(target, height * fh_frac, view, name)
        render_one(name, view, target, height * fh_frac, enforce_center=True)
        if opened:
            set_shape_keys(opened, 0.0)
            bpy.context.view_layer.update()

    # --- poses (rigged only) ------------------------------------------------
    vrm_map = vrm_humanoid_nodes(input_abs)
    armature = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    poses_applied = []
    poses_skipped = []
    pose_bones_used = {}
    sit_box_contact = None
    box_obj = None

    def pose_bones(keys):
        out = {}
        for key in keys:
            nm = (vrm_map or {}).get(key)
            pb = armature.pose.bones.get(nm) if (armature and nm) else None
            if pb is not None:
                out[key] = pb
        return out

    def set_deg(pb, axis, deg):
        if pb.rotation_mode != "XYZ":
            pb.rotation_mode = "XYZ"
        setattr(pb.rotation_euler, axis, math.radians(deg))

    def saved_pose():
        return {n: p.matrix.copy() for n, p in armature.pose.bones.items()} if armature else {}

    def restore_pose(saved, obj_loc):
        for n, m in saved.items():
            armature.pose.bones[n].matrix = m
        if armature and obj_loc is not None:
            armature.location = obj_loc
        bpy.context.view_layer.update()

    def seat_surface_z():
        band_lo, band_hi = height * 0.40, height * 0.60
        zs = []
        for o in meshes:
            for w in evaluated_world_verts(o):
                if band_lo <= w.z <= band_hi:
                    zs.append(w.z)
        return min(zs) if zs else None

    if armature is None:
        poses_skipped.append({"pose": "ALL", "reason": "NoArmature"})
    else:
        need = ("hips", "neck", "head", "leftUpperArm", "rightUpperArm",
                "leftLowerArm", "rightLowerArm", "leftUpperLeg", "rightUpperLeg",
                "leftLowerLeg", "rightLowerLeg", "leftFoot", "rightFoot")
        found = pose_bones(need)
        if len(found) < 6:
            poses_skipped.append({"pose": "ALL", "reason": "PoseBonesNotFound:%d/13" % len(found)})
        else:
            def apply_pose(which):
                if which == "walk":
                    for k, s in (("leftUpperLeg", 26), ("rightUpperLeg", -24)):
                        if k in found: set_deg(found[k], "x", s)
                    for k, s in (("leftLowerLeg", -8), ("rightLowerLeg", 14)):
                        if k in found: set_deg(found[k], "x", s)
                    for k, s in (("leftUpperArm", -18), ("rightUpperArm", 18)):
                        if k in found: set_deg(found[k], "x", s)
                    for k, s in (("leftLowerArm", -12), ("rightLowerArm", -12)):
                        if k in found: set_deg(found[k], "z", s)
                elif which == "sit":
                    for k in ("leftUpperLeg", "rightUpperLeg"):
                        if k in found: set_deg(found[k], "x", -84)
                    for k in ("leftLowerLeg", "rightLowerLeg"):
                        if k in found: set_deg(found[k], "x", 84)
                    for k in ("leftFoot", "rightFoot"):
                        if k in found: set_deg(found[k], "x", 12)
                    for k, s in (("leftUpperArm", 62), ("rightUpperArm", 62)):
                        if k in found: set_deg(found[k], "z", s if k == "leftUpperArm" else -s)
                    for k in ("leftLowerArm", "rightLowerArm"):
                        if k in found: set_deg(found[k], "y", -18)
                elif which == "elbow90":
                    for k, s in (("leftLowerArm", 84), ("rightLowerArm", -84)):
                        if k in found: set_deg(found[k], "z", s)
                elif which == "elbow140":
                    for k, s in (("leftLowerArm", 40), ("rightLowerArm", -40)):
                        if k in found: set_deg(found[k], "z", s)
                elif which == "armsraised":
                    for k, s in (("leftUpperArm", 75), ("rightUpperArm", -75)):
                        if k in found: set_deg(found[k], "z", s)
                    for k, s in (("leftLowerArm", 8), ("rightLowerArm", -8)):
                        if k in found: set_deg(found[k], "z", s)

            for which in POSES:
                saved = saved_pose()
                obj_loc = armature.location.copy()
                box_obj = None
                try:
                    apply_pose(which)
                    if which == "sit":
                        bpy.context.view_layer.update()
                        seat0 = seat_surface_z()
                        if seat0 is not None:
                            armature.location.z += (SIT_BOX_M - seat0)
                            bpy.context.view_layer.update()
                        seat1 = seat_surface_z()
                        pelvis_pts = [w for w in all_verts if height * 0.45 <= w.z <= height * 0.60]
                        pelvis_xy = None
                        if pelvis_pts:
                            pelvis_xy = Vector((sum(p.x for p in pelvis_pts) / len(pelvis_pts),
                                                sum(p.y for p in pelvis_pts) / len(pelvis_pts), 0))
                        bm = bpy.data.meshes.new("sitbox")
                        s = SIT_BOX_M
                        vs = [(-s / 2, -s / 2, 0), (s / 2, -s / 2, 0), (s / 2, s / 2, 0), (-s / 2, s / 2, 0),
                              (-s / 2, -s / 2, s), (s / 2, -s / 2, s), (s / 2, s / 2, s), (-s / 2, s / 2, s)]
                        fs = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
                        bm.from_pydata(vs, [], fs)
                        box_obj = bpy.data.objects.new("sit-box-45cm", bm)
                        box_obj.color = (0.35, 0.35, 0.35, 1)
                        bpy.context.scene.collection.objects.link(box_obj)
                        box_obj.location = ((pelvis_xy.x if pelvis_xy else 0.0) + F.x * 0.10,
                                            (pelvis_xy.y if pelvis_xy else 0.0) + F.y * 0.10,
                                            0.0)
                        bpy.context.view_layer.update()
                        penetration = (SIT_BOX_M - seat1) if seat1 is not None else None
                        sit_box_contact = {
                            "box_top_m": SIT_BOX_M,
                            "seat_z_m": round(seat1, 6) if seat1 is not None else None,
                            "penetration_m": round(penetration, 6) if penetration is not None else None,
                        }
                        if penetration is None or penetration > SIT_PENETRATION_MAX_M:
                            fail(1, "SitBoxPenetration:%s" % penetration)
                    bpy.context.view_layer.update()
                    for angle in POSE_ANGLES:
                        place_cam(body_target, height, angle, "pose-%s-%s" % (which, angle))
                        render_one("pose-%s-%s" % (which, angle), angle, body_target, height)
                    poses_applied.append(which)
                    pose_bones_used[which] = sorted(found.keys())
                finally:
                    if box_obj is not None:
                        bpy.data.meshes.remove(box_obj.data, do_unlink=True)
                        box_obj = None
                    restore_pose(saved, obj_loc)

    # --- manifest -----------------------------------------------------------
    expected = EXPECTED_RIGGED if poses_applied else EXPECTED_STATIC
    produced = sorted(f["name"] for f in files)
    if produced != sorted(expected):
        fail(1, "RenderViewSetMismatch:missing=%s;extra=%s" % (
            sorted(set(expected) - set(produced)), sorted(set(produced) - set(expected))))

    nonbg_values = [f["nonbg_ratio"] for f in files]
    manifest = {
        "tool": "tools/gate/render-views.py",
        "tool_version": TOOL_VERSION,
        "preset": ns.preset,
        "input": {"file": os.path.basename(input_abs), "path": input_abs, "sha256": sha256_file(input_abs),
                  "bytes": os.path.getsize(input_abs)},
        "contract": {
            "engine": eng_key,
            "seed": ns.seed if eng_key == "CYCLES" else None,
            "resolution": [RESOLUTION, RESOLUTION],
            "lens_mm": LENS_MM,
            "sensor_mm": SENSOR_MM,
            "background_gray": BG_GRAY,
            "background_gray_linear": BG_GRAY_LINEAR,
            "camera_rig": "gate-fixed-v5",
            "camera_construction": "explicit look-at basis, fixed world up, roll<%.1fdeg asserted" % ROLL_LIMIT_DEG,
            "facing_check": "independent geometric measurement (chin-band support argmax + chin/face ratio argmax, agreement<20deg) vs the declared axis (<20deg); relative skin renders and ray visibility are recorded secondary signals only",
            "blank_frame": {"nonbg_min": BLANK_NONBG_MIN, "std_min": BLANK_STD_MIN,
                            "measured": {"min_nonbg": round(min(nonbg_values), 5),
                                          "median_nonbg": round(sorted(nonbg_values)[len(nonbg_values) // 2], 5),
                                          "max_nonbg": round(max(nonbg_values), 5)}},
            "crop_centering": {"max_offset": CROP_CENTER_MAX_OFF},
            "lighting": {"rig": "fixed-3point-sun-v1", "suns": [
                {"name": n, "azimuth_deg": a, "elevation_deg": e, "energy": en}
                for n, a, e, en in sun_specs]},
            "color_management": {"view_transform": "Standard", "look": "None",
                                 "exposure": 0.0, "gamma": 1.0},
            "png": {"format": "PNG", "color_mode": "RGB", "depth": 8, "compression": 15,
                    "deterministic": "Date/RenderTime tEXt chunks stripped; identical inputs produce identical sha256"},
            "blender_version": bpy.app.version_string,
            "view_spec": "docs/gate/vision-review.ko.md#render-contract",
        },
        "facing": {
            "source": facing_source,
            "axis": [round(c, 6) for c in F],
            "up": [round(c, 6) for c in U],
            "confidence": facing_confidence,
            "torso_facing": torso_facing,
            "check": facing_check,
        },
        "camera_rolls_deg": camera_rolls,
        "crop_offsets": crop_offsets,
        "anatomy": anatomy,
        "anatomy_ok": anatomy_ok,
        "geometry": {"height_m": round(height, 6), "head_len_m": round(head_len, 6)},
        "rigged": armature is not None,
        "landmark_source": landmark_source,
        "hidden_scaffolding": sorted(hidden_scaffolding),
        "expressions": {
            "mouth_open": {
                "shape_keys": sorted(set("%s:%s" % e for e in mouth_open_keys)),
                "applied": bool(mouth_open_keys),
            },
        },
        "poses": {"applied": poses_applied, "skipped": poses_skipped, "bones": pose_bones_used,
                  "sit_box_contact": sit_box_contact},
        "file_count": len(files),
        "files": sorted(files, key=lambda f: f["name"]),
    }
    mpath = os.path.join(out_abs, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({"ok": True, "manifest": mpath, "files": len(files),
                      "rigged": manifest["rigged"], "poses": poses_applied,
                      "facing": manifest["facing"]["source"],
                      "anatomy_ok": anatomy_ok}, sort_keys=True))


if __name__ == "__main__":
    ns = parse_args(sys.argv)
    try:
        main(ns)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the Blender -P runner swallows
        # uncaught exceptions and exits 0; force a real failure code.
        sys.stderr.write("RenderFailed:%s:%s\n" % (type(exc).__name__, exc))
        sys.stderr.flush()
        sys.exit(1)
