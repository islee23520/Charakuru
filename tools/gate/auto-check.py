"""Automatic quality gate checks G1-G5 (Blender part of todo 5).

Run: blender -b --factory-startup -P tools/gate/auto-check.py -- <in> <gate> <out.json> [options]

  gate      G1 | G2 | G3 | G4 | G5
  --profile <name>     gen-profiles.json profile (G1). Fallback: "profile" in part.json /
                       selection.json next to or above <in>. No profile -> ProfileMissing.
  --kind body|head|part  G1 kind (default: head for profile "head", body for "base_body", else part)
  --only a,b,...       G1 subset: format,components,arms,neck,fingers,anatomy,envelope,eyes,mouth,baked_eye
  --source <glb>       G1 fingers: compare finger-tip counts with this unreduced source (FingersMerged)
  --gender f|m         anatomy height band (default: from --height-band, else either band 1.55-1.90 m)
  --stated-height <m>  anatomy: total height within 4% of the person's stated height
  --bones a,b,...      G2 required bone names (used when characters/_cast/contract.json is absent)
  --bones-json <file>  G2 required bones as [{"name":..,"parent":..}] or {"bones":[...]}
  --budget-person <id> G5 label only

Output JSON: {gate, input, input_sha256, format, ok, blocking[], pass, codes[], checks{}, notes[]}.
  ok/blocking is the gate.mjs contract: ok = the check ran (exit 0/1), blocking = failure codes (empty
  on pass). pass = no failure, codes = alias of blocking.
Exit: 0 pass, 1 fail (codes non-empty), 2 usage/IO error (code in "error").
Units: G1 head/body heuristics are relative to the mesh size (Tripo meshes are normalised);
G3/G4 tolerances are in metres.
"""
import bpy
import bmesh
import hashlib
import json
import math
import os
import re
import sys
import traceback

import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PROFILES = os.path.join(REPO, "characters", "_cast", "gen-profiles.json")
CONTRACT = os.path.join(REPO, "characters", "_cast", "contract.json")

EYE_TOKENS = ("eye_core", "eye_cornea", "irisdisc", "pupildisc", "eyeball", "cornea", "iris", "pupil")
ORAL_TOKENS = ("tooth", "teeth", "gum", "tongue")
CAVITY_TOKENS = ("innermouth", "cavity")


