// tools/gate/vision/fixtures/make-fixtures.mjs
// Deterministic synthetic fixtures for the vision checker self-test.
// These are NOT real assets or real verdicts: asset/reviewer are the
// "__selftest__" sentinels so they can never be mistaken for gate records.
//
// Usage: node tools/gate/vision/fixtures/make-fixtures.mjs <target_dir>

import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { argv } from "node:process";

const dir = argv[2];
if (!dir) {
  console.error("Usage: make-fixtures.mjs <target_dir>");
  process.exit(2);
}
rmSync(dir, { recursive: true, force: true });
mkdirSync(dir, { recursive: true });

// Fixture render set: the static (20-file) contract, junk-byte PNGs whose
// content is a pure function of the file name (deterministic).
const FULL = ["body-front", "body-right", "body-back", "body-left", "body-threequarter"];
const HEAD = ["head-front", "head-right", "head-threequarter"];
const CROPS = [
  "crop-eyes", "crop-lips", "crop-mouthopen", "crop-ear-left", "crop-ear-right",
  "crop-neck", "crop-hand-left", "crop-hand-right", "crop-elbow-left",
  "crop-elbow-right", "crop-feet-left", "crop-feet-right",
];
const names = [...FULL, ...HEAD, ...CROPS].map((n) => `${n}.png`).sort();

const files = names.map((name) => {
  const body = Buffer.from(`fixture-png-bytes:${name}:`, "utf-8");
  const buf = Buffer.concat([body, Buffer.alloc(64, name.length)]);
  writeFileSync(join(dir, name), buf);
  return { name, sha256: createHash("sha256").update(buf).digest("hex"), bytes: buf.length };
});

const manifest = {
  tool: "tools/gate/render-views.py",
  tool_version: 3,
  preset: "G3",
  input: { file: "__selftest__head.glb", path: "__selftest__/head.glb", sha256: "0".repeat(64), bytes: 1 },
  contract: { engine: "FIXTURE", resolution: [1024, 1024], camera_rig: "gate-fixed-v3",
              blank_frame: { nonbg_min: 0.02, std_min: 0.01 } },
  facing: { source: "heuristic-head-protrusion", axis: [0, -1, 0], up: [0, 0, 1], confidence: "low",
            check: { skin_ratio_head_front: 0.30, eyes_in_frame: true } },
  camera_rolls_deg: Object.fromEntries(names.map((n) => [n.replace(/\.png$/, ""), 0.1])),
  anatomy: {
    measured: { height_m: 1.0, head_to_height: 0.2, shoulder_to_height: 0.3, crotch_to_height: 0.5 },
    bands: { height_m: "fail", head_to_height: "fail", crotch_to_height: "pass", arm_span_to_height: "pass" },
    rest_pose: "T-pose", anatomy_ok: false, SourceOutOfBand: ["height_m", "head_to_height"], notes: [],
  },
  anatomy_ok: false,
  geometry: { height_m: 1.0 },
  rigged: false,
  landmark_source: "evaluated-heuristics",
  poses: { applied: [], skipped: [], bones: {}, sit_box_contact: null },
  file_count: files.length,
  files: files.map((f) => ({ ...f, nonbg_ratio: 0.5, std_luma: 0.2 })),
};
writeFileSync(join(dir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");

const someRenders = ["crop-eyes.png", "crop-mouthopen.png"].map((name) => {
  const f = files.find((x) => x.name === name);
  return { name: f.name, sha256: f.sha256 };
});

const verdictPass = {
  asset: "__selftest__",
  gate: "G3",
  reviewer: "__selftest__",
  renders: someRenders,
  checklist: [
    { id: "identity-traits", pass: true, note: "fixture: always passes", crop: "" },
    { id: "eyes-socket-seated", pass: true, note: "fixture", crop: "" },
  ],
  identity_score: 5,
  verdict: "PASS",
  blocking: [],
};

const verdictMalformed = { ...verdictPass };
delete verdictMalformed.identity_score; // missing required field

const verdictStale = {
  ...verdictPass,
  renders: someRenders.map((r) => ({ ...r, sha256: "f".repeat(64) })),
};

const verdictContradiction = {
  ...verdictPass,
  checklist: [
    { id: "identity-traits", pass: false, note: "fixture", crop: "crop-eyes.png" },
    { id: "eyes-socket-seated", pass: true, note: "fixture", crop: "" },
  ],
};

const verdictCropMissing = {
  ...verdictPass,
  verdict: "FAIL",
  checklist: [
    { id: "identity-traits", pass: false, note: "fixture: fail without crop", crop: "" },
  ],
  blocking: ["fixture blocking reason"],
};

const write = (name, obj) =>
  writeFileSync(join(dir, name), JSON.stringify(obj, null, 2) + "\n");
write("verdict-pass.json", verdictPass);
write("verdict-malformed.json", verdictMalformed);
write("verdict-stale.json", verdictStale);
write("verdict-contradiction.json", verdictContradiction);
write("verdict-crop-missing.json", verdictCropMissing);

console.log(JSON.stringify({ ok: true, dir, files: files.length }, null, 2));
