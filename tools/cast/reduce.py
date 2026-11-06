"""Reduce a Tripo mesh to a gen-profile budget.

Run: blender -b --factory-startup -P tools/cast/reduce.py -- <in.glb> <out-base> --profile <name>
     [--bake-size 2048] [--voxel-div N[,N...]] [--height <metres>]

--voxel-div  voxel pre-remesh resolutions tried in order (voxel size = longest side / N); default
             150,200,120 for the body (11 mm voxels on a 1.65 m body). Hands of a T-pose body are
             cut off and reduced separately without voxels (decimate + simple subdivision +
             shrinkwrap, all quads) so fingers stay separate, then joined back.
             --voxel-div 150 --no-hand-split reproduces the earlier finger-merging output (fixture).
--height     uniform rescale so the mesh is <metres> tall, feet on Z=0, centred on X/Y (the plan's
             "rescaled to its slot target at reduce.py time").
--gender f|m slot target height when --height is not given: base_body f 1.65 m, m 1.78 m. base_body
             without --height requires --gender. Other profiles (head, parts) are not rescaled unless
             --height is given. scale_factor (1.0 when unchanged) is always written to reduce.json.

Quality note: the body is voxel-remeshed (default --voxel-div 150,200,120, 11 mm on 1.65 m) and the
hands of a T-pose body are split off (default; --no-hand-split disables) and reduced without voxels.
bake_rmse is also reported per region (bake_rmse_regions: hands / body). On iyeon body-wide the
body region (thin skirt layers) is the one that degrades; that is acceptable for a G1 fixture but
a delivered base needs a review of bake_rmse_regions.body.
Determinism: the script always runs under 'blender -t 1' (it re-launches itself when started without
it) and pins the Cycles bake settings (pin_bake_determinism); see ensure_single_thread for the cause.

Quad profiles: QuadriFlow remesh to <= face_limit (voxel pre-remesh if the source is not
manifold). Non-quad profiles (props, single parts): Decimate collapse to <= tri_limit.
Base color and normal are baked from the source onto new UVs, then written as
<out-base>.blend (quad topology), <out-base>.glb (triangulated) and <out-base>.reduce.json
{src_sha, blend_sha, glb_sha, quads, tris, bake_rmse, ...}. Exit 0 ok, 2 error.
"""
import bpy
import bmesh
import hashlib
import json
import os
import sys
import traceback

import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

NO_HAND_SPLIT = False
SLOT_HEIGHT = {"f": 1.65, "m": 1.78}
HAND_REGION = {}
TOOL_VERSION = "reduce-5b-3-t1"
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PROFILES = os.path.join(REPO, "characters", "_cast", "gen-profiles.json")


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
        if argv[i] == "--no-hand-split":
            opts["no-hand-split"] = True
            i += 1
        elif argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 2 or "profile" not in opts:
        raise SystemExit("usage: reduce.py -- <in.glb> <out-base> --profile <name> [--bake-size N] "
                         "[--voxel-div N,...] [--height m]")
    divs = tuple(int(x) for x in opts.get("voxel-div", "150,200,120").split(","))
    height = float(opts["height"]) if "height" in opts else None
    gender = opts.get("gender")
    if height is None and opts["profile"] == "base_body":
        if gender not in SLOT_HEIGHT:
            raise SystemExit("base_body needs --height or --gender f|m (slot target height)")
        height = SLOT_HEIGHT[gender]
    global NO_HAND_SPLIT
    NO_HAND_SPLIT = "no-hand-split" in opts
    return pos[0], pos[1], opts["profile"], int(opts.get("bake-size", 2048)), divs, height


def face_stats(obj):
    quads = sum(1 for p in obj.data.polygons if len(p.vertices) == 4)
    tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    return len(obj.data.polygons), quads, tris


