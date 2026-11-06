// tools/gate/vision/check-verdict.mjs
// Validates a gate vision-review verdict against tools/gate/verdict.schema.json
// and against the CURRENT render manifest. A verdict is only counted as a
// pass when this checker exits 0.
//
// Rejections:
//   VerdictInvalid:<path>   schema violation (missing/wrongly typed fields ...)
//   StaleVerdict:<name>     renders[].sha256 does not match the current manifest
//   RenderNotFound:<name>   renders[].name absent from the manifest
//   VerdictContradiction   verdict=PASS with a failing checklist item, or
//                          identity_score < 4 on G3/G5
//   CropMissing:<id>        FAIL verdict: a pass=false item without a crop file
//
// Usage:
//   node tools/gate/vision/check-verdict.mjs <verdict.json> --manifest <render_dir>/manifest.json
//
// Exit codes: 0 accepted, 1 rejected, 2 usage errors.

import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { argv } from "node:process";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCHEMA_PATH = join(HERE, "..", "verdict.schema.json");

const args = argv.slice(2);
if (args.length < 1 || args[0].startsWith("-")) {
  console.error("Usage: check-verdict.mjs <verdict.json> --manifest <manifest.json>");
  process.exit(2);
}
const verdictPath = args[0];
const mi = args.indexOf("--manifest");
const manifestPath = mi >= 0 ? args[mi + 1] : null;
if (!manifestPath) {
  console.error("Usage: check-verdict.mjs <verdict.json> --manifest <manifest.json>");
  process.exit(2);
}

function reject(code, msg) {
  console.error(JSON.stringify({ ok: false, code, detail: msg }, null, 2));
  process.exit(1);
}

// --- minimal draft-07 subset validator (no external dependencies) ----------
function typeOk(value, type) {
  switch (type) {
    case "object": return typeof value === "object" && value !== null && !Array.isArray(value);
    case "array": return Array.isArray(value);
    case "string": return typeof value === "string";
    case "boolean": return typeof value === "boolean";
    case "integer": return typeof value === "number" && Number.isInteger(value);
    case "number": return typeof value === "number";
    default: return true;
  }
}

function validate(value, schema, at, errors) {
  if (schema.type !== undefined && !typeOk(value, schema.type)) {
    errors.push(`${at}: type ${schema.type} expected, got ${Array.isArray(value) ? "array" : typeof value}`);
    return;
  }
  if (schema.enum !== undefined && !schema.enum.includes(value)) {
    errors.push(`${at}: must be one of ${JSON.stringify(schema.enum)}`);
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(`${at}: shorter than minLength ${schema.minLength}`);
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      errors.push(`${at}: does not match ${schema.pattern}`);
    }
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${at}: below minimum ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${at}: above maximum ${schema.maximum}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(`${at}: fewer than minItems ${schema.minItems}`);
    }
    if (schema.items !== undefined) value.forEach((v, i) => validate(v, schema.items, `${at}[${i}]`, errors));
  }
  if (typeOk(value, "object")) {
    for (const key of schema.required || []) {
      if (!(key in value)) errors.push(`${at}: missing required field '${key}'`);
    }
    if (schema.properties) {
      for (const [key, sub] of Object.entries(schema.properties)) {
        if (key in value) validate(value[key], sub, `${at}.${key}`, errors);
      }
    }
    if (schema.additionalProperties === false && schema.properties) {
      for (const key of Object.keys(value)) {
        if (!(key in schema.properties)) errors.push(`${at}: additional property '${key}' not allowed`);
      }
    }
  }
}

// --- load inputs ------------------------------------------------------------
if (!existsSync(verdictPath)) reject("VerdictMissing", verdictPath);
let verdict;
try {
  verdict = JSON.parse(readFileSync(verdictPath, "utf-8"));
} catch (e) {
  reject("VerdictInvalid", `${verdictPath}: not valid JSON (${e.message})`);
}
if (!existsSync(manifestPath)) reject("ManifestMissing", manifestPath);
let manifest;
try {
  manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
} catch (e) {
  reject("RenderManifestInvalid", `${manifestPath}: not valid JSON (${e.message})`);
}

// --- schema ----------------------------------------------------------------
const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf-8"));
const schemaErrors = [];
validate(verdict, schema, "verdict", schemaErrors);
if (schemaErrors.length) reject("VerdictInvalid", schemaErrors.join("; "));

// --- staleness: renders[] vs current manifest and files on disk -------------
const byName = new Map((manifest.files || []).map((f) => [f.name, f]));
for (const r of verdict.renders) {
  const entry = byName.get(r.name);
  if (!entry) reject("RenderNotFound", `${r.name} is not in ${manifestPath}`);
  if (entry.sha256 !== r.sha256) {
    reject("StaleVerdict", `${r.name}: verdict sha ${r.sha256.slice(0, 12)} != manifest sha ${entry.sha256.slice(0, 12)}`);
  }
  const onDisk = join(dirname(manifestPath), r.name);
  if (!existsSync(onDisk)) reject("RenderNotFound", `${r.name}: file missing on disk (${onDisk})`);
  const diskSha = createHash("sha256").update(readFileSync(onDisk)).digest("hex");
  if (diskSha !== entry.sha256) {
    reject("StaleVerdict", `${r.name}: disk sha does not match the manifest (renders were regenerated)`);
  }
}

// --- semantic contract ------------------------------------------------------
const identityGates = new Set(["G3", "G5"]);
if (verdict.verdict === "PASS") {
  for (const item of verdict.checklist) {
    if (item.pass !== true) {
      reject("VerdictContradiction", `PASS verdict with checklist item '${item.id}' pass=${item.pass}`);
    }
  }
  if (identityGates.has(verdict.gate) && verdict.identity_score < 4) {
    reject("VerdictContradiction", `PASS verdict on ${verdict.gate} with identity_score ${verdict.identity_score} < 4`);
  }
} else {
  for (const item of verdict.checklist) {
    if (item.pass === false && (!item.crop || item.crop.trim() === "")) {
      reject("CropMissing", `FAIL checklist item '${item.id}' cites no crop file`);
    }
  }
}

console.log(JSON.stringify({
  ok: true,
  asset: verdict.asset,
  gate: verdict.gate,
  reviewer: verdict.reviewer,
  verdict: verdict.verdict,
  identity_score: verdict.identity_score,
  checklist: verdict.checklist.length,
  renders: verdict.renders.length,
}, null, 2));
