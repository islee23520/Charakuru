// tools/gate/vision/verify.mjs
// Runs the vision-checker cases from cases.json and prints
//   VISION passed=N failed=M
// Steps are declarative (no shell): {mkfixtures}, {unlink}, {appendByte}, {run}.
//
// Usage: node tools/gate/vision/verify.mjs [--filter <case>]

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, appendFileSync, existsSync, unlinkSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { argv } from "node:process";

const HERE = dirname(fileURLToPath(import.meta.url));
const WT_ROOT = join(HERE, "..", "..", "..");
const cases = JSON.parse(readFileSync(join(HERE, "cases.json"), "utf-8"));
const filterIdx = argv.indexOf("--filter");
const runner = filterIdx >= 0 ? argv[filterIdx + 1] : null;

// Real-render input (read-only, never copied or modified): E:/git/Charakuru/characters/jojaepyo/exports/jojaepyo.vrm
// $REAL is ONE temp G5 render of that VRM per verify run, created lazily by the first
// "ensureRealRender" step and reused by every real-g5-* case (no committed/untracked evidence needed).
const REAL_VRM = "E:/git/Charakuru/characters/jojaepyo/exports/jojaepyo.vrm";
const BLENDER = "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe";
let realDir = null;
function ensureRealRender() {
  if (realDir && existsSync(join(realDir, "manifest.json"))) return realDir;
  if (!existsSync(REAL_VRM)) throw new Error(`RealInputMissing: VRM not found: ${REAL_VRM}`);
  if (!existsSync(BLENDER)) throw new Error(`RealInputMissing: Blender not found: ${BLENDER}`);
  if (!realDir) realDir = mkdtempSync(join(tmpdir(), "gate-vision-real-"));
  const res = spawnSync(BLENDER, ["-b", "-P", "tools/gate/render-views.py", "--", REAL_VRM, realDir, "--preset", "G5"], { cwd: WT_ROOT, encoding: "utf-8", timeout: 300000 });
  const out = (res.stdout || "") + (res.stderr || "");
  if (res.status !== 0) throw new Error(`RealRenderFailed: exit ${res.status} :: ${out.trim().split("\n").slice(-3).join(" | ")}`);
  return realDir;
}

let passed = 0;
let failed = 0;
const lines = [];