def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def import_source(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("NoMesh")
    activate(meshes[0])
    for o in meshes:
        o.select_set(True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    src = bpy.context.view_layer.objects.active
    # Bake world transforms into the mesh so source and target share one space.
    src.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in list(bpy.context.scene.objects):
        if o != src:
            bpy.data.objects.remove(o, do_unlink=True)
    src.name = "source"
    return src


def repair_manifold(obj):
    """Collapsing short edges can pinch a few spots into non-manifold vertices/edges. Delete the faces
    around them and fill the holes (planar ngon fill, then triangulate the fills)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bad = {v for e in bm.edges if not e.is_manifold or not e.is_contiguous for v in e.verts}
    bad |= {v for v in bm.verts if not v.is_manifold}
    n = len(bad)
    if bad:
        faces = {f for v in bad for f in v.link_faces}
        bmesh.ops.delete(bm, geom=list(faces), context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bnd = [e for e in bm.edges if e.is_boundary]
        res = bmesh.ops.holes_fill(bm, edges=bnd, sides=0)
        bmesh.ops.triangulate(bm, faces=res["faces"])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    keep_largest_component(obj)
    return {"bad_verts": n, "short_after": short_edges(obj.data)}


def qf_diag(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    d = {"nonmanifold_e": sum(1 for e in bm.edges if not e.is_manifold), "boundary_e": sum(1 for e in bm.edges if e.is_boundary),
         "nonmanifold_v": sum(1 for v in bm.verts if not v.is_manifold), "noncontig": sum(1 for e in bm.edges if not e.is_contiguous),
         "short": short_edges(obj.data)}
    bm.free()
    return d


def is_qf_ready(obj):
    """Mirror of Blender's QuadriFlow precondition: closed 2-manifold, consistent winding and
    no edge with all |d| <= 1e-4."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    ok = (all(e.is_manifold and not e.is_boundary for e in bm.edges)
          and all(v.is_manifold for v in bm.verts)
          and all(e.is_contiguous for e in bm.edges))
    bm.free()
    return ok and short_edges(obj.data) == 0


def keep_largest_component(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seen, comps = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, comp = [f], []
        seen.add(f)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    drop = [f for c in comps[1:] for f in c]
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
        bm.to_mesh(obj.data)
    bm.free()


def short_edges(mesh):
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    ed = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    return int((np.abs(co[ed[:, 0]] - co[ed[:, 1]]) <= 1e-4).all(axis=1).sum())


def dissolve_short(obj, log):
    """Blender's QuadriFlow wrapper rejects any edge whose |dx|,|dy|,|dz| are all <= 1e-4 as
    'not manifold'. Voxel meshes of a 1 m character always contain some, so collapse them."""
    before = short_edges(obj.data)
    for _ in range(4):
        if short_edges(obj.data) == 0:
            break
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=2e-4)
        bm.to_mesh(obj.data)
        bm.free()
    log["short_edges"] = {"before": before, "after": short_edges(obj.data)}


def quadriflow(target):
    return bpy.ops.object.quadriflow_remesh(
        target_faces=target, mode="FACES", seed=0, use_mesh_symmetry=False,
        use_preserve_sharp=False, use_preserve_boundary=False,
        preserve_attributes=False, smooth_normals=False)


HAND_LEN = 0.115   # hand piece = beyond tip - HAND_LEN * height (plan hand length 0.095-0.115 H)
HAND_OVERLAP = 0.02  # body piece keeps up to tip - (HAND_LEN - HAND_OVERLAP) * height; caps hide inside


def mesh_size(obj):
    """Longest bbox side from the vertex coordinates (obj.dimensions is stale right after bmesh edits)."""
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float(np.ptp(co, 0).max()) if len(co) else 0.0


def weld(obj, size):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=size * 1e-5)
    bm.to_mesh(obj.data)
    bm.free()


def side_frame(obj):
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    H = float(np.ptp(co[:, 2]))
    lat = int(np.argmax(np.ptp(co[:, :2], 0)))
    cl = float((co[:, lat].min() + co[:, lat].max()) / 2)
    return co, H, lat, cl


