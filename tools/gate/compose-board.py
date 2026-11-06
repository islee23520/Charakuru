# tools/gate/compose-board.py
"""Compose a vision-review board: portrait + input images + gate renders.

Run (system Python, needs Pillow; Blender's bundled Python has no PIL):

    python tools/gate/compose-board.py --renders <render_dir> --out <board.png> \
        [--portrait <path>] [--input <path>]... [--cell 384] [--cols 6]

Reads <render_dir>/manifest.json, groups the renders by section
(body/head/crop/pose) and lays every image out as a labelled cell next to
the portrait (cast.json portrait) and the 8 input images.

Exit codes: 0 ok, 1 build failure, 2 usage errors
    (RenderDirMissing / RenderManifestMissing / InputMissing).
"""

import argparse
import hashlib
import json
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.stderr.write("PillowMissing: compose-board needs system Python with Pillow "
                     "(Blender's Python has no PIL)\n")
    sys.exit(1)

BG = (128, 128, 128)
SECTION_BG = (70, 70, 70)
LABEL_BG = (40, 40, 40)
TEXT = (240, 240, 240)
MARGIN = 12
LABEL_H = 22
SECTION_H = 34

FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
FONT_CACHE = {}


def load_font(size):
    if size in FONT_CACHE:
        return FONT_CACHE[size]
    for path in FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(path, size)
            FONT_CACHE[size] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    FONT_CACHE[size] = f
    return f


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("--renders", required=True, help="render-views.py output dir")
    ap.add_argument("--out", required=True, help="board PNG path")
    ap.add_argument("--portrait", default=None, help="portrait image (cast.json portrait)")
    ap.add_argument("--input", action="append", default=[], dest="inputs",
                    help="input image (repeat for each of the 8 input images)")
    ap.add_argument("--cell", type=int, default=384)
    ap.add_argument("--cols", type=int, default=6)
    ns = ap.parse_args(argv)
    if not os.path.isdir(ns.renders):
        sys.stderr.write("RenderDirMissing:%s\n" % ns.renders)
        sys.exit(2)
    if not os.path.isfile(os.path.join(ns.renders, "manifest.json")):
        sys.stderr.write("RenderManifestMissing:%s\n" % os.path.join(ns.renders, "manifest.json"))
        sys.exit(2)
    if ns.portrait and not os.path.isfile(ns.portrait):
        sys.stderr.write("InputMissing:portrait:%s\n" % ns.portrait)
        sys.exit(2)
    for p in ns.inputs:
        if not os.path.isfile(p):
            sys.stderr.write("InputMissing:%s\n" % p)
            sys.exit(2)
    return ns


def section_of(name):
    if name.startswith("pose-"):
        return "pose"
    if name.startswith("crop-"):
        return "crop"
    if name.startswith("head-"):
        return "head"
    return "body"


SECTION_TITLES = {
    "inputs": "초상 / 입력 이미지 (portrait + inputs)",
    "body": "전신 (body)",
    "head": "머리 (head)",
    "crop": "클로즈업 (crops)",
    "pose": "포즈 (poses)",
}
SECTION_ORDER = ["inputs", "body", "head", "crop", "pose"]


def fit_cell(img, cell):
    im = img.convert("RGB")
    im.thumbnail((cell, cell), Image.LANCZOS)
    canvas = Image.new("RGB", (cell, cell), BG)
    canvas.paste(im, ((cell - im.width) // 2, (cell - im.height) // 2))
    return canvas


def main(ns):
    manifest = json.load(open(os.path.join(ns.renders, "manifest.json"), encoding="utf-8"))
    renders = sorted(f["name"] for f in manifest.get("files", []))

    sections = {k: [] for k in SECTION_ORDER}
    if ns.portrait:
        sections["inputs"].append(("portrait:" + os.path.basename(ns.portrait), ns.portrait))
    for p in ns.inputs:
        sections["inputs"].append(("input:" + os.path.basename(p), p))
    for name in renders:
        sections[section_of(name)].append((name[:-4] if name.endswith(".png") else name,
                                           os.path.join(ns.renders, name)))

    cols = max(ns.cols, 1)
    cell_h = ns.cell + LABEL_H
    rows_total = 0
    for k in SECTION_ORDER:
        if sections[k]:
            rows_total += 1 + (len(sections[k]) + cols - 1) // cols
    width = cols * (ns.cell + MARGIN) + MARGIN
    height = rows_total * (cell_h + MARGIN) + MARGIN
    board = Image.new("RGB", (width, height), (30, 30, 30))
    draw = ImageDraw.Draw(board)
    font_label = load_font(14)
    font_section = load_font(20)

    y = MARGIN
    for k in SECTION_ORDER:
        if not sections[k]:
            continue
        draw.rectangle([MARGIN, y, width - MARGIN, y + SECTION_H - 6], fill=SECTION_BG)
        draw.text((MARGIN + 8, y + 4), SECTION_TITLES[k], fill=TEXT, font=font_section)
        y += SECTION_H
        for i, (label, path) in enumerate(sections[k]):
            cx = MARGIN + (i % cols) * (ns.cell + MARGIN)
            cy = y + (i // cols) * (cell_h + MARGIN)
            try:
                tile = fit_cell(Image.open(path), ns.cell)
            except Exception as e:
                tile = Image.new("RGB", (ns.cell, ns.cell), (90, 40, 40))
                d2 = ImageDraw.Draw(tile)
                d2.text((8, 8), "unreadable: %s" % e, fill=TEXT, font=font_label)
            board.paste(tile, (cx, cy))
            draw.rectangle([cx, cy + ns.cell, cx + ns.cell, cy + ns.cell + LABEL_H], fill=LABEL_BG)
            draw.text((cx + 4, cy + ns.cell + 3), label[: max(1, ns.cell // 8)], fill=TEXT, font=font_label)
        y += ((len(sections[k]) + cols - 1) // cols) * (cell_h + MARGIN)

    out_abs = os.path.abspath(ns.out)
    parent = os.path.dirname(out_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)
    board.save(out_abs, "PNG")
    print(json.dumps({
        "ok": True,
        "board": out_abs,
        "bytes": os.path.getsize(out_abs),
        "cells": sum(len(v) for v in sections.values()),
        "renders": len(renders),
        "board_sha256": sha256_file(out_abs),
    }, sort_keys=True))


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