for (const c of cases) {
  if (runner && c.name !== runner) continue;
  const tmp = mkdtempSync(join(tmpdir(), "gate-vision-"));
  const fix = join(tmp, "fixture");
  let ok = true;
  const reasons = [];
  for (const step of c.steps) {
    const exp = (s) => {
      s = s.replaceAll("$FIX", fix).replaceAll("$TMP", tmp);
      return s.includes("$REAL") ? s.replaceAll("$REAL", ensureRealRender()) : s;
    };
    try {
      if (step.mkfixtures !== undefined) {
        const res = spawnSync("node", [join(HERE, "fixtures", "make-fixtures.mjs"), exp(step.mkfixtures)], { encoding: "utf-8" });
        if (res.status !== 0) throw new Error(`mkfixtures exit ${res.status}: ${(res.stderr || "").slice(0, 200)}`);
      } else if (step.ensureRealRender !== undefined) {
        ensureRealRender();
      } else if (step.unlink !== undefined) {
        const p = exp(step.unlink);
        if (existsSync(p)) unlinkSync(p);
      } else if (step.appendByte !== undefined) {
        appendFileSync(exp(step.appendByte), "x");
      } else if (step.copyFile !== undefined) {
        copyFileSync(exp(step.copyFile[0]), exp(step.copyFile[1]));
      } else if (step.sedReplace !== undefined) {
        const [file, search, replace] = step.sedReplace;
        const p = exp(file);
        const src = readFileSync(p, "utf-8");
        if (!src.includes(search)) throw new Error(`sedReplace: pattern not found: ${search.slice(0, 80)}`);
        writeFileSync(p, src.replace(search, exp(replace)));
      } else if (step.stripGlbExtension !== undefined) {
        const [srcPath, dstPath, ...extNames] = step.stripGlbExtension;
        const bin = readFileSync(exp(srcPath));
        if (bin.readUInt32LE(0) !== 0x46546c67) throw new Error("stripGlbExtension: not a GLB");
        const jsonLen = bin.readUInt32LE(12);
        const jsonTxt = bin.subarray(20, 20 + jsonLen).toString("utf-8");
        const doc = JSON.parse(jsonTxt);
        for (const name of extNames) {
          delete (doc.extensions || {})[name];
          if (doc.extensionsUsed) doc.extensionsUsed = doc.extensionsUsed.filter((e) => e !== name);
          if (doc.extensionsRequired) doc.extensionsRequired = doc.extensionsRequired.filter((e) => e !== name);
        }
        let json2 = JSON.stringify(doc);
        while (json2.length % 4 !== 0) json2 += " ";
        const jsonBuf = Buffer.from(json2, "utf-8");
        const rest = bin.subarray(20 + jsonLen); // BIN chunk (with its header)
        const total = 12 + 8 + jsonBuf.length + rest.length;
        const out = Buffer.alloc(total);
        out.writeUInt32LE(0x46546c67, 0);
        out.writeUInt32LE(2, 4);
        out.writeUInt32LE(total, 8);
        out.writeUInt32LE(jsonBuf.length, 12);
        out.write("JSON", 16, "ascii");
        jsonBuf.copy(out, 20);
        rest.copy(out, 20 + jsonBuf.length);
        writeFileSync(exp(dstPath), out);
      } else if (step.assertNoFile !== undefined) {
        if (existsSync(exp(step.assertNoFile))) throw new Error(`assertNoFile: ${step.assertNoFile} exists`);
      } else if (step.assertJson !== undefined) {
        const [file, dotted, expected] = step.assertJson;
        const doc = JSON.parse(readFileSync(exp(file), "utf-8"));
        let node = doc;
        for (const k of dotted.split(".")) node = node ? node[k] : undefined;
        if (node !== expected) throw new Error(`assertJson: ${dotted}=${JSON.stringify(node)} expected ${JSON.stringify(expected)}`);
      } else if (step.assertJsonClose !== undefined) {
        const [file, dotted, expected, tolPct] = step.assertJsonClose;
        const doc = JSON.parse(readFileSync(exp(file), "utf-8"));
        let node = doc;
        for (const k of dotted.split(".")) node = node ? node[k] : undefined;
        if (typeof node !== "number") throw new Error(`assertJsonClose: ${dotted} is not a number`);
        if (Math.abs(node - expected) / expected > tolPct / 100) {
          throw new Error(`assertJsonClose: ${dotted}=${node} differs from ${expected} by more than ${tolPct}%`);
        }
      } else if (step.assertJsonNotIn !== undefined) {
        const [file, dottedArray, value] = step.assertJsonNotIn;
        const doc = JSON.parse(readFileSync(exp(file), "utf-8"));
        const arr = dottedArray.split(".").reduce((n, k) => (n ? n[k] : undefined), doc);
        if (!Array.isArray(arr)) throw new Error(`assertJsonNotIn: ${dottedArray} is not an array`);
        if (arr.includes(value)) throw new Error(`assertJsonNotIn: ${dottedArray} contains ${JSON.stringify(value)}`);
      } else if (step.assertIdenticalRenders !== undefined) {
        const [dirA, dirB] = step.assertIdenticalRenders.map(exp);
        const bufA = readFileSync(join(dirA, "manifest.json"));
        const bufB = readFileSync(join(dirB, "manifest.json"));
        if (!bufA.equals(bufB)) throw new Error("assertIdenticalRenders: manifest.json bytes differ");
        const mA = JSON.parse(bufA.toString("utf-8"));
        const mB = JSON.parse(bufB.toString("utf-8"));
        const fa = mA.files.map((f) => f.name + ":" + f.sha256).sort().join("|");
        const fb = mB.files.map((f) => f.name + ":" + f.sha256).sort().join("|");
        if (fa !== fb) throw new Error("assertIdenticalRenders: per-file sha256 lists differ");
      } else if (step.assertJsonNe !== undefined) {
        const [file, dottedA, dottedB] = step.assertJsonNe;
        const doc = JSON.parse(readFileSync(exp(file), "utf-8"));
        const get = (dotted) => dotted.split(".").reduce((n, k) => (n ? n[k] : undefined), doc);
        const a = get(dottedA), b = get(dottedB);
        if (a === b) throw new Error(`assertJsonNe: ${dottedA} == ${dottedB} (${a}) — one metric overwrote the other`);
      } else if (step.patchJson !== undefined) {
        const [file, patches] = step.patchJson;
        const p = exp(file);
        const doc = JSON.parse(readFileSync(p, "utf-8"));
        for (const [dotted, value] of Object.entries(patches)) {
          const parts = dotted.split(".");
          let node = doc;
          for (let i = 0; i < parts.length - 1; i++) {
            if (node[parts[i]] === undefined) node[parts[i]] = {};
            node = node[parts[i]];
          }
          if (value === null) delete node[parts[parts.length - 1]];
          else node[parts[parts.length - 1]] = value;
        }
        writeFileSync(p, JSON.stringify(doc, null, 2) + "\n");
      } else if (step.run !== undefined) {
        const args = step.run.map(exp);
        const res = spawnSync(args[0], args.slice(1), { cwd: WT_ROOT, encoding: "utf-8", timeout: 300000 });
        const out = (res.stdout || "") + (res.stderr || "");
        if (res.status === null) throw new Error(`spawn failed or timed out: ${(res.error || "").toString()}`);
        if (c.expect.exit !== undefined && res.status !== c.expect.exit) {
          throw new Error(`exit ${res.status} != ${c.expect.exit} :: ${out.trim().split("\n").slice(-3).join(" | ")}`);
        }
        if (c.expect.contains && !out.includes(c.expect.contains)) {
          throw new Error(`output missing '${c.expect.contains}' :: ${out.trim().split("\n").slice(-3).join(" | ")}`);
        }
      } else {
        throw new Error("unknown step " + JSON.stringify(Object.keys(step)));
      }
    } catch (e) {
      ok = false;
      reasons.push(String(e.message));
      break;
    }
  }
  rmSync(tmp, { recursive: true, force: true });
  if (ok) {
    passed++;
    lines.push(`ok   ${c.name}`);
  } else {
    failed++;
    lines.push(`FAIL ${c.name} :: ${reasons.join(" ; ")}`);
  }
}

if (realDir) rmSync(realDir, { recursive: true, force: true });
for (const l of lines) console.log(l);
console.log(`VISION passed=${passed} failed=${failed}`);
process.exit(failed ? 1 : 0);