def cut(obj, lat, cl, sgn, threshold, keep_outer):
    """Delete faces with any vertex on the unwanted side of rel = sgn*(x-cl) = threshold."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if keep_outer:
        drop = [f for f in bm.faces if any(sgn * (v.co[lat] - cl) < threshold for v in f.verts)]
    else:
        drop = [f for f in bm.faces if any(sgn * (v.co[lat] - cl) >= threshold for v in f.verts)]
    bmesh.ops.delete(bm, geom=drop, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()


def hand_pieces(src_welded, log):
    """T-pose body: copies of each hand (beyond tip - HAND_LEN*H along the lateral axis) cut from the
    welded source. Returns [(obj, side, sgn, cut_body)] or [] when the arms are not spread."""
    co, H, lat, cl = side_frame(src_welded)
    out, info = [], {"lateral_axis": "xyz"[lat], "height": H}
    for side, sgn in (("L", 1), ("R", -1)):
        tip = float((sgn * (co[:, lat] - cl)).max())
        if tip < 0.3 * H:
            return [], None
        h = src_welded.copy()
        h.data = src_welded.data.copy()
        bpy.context.scene.collection.objects.link(h)
        h.name = "hand_" + side
        cut(h, lat, cl, sgn, tip - HAND_LEN * H, keep_outer=True)
        out.append((h, side, sgn, tip - (HAND_LEN - HAND_OVERLAP) * H))
        HAND_REGION.setdefault("split", {"lat": lat, "cl": cl})[side] = tip - HAND_LEN * H
        info[side] = {"tip": tip, "cut_hand": tip - HAND_LEN * H, "cut_body": tip - (HAND_LEN - HAND_OVERLAP) * H}
    log["hand_split"] = info
    return out, (lat, cl)


def hand_quads(obj, limit, entry_log):
    """Hands keep the source topology class: QuadriFlow on a voxel hand either fails or tears the
    1 cm fingers (evidence task-5b-fix.txt). Instead: triangulate, collapse-decimate to limit/3 triangles
    (edge collapse never merges separate fingers), one SIMPLE subdivision (each triangle -> 3 quads, all
    faces quads), then shrinkwrap the new vertices back onto the source hand surface."""
    ref = obj.copy()
    ref.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(ref)
    activate(obj)
    tri = obj.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=tri.name)
    n = len(obj.data.polygons)
    want = max(limit // 3, 50)
    if n > want:
        dec = obj.modifiers.new("dec", "DECIMATE")
        dec.decimate_type = "COLLAPSE"
        dec.ratio = want / n
        dec.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=dec.name)
    sub = obj.modifiers.new("sub", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = 1
    bpy.ops.object.modifier_apply(modifier=sub.name)
    sw = obj.modifiers.new("sw", "SHRINKWRAP")
    sw.wrap_method = "NEAREST_SURFACEPOINT"
    sw.target = ref
    bpy.ops.object.modifier_apply(modifier=sw.name)
    bpy.data.objects.remove(ref, do_unlink=True)
    faces, quads, _ = face_stats(obj)
    entry_log.update({"method": "decimate+simple-subdiv+shrinkwrap", "source_faces": n,
                      "decimated_tris": len(obj.data.polygons) // 3, "faces": faces, "quads": quads})


def quad_piece(obj, limit, divs, entry_log):
    """Voxel pre-remesh (when needed) + QuadriFlow to <= limit faces on one object.
    Blender's QuadriFlow rejects edges whose |dx|,|dy|,|dz| are all <= 1e-4 m (absolute), which a
    millimetre voxel grid on a 25 cm hand always produces; the piece is therefore scaled to a 10 m
    working size for the remesh and scaled back afterwards."""
    activate(obj)
    work = 10.0 / max(mesh_size(obj), 1e-9)
    obj.data.transform(Matrix.Scale(work, 4))
    obj.data.update()
    entry_log["work_scale"] = work
    try:
        _quad_piece(obj, limit, divs, entry_log)
    finally:
        obj.data.transform(Matrix.Scale(1.0 / work, 4))
        obj.data.update()
        if entry_log.get("voxel_size"):
            entry_log["voxel_size_m"] = entry_log["voxel_size"] / work


def _quad_piece(obj, limit, divs, entry_log):
    size = mesh_size(obj)
    welded = obj.data.copy()
    entry_log["tries"] = tries = []
    for div in (None,) + tuple(divs):
        obj.data = welded.copy()
        entry = {"voxel_div": div}
        tries.append(entry)
        print("REDUCE_STEP piece=%s limit=%d voxel_div=%s" % (obj.name, limit, div), flush=True)
        if div is not None:
            obj.data.remesh_voxel_size = size / div
            obj.data.use_remesh_preserve_volume = True
            bpy.ops.object.voxel_remesh()
            keep_largest_component(obj)
            tmp = {}
            dissolve_short(obj, tmp)
            entry["short_edges"] = tmp["short_edges"]
            if not is_qf_ready(obj):
                entry["repair"] = repair_manifold(obj)
            entry["voxel_size"] = size / div
        entry["input_faces"] = len(obj.data.polygons)
        entry["qf_ready"] = is_qf_ready(obj)
        if not entry["qf_ready"]:
            entry["diag"] = qf_diag(obj)
            continue
        base = obj.data.copy()
        target, best, done = int(limit * 0.92), None, False
        entry["attempts"] = attempts = []
        try:
            for _ in range(4):
                obj.data = base.copy()
                res = quadriflow(target)
                faces, quads, _ = face_stats(obj)
                attempts.append({"target": target, "result": sorted(res), "faces": faces, "quads": quads})
                if "FINISHED" not in res:
                    break
                if faces <= limit and (best is None or faces > best[0]):
                    best = (faces, target)
                if 0.75 * limit <= faces <= limit:
                    done = True
                    break
                target = max(100, int(target * (0.92 * limit) / max(faces, 1)))
        except RuntimeError as e:
            attempts.append({"target": target, "error": str(e).strip()})
        if not done and best is not None:
            obj.data = base.copy()
            quadriflow(best[1])
            entry["final_target"] = best[1]
            done = len(obj.data.polygons) <= limit
        if done:
            entry_log["faces"] = len(obj.data.polygons)
            entry_log["voxel_size"] = entry.get("voxel_size")
            bpy.data.meshes.remove(welded)
            return
    raise RuntimeError("QuadriFlowFailed:%s" % json.dumps(tries))


def make_target(src, profile, log, divs, hand_divs=(90, 75, 60, 120)):
    tgt = src.copy()
    tgt.data = src.data.copy()
    tgt.name = "reduced"
    bpy.context.scene.collection.objects.link(tgt)
    tgt.data.materials.clear()
    activate(tgt)
    dims = tgt.dimensions
    size = max(dims)
    if profile["quad"]:
        weld(tgt, size)
        limit = profile["face_limit"]
        hands, frame = ([], None)
        if not NO_HAND_SPLIT and log.get("kind") == "body":
            hands, frame = hand_pieces(tgt, log)
        log["hand_split_used"] = bool(hands)
        if hands:
            budget = {"body": int(limit * 0.62), "hand_L": int(limit * 0.18), "hand_R": int(limit * 0.18)}
            log["hand_budget"] = budget
            quad_piece(tgt, budget["body"], divs, log.setdefault("pieces", {}).setdefault("body", {}))
            lat, cl = frame
            for h, side, sgn, cut_body in hands:
                cut(tgt, lat, cl, sgn, cut_body, keep_outer=False)
                hand_quads(h, budget["hand_" + side], log["pieces"].setdefault("hand_" + side, {}))
            # join (no boolean): each closed hand shell overlaps the cut wrist of the body by
            # HAND_OVERLAP x height, so the seam is hidden inside the hand. A hole-tolerant exact union
            # was tried and tore the 2.8 mm hand surface apart (evidence task-5b-fix.txt).
            activate(tgt)
            for h, _, _, _ in hands:
                h.select_set(True)
            bpy.ops.object.join()
            tgt = bpy.context.view_layer.objects.active
            tgt.name = "reduced"
            faces, quads, _ = face_stats(tgt)
            log["after_join"] = {"faces": faces, "quads": quads, "components": 1 + len(hands)}
            if faces > limit:
                raise RuntimeError("FaceLimitAfterJoin:%d>%d" % (faces, limit))
        else:
            quad_piece(tgt, limit, divs, log.setdefault("pieces", {}).setdefault("whole", {}))
    else:
        limit = profile["tri_limit"]
        bpy.ops.object.modifier_add(type="TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier=tgt.modifiers[-1].name)
        n = len(tgt.data.polygons)
        if n > limit:
            mod = tgt.modifiers.new("dec", "DECIMATE")
            mod.ratio = (limit * 0.98) / n
            bpy.ops.object.modifier_apply(modifier=mod.name)
        log["decimate"] = {"from": n, "to": len(tgt.data.polygons)}
    for p in tgt.data.polygons:
        p.use_smooth = True
    for layer in list(tgt.data.uv_layers):
        tgt.data.uv_layers.remove(layer)
    tgt.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.003, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return tgt


def principled(mat):
    if not mat or not mat.use_nodes:
        return None
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def rewire_emission(src):
    """Route each source material's base color into an emission output for an exact color bake."""
    for slot in src.material_slots:
        mat = slot.material
        bsdf = principled(mat)
        if not bsdf:
            continue
        nt = mat.node_tree
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        em = nt.nodes.new("ShaderNodeEmission")
        base = bsdf.inputs["Base Color"]
        if base.is_linked:
            nt.links.new(base.links[0].from_socket, em.inputs["Color"])
        else:
            em.inputs["Color"].default_value = base.default_value
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])


