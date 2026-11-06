// tools/gate/vision/check-render-manifest.mjs
// Asserts the expected file list of a tools/gate/render-views.py output
// directory against its manifest.json AND the files actually on disk.
// Counting the log line is not enough: this is the render-manifest gate.
//
// Usage:
//   node tools/gate/vision/check-render-manifest.mjs <render_dir> \
//        [--preset G5] [--min-png 25] [--rigged|--static]
//
// Exit codes: 0 ok, 1 assertion failure, 2 usage/manifest errors.

import { createHash } from "node:crypto";
import { readFileSync, statSync, existsSync } from "node:fs";
import { join, basename } from "node:path";
import { argv, exitCode } from "node:process";

// ---------------------------------------------------------------------------
// Expected view sets. MUST match tools/gate/render-views.py
// (FULL_VIEWS / HEAD_VIEWS / CROP_VIEWS / POSES / POSE_ANGLES).
// Duplicated on purpose: an independent assertion must not import the code
// it asserts.
// ---------------------------------------------------------------------------
const FULL = ["body-front", "body-right", "body-back", "body-left", "body-threequarter"];
const HEAD = ["head-front", "head-right", "head-threequarter"];
const CROPS = [
  "crop-eyes", "crop-lips", "crop-mouthopen", "crop-ear-left", "crop-ear-right",
  "crop-neck", "crop-hand-left", "crop-hand-right", "crop-elbow-left",
  "crop-elbow-right", "crop-feet-left", "crop-feet-right",
];
const POSES = ["walk", "sit", "elbow90", "elbow140", "armsraised"];
const POSE_ANGLES = ["front", "threequarter"];
const EXPECTED_RIGGED = new Set(
  [...FULL, ...HEAD, ...CROPS].map((n) => `${n}.png`)
    .concat(POSES.flatMap((p) => POSE_ANGLES.map((a) => `pose-${p}-${a}.png`))),
);
const EXPECTED_STATIC = new Set([...FULL, ...HEAD, ...CROPS].map((n) => `${n}.png`));

const args = argv.slice(2);
if (args.length < 1 || args[0].startsWith("-")) {
  console.error("Usage: check-render-manifest.mjs <render_dir> [--preset G5] [--min-png 25] [--rigged|--static]");
  process.exit(2);
}
const dir = args[0];
const flag = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const preset = flag("--preset", null);
const minPng = parseInt(flag("--min-png", preset === "G5" ? "25" : "0"), 10);
const forceKind = args.includes("--rigged") ? "rigged" : args.includes("--static") ? "static" : null;

function fail(code, msg) {
  console.error(msg);
  process.exit(code);
}

const mpath = join(dir, "manifest.json");
if (!existsSync(mpath)) fail(2, `RenderManifestMissing:${mpath}`);
let manifest;
try {
  manifest = JSON.parse(readFileSync(mpath, "utf-8"));
} catch (e) {
  fail(2, `RenderManifestInvalid:${mpath} (${e.message})`);
}

const errors = [];
// the expected view set follows the POSES actually rendered (a rigged asset
// whose pose bones were unavailable legitimately produces the static set)
const rigged = forceKind ?? ((manifest.poses && Array.isArray(manifest.poses.applied) && manifest.poses.applied.length) ? "rigged" : "static");
const expected = rigged === "rigged" ? EXPECTED_RIGGED : EXPECTED_STATIC;
if (forceKind === "rigged" && !(manifest.poses && manifest.poses.applied && manifest.poses.applied.length)) {
  errors.push("RiggedMismatch:manifest has no applied poses");
}

if (preset && manifest.preset !== preset) {
  errors.push(`PresetMismatch:manifest=${manifest.preset} expected=${preset}`);
}

// facing must be recorded (VRM1/VRM0/heuristic) and verified
const facing = manifest.facing;
if (!facing || typeof facing !== "object"
    || !Array.isArray(facing.axis) || facing.axis.length !== 3
    || !facing.axis.every((c) => typeof c === "number")
    || typeof facing.source !== "string" || facing.source.length === 0
    || !Array.isArray(facing.up) || typeof facing.confidence !== "string") {
  errors.push("FacingInvalid");
}

// anatomy must be recorded for the proportion review: mesh-measured
// values, band verdicts, rest pose, anatomy_ok and SourceOutOfBand list
const an = manifest.anatomy;
if (!an || typeof an !== "object"
    || !an.measured || typeof an.measured.height_m !== "number"
    || !an.measured || typeof an.measured.head_to_height !== "number"
    || !an.bands || typeof an.bands !== "object"
    || typeof an.rest_pose !== "string"
    || typeof an.anatomy_ok !== "boolean"
    || !Array.isArray(an.SourceOutOfBand)) {
  errors.push("AnatomyInvalid");
}

// sit contact: measured and within 2 cm when the sit pose was rendered
if ((manifest.poses && Array.isArray(manifest.poses.applied) && manifest.poses.applied.includes("sit"))) {
  const sit = manifest.poses && manifest.poses.sit_box_contact;
  if (!sit || typeof sit.penetration_m !== "number") {
    errors.push("SitContactMissing");
  } else if (sit.penetration_m > 0.02) {
    errors.push(`SitBoxPenetration:${sit.penetration_m}`);
  }
}

const listed = new Set((manifest.files || []).map((f) => f.name));
for (const name of expected) {
  if (!listed.has(name)) errors.push(`FileNotListed:${name}`);
}
for (const name of listed) {
  if (!expected.has(name)) errors.push(`FileUnexpected:${name}`);
}

let checked = 0;
for (const f of manifest.files || []) {
  const p = join(dir, f.name);
  if (!existsSync(p)) {
    errors.push(`FileMissing:${f.name}`);
    continue;
  }
  const buf = readFileSync(p);
  const sha = createHash("sha256").update(buf).digest("hex");
  checked++;
  if (sha !== f.sha256) errors.push(`ShaMismatch:${f.name}`);
  if (f.bytes !== undefined && buf.length !== f.bytes) errors.push(`ByteSizeMismatch:${f.name}`);
  if (!f.name.endsWith(".png")) errors.push(`NotPng:${f.name}`);
  // per-frame content record (blank-frame rejection was enforced at render
  // time; the manifest must carry the measurements)
  if (typeof f.nonbg_ratio !== "number") errors.push(`FrameStatsMissing:${f.name}`);
  else if (f.nonbg_ratio < 0.02) errors.push(`BlankFrameInManifest:${f.name}`);
  // per-view measured camera roll must exist and stay under 2 degrees
  const view = f.name.replace(/\.png$/, "");
  const roll = manifest.camera_rolls_deg ? manifest.camera_rolls_deg[view] : undefined;
  if (typeof roll !== "number") errors.push(`RollMissing:${view}`);
  else if (Math.abs(roll) >= 2) errors.push(`RollExceeded:${view}:${roll}`);
}

if (checked < minPng) errors.push(`PngCountTooLow:${checked}<${minPng}`);
if (manifest.file_count !== undefined && manifest.file_count !== checked) {
  errors.push(`FileCountMismatch:manifest=${manifest.file_count} disk=${checked}`);
}
if (!manifest.input || !manifest.input.sha256 || !manifest.input.file) {
  errors.push("InputRecordMissing");
}

if (errors.length) {
  console.error(JSON.stringify({ ok: false, kind: rigged, checked, errors }, null, 2));
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, kind: rigged, checked, files: [...expected].sort() }, null, 2));
