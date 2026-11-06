#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const gateScript = path.join(here, "gate.mjs");
const { cases } = JSON.parse(await readFile(path.join(here, "path-cases.json"), "utf8"));

async function tree(dir) {
  const found = [];
  async function walk(current) {
    const entries = await readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else found.push(full);
    }
  }
  await walk(dir);
  return found.sort();
}

let passed = 0;
let failed = 0;
for (const test of cases) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "gate-path-"));
  const characters = path.join(dir, "chars", "sub");
  await mkdir(characters, { recursive: true });
  const auto = path.join(dir, "auto.json");
  const verdictA = path.join(dir, "va.json");
  const verdictB = path.join(dir, "vb.json");
  const verdict = { asset: "fixture", gate: "G0", reviewer: "a", renders: [], checklist: [{ id: "fixture", pass: true, note: "fixture" }], identity_score: 5, verdict: "PASS", blocking: [] };
  await writeFile(auto, `${JSON.stringify({ ok: true, blocking: [] })}\n`);
  await writeFile(verdictA, `${JSON.stringify({ ...verdict, reviewer: "reviewer-a" })}\n`);
  await writeFile(verdictB, `${JSON.stringify({ ...verdict, reviewer: "reviewer-b" })}\n`);
  const before = await tree(dir);
  const digestBefore = createHash("sha256").update(before.join("\n")).digest("hex");
  const env = { ...process.env, GATE_CHARACTERS_ROOT: characters, GATE_PROFILES_FILE: path.join(here, "profiles.json") };
  const envAsset = test.asset === "" || test.asset.includes("\0");
  const assetArgs = envAsset ? ["placeholder"] : [test.asset];
  const args = test.command === "aggregate"
    ? ["aggregate", ...assetArgs, test.gate, "--auto", auto, "--verdict", verdictA, "--verdict-b", verdictB]
    : test.command === "confirm"
      ? ["confirm", ...assetArgs, test.gate, "--note", "escape attempt"]
      : ["require", ...assetArgs, test.gate];
  const stepEnv = envAsset ? { ...env, ADVERSARIAL_ARG: test.asset === "" ? "-" : "nul" } : env;
  const launch = stepEnv.ADVERSARIAL_ARG ? ["--import", "E:/git/Charakuru-wt/modular-cast-w0/tools/gate/fixtures/adversarial-argv.mjs"] : [];
  const result = spawnSync(process.execPath, [...launch, gateScript, ...args], { env: stepEnv, encoding: "utf8" });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  const after = await tree(dir);
  const outside = after.filter((file) => !before.includes(file) && !file.startsWith(`${characters}${path.sep}`));
  const codeOk = result.status !== 0 && output.includes(test.code);
  const treeOk = outside.length === 0 && after.length === before.length;
  if (codeOk && treeOk && createHash("sha256").update(after.join("\n")).digest("hex") === digestBefore) passed += 1;
  else {
    failed += 1;
    console.error(`${test.name}: exit=${result.status} code=${output.trim()} outside=${outside.join(",")}`);
  }
  await rm(dir, { recursive: true, force: true });
}

const happyDir = await mkdtemp(path.join(os.tmpdir(), "gate-path-happy-"));
const characters = path.join(happyDir, "chars", "sub");
await mkdir(characters, { recursive: true });
const auto = path.join(happyDir, "auto.json");
const verdictA = path.join(happyDir, "va.json");
const verdictB = path.join(happyDir, "vb.json");
const verdict = { asset: "fixture", gate: "G0", reviewer: "a", renders: [], checklist: [{ id: "fixture", pass: true, note: "fixture" }], identity_score: 5, verdict: "PASS", blocking: [] };
await writeFile(auto, `${JSON.stringify({ ok: true, blocking: [] })}\n`);
await writeFile(verdictA, `${JSON.stringify({ ...verdict, reviewer: "reviewer-a" })}\n`);
await writeFile(verdictB, `${JSON.stringify({ ...verdict, reviewer: "reviewer-b" })}\n`);
const env = { ...process.env, GATE_CHARACTERS_ROOT: characters, GATE_PROFILES_FILE: path.join(here, "profiles.json") };
const happy = spawnSync(process.execPath, [gateScript, "aggregate", "_base/male", "G0", "--auto", auto, "--verdict", verdictA, "--verdict-b", verdictB], { env, encoding: "utf8" });
const happyFile = path.join(characters, "_base", "gates", "G0-_base-male.json");
let happyOk = false;
try {
  const record = JSON.parse(await readFile(happyFile, "utf8"));
  happyOk = happy.status === 0 && record.status === "REVIEW_PASS";
} catch {
  happyOk = false;
}
if (happyOk) passed += 1;
else {
  failed += 1;
  console.error(`nested asset happy: exit=${happy.status} out=${happy.stdout}${happy.stderr}`);
}
await rm(happyDir, { recursive: true, force: true });

console.log(`GATE_PATH passed=${passed} failed=${failed}`);
if (failed) process.exitCode = 1;