def bake(src, tgt, size, kind, img_name, extrusion):
    img = bpy.data.images.new(img_name, size, size, alpha=False, float_buffer=False)
    if kind == "NORMAL":
        img.colorspace_settings.name = "Non-Color"
    mat = tgt.data.materials[0]
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    mat.node_tree.nodes.active = node
    bpy.ops.object.select_all(action="DESELECT")
    src.select_set(True)
    tgt.select_set(True)
    bpy.context.view_layer.objects.active = tgt
    bpy.ops.object.bake(type=kind, use_selected_to_active=True, cage_extrusion=extrusion,
                        max_ray_distance=extrusion * 2, margin=8, normal_space="TANGENT")
    return img, node


def source_basecolor(src):
    imgs = []
    for slot in src.material_slots:
        bsdf = principled(slot.material)
        img = None
        if bsdf and bsdf.inputs["Base Color"].is_linked:
            n = bsdf.inputs["Base Color"].links[0].from_node
            if n.type == "TEX_IMAGE":
                img = n.image
        imgs.append(img)
    return imgs


def img_array(img):
    w, h = img.size
    return np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)


def sample(arr, uv):
    h, w = arr.shape[:2]
    x = int((uv[0] % 1.0) * w) % w
    y = int((uv[1] % 1.0) * h) % h
    return arr[y, x, :3]