class GateError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    pos, opts, i = [], {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            if i + 1 >= len(argv):
                raise GateError("Usage:missing value for %s" % argv[i])
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 3:
        raise GateError("Usage:auto-check.py -- <in> <gate> <out.json> [--profile p] [--kind k]")
    return pos[0], pos[1].upper(), pos[2], opts


# ---------------------------------------------------------------- loading

def load_scene(path):
    if not os.path.isfile(path):
        raise GateError("InputMissing:%s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if ext in (".glb", ".gltf", ".vrm"):
            bpy.ops.import_scene.gltf(filepath=path)
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        else:
            raise GateError("UnsupportedFormat:%s" % ext)
    return ext.lstrip(".")


def role_of(obj):
    r = obj.get("cast_role")
    if r:
        return str(r)
    n = obj.name.lower()
    if any(t in n for t in EYE_TOKENS):
        return "eyes"
    if any(t in n for t in CAVITY_TOKENS):
        return "cavity"
    if any(t in n for t in ORAL_TOKENS):
        return "oral"
    return "main"


def mesh_objects(roles=None):
    out = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if roles is not None:
        out = [o for o in out if role_of(o) in roles]
    return out


class Mesh:
    """World-space arrays of one or more mesh objects, vertices welded by position."""

    def __init__(self, objs, weld=1e-6):
        cos, polys, offs = [], [], 0
        self.loop_vert, self.poly_start, self.poly_total = [], [], []
        lv_all, ps_all, pt_all, loff = [], [], [], 0
        for o in objs:
            m = o.data
            co = np.empty(len(m.vertices) * 3)
            m.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            M = np.array(o.matrix_world)
            cos.append(co @ M[:3, :3].T + M[:3, 3])
            lv = np.empty(len(m.loops), dtype=np.int64)
            m.loops.foreach_get("vertex_index", lv)
            ps = np.empty(len(m.polygons), dtype=np.int64)
            pt = np.empty(len(m.polygons), dtype=np.int64)
            m.polygons.foreach_get("loop_start", ps)
            m.polygons.foreach_get("loop_total", pt)
            lv_all.append(lv + offs)
            ps_all.append(ps + loff)
            pt_all.append(pt)
            offs += len(co)
            loff += len(lv)
        self.raw_co = np.concatenate(cos) if cos else np.zeros((0, 3))
        lv = np.concatenate(lv_all) if lv_all else np.zeros(0, dtype=np.int64)
        self.poly_start = np.concatenate(ps_all) if ps_all else np.zeros(0, dtype=np.int64)
        self.poly_total = np.concatenate(pt_all) if pt_all else np.zeros(0, dtype=np.int64)
        q = np.round(self.raw_co / weld).astype(np.int64)
        _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        self.co = self.raw_co[first]
        self.loop_vert = inv[lv]
        L = len(lv)
        nxt = np.arange(L) + 1
        ends = self.poly_start + self.poly_total - 1
        nxt[ends] = self.poly_start
        a, b = self.loop_vert, self.loop_vert[nxt]
        self.loop_poly = np.repeat(np.arange(len(self.poly_start)), self.poly_total)
        keep = a != b
        a, b, lp = a[keep], b[keep], self.loop_poly[keep]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        key = lo * len(self.co) + hi
        uk, kinv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        self.edges = np.stack([uk // len(self.co), uk % len(self.co)], 1)
        self.edge_faces = cnt
        self.boundary = self.edges[cnt == 1]
        self.nonmanifold = int((cnt > 2).sum())

    def polys(self):
        lv = self.loop_vert
        return [lv[s:s + t].tolist() for s, t in zip(self.poly_start, self.poly_total)]

    def tris(self):
        return int(np.maximum(self.poly_total - 2, 0).sum())


def components(n, edges):
    """Connected component label per vertex (label propagation with pointer jumping)."""
    lab = np.arange(n)
    if len(edges) == 0:
        return lab
    a, b = edges[:, 0], edges[:, 1]
    while True:
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m)
        np.minimum.at(new, b, m)
        new = new[new]
        new = new[new]
        if np.array_equal(new, lab):
            return lab
        lab = new


def label_sets(lab, mask=None):
    idx = np.arange(len(lab)) if mask is None else np.nonzero(mask)[0]
    order = np.argsort(lab[idx], kind="stable")
    s = lab[idx][order]
    cuts = np.nonzero(np.diff(s))[0] + 1
    return [idx[order][g] for g in np.split(np.arange(len(s)), cuts)] if len(s) else []


def bbox(co):
    return {"min": [round(float(x), 4) for x in co.min(0)], "max": [round(float(x), 4) for x in co.max(0)]}


def submesh_components(mesh, vmask, min_verts=1):
    e = mesh.edges[vmask[mesh.edges[:, 0]] & vmask[mesh.edges[:, 1]]]
    lab = components(len(mesh.co), e)
    return [g for g in label_sets(lab, vmask) if len(g) >= min_verts]


def ordered_loop(edges):
    adj = {}
    for a, b in edges:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    start = int(edges[0][0])
    order, prev, cur = [start], None, start
    while True:
        nbrs = [v for v in adj[cur] if v != prev]
        if not nbrs or nbrs[0] == start or len(order) > len(adj):
            break
        prev, cur = cur, nbrs[0]
        order.append(cur)
    return order


def boundary_loops(mesh):
    if len(mesh.boundary) == 0:
        return []
    lab = components(len(mesh.co), mesh.boundary)
    groups = {}
    for e in mesh.boundary:
        groups.setdefault(int(lab[e[0]]), []).append(e)
    loops = []
    for es in groups.values():
        order = ordered_loop(np.array(es))
        p = mesh.co[order]
        c = p.mean(0)
        nrm = np.zeros(3)
        for i in range(len(p)):  # Newell
            a, b = p[i] - c, p[(i + 1) % len(p)] - c
            nrm += np.cross(a, b)
        area = float(np.linalg.norm(nrm)) / 2.0
        loops.append({"verts": order, "n": len(order), "center": c, "radius": float(np.linalg.norm(p - c, axis=1).max()),
                      "area": area, "normal": nrm / (np.linalg.norm(nrm) + 1e-12)})
    loops.sort(key=lambda l: -l["n"])
    return loops


# ---------------------------------------------------------------- profiles

def load_profile(path, opts):
    with open(PROFILES, encoding="utf-8") as f:
        gp = json.load(f)
    name = opts.get("profile")
    source = "--profile"
    if not name:
        d = os.path.dirname(os.path.abspath(path))
        for _ in range(4):
            for fn in ("part.json", "selection.json"):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as f:
                        j = json.load(f)
                    if j.get("profile"):
                        name, source = j["profile"], p
                        break
            if name:
                break
            d = os.path.dirname(d)
    if not name:
        return None, None, gp
    if name not in gp["profiles"]:
        raise GateError("UnknownProfile:%s" % name)
    return name, dict(gp["profiles"][name], source=source), gp


# ---------------------------------------------------------------- G1

def frame_axes(co):
    """Z is up after import. Lateral = horizontal axis with the larger extent of the central-height
    band for bodies (T-pose arms); returned as (lat, dep) axis indices."""
    ext = np.ptp(co[:, :2], axis=0)
    lat = int(np.argmax(ext))
    return lat, 1 - lat


def symmetric_lateral(co):
    """Lateral axis of a head = horizontal axis of best mirror symmetry."""
    c = (co.min(0) + co.max(0)) / 2
    sub = co[np.linspace(0, len(co) - 1, min(3000, len(co))).astype(int)]
    kd = KDTree(len(co))
    for i, p in enumerate(co):
        kd.insert(p, i)
    kd.balance()
    score = []
    for a in (0, 1):
        m = sub.copy()
        m[:, a] = 2 * c[a] - m[:, a]
        score.append(float(np.mean([kd.find(Vector(p))[2] for p in m])))
    lat = int(np.argmin(score))
    return lat, 1 - lat, score


def head_region(co):
    """Vertices of the head. A head-only mesh is returned whole; for a full body the cut is the
    narrowest lateral width of the central column in the top 25%."""
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    H = zmax - zmin
    top = co[co[:, 2] > zmax - 0.15 * H]
    if H < 3.0 * max(np.ptp(top[:, 0]), np.ptp(top[:, 1])):
        return np.ones(len(co), bool), {"mode": "head_only"}
    lat, dep = frame_axes(co)
    cl = np.median(co[:, lat])
    col = np.abs(co[:, lat] - cl) < 0.08 * H
    widths = []
    zs = np.linspace(zmax - 0.25 * H, zmax - 0.02 * H, 24)
    for z in zs:
        s = co[col & (np.abs(co[:, 2] - z) < 0.006 * H)]
        widths.append(np.ptp(s[:, lat]) if len(s) > 3 else 1e9)
    # neck = narrowest slice that has wider geometry above it (the head)
    best = None
    for i, z in enumerate(zs[:-3]):
        above = max(widths[i + 1:])
        if above < 1e8 and widths[i] < 0.8 * above and (best is None or widths[i] < widths[best]):
            best = i
    if best is None:
        return co[:, 2] > zmax - 0.12 * H, {"mode": "top12pct"}
    zc = float(zs[best])
    return (co[:, 2] > zc) & col, {"mode": "above_neck", "neck_z": round(zc, 4)}


def g1_format(path, fmt, mesh, profile, codes, checks):
    faces = len(mesh.poly_start)
    quads = int((mesh.poly_total == 4).sum())
    tris = mesh.tris()
    c = {"faces": faces, "quads": quads, "tris": tris, "quad_ratio": round(quads / faces, 4) if faces else 0}
    if fmt in ("blend", "fbx"):
        if profile.get("quad"):
            c["face_limit"] = profile["face_limit"]
            if c["quad_ratio"] < 0.95:
                codes.append("QuadRatio:%.3f<0.95" % c["quad_ratio"])
            if faces > profile["face_limit"]:
                codes.append("FaceLimit:%d>%d" % (faces, profile["face_limit"]))
        else:
            c["tri_limit"] = profile["tri_limit"]
            if tris > profile["tri_limit"]:
                codes.append("TriLimit:%d>%d" % (tris, profile["tri_limit"]))
    else:
        c["tri_limit"] = profile["tri_limit"]
        if tris > profile["tri_limit"]:
            codes.append("TriLimit:%d>%d" % (tris, profile["tri_limit"]))
        if profile.get("quad"):
            blend = os.path.splitext(path)[0] + ".blend"
            c["quad_source"] = blend if os.path.isfile(blend) else None
            if not c["quad_source"]:
                codes.append("QuadSourceMissing")
    checks["format"] = c


def g1_components(mesh, checks):
    lab = components(len(mesh.co), mesh.edges)
    groups = sorted(label_sets(lab), key=len, reverse=True)
    checks["components"] = {
        "count": len(groups),
        "list": [dict(verts=int(len(g)), **bbox(mesh.co[g])) for g in groups[:40]],
        "truncated": len(groups) > 40,
        "nonmanifold_edges": mesh.nonmanifold,
        "boundary_edges": int(len(mesh.boundary)),
    }


def g1_arms(mesh, codes, checks):
    """Orientation-free: arms are the connected cross-sections of the upper body in thin cylindrical
    shells around the vertical torso axis; hands are the connected parts beyond 82% of the reach."""
    co = mesh.co
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    H = zmax - zmin
    axis = np.median(co[(co[:, 2] > zmin + 0.3 * H) & (co[:, 2] < zmin + 0.5 * H)][:, :2], axis=0)
    rad = np.linalg.norm(co[:, :2] - axis, axis=1)
    upper = co[:, 2] > zmin + 0.5 * H
    R = float(rad[upper].max())
    shells = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75)
    counts, sections = [], {}
    for t in shells:
        sl = upper & (np.abs(rad - t * R) < 0.015 * R)
        comps = submesh_components(mesh, sl, 3)
        big = max((len(g) for g in comps), default=0)
        keep = [g for g in comps if len(g) >= 0.15 * big]
        counts.append(len(keep))
        sections[str(t)] = [{"verts": int(len(g)), "angle_deg": round(math.degrees(math.atan2(
            *(co[g, :2].mean(0) - axis)[::-1])), 1), **bbox(co[g])} for g in keep]
    horizontal = max(set(counts), key=lambda k: (counts.count(k), k))
    # hands: per arm section at the 0.65 shell, the distal part of that arm (azimuth +-25 deg, within
    # 0.15 reach of that arm's own tip) must be one connected piece (palm joins the fingers)
    az = np.degrees(np.arctan2(co[:, 1] - axis[1], co[:, 0] - axis[0]))
    hands = []
    seen_angles = []
    for sec in sections["0.65"]:
        # several sections of one arm (sleeve layers) share its azimuth: count each arm once
        if any(abs((sec["angle_deg"] - a + 180) % 360 - 180) < 25 for a in seen_angles):
            continue
        seen_angles.append(sec["angle_deg"])
        dz = (az - sec["angle_deg"] + 180) % 360 - 180
        arm_v = upper & (np.abs(dz) < 25) & (rad > 0.6 * R)
        if not arm_v.any():
            continue
        tip = float(rad[arm_v].max())
        dist = arm_v & (rad > tip - 0.15 * R)
        parts = submesh_components(mesh, dist, 1)
        tot = sum(len(g) for g in parts)
        big = [g for g in parts if len(g) >= 0.2 * tot]
        hands.append({"arm_angle_deg": sec["angle_deg"], "tip_reach": round(tip / R, 3), "parts": len(big),
                      **bbox(co[np.concatenate(big)])})
    # raised limb: on a neck-stump body nothing but the stump rises above the shoulder line; geometry
    # rising > 9% of the height above it that shows >= 2 separate sections in at least half of the
    # horizontal slabs is one raised arm (count capped at 1: merged sections cannot be told apart)
    lateral = rad > 0.3 * R
    arm_top = float(co[upper & lateral][:, 2].max()) if (upper & lateral).any() else float(zmax)
    rise = (zmax - arm_top) / H
    multi = []
    for z in np.arange(arm_top + 0.02 * H, zmax - 0.01 * H, 0.02 * H):
        comps = submesh_components(mesh, np.abs(co[:, 2] - z) < 0.004 * H, 3)
        big = max((len(g) for g in comps), default=0)
        multi.append(sum(1 for g in comps if len(g) >= 0.15 * big))
    raised = 1 if rise > 0.09 and multi and sum(1 for m in multi if m >= 2) >= 0.5 * len(multi) else 0
    arms = horizontal + raised
    nhands = sum(1 for h in hands if h["parts"] == 1) + raised
    checks["arms"] = {
        "torso_axis_xy": [round(float(x), 4) for x in axis], "reach": round(R, 4),
        "shell_counts": dict(zip(map(str, shells), counts)), "sections": sections,
        "horizontal_arms": horizontal, "shoulder_top_z": round(arm_top, 4), "rise_above_shoulder": round(float(rise), 4),
        "slab_sections_above_shoulder": multi, "raised_limbs": raised,
        "hands_detail": hands, "arms": arms, "hands": nhands,
        "method": "arms = most frequent number of connected sections (>=15% of the largest) of the upper half "
                  "in cylindrical shells at 50-75% of the horizontal reach around the torso axis, plus one "
                  "raised limb when geometry rises >9% of the height above the shoulder line with >=2 sections "
                  "in at least half of the slabs; hands = arms whose distal 15% (by own reach) is one piece, "
                  "plus the raised limb",
    }
    if arms != 2:
        codes.append("ArmCount:%d" % arms)
    if nhands != 2:
        codes.append("HandCount:%d" % nhands)


FINGER_REGION = 0.13      # hand region: beyond tip - 0.13 * height along the lateral axis
FINGER_WRIST = 0.12       # geodesic sources: the wrist band at tip - 0.12 * height
FINGER_PROMINENCE = 0.015  # a finger tip is a geodesic-distance peak with persistence >= 1.5% of height


def finger_tips(mesh):
    """Per side (L = +lateral, R = -lateral): number of finger tips = peaks of geodesic distance from the
    wrist over the hand surface whose 0-dim persistence (prominence) is >= FINGER_PROMINENCE * height.
    Merged fingers form one blob with a single peak; separated fingers each keep their own peak."""
    import heapq
    co = mesh.co
    H = float(np.ptp(co[:, 2]))
    lat = int(np.argmax(np.ptp(co[:, :2], 0)))
    cl = float((co[:, lat].min() + co[:, lat].max()) / 2)
    out = {}
    for side, sgn in (("L", 1), ("R", -1)):
        rel = sgn * (co[:, lat] - cl)
        tip = float(rel.max())
        reg = rel > tip - FINGER_REGION * H
        idx = np.nonzero(reg)[0]
        loc = -np.ones(len(co), dtype=np.int64)
        loc[idx] = np.arange(len(idx))
        e = mesh.edges[reg[mesh.edges[:, 0]] & reg[mesh.edges[:, 1]]]
        adj = [[] for _ in idx]
        for a, b in e:
            w = float(np.linalg.norm(co[a] - co[b]))
            adj[loc[a]].append((loc[b], w))
            adj[loc[b]].append((loc[a], w))
        dist = np.full(len(idx), np.inf)
        hq = []
        src_mask = rel[idx] < tip - FINGER_WRIST * H
        # a hand that is its own component (reduce.py joins separately remeshed hands) has no wrist-band
        # vertex: seed it at its own proximal end
        lab = components(len(idx), np.stack([loc[e[:, 0]], loc[e[:, 1]]], 1) if len(e) else np.zeros((0, 2), int))
        for g in label_sets(lab):
            if not src_mask[g].any():
                r0 = rel[idx[g]].min()
                src_mask[g[rel[idx[g]] < r0 + 0.005 * H]] = True
        for i in np.flatnonzero(src_mask):
            dist[i] = 0.0
            hq.append((0.0, int(i)))
        heapq.heapify(hq)
        while hq:
            d, i = heapq.heappop(hq)
            if d > dist[i]:
                continue
            for j, w in adj[i]:
                if d + w < dist[j]:
                    dist[j] = d + w
                    heapq.heappush(hq, (d + w, j))
        fin = np.isfinite(dist)
        if not fin.any():
            out[side] = {"tips": 0, "prominences": []}
            continue
        dmax = float(dist[fin].max())
        par, birth, pers = {}, {}, []

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for i in [int(i) for i in np.argsort(-np.where(fin, dist, -1.0)) if fin[i]]:
            par[i] = i
            birth[i] = dist[i]
            for j, _ in adj[i]:
                if j in par:
                    a, b = find(i), find(j)
                    if a == b:
                        continue
                    if birth[a] < birth[b]:
                        a, b = b, a
                    pers.append(birth[b] - dist[i])
                    par[b] = a
        pers.append(dmax)
        prom = sorted((p / H for p in pers if p / H >= FINGER_PROMINENCE), reverse=True)
        out[side] = {"tips": len(prom), "prominences": [round(float(p), 4) for p in prom[:8]],
                     "hand_geodesic_len": round(dmax / H, 4)}
    return out


def g1_fingers(mesh, opts, codes, checks):
    res = {"out": finger_tips(mesh), "method": "geodesic distance from the wrist band over the hand region; "
           "tips = distance peaks with persistence >= %.3f x height" % FINGER_PROMINENCE}
    src = opts.get("source")
    if src:
        if not os.path.isfile(src):
            raise GateError("InputMissing:%s" % src)
        res["source"] = os.path.abspath(src)
        res["source_sha256"] = sha256(src)
        keep = list(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=src)
        new = [o for o in bpy.data.objects if o not in keep and o.type == "MESH"]
        res["src"] = finger_tips(Mesh(new))
        for o in [o for o in bpy.data.objects if o not in keep]:
            bpy.data.objects.remove(o, do_unlink=True)
        for side in ("L", "R"):
            n_src, n_out = res["src"][side]["tips"], res["out"][side]["tips"]
            if n_out < n_src:
                codes.append("FingersMerged:%s:%d->%d" % (side, n_src, n_out))
            elif n_out > n_src:
                codes.append("FingersExtra:%s:%d->%d" % (side, n_src, n_out))
    else:
        res["note"] = "no --source: counts reported only"
    checks["fingers"] = res


# Gated bands (user clarification 2026-11-06: realistic 7-head canon, not SD/chibi). Fractions of height.
GATED = {
    "head": (0.128, 0.143),      # crown to chin; hard fail above 0.16 is implied by the band
    "crotch": (0.45, 0.52),
}
# Warned (not blocking): T-pose arm span "about 0.95-1.05". The reference avatar-gen female body, which
# the user named as the passing fixture, measures 0.882, so gating it would fail the reference.
WARNED = {"arm_span": (0.95, 1.05)}
# Reported only (plan Must-have bands; measured from silhouettes, not yet calibrated on enough bodies)
REPORTED = {"shoulder": (0.20, 0.27), "hip": (0.17, 0.24), "knee": (0.26, 0.30), "hand": (0.095, 0.115),
            "foot": (0.14, 0.16), "neck": (0.04, 0.07)}
HEIGHT_BANDS = {"f": (1.55, 1.75), "m": (1.65, 1.90), None: (1.55, 1.90)}


def silhouette(bvh, co, a_axis, ray_axis, res):
    """Boolean raster [z, a] of the orthographic silhouette seen along ray_axis (res metres per cell)."""
    zmin = float(co[:, 2].min())
    a0 = float(co[:, a_axis].min())
    na = int(np.ptp(co[:, a_axis]) / res) + 2
    nz = int(np.ptp(co[:, 2]) / res) + 2
    start = float(co[:, ray_axis].min()) - 0.05
    d = [0.0, 0.0, 0.0]
    d[ray_axis] = 1.0
    d = Vector(d)
    M = np.zeros((nz, na), bool)
    o = [0.0, 0.0, 0.0]
    for i in range(nz):
        o[2] = zmin + (i + 0.5) * res
        for j in range(na):
            o[a_axis] = a0 + (j + 0.5) * res
            o[ray_axis] = start
            if bvh.ray_cast(Vector(o), d, 10.0)[0] is not None:
                M[i, j] = True
    return M, a0


def runs(row):
    idx = np.flatnonzero(np.diff(np.r_[0, row.astype(np.int8), 0]))
    return list(zip(idx[::2].tolist(), idx[1::2].tolist()))


def anatomy_measure(objs, mesh, kind):
    """Landmarks from the front silhouette (lateral x Z) and side silhouette (depth x Z), 2 mm-ish
    cells (0.2% of height). Rows are fractions of height from the feet.
      crotch   = highest row with two separate leg runs, + 1 row
      neck     = narrowest central run between the shoulder line and 97%
      shoulder = row just below the arm line where the torso run is widest (deltoid span)
      head     = crown - neck top (where the run widens to 1.4x the neck width going up = jaw)
      neck len = neck top - shoulder line (the highest row where the central run is > 2.2x neck width)
      hip      = widest single run between crotch and crotch + 12%
      knee     = narrowest leg depth in the side silhouette between 22% and 36%
      arm span = total lateral extent of the front silhouette (T-pose)
      hand     = wrist (thinnest arm column in the outer 30% of each arm) to fingertip
      foot     = depth extent of the side silhouette in the lowest 3%
    """
    co = mesh.co
    zmin, zmax = float(co[:, 2].min()), float(co[:, 2].max())
    H = zmax - zmin
    lat = int(np.argmax(np.ptp(co[:, :2], 0)))
    dep = 1 - lat
    m = {"height_m": round(H, 4), "zmin_m": round(zmin, 4), "lateral_axis": "xyz"[lat]}
    if kind == "head":
        m["head_m"] = round(H, 4)
        return m, H, zmin
    bvh = BVHTree.FromPolygons([Vector(p) for p in co], mesh.polys(), all_triangles=False)
    res = 0.002 * H
    F, a0 = silhouette(bvh, co, lat, dep, res)
    S, _ = silhouette(bvh, co, dep, lat, res)
    nz = F.shape[0]
    fr = lambda i: round(i / nz, 4)  # noqa: E731
    # centre line = midpoint of the widest silhouette run at the chest band (65-72% height, below the
    # arm line, above any skirt); the vertex median is biased by one-sided garments and the fingertip
    # midpoint by an unequal arm
    chest = []
    for i in range(int(0.65 * nz), int(0.72 * nz)):
        rr = runs(F[i])
        if rr:
            a_, b_ = max(rr, key=lambda t: t[1] - t[0])
            chest.append((a_ + b_) / 2)
    cx = int(round(np.median(chest))) if chest else int(round((float(co[:, lat].min() + co[:, lat].max()) / 2 - a0) / res))

    def centre_run(i):
        for a, b in runs(F[i]):
            if a <= cx < b:
                return a, b
        return None

    crotch_i = max((i for i in range(int(0.3 * nz), int(0.65 * nz)) if centre_run(i) is None
                    and len(runs(F[i])) >= 2), default=0) + 1
    m["crotch"] = fr(crotch_i)
    # arm line: lowest row above 60% where the silhouette extends beyond 2x the hip half-width
    hip_i, hip_w = max(((i, (lambda r: r[1] - r[0] if r else 0)(centre_run(i)))
                        for i in range(crotch_i, min(nz, crotch_i + int(0.12 * nz)))), key=lambda t: t[1])
    m["hip"] = round(hip_w * res / H, 4)
    far = [i for i in range(int(0.6 * nz), nz) if F[i].any() and
           (np.flatnonzero(F[i]).max() - cx) * res > 0.3 * H]
    arm_lo = min(far) if far else int(0.8 * nz)
    arm_hi = max(far) if far else int(0.8 * nz)
    m["arm_line"] = [fr(arm_lo), fr(arm_hi)]
    width = lambda i: (lambda r: (r[1] - r[0]) if r else 0)(centre_run(i))  # noqa: E731
    neck_i = min(range(arm_hi + 1, int(0.97 * nz)), key=width)
    nw = width(neck_i)
    sh_i = next((i for i in range(neck_i, arm_lo - 1, -1) if width(i) > 2.2 * nw), arm_lo)
    # chin: in the side silhouette the front contour jumps forward from the neck to the jaw; the face
    # side is the one whose contour moves most between the neck row and 6% above it
    rows = [i for i in range(neck_i, min(nz, neck_i + int(0.08 * nz))) if S[i].any()]
    fmin = {i: int(np.flatnonzero(S[i]).min()) for i in rows}
    fmax = {i: int(np.flatnonzero(S[i]).max()) for i in rows}
    front_is_min = (fmin[rows[0]] - min(fmin.values())) >= (max(fmax.values()) - fmax[rows[0]])
    ref = fmin[rows[0]] if front_is_min else fmax[rows[0]]
    jump = 0.02 * nz
    chin_i = next((i for i in rows if (ref - fmin[i] if front_is_min else fmax[i] - ref) > jump), neck_i)
    m["chin_z_frac"] = fr(chin_i)
    m["head"] = fr(nz - chin_i)
    m["neck"] = fr(chin_i - sh_i)
    # shoulder: deltoid span = where the arm band thickness falls to 1.25x the upper-arm thickness
    band_t = F[arm_lo:min(nz, arm_hi + 1)].sum(0)
    widths = []
    for sgn in (1, -1):
        js = np.arange(cx, F.shape[1]) if sgn > 0 else np.arange(cx, -1, -1)
        reach = max((abs(j - cx) for j in js if band_t[j] > 0), default=1)
        seg = [j for j in js if 0.2 * reach < abs(j - cx) < 0.35 * reach]
        arm_t = float(np.median(band_t[seg])) if seg else 0.0
        d = next((j for j in js if band_t[j] <= 1.25 * arm_t), js[0])
        widths.append(abs(d - cx))
    m["shoulder"] = round(sum(widths) * res / H, 4)
    cols = np.flatnonzero(F.any(0))
    m["arm_span"] = round((cols.max() - cols.min() + 1) * res / H, 4)
    side = {}
    band = F[arm_lo - int(0.03 * nz): min(nz, arm_hi + int(0.03 * nz))]
    thick = band.sum(0)
    for name, sgn in (("L", 1), ("R", -1)):
        js = np.arange(cx, F.shape[1]) if sgn > 0 else np.arange(cx, -1, -1)
        js = js[thick[js] > 0]
        tip = js[-1]
        reach = abs(tip - cx)
        outer = js[np.abs(js - cx) > 0.7 * reach]
        wrist = outer[np.argmin(thick[outer][:-max(1, len(outer) // 6)])] if len(outer) > 6 else tip
        side[name] = {"reach": round(reach * res / H, 4), "hand": round(abs(tip - wrist) * res / H, 4)}
    # knee: the front-contour of the side silhouette between 20% and 36% bulges forward at the patella
    srows = [i for i in range(int(0.20 * nz), int(0.36 * nz)) if S[i].any()]
    if front_is_min:
        kn = min(srows, key=lambda i: int(np.flatnonzero(S[i]).min()))
    else:
        kn = max(srows, key=lambda i: int(np.flatnonzero(S[i]).max()))
    m["knee"] = fr(kn)
    foot_rows = [i for i in range(0, int(0.08 * nz)) if S[i].any()]
    if foot_rows:
        lo_ = min(int(np.flatnonzero(S[i]).min()) for i in foot_rows)
        hi_ = max(int(np.flatnonzero(S[i]).max()) for i in foot_rows)
        m["foot"] = round((hi_ - lo_ + 1) * res / H, 4)
    else:
        m["foot"] = 0.0
    # per-leg symmetry from the front silhouette below the crotch
    legL = legR = 0
    for i in range(int(0.05 * nz), crotch_i - 2):
        for a, b2 in runs(F[i]):
            if a >= cx:
                legL += b2 - a
            elif b2 <= cx:
                legR += b2 - a
    side["L"]["leg_area"], side["R"]["leg_area"] = round(legL * res * res, 5), round(legR * res * res, 5)
    m["sides"] = side
    m["hand"] = round((side["L"]["hand"] + side["R"]["hand"]) / 2, 4)
    m["t_pose"] = bool(far) and (arm_hi - arm_lo) < 0.1 * nz
    # a base body ends in a neck stump: less than 9% of the height above the arm line (as in G1 neck)
    m["headless"] = bool(far) and (nz - arm_hi) / nz < 0.09
    m["raster_cell_m"] = round(res, 5)
    return m, H, zmin


def run_anatomy(objs, opts, codes, checks, kind, prefix=""):
    try:
        _run_anatomy(objs, opts, codes, checks, kind, prefix)
    except (ValueError, OverflowError, IndexError, ZeroDivisionError, StopIteration) as e:
        codes.append("%sAnatomyUnmeasurable" % prefix)
        checks["source_anatomy" if prefix else "anatomy"] = {"error": "%s: %s" % (type(e).__name__, e),
                                                             "note": "no humanoid silhouette landmarks found"}


def _run_anatomy(objs, opts, codes, checks, kind, prefix=""):
    """prefix "" gates the asset; prefix "Source" reports a --source mesh (SourceOutOfBand)."""
    res = {"notes": [], "skipped": []}
    for o in objs:
        sc = o.matrix_world.to_scale()
        if role_of(o) in ("eyes", "oral", "cavity"):
            if max(sc) - min(sc) > 1e-4 * max(abs(max(sc)), 1e-9):
                res["notes"].append("component %s has non-uniform scale %s (reported, not gated: the "
                                    "shared-eyes reference ships this way)" % (o.name, tuple(round(x, 4) for x in sc)))
            continue
        if max(sc) - min(sc) > 1e-4 * max(abs(max(sc)), 1e-9):
            codes.append("NonUniformScale")
            res["notes"].append("%s scale %s" % (o.name, tuple(round(x, 4) for x in sc)))
            break
    shape = [o for o in objs if role_of(o) == "main"] or objs
    mesh = Mesh(shape)
    m, H, zmin = anatomy_measure(shape, mesh, kind)
    res["measured_objects"] = [o.name for o in shape]
    gender = opts.get("gender")
    lo, hi = HEIGHT_BANDS.get(gender, HEIGHT_BANDS[None])
    res.update({"measures": m, "gated_bands": GATED, "warned_bands": WARNED, "reported_bands": REPORTED, "height_band_m": [lo, hi],
                "gender": gender})
    out = (lambda name, v: codes.append("%sOutOfBand:%s:%.4f" % (prefix or "Proportion", name, v))) if prefix \
        else (lambda name, v: codes.append("ProportionOutOfBand:%s:%.4f" % (name, v)))
    if kind == "head":
        stated = float(opts["stated-height"]) if opts.get("stated-height") else None
        res["skipped"].append("body proportions (head-only asset)")
        if stated:
            f = H / stated
            m["head"] = round(f, 4)
            if not GATED["head"][0] <= f <= GATED["head"][1]:
                out("head", f)
        else:
            res["skipped"].append("head band needs --stated-height")
        checks[prefix.lower() + "anatomy" if prefix else "anatomy"] = res
        return
    if not prefix and abs(zmin) > 0.01:
        codes.append("NotOnGround:%.4f" % zmin)
    res["height_m"] = round(H, 4)  # always reported, also when not gated
    if m.get("headless"):
        res["skipped"].append("head, neck and total height (neck-stump body: the head is a separate part; "
                              "measure the assembled person in G5); height_m is reported, not gated")
    elif not lo <= H <= hi:
        codes.append(("SourceOutOfBand:height:%.3f" if prefix else "HeightOutOfBand:%.3f") % H)
    if opts.get("stated-height") and not prefix:
        st = float(opts["stated-height"])
        if abs(H - st) > 0.04 * st:
            codes.append("HeightOutOfBand:%.3f" % H)
    for k, (a_, b_) in GATED.items():
        if k == "head" and m.get("headless"):
            continue
        if not a_ <= m[k] <= b_:
            out(k, m[k])
    res["warnings"] = []
    for k, (a_, b_) in WARNED.items():
        if k == "arm_span" and not m["t_pose"]:
            res["skipped"].append("arm_span (not a T-pose)")
            continue
        if not a_ <= m[k] <= b_:
            res["warnings"].append("%sOutOfBand:%s:%.4f" % (prefix or "Proportion", k, m[k]))
    res["reported_out_of_band"] = {k: m[k] for k, (a_, b_) in REPORTED.items() if not a_ <= m[k] <= b_}
    if not prefix:
        res["asymmetry"] = {}
        for limb, key in (("arm", "reach"), ("hand", "hand"), ("leg", "leg_area")):
            a_, b_ = m["sides"]["L"][key], m["sides"]["R"][key]
            res["asymmetry"][limb] = round(abs(a_ - b_) / max(a_, b_, 1e-9), 4)
        # hand length uses the thinnest-arm wrist estimate, which cuffs and sleeves disturb: reported only
        for limb, key in (("arm", "reach"), ("leg", "leg_area")):
            a_, b_ = m["sides"]["L"][key], m["sides"]["R"][key]
            if max(a_, b_) > 0 and abs(a_ - b_) / max(a_, b_) > 0.02:
                codes.append("AsymmetryOver2pct:%s" % limb)
    checks["source_anatomy" if prefix else "anatomy"] = res


def g1_neck(mesh, codes, checks):
    co = mesh.co
    lat, dep = frame_axes(co)
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    H = zmax - zmin
    cl = (co[:, lat].min() + co[:, lat].max()) / 2
    rel = np.abs(co[:, lat] - cl)
    half = rel.max()
    arm_top = float(co[rel > 0.3 * half][:, 2].max())
    above = (zmax - arm_top) / H
    checks["neck"] = {"arm_top_z": round(arm_top, 4), "top_z": round(float(zmax), 4),
                      "above_arm_top_frac": round(float(above), 4), "limit_frac": 0.09,
                      "method": "height above the top of the arm/shoulder line as a fraction of body height"}
    if above > 0.09:
        codes.append("FacesAboveNeck:%.3f" % above)


def g1_envelope(mesh, codes, checks):
    co_all = mesh.co
    hm, info = head_region(co_all)
    co = co_all[hm]
    lm = head_landmarks(mesh, hm)
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    H = zmax - zmin
    h = (co[:, 2] - zmin) / H
    nose_h = (lm["nose"][2] - zmin) / H
    c, r, rms = fit_sphere(co[h > 0.6])
    d = np.linalg.norm(co - c, axis=1) / r
    upper = h > nose_h + 0.15
    out = upper & (d > 1.25)
    frac = float(out.sum()) / max(int(upper.sum()), 1)
    info.update({"skull_center": [round(float(x), 4) for x in c], "skull_radius": round(r, 4),
                 "skull_fit_rms_rel": round(rms / r, 4), "verts": int(len(co)), "nose_h": round(float(nose_h), 3),
                 "r_max": round(float(d[upper].max()), 3), "r_p999": round(float(np.percentile(d[upper], 99.9)), 3),
                 "outside_frac": round(frac, 5), "limit": {"r": 1.25, "outside_frac": 0.001},
                 "method": "sphere fitted to the top 40% of the head (skull+cap); vertices above nose+15% head "
                           "height farther than 1.25 skull radii are outside the envelope (baked hair, ornaments)"})
    if out.any():
        info["outside_bbox"] = bbox(co[out])
    checks["envelope"] = info
    if frac > 0.001:
        codes.append("HeadEnvelope:%.4f" % frac)


class Shell:
    """Ray casting helper over a set of mesh objects."""

    def __init__(self, mesh):
        self.mesh = mesh
        self.bvh = BVHTree.FromPolygons([Vector(p) for p in mesh.co], mesh.polys(), all_triangles=False)

    def hits(self, origin, direction, maxdist, limit=6):
        out, o, d, travelled = [], Vector(origin), Vector(direction).normalized(), 0.0
        eps = 1e-6
        while len(out) < limit:
            loc, nrm, idx, dist = self.bvh.ray_cast(o, d, maxdist - travelled)
            if loc is None:
                break
            travelled += dist
            out.append((travelled, nrm.dot(d) < 0))  # (distance, front_facing)
            o = loc + d * eps
            travelled += eps
        return out


def head_landmarks(mesh, hm):
    co = mesh.co[hm]
    lat, dep, sym = symmetric_lateral(co)
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    Hh = zmax - zmin
    W = np.percentile(co[:, lat], 98) - np.percentile(co[:, lat], 2)
    cl = float(np.median(co[:, lat]))
    band = (co[:, 2] > zmin + 0.15 * Hh) & (co[:, 2] < zmin + 0.6 * Hh)
    central = band & (np.abs(co[:, lat] - cl) < 0.06 * W)
    side = band & (np.abs(np.abs(co[:, lat] - cl) - 0.26 * W) < 0.04 * W)
    point = {}
    for s in (1, -1):
        point[s] = float((s * co[central, dep]).max() - (s * co[side, dep]).max())
    fwd = 1 if point[1] > point[-1] else -1
    ci = np.nonzero(central)[0]
    nose = co[ci[np.argmax(fwd * co[ci, dep])]]
    return {"lat": lat, "dep": dep, "fwd": fwd, "zmin": float(zmin), "zmax": float(zmax), "Hh": float(Hh),
            "W": float(W), "cl": cl, "nose": nose, "front": float((fwd * co[:, dep]).max()),
            "depth": float(np.ptp(co[:, dep])), "symmetry_score": sym, "pointiness": point}


def eye_rims(loops, lm):
    """Pairs of boundary loops placed like eye sockets: front half, above the nose, mirrored."""
    lat, dep, fwd = lm["lat"], lm["dep"], lm["fwd"]
    cand = []
    for l in loops:
        c = l["center"]
        if l["radius"] < 0.02 * lm["Hh"] or l["radius"] > 0.2 * lm["Hh"]:
            continue
        if c[2] <= lm["nose"][2] or fwd * c[dep] < fwd * lm["nose"][dep] - 0.5 * lm["depth"]:
            continue
        if abs(c[lat] - lm["cl"]) < 0.05 * lm["W"]:
            continue
        cand.append(l)
    best = None
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i]["center"], cand[j]["center"]
            if (a[lat] - lm["cl"]) * (b[lat] - lm["cl"]) >= 0:
                continue
            mis = abs(a[2] - b[2]) + abs(a[dep] - b[dep]) + abs(abs(a[lat] - lm["cl"]) - abs(b[lat] - lm["cl"]))
            if mis < 0.05 * lm["Hh"] and (best is None or mis < best[0]):
                best = (mis, cand[i], cand[j])
    if not best:
        return cand, None
    return cand, sorted(best[1:], key=lambda l: l["center"][lat])


def loop_json(l):
    return {"n": l["n"], "center": [round(float(x), 4) for x in l["center"]], "radius": round(l["radius"], 4),
            "area": round(l["area"], 6)}


def g1_head_topology(objs_shell, only, codes, checks):
    mesh = Mesh(objs_shell)
    hm, info = head_region(mesh.co)
    lm = head_landmarks(mesh, hm)
    loops = [l for l in boundary_loops(mesh) if hm[l["verts"]].all()]
    cand, rims = eye_rims(loops, lm)
    lat, dep, fwd = lm["lat"], lm["dep"], lm["fwd"]
    checks["head_frame"] = {"region": info, "lateral_axis": "xyz"[lat], "forward": ("+" if fwd > 0 else "-") + "xyz"[dep],
                            "nose": [round(float(x), 4) for x in lm["nose"]], "head_height": round(lm["Hh"], 4),
                            "head_width": round(lm["W"], 4), "pointiness": lm["pointiness"],
                            "boundary_loops": len(loops), "objects": [o.name for o in objs_shell]}
    if "eyes" in only:
        checks["eye_sockets"] = {"candidates": [loop_json(l) for l in cand],
                                 "rims": [loop_json(l) for l in rims] if rims else None,
                                 "method": "mirrored pair of boundary loops above the nose tip, radius 2-20% of head height"}
        if not rims:
            sides = sorted({("pos" if l["center"][lat] > lm["cl"] else "neg") for l in cand})
            codes.append("EyeSocketClosed:%s" % ("both" if not sides else ("neg" if sides == ["pos"] else "pos")))
    shell = Shell(mesh)
    if rims:
        eye_z = float(np.mean([l["center"][2] for l in rims]))
        iod = float(abs(rims[0]["center"][lat] - rims[1]["center"][lat]))
        band = (eye_z - 1.4 * iod, eye_z - 0.45 * iod)
        half_w = 0.35 * iod
        min_span = 0.2 * iod
    else:
        nz = float(lm["nose"][2])
        band = (nz - 0.22 * lm["Hh"], nz - 0.03 * lm["Hh"])
        half_w = 0.12 * lm["W"]
        min_span = 0.06 * lm["W"]
        iod = None
    if "mouth" in only:
        res = mouth_probe(shell, lm, band, half_w, min_span, loops)
        res["band_z"] = [round(band[0], 4), round(band[1], 4)]
        res["frame"] = "eye_rims" if rims else "nose_landmark"
        checks["mouth"] = res
        if not res["open"]:
            codes.append("MouthClosed")
    if "baked_eye" in only:
        res = baked_eye_probe(mesh, objs_shell, lm, rims)
        checks["baked_eye"] = res
        if res["fused"]:
            codes.append("BakedEyeball:%s" % ",".join(res["fused"]))
    return mesh, lm, rims


MOUTH_DEPTH = 0.15  # a mouth opening reaches >= 15% of head height deeper than the surrounding lips


def mouth_probe(shell, lm, band, half_w, min_span, loops):
    """Rays straight into the face along the centre line, rows over the mouth band. Closed lips stop
    every ray at the lip surface (depth profile is smooth). An open mouth lets rays continue into the
    oral cavity: the first hit sits >= MOUTH_DEPTH x head height behind the lips above and below it.
    A boundary loop in the mouth region (a hole) also counts as open."""
    lat, dep, fwd = lm["lat"], lm["dep"], lm["fwd"]
    Hh = lm["Hh"]
    d = np.zeros(3)
    d[dep] = -fwd
    front = lm["front"] + 0.05 * Hh
    maxd = lm["depth"] + 0.1 * Hh
    hole_loops = [loop_json(l) for l in loops
                  if band[0] <= l["center"][2] <= band[1] and abs(l["center"][lat] - lm["cl"]) < half_w
                  and l["radius"] > 0.25 * min_span]
    zs = np.linspace(band[0], band[1], 40)
    prof = []
    for z in zs:
        best = 0.0
        for x in np.linspace(lm["cl"] - 0.03 * lm["W"], lm["cl"] + 0.03 * lm["W"], 5):
            o = np.zeros(3)
            o[lat], o[dep], o[2] = x, fwd * front, z
            h = shell.hits(o, d, maxd, limit=1)
            best = max(best, (h[0][0] if h else maxd) - 0.05 * Hh)
        prof.append(best / Hh)
    prof = np.array(prof)
    base = float(np.median(prof))
    deep = prof > base + MOUTH_DEPTH
    # only deep runs bounded by lip surface rows on both sides (excludes rows under the chin)
    rows = 0
    i = 0
    while i < len(deep):
        if deep[i]:
            j = i
            while j < len(deep) and deep[j]:
                j += 1
            if i > 0 and j < len(deep):
                rows = max(rows, j - i)
            i = j
        else:
            i += 1
    span = rows * (zs[1] - zs[0]) if rows else 0.0
    is_open = bool(hole_loops) or rows >= 2
    return {"open": is_open, "hole_loops": hole_loops, "deep_rows": rows, "deep_span_m": round(float(span), 4),
            "lip_depth_median": round(base, 4), "max_depth": round(float(prof.max()), 4),
            "depth_profile": [round(float(v), 3) for v in prof],
            "method": "centre-line rays into the face across the mouth band; open when a run of >= 2 rows, "
                      "bounded above and below by lip-depth rows, reaches >= %.2f x head height deeper than the "
                      "median lip depth, or a mouth boundary loop exists"
                      % MOUTH_DEPTH}


def enclosed(shell, p, lm):
    lat = lm["lat"]
    for axis, reach in ((2, 0.12 * lm["Hh"]), (lat, 0.25 * lm["W"])):
        for s in (1, -1):
            d = np.zeros(3)
            d[axis] = s
            if not shell.hits(p, d, reach, limit=1):
                return False
    return True


def fit_sphere(p):
    A = np.c_[2 * p, np.ones(len(p))]
    b = (p ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:3]
    r = math.sqrt(max(sol[3] + c.dot(c), 0))
    res = np.abs(np.linalg.norm(p - c, axis=1) - r)
    return c, r, float(np.sqrt((res ** 2).mean()))


def baked_eye_probe(mesh, objs_shell, lm, rims):
    """Eyeball-shaped geometry inside each eye window that belongs to the face shell objects."""
    lat, dep, fwd = lm["lat"], lm["dep"], lm["fwd"]
    co = mesh.co
    windows = []
    if rims:
        for l in rims:
            windows.append(("pos" if l["center"][lat] > lm["cl"] else "neg", l["center"], l["radius"], "rim"))
    else:
        return {"fused": [], "windows": [], "evaluated": False,
                "reason": "no eye rims; a closed socket is reported by EyeSocketClosed and the eye position "
                          "cannot be located reliably on a closed Tripo face"}
    bvh = BVHTree.FromPolygons([Vector(p) for p in co], mesh.polys(), all_triangles=False)
    out = {"fused": [], "windows": [], "evaluated": True,
           "method": "face-shell vertices projecting inside the rim (front view) and lying within 1.5 rim radii "
                     "behind it; fused when they fit a sphere of 0.6-2.5 rim radii with RMS < 12% of its radius "
                     "and are convex (mean normal . radial > 0.5; a socket cup is concave)"}
    for side, c, R, src in windows:
        rel = co - c
        planar = np.sqrt(rel[:, lat] ** 2 + rel[:, 2] ** 2)
        behind = -fwd * rel[:, dep]
        sel = (planar < 0.9 * R) & (behind > -0.3 * R) & (behind < 1.5 * R)
        w = {"side": side, "source": src, "center": [round(float(x), 4) for x in c], "radius": round(R, 4),
             "verts_inside": int(sel.sum())}
        if sel.sum() >= 30:
            sc, sr, rms = fit_sphere(co[sel])
            # convexity: an eyeball's normals point away from the fitted centre, a socket cup's towards it
            dots = []
            for p in co[sel]:
                loc, nrm, _, _ = bvh.find_nearest(Vector(p))
                if loc is not None:
                    r = Vector(p) - Vector(sc)
                    if r.length > 1e-9:
                        dots.append(nrm.dot(r.normalized()))
            convex = float(np.mean(dots)) if dots else 0.0
            w.update({"sphere_r": round(sr, 4), "sphere_rms": round(rms, 5), "convexity": round(convex, 3)})
            if 0.6 * R <= sr <= 2.5 * R and rms < 0.12 * sr and convex > 0.5:
                out["fused"].append(side)
        out["windows"].append(w)
    return out


def run_g1(path, fmt, opts, result):
    name, profile, _ = load_profile(path, opts)
    codes, checks = result["codes"], result["checks"]
    if not profile:
        codes.append("ProfileMissing")
        return
    result["profile"] = name
    result["profile_def"] = profile
    kind = opts.get("kind") or {"head": "head", "base_body": "body"}.get(name, "part")
    result["kind"] = kind
    default = {"body": "format,components,arms,neck,fingers,anatomy", "head": "format,components,envelope,eyes,mouth,baked_eye,anatomy",
               "part": "format,components"}[kind]
    only = set((opts.get("only") or default).split(","))
    result["checks_run"] = sorted(only)
    objs = mesh_objects()
    if not objs:
        codes.append("NoMesh")
        return
    main = [o for o in objs if role_of(o) == "main"] or objs
    mesh = Mesh(main)
    checks["objects"] = [{"name": o.name, "role": role_of(o)} for o in objs]
    if "format" in only:
        g1_format(path, fmt, Mesh(objs), profile, codes, checks)
    if "components" in only:
        g1_components(mesh, checks)
    if kind == "body":
        if "arms" in only:
            g1_arms(mesh, codes, checks)
        if "neck" in only:
            g1_neck(mesh, codes, checks)
        if "fingers" in only:
            g1_fingers(mesh, opts, codes, checks)
        if "anatomy" in only:
            run_anatomy(main, opts, codes, checks, "body")
            if opts.get("source"):
                keep = set(bpy.data.objects)
                bpy.ops.import_scene.gltf(filepath=opts["source"])
                new = [o for o in bpy.data.objects if o not in keep and o.type == "MESH"]
                src_codes = []
                run_anatomy(new, opts, src_codes, checks, "body", prefix="Source")
                # the unreduced Tripo source is reported, not gated: it arrives at arbitrary scale
                checks["source_anatomy"]["codes"] = [c for c in src_codes if c.startswith("Source")]
                for o in new:
                    bpy.data.objects.remove(o, do_unlink=True)
    if kind == "head":
        if "envelope" in only:
            g1_envelope(mesh, codes, checks)
        if "anatomy" in only:
            run_anatomy(main, opts, codes, checks, "head")
        if only & {"eyes", "mouth", "baked_eye"}:
            shell = [o for o in objs if role_of(o) in ("main", "cavity")]
            g1_head_topology(shell, only, codes, checks)


# ---------------------------------------------------------------- G2

ARM_PARTS = [("upper_arm", r"(upper_?arm|uparm|arm(?!.*(fore|lower|twist)))"),
             ("forearm", r"(fore_?arm|lower_?arm|lowerarm|elbow)"), ("hand", r"(hand|wrist)")]


def side_of(name):
    n = name.lower()
    if re.search(r"(^|[^a-z])(l|left)([^a-z]|$)|left|\.l$|_l$|^l_", n):
        return "L"
    if re.search(r"(^|[^a-z])(r|right)([^a-z]|$)|right|\.r$|_r$|^r_", n):
        return "R"
    return None


def norm_bone(b):
    """contract.json rows are {vrm_bone, node_name, role?, parent?}; fixtures may use {name, parent?}."""
    if not isinstance(b, dict):
        return {"name": str(b)}
    return {"name": b.get("node_name") or b.get("name"), "parent": b.get("parent")}


def required_bones(opts, notes):
    if os.path.isfile(CONTRACT):
        with open(CONTRACT, encoding="utf-8") as f:
            j = json.load(f)
        notes.append("bones from characters/_cast/contract.json")
        bones = j.get("bones", j)
        return [norm_bone(b) for b in bones], "characters/_cast/contract.json"
    notes.append("characters/_cast/contract.json absent; bone list taken from the explicit parameter")
    if opts.get("bones-json"):
        with open(opts["bones-json"], encoding="utf-8") as f:
            j = json.load(f)
        bones = j.get("bones", j) if isinstance(j, dict) else j
        return [norm_bone(b) for b in bones], opts["bones-json"]
    if opts.get("bones"):
        return [{"name": b} for b in opts["bones"].split(",") if b], "--bones"
    raise GateError("ContractMissing:no characters/_cast/contract.json and no --bones/--bones-json")


def run_g2(path, fmt, opts, result):
    codes, checks, notes = result["codes"], result["checks"], result["notes"]
    req, src = required_bones(opts, notes)
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    checks["armatures"] = [a.name for a in arms]
    if not arms:
        codes.append("NoArmature")
        return
    arm = max(arms, key=lambda a: len(a.data.bones))
    bones = {b.name: b for b in arm.data.bones}
    checks["bone_source"] = src
    checks["bone_count"] = len(bones)
    missing = [b["name"] for b in req if b["name"] not in bones]
    badparent = []
    for b in req:
        if b.get("parent") and b["name"] in bones:
            p = bones[b["name"]].parent
            if not p or p.name != b["parent"]:
                badparent.append("%s<-%s" % (b["name"], p.name if p else None))
    checks["missing"], checks["bad_parent"] = missing, badparent
    if missing:
        codes.append("BoneMissing:%s" % ",".join(missing[:10]))
    if badparent:
        codes.append("ParentChain:%s" % ",".join(badparent[:10]))
    chains = {}
    for side in ("L", "R"):
        found = {}
        for part, pat in ARM_PARTS:
            hits = [n for n in bones if side_of(n) == side and re.search(pat, n.lower())
                    and not re.search(r"(finger|thumb|index|middle|ring|pinky|twist|pole|_ik|ik_|offset|ref)", n.lower())]
            found[part] = hits[0] if hits else None
        ok = all(found.values())
        if ok:  # parent chain hand -> forearm -> upper_arm
            h, f, u = bones[found["hand"]], bones[found["forearm"]], bones[found["upper_arm"]]
            ok = f.name in [p.name for p in h.parent_recursive] and u.name in [p.name for p in f.parent_recursive]
        chains[side] = {"bones": found, "ok": ok}
        if not ok:
            codes.append("ArmChainMissing:%s" % side)
    checks["arm_chains"] = chains
    deform = {b.name for b in arm.data.bones if b.use_deform}
    worst, zero, nverts = 0, 0, 0
    per_obj = []
    for o in mesh_objects():
        if not any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers) and o.parent != arm:
            continue
        gi = {g.index: g.name for g in o.vertex_groups if g.name in deform}
        mx = z = 0
        for v in o.data.vertices:
            ws = [g.weight for g in v.groups if g.group in gi and g.weight > 1e-6]
            mx = max(mx, len(ws))
            if sum(ws) <= 1e-6:
                z += 1
        per_obj.append({"object": o.name, "verts": len(o.data.vertices), "max_influences": mx, "zero_weight": z})
        worst, zero, nverts = max(worst, mx), zero + z, nverts + len(o.data.vertices)
    checks["weights"] = per_obj
    if not per_obj:
        codes.append("NoSkinnedMesh")
    if worst > 4:
        codes.append("MaxInfluences:%d>4" % worst)
    if zero:
        codes.append("ZeroWeightVerts:%d" % zero)


# ---------------------------------------------------------------- G3

def shape_coords(obj, key):
    co = np.empty(len(obj.data.vertices) * 3)
    key.data.foreach_get("co", co)
    co = co.reshape(-1, 3)
    M = np.array(obj.matrix_world)
    return co @ M[:3, :3].T + M[:3, 3]


def behind_skin(shell_co, shell_polys, comp_objs, tol, dirs, reach=0.05):
    """Per component: indices of vertices inside the face tissue, and the deepest such distance.
    Inside = for at least 2 of the 3 directions the first face/cavity surface hit within reach is met
    from its back side (normal . ray > 0) more than tol away. A vertex in the air of a socket or mouth
    sees the inner skin from its front side or escapes through the opening."""
    bvh = BVHTree.FromPolygons([Vector(p) for p in shell_co], shell_polys, all_triangles=False)
    dirs = [Vector(d).normalized() for d in dirs]
    out = {}
    for o in comp_objs:
        m = Mesh([o], weld=1e-9)
        ids, depth = set(), 0.0
        for i, p in enumerate(m.co):
            v, votes, dmin = Vector(p), 0, None
            for d in dirs:
                loc, nrm, _, dist = bvh.ray_cast(v, d, reach)
                if loc is not None and nrm.dot(d) > 0 and dist > tol:
                    votes += 1
                    dmin = dist if dmin is None else min(dmin, dist)
            if votes >= 2:
                ids.add(i)
                depth = max(depth, dmin)
        out[o.name] = (ids, depth)
    return out


def run_g3(path, fmt, opts, result):
    codes, checks, notes = result["codes"], result["checks"], result["notes"]
    tol = float(opts.get("tol", 0.0005))
    objs = mesh_objects()
    faces = [o for o in objs if role_of(o) == "main" and o.data.shape_keys]
    if not faces:
        codes.append("NoShapeKeys")
        return
    face = max(faces, key=lambda o: len(o.data.vertices))
    checks["face_object"] = face.name
    kb = face.data.shape_keys.key_blocks
    basis = face.data.shape_keys.reference_key
    base = shape_coords(face, basis)
    fmesh = Mesh([face], weld=1e-7)
    loops = boundary_loops(fmesh)
    # neck ring: vertex group "neck_ring", else the lowest large boundary loop
    raw_of_welded = {}
    q = np.round(base / 1e-7).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    vg = face.vertex_groups.get("neck_ring")
    if vg:
        ring = [v.index for v in face.data.vertices if any(g.group == vg.index and g.weight > 0 for g in v.groups)]
        ring_src = "vertex_group:neck_ring"
    elif loops:
        low = min(loops, key=lambda l: l["center"][2])
        ws = set(low["verts"])
        ring = [i for i in range(len(base)) if inv[i] in ws]
        ring_src = "lowest_boundary_loop"
    else:
        ring, ring_src = [], None
    checks["neck_ring"] = {"source": ring_src, "verts": len(ring)}
    if not ring:
        codes.append("NeckRingMissing")
    objs_shell = [face] + [o for o in objs if role_of(o) == "cavity"]
    hmesh = Mesh(objs_shell)
    hm, _ = head_region(hmesh.co)
    lm = head_landmarks(hmesh, hm)
    hloops = [l for l in boundary_loops(hmesh) if hm[l["verts"]].all()]
    _, rims = eye_rims(hloops, lm)
    eyes = [o for o in objs if role_of(o) == "eyes"]
    oral = [o for o in objs if role_of(o) == "oral"]
    checks["components"] = {"eyes": [o.name for o in eyes], "oral": [o.name for o in oral],
                            "eye_rims": [loop_json(l) for l in rims] if rims else None}
    if not rims:
        codes.append("EyeSocketClosed")
    # rims in face-object vertex indices
    rim_raw = []
    if rims:
        hq = np.round(hmesh.co / 1e-6).astype(np.int64)
        fq = np.round(base / 1e-6).astype(np.int64)
        lookup = {tuple(k): i for i, k in enumerate(fq)}
        for l in rims:
            rim_raw.append([lookup.get(tuple(hq[v])) for v in l["verts"]])
    keys = []
    lat, dep, fwd = lm["lat"], lm["dep"], lm["fwd"]
    face_polys = [list(p.vertices) for p in face.data.polygons]
    cav = [o for o in objs if role_of(o) == "cavity"]
    cav_mesh = Mesh(cav) if cav else None
    cav_co = cav_mesh.co if cav else np.zeros((0, 3))
    cav_polys = [[v + len(base) for v in p] for p in cav_mesh.polys()] if cav else []
    dirs = [np.eye(3)[dep] * fwd, np.eye(3)[2], -np.eye(3)[2]]
    rest = behind_skin(np.concatenate([base, cav_co]) if len(cav_co) else base, face_polys + cav_polys, eyes + oral, tol, dirs)
    for key in kb:
        if key == basis:
            continue
        co = shape_coords(face, key)
        disp = np.linalg.norm(co - base, axis=1)
        k = {"key": key.name, "max_displacement_m": round(float(disp.max()), 6)}
        if ring:
            k["neck_ring_delta_m"] = float(disp[ring].max())
            if k["neck_ring_delta_m"] > 0:
                codes.append("NeckRingDelta:%s:%.6f" % (key.name, k["neck_ring_delta_m"]))
        rk = []
        for l, raw in zip(rims or [], rim_raw):
            if None in raw:
                continue
            p0, p1 = base[raw], co[raw]

            def area(p):
                c = p.mean(0)
                n = np.zeros(3)
                for i in range(len(p)):
                    n += np.cross(p[i] - c, p[(i + 1) % len(p)] - c)
                return float(np.linalg.norm(n)) / 2

            a0, a1 = area(p0), area(p1)
            gap = float(np.ptp(p1[:, 2]))
            side = "pos" if l["center"][lat] > lm["cl"] else "neg"
            ratio = a1 / a0 if a0 else 0
            rk.append({"side": side, "area_ratio": round(ratio, 3), "vertical_opening_m": round(gap, 5)})
            if ratio < 0.5 or gap < 0.25 * float(np.ptp(p0[:, 2])):
                codes.append("RimClosed:eye_%s:%s" % (side, key.name))
        k["eye_rims"] = rk
        now = behind_skin(np.concatenate([co, cav_co]) if len(cav_co) else co, face_polys + cav_polys, eyes + oral, tol, dirs)
        pen = []
        for name, (ids, depth) in now.items():
            new = ids - rest[name][0]
            pen.append({"object": name, "behind_skin": len(ids), "new_vs_basis": len(new),
                        "max_depth_m": round(depth, 5)})
            if new:
                codes.append("Penetration:%s:%s:%d" % (name, key.name, len(new)))
        k["penetration"] = pen
        keys.append(k)
    checks["rest_penetration"] = [{"object": n, "behind_skin": len(i), "max_depth_m": round(d, 5)}
                                   for n, (i, d) in rest.items()]
    notes.append("rest-pose penetration is reported, not gated: the shared-eyes reference fit itself has "
                 "eyeball/lid triangle intersections, so no absolute threshold separates good from bad fits; "
                 "the gate fails on component vertices that move behind the skin under a shape key")
    # each eye sits inside its rim opening (front projection) and behind the rim plane
    seat = []
    for o in eyes:
        if "core" not in o.name.lower() and "eyeball" not in o.name.lower():
            continue
        c = np.array(o.matrix_world.translation) if not o.data.vertices else Mesh([o]).co.mean(0)
        ok = False
        for l in rims or []:
            rel = c - l["center"]
            planar = math.hypot(rel[lat], rel[2])
            if planar < 0.6 * l["radius"]:
                ok = True
        seat.append({"object": o.name, "inside_rim": ok})
        if not ok:
            codes.append("EyeNotSeated:%s" % o.name)
    checks["eye_seating"] = seat
    if oral:
        oc = Mesh(oral).co.mean(0)
        eye_z = float(np.mean([l["center"][2] for l in rims])) if rims else float(lm["nose"][2])
        mouth_loops = [l for l in hloops if l["center"][2] < lm["nose"][2] and abs(l["center"][lat] - lm["cl"]) < 0.1 * lm["W"]]
        seated, info = False, None
        for l in mouth_loops:
            rel = oc - l["center"]
            planar = math.hypot(rel[lat], rel[2])
            behind = -fwd * rel[dep]
            info = {"mouth_loop": loop_json(l), "planar_offset_m": round(planar, 5), "behind_m": round(float(behind), 5)}
            if planar < 1.5 * l["radius"] and behind > 0:
                seated = True
                break
        checks["oral_seating"] = {"centroid": [round(float(x), 4) for x in oc], "seated": seated, "detail": info,
                                  "method": "oral centroid projects within 1.5x a mouth boundary loop radius and lies behind it"}
        if not mouth_loops:
            codes.append("MouthClosed")
        elif not seated:
            codes.append("OralNotSeated")
    checks["shape_keys"] = keys
    if not keys:
        codes.append("NoShapeKeys")


# ---------------------------------------------------------------- G4

def run_g4(path, fmt, opts, result):
    codes, checks = result["codes"], result["checks"]
    objs = mesh_objects()
    body = [o for o in objs if (o.get("cast_role") or "").lower() == "body"] or \
        [o for o in objs if "body" in o.name.lower()]
    garments = [o for o in objs if (o.get("cast_role") or "").lower() == "garment"]
    if not body or not garments:
        codes.append("G4Inputs:body=%d,garments=%d" % (len(body), len(garments)))
        return
    body = body[0]
    depth_tol = float(opts.get("depth", 0.003))
    frac_tol = float(opts.get("frac", 0.005))
    cover = float(opts.get("cover", 0.05))
    hides = []
    for g in garments:
        for h in list(g.get("hides", [])):
            hides.append((g.name, str(h), body.vertex_groups.get(str(h)) is not None))
    checks["hides"] = [{"garment": g, "group": h, "exists": e} for g, h, e in hides]
    for g, h, e in hides:
        if not e:
            codes.append("HideGroupMissing:%s:%s" % (g, h))
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    poses = [("rest", None, None)]
    if arm:
        for want in ("walk", "sit", "elbow90"):
            act = next((a for a in bpy.data.actions if want in a.name.lower()), None)
            if act:
                fr = act.frame_range
                poses.append((want, act, (fr[0] + fr[1]) / 2))
            else:
                result["notes"].append("pose action '%s' not found; pose skipped" % want)
                codes.append("PoseMissing:%s" % want)
    dg = bpy.context.evaluated_depsgraph_get()
    res = []
    for name, act, frame in poses:
        if arm and act:
            ad = arm.animation_data_create()
            ad.action = act
            if getattr(ad, "action_slot", 1) is None and getattr(act, "slots", None):
                ad.action_slot = act.slots[0]
            bpy.context.scene.frame_set(int(frame))
        elif arm and arm.animation_data:
            arm.animation_data.action = None
            for pb in arm.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
        dg.update()
        be = body.evaluated_get(dg)
        bm_ = be.to_mesh()
        mw = body.matrix_world
        bv = [(mw @ v.co, (mw.to_3x3() @ v.normal).normalized()) for v in bm_.vertices]
        be.to_mesh_clear()
        for g in garments:
            ge = g.evaluated_get(dg)
            gm = ge.to_mesh()
            gw = g.matrix_world
            bvh = BVHTree.FromPolygons([gw @ v.co for v in gm.vertices], [list(p.vertices) for p in gm.polygons])
            ge.to_mesh_clear()
            covered = pen = 0
            worst = 0.0
            for p, n in bv:
                out_hit = bvh.ray_cast(p + n * 1e-5, n, cover)
                in_hit = bvh.ray_cast(p - n * 1e-5, -n, cover)
                if out_hit[0] is None and in_hit[0] is None:
                    continue
                covered += 1
                if in_hit[0] is not None and (out_hit[0] is None or in_hit[3] < out_hit[3]):
                    worst = max(worst, in_hit[3])
                    if in_hit[3] > depth_tol:
                        pen += 1
            frac = pen / covered if covered else 0.0
            r = {"pose": name, "garment": g.name, "covered": covered, "penetrating": pen,
                 "frac": round(frac, 5), "max_depth_m": round(worst, 5)}
            res.append(r)
            if frac > frac_tol:
                codes.append("Penetration:%s:%s:%.4f" % (g.name, name, frac))
    checks["penetration"] = res
    checks["limits"] = {"depth_m": depth_tol, "frac": frac_tol, "cover_m": cover}


# ---------------------------------------------------------------- G5

def run_g5(path, fmt, opts, result):
    codes, checks = result["codes"], result["checks"]
    only = set((opts.get("only") or "budget,textures,anatomy").split(","))
    result["checks_run"] = sorted(only)
    if "anatomy" in only:
        # assembled avatar: body + garments (+ head/eyes/oral), hair excluded from the height
        body = [o for o in mesh_objects() if not o.hide_render and not o.hide_get()
                and "hair" not in o.name.lower() and (o.get("cast_role") or "").lower() not in ("hair", "hair_cards")]
        run_anatomy(body, opts, codes, checks, "body")
    if not only & {"budget", "textures"}:
        return
    with open(PROFILES, encoding="utf-8") as f:
        budget = json.load(f)["budget"]
    comps = budget["components"]
    buckets = {k: 0 for k in comps}
    buckets["eyes_oral"] = 0
    buckets["unclassified"] = 0
    hidden_groups = set()
    objs = [o for o in mesh_objects() if not o.hide_render and not o.hide_get()]
    for o in objs:
        if (o.get("cast_role") or "").lower() == "garment" or "garment" in o.name.lower():
            hidden_groups.update(str(h) for h in o.get("hides", []))
    rows = []
    for o in objs:
        role = (o.get("cast_role") or "").lower()
        n = o.name.lower()
        if not role:
            if role_of(o) in ("eyes", "oral", "cavity"):
                role = "eyes_oral"
            elif "hair" in n:
                role = "hair_cards"
            elif "head" in n or "face" in n:
                role = "head"
            elif "body" in n:
                role = "body"
            elif "prop" in n:
                role = "props"
            elif any(t in n for t in ("garment", "cloth", "shirt", "pants", "skirt", "dress", "shoe", "boot")):
                role = "garments"
            else:
                role = "unclassified"
        role = {"hair": "hair_cards", "garment": "garments", "prop": "props", "eyes": "eyes_oral",
                "oral": "eyes_oral"}.get(role, role)
        gidx = {g.index for g in o.vertex_groups if g.name in hidden_groups}
        tris = 0
        hidden = 0
        for p in o.data.polygons:
            t = len(p.vertices) - 2
            if gidx and all(any(g.group in gidx and g.weight > 0 for g in o.data.vertices[v].groups)
                            for v in p.vertices):
                hidden += t
                continue
            tris += t
        buckets[role] = buckets.get(role, 0) + tris
        rows.append({"object": o.name, "role": role, "visible_tris": tris, "hidden_tris": hidden})
    total = sum(buckets.values())
    checks["objects"] = rows
    checks["buckets"] = buckets
    checks["total_visible_tris"] = total
    checks["cap"] = budget["assembled_cap"]
    if total > budget["assembled_cap"]:
        codes.append("TriTotal:%d>%d" % (total, budget["assembled_cap"]))
    for k, cap in comps.items():
        if buckets.get(k, 0) > cap:
            codes.append("ComponentBudget:%s:%d>%d" % (k, buckets[k], cap))
    if buckets["unclassified"]:
        codes.append("UnclassifiedMesh:%d" % buckets["unclassified"])
    if buckets["eyes_oral"]:
        result["notes"].append("eyes/oral triangles have no row in the budget table; counted only in the total")
    tex = []
    for o in objs:
        for slot in o.material_slots:
            m = slot.material
            if not m or not m.use_nodes:
                continue
            for node in m.node_tree.nodes:
                if node.type != "TEX_IMAGE" or not node.image:
                    continue
                img = node.image
                colour = any(l.to_socket.name == "Base Color" for out in node.outputs for l in out.links)
                cap = 2048 if colour else 1024
                size = max(img.size)
                tex.append({"image": img.name, "size": list(img.size), "colour": colour, "cap": cap})
                if size > cap:
                    codes.append("TextureSize:%s:%d>%d" % (img.name, size, cap))
    seen = set()
    checks["textures"] = [t for t in tex if not (t["image"] in seen or seen.add(t["image"]))]
    result["notes"].append("node characters/test_vrm_load.mjs is run by gate.mjs (part A), not here")


# ---------------------------------------------------------------- main

def main():
    result = {"codes": [], "checks": {}, "notes": [], "blender": bpy.app.version_string,
              "script_sha256": sha256(os.path.abspath(__file__))}
    out = None
    try:
        path, gate, out, opts = parse_args()
        result.update({"gate": gate, "input": os.path.abspath(path), "options": opts})
        result["input_sha256"] = sha256(path) if os.path.isfile(path) else None
        # Overwrite the output first: a hard-killed run (os.abort, exit 127, timeout) must never leave
        # an older passing JSON at this path. The placeholder is bound to the input by its sha256.
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"ok": False, "blocking": ["AutoRunIncomplete"], "pass": False, "codes": ["AutoRunIncomplete"],
                       "gate": gate, "input": os.path.abspath(path), "input_sha256": result["input_sha256"],
                       "exit_code": None}, f, indent=2)
        if os.environ.get("AUTO_CHECK_TEST_ABORT"):  # test hook: simulate a hard kill after the placeholder
            sys.stdout.flush()
            os.abort()
        fmt = load_scene(path)
        result["format"] = fmt
        {"G1": run_g1, "G2": run_g2, "G3": run_g3, "G4": run_g4, "G5": run_g5}[gate](path, fmt, opts, result) \
            if gate in ("G1", "G2", "G3", "G4", "G5") else (_ for _ in ()).throw(GateError("UnknownGate:%s" % gate))
        result["pass"] = not result["codes"]
        code = 0 if result["pass"] else 1
    except GateError as e:
        result["pass"] = False
        result["error"] = str(e)
        result["codes"].append(str(e).split(":")[0])
        code = 2
    except Exception as e:
        result["pass"] = False
        result["error"] = "Exception:%s" % e
        result["traceback"] = traceback.format_exc()
        code = 2
    result["exit_code"] = code
    # gate.mjs contract (part A, tools/gate/gate.mjs aggregate): {ok: boolean, blocking: string[]}.
    # gate.mjs rejects ok !== true as AutoContractInvalid and turns a non-empty blocking into
    # "AutoFail" + the codes, so ok means "the check ran" (exit 0 or 1) and blocking carries the
    # failure codes (empty on pass). A crashed or unusable run (exit 2) is ok=false.
    result["ok"] = code in (0, 1)
    result["blocking"] = list(result["codes"]) if code == 1 else ([] if code == 0 else
                                                                   [result.get("error", "AutoCheckError")])
    text = json.dumps(result, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    print("AUTO_CHECK " + json.dumps({"gate": result.get("gate"), "pass": result["pass"], "codes": result["codes"],
                                      "exit": code}))
    sys.stdout.flush()
    os._exit(code)


main()