def bake_rmse(src, tgt, baked):
    """RMSE (0..1 sRGB) between the source base color at the nearest source point and the baked
    texel at each reduced face centre. None when the source has no base color texture."""
    src_imgs = source_basecolor(src)
    if not any(src_imgs):
        return None, 0
    arrs = [img_array(i) if i else None for i in src_imgs]
    sm = src.data
    sm.calc_loop_triangles()
    s_uv = sm.uv_layers.active.data
    tris = list(sm.loop_triangles)
    bvh = BVHTree.FromPolygons([v.co for v in sm.vertices], [t.vertices for t in tris])
    baked_arr = img_array(baked)
    t_uv = tgt.data.uv_layers.active.data
    err, n = 0.0, 0
    regions = {}
    hs = HAND_REGION.get("split")
    for p in tgt.data.polygons:
        loops = list(p.loop_indices)
        uv = sum((t_uv[l].uv for l in loops), Vector((0, 0))) / len(loops)
        loc, _, idx, _ = bvh.find_nearest(p.center)
        if idx is None:
            continue
        t = tris[idx]
        a = arrs[t.material_index] if t.material_index < len(arrs) else None
        if a is None:
            continue
        vs = [sm.vertices[v].co for v in t.vertices]
        us = [s_uv[l].uv.to_3d() for l in t.loops]
        suv = barycentric_transform(loc, vs[0], vs[1], vs[2], us[0], us[1], us[2])
        d = sample(a, suv) - sample(baked_arr, uv)
        e = float(np.dot(d, d)) / 3.0
        err += e
        n += 1
        reg = "body"
        if hs:
            lat, cl = hs["lat"], hs["cl"]
            r_ = p.center[lat] - cl
            if r_ > hs["L"] or -r_ > hs["R"]:
                reg = "hands"
        acc = regions.setdefault(reg, [0.0, 0])
        acc[0] += e
        acc[1] += 1
    HAND_REGION["rmse"] = {k: {"rmse": (v[0] / v[1]) ** 0.5, "samples": v[1]} for k, v in sorted(regions.items())}
    return (err / n) ** 0.5 if n else None, n


BAKE_THREADS = 1


def pin_bake_determinism(scene, log):
    """Cycles bake settings; with the process-wide single thread (see ensure_single_thread) they remove
    every run-to-run variation found. Root cause (task-5b-det.txt): Mesh.calc_tangents (MikkTSpace),
    which the TANGENT-space normal bake uses, runs on Blender's global task scheduler; its float
    tangents differ by up to 1.8e-7 between calls when more than one thread is available, and one
    texel of the 8-bit normal map lies on a 1/255 rounding boundary, so about 1 run in 32 rounded
    differently. scene.render.threads does not reach that scheduler; only the command-line -t does."""
    scene.render.threads_mode = "FIXED"
    scene.render.threads = BAKE_THREADS
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = 1
    cy.seed = 0
    cy.use_animated_seed = False
    cy.use_denoising = False
    cy.use_adaptive_sampling = False
    if hasattr(cy, "sample_offset"):
        cy.sample_offset = 0
    if hasattr(cy, "debug_use_spatial_splits"):
        cy.debug_use_spatial_splits = False
    log["bake_settings"] = {"threads_mode": scene.render.threads_mode, "threads": scene.render.threads,
                            "device": cy.device, "samples": cy.samples, "seed": cy.seed,
                            "animated_seed": cy.use_animated_seed, "denoise": cy.use_denoising,
                            "adaptive": cy.use_adaptive_sampling}


def mesh_sha(obj):
    h = hashlib.sha256()
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    h.update(co.tobytes())
    lv = np.empty(len(obj.data.loops), dtype=np.int32)
    obj.data.loops.foreach_get("vertex_index", lv)
    h.update(lv.tobytes())
    if obj.data.uv_layers.active:
        uv = np.empty(len(obj.data.loops) * 2)
        obj.data.uv_layers.active.data.foreach_get("uv", uv)
        h.update(uv.tobytes())
    return h.hexdigest()[:12]


def image_sha(img):
    px = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
    img.pixels.foreach_get(px)
    return hashlib.sha256(px.tobytes()).hexdigest()[:12]


def stage_sha(log, name, value):
    log.setdefault("stages", []).append([name, value])


def ensure_single_thread():
    """Re-run this script under 'blender -t 1' unless it already runs that way. The global task
    scheduler thread count can only be set on the command line, and calc_tangents (used by the
    tangent-space normal bake) is not bit-stable with more than one thread."""
    argv = sys.argv
    if "-t" in argv and argv[argv.index("-t") + 1] == "1":
        return "cli -t 1"
    if os.environ.get("REDUCE_SINGLE_THREAD_CHILD"):
        raise SystemExit("SingleThreadRelaunchFailed")
    import subprocess
    cmd = [bpy.app.binary_path, "-b", "--factory-startup", "-t", "1", "-P", os.path.abspath(__file__), "--"] + \
        argv[argv.index("--") + 1:]
    env = dict(os.environ, REDUCE_SINGLE_THREAD_CHILD="1")
    print("REDUCE_STEP relaunch -t 1", flush=True)
    r = subprocess.run(cmd, env=env)
    sys.stdout.flush()
    os._exit(r.returncode)


def main():
    threading_mode = ensure_single_thread()
    src_path, out_base, profile_name, bake_size, divs, height = parse_args()
    with open(PROFILES, encoding="utf-8") as f:
        profiles = json.load(f)["profiles"]
    if profile_name not in profiles:
        raise SystemExit("UnknownProfile:%s" % profile_name)
    profile = profiles[profile_name]
    log = {"tool_version": TOOL_VERSION, "threading": threading_mode, "profile": profile_name, "profile_def": profile,
           "src": os.path.abspath(src_path), "scale_factor": 1.0, "height_m": None,
           "blender": bpy.app.version_string, "bake_size": bake_size, "voxel_divs": list(divs)}
    log["src_sha"] = sha256(src_path)
    src = import_source(src_path)
    log["src_faces"], _, log["src_tris"] = face_stats(src)
    if height:
        co = np.empty(len(src.data.vertices) * 3)
        src.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        mn, mx = co.min(0), co.max(0)
        k = height / float(mx[2] - mn[2])
        off = np.array([-(mn[0] + mx[0]) / 2, -(mn[1] + mx[1]) / 2, -mn[2]])
        src.data.vertices.foreach_set("co", ((co + off) * k).reshape(-1))
        src.data.update()
        log["scale_factor"] = k
        log["height_m"] = height
    log["kind"] = "body" if profile_name == "base_body" else profile_name
    stage_sha(log, "src_mesh", mesh_sha(src))
    tgt = make_target(src, profile, log, divs)
    stage_sha(log, "target_mesh_uv", mesh_sha(tgt))

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    pin_bake_determinism(scene, log)
    scene.render.bake.use_pass_direct = False
    print("REDUCE_STEP bake_settings %s" % json.dumps(log["bake_settings"]), flush=True)
    scene.render.bake.use_pass_indirect = False

    mat = bpy.data.materials.new("reduced")
    mat.use_nodes = True
    tgt.data.materials.clear()
    tgt.data.materials.append(mat)
    for p in tgt.data.polygons:
        p.material_index = 0
    # the 11 mm voxel body sits up to about one voxel off thin skirt layers; a 2% (3 cm) cage reaches
    # them (1% left black unbaked patches on the skirt)
    extrusion = mesh_size(tgt) * 0.02
    print("REDUCE_STEP bake", flush=True)
    nrm_img, nrm_node = bake(src, tgt, bake_size, "NORMAL", "reduced_normal", extrusion)
    stage_sha(log, "normal_px", image_sha(nrm_img))
    if os.environ.get("REDUCE_DUMP_NORMAL"):
        px = np.empty(nrm_img.size[0] * nrm_img.size[1] * nrm_img.channels, dtype=np.float32)
        nrm_img.pixels.foreach_get(px)
        np.save(out_base + ".normal.npy", px.reshape(nrm_img.size[1], nrm_img.size[0], -1))
    rewire_emission(src)
    col_img, col_node = bake(src, tgt, bake_size, "EMIT", "reduced_basecolor", extrusion)
    stage_sha(log, "color_px", image_sha(col_img))
    log["bake_rmse"], log["bake_rmse_samples"] = bake_rmse(src, tgt, col_img)
    log["bake_rmse_regions"] = HAND_REGION.get("rmse")

    nt = mat.node_tree
    bsdf = principled(mat)
    nt.links.new(col_node.outputs["Color"], bsdf.inputs["Base Color"])
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(nrm_node.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.6

    bpy.data.objects.remove(src, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    for img in (col_img, nrm_img):
        img.pack()
    log["faces"], log["quads"], log["tris"] = face_stats(tgt)
    # content hash of the reduced result, independent of .blend pointer addresses: vertex positions
    # (rounded to 1e-6), face vertex lists, UVs and the baked pixels
    h = hashlib.sha256()
    co = np.empty(len(tgt.data.vertices) * 3)
    tgt.data.vertices.foreach_get("co", co)
    h.update(np.round(co, 6).tobytes())
    for p in tgt.data.polygons:
        h.update(np.array(p.vertices, dtype=np.int32).tobytes())
    uv = np.empty(len(tgt.data.loops) * 2)
    tgt.data.uv_layers.active.data.foreach_get("uv", uv)
    h.update(np.round(uv, 6).tobytes())
    for img in (col_img, nrm_img):
        px = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
        img.pixels.foreach_get(px)
        h.update(np.round(px, 4).tobytes())
    log["content_sha"] = h.hexdigest()


    os.makedirs(os.path.dirname(os.path.abspath(out_base)) or ".", exist_ok=True)
    blend_path = os.path.abspath(out_base + ".blend")
    glb_path = os.path.abspath(out_base + ".glb")
    activate(tgt)
    bpy.ops.export_scene.gltf(filepath=glb_path, export_format="GLB", use_selection=True,
                              export_apply=True, export_yup=True)
    bpy.ops.wm.save_as_mainfile(filepath=blend_path, compress=False, relative_remap=False)
    log["blend"], log["glb"] = blend_path, glb_path
    log["blend_sha"], log["glb_sha"] = sha256(blend_path), sha256(glb_path)
    with open(out_base + ".reduce.json", "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    print("REDUCE_OK " + json.dumps({k: log[k] for k in ("quads", "tris", "faces", "bake_rmse")}))


try:
    main()
except SystemExit as e:
    print("REDUCE_ERROR %s" % e)
    sys.stdout.flush()
    os._exit(2)
except Exception:
    traceback.print_exc()
    sys.stdout.flush()
    os._exit(2)
sys.stdout.flush()
os._exit(0)
