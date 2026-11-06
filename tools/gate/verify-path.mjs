#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const gateScript = path.join(here, "gate.mjs");
// NUL cannot travel through a Windows argv, so NUL cases run the CLI through this wrapper (see its header).
const nulWrapper = path.join(here, "fixtures", "adversarial-argv.mjs");
const { cases } = JSON.parse(await readFile(path.join(here, "path-cases.json"), "utf8"));
const linkType = process.platform === "win32" ? "junction" : "dir";

// Every file and link under dir with its content hash; links are recorded, never followed.
async function tree(dir) {
  const found = [];
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isSymbolicLink()) found.push(`${full}\tlink`);
      else if (entry.isDirectory()) await walk(full);
      else found.push(`${full}\t${createHash("sha256").update(await readFile(full)).digest("hex")}`);
    }
  }
  await walk(dir);
  return found.sort();
}

function gateRecord(asset, gate, status) {
  return `${JSON.stringify({ asset, gate, status, userConfirmation: null, supersededConfirmations: [] })}\n`;
}

// Builds the junction layout a case names inside the characters root. Returns the directory the
// gate file really lives in, or null for a loop.
async function plantJunction(test, dir, characters) {
  const kind = test.junction;
  const linkName = test.asset.split("/")[0];
  if (kind === "loop") {
    await symlink(path.join(characters, `${linkName}2`), path.join(characters, linkName), linkType);
    await symlink(path.join(characters, linkName), path.join(characters, `${linkName}2`), linkType);
    return null;
  }
  const real = kind === "outside" ? path.join(dir, "outside") : path.join(characters, "real");
  await mkdir(path.join(real, "gates"), { recursive: true });
  await symlink(real, path.join(characters, linkName), linkType);
  const status = test.command === "confirm" ? "REVIEW_PASS" : test.command === "require" ? "PASS" : null;
  if (status) await writeFile(path.join(real, "gates", `${test.gate}-${test.asset.replaceAll("/", "-")}.json`), gateRecord(test.asset, test.gate, status));
  return real;
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
  let real = null;
  if (test.junction) {
    try {
      real = await plantJunction(test, dir, characters);
    } catch (error) {
      if (error.code !== "EPERM") throw error;
      failed += 1;
      console.error(`${test.name}: SKIP: symlinks not permitted`);
      await rm(dir, { recursive: true, force: true });
      continue;
    }
  }
  const before = await tree(dir);
  const env = { ...process.env, GATE_CHARACTERS_ROOT: characters, GATE_PROFILES_FILE: path.join(here, "profiles.json") };
  const nul = test.asset.includes("\0");
  const assetArg = nul ? "placeholder" : test.asset;
  const args = test.command === "aggregate"
    ? ["aggregate", assetArg, test.gate, "--auto", auto, "--verdict", verdictA, "--verdict-b", verdictB]
    : test.command === "confirm"
      ? ["confirm", assetArg, test.gate, "--note", "escape attempt"]
      : ["require", assetArg, test.gate];
  const launch = nul ? [nulWrapper] : [];
  const result = spawnSync(process.execPath, [...launch, gateScript, ...args], { env, encoding: "utf8", timeout: 10_000 });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  const after = await tree(dir);
  const insideRoot = (line) => line.startsWith(`${characters}${path.sep}`);
  const outsideChanged = JSON.stringify(before.filter((line) => !insideRoot(line))) !== JSON.stringify(after.filter((line) => !insideRoot(line)));
  let ok;
  if (test.exit === 0) {
    const gateFile = path.join(real, "gates", `${test.gate}-${test.asset.replaceAll("/", "-")}.json`);
    let status = null;
    try {
      status = JSON.parse(await readFile(gateFile, "utf8")).status;
    } catch {
      status = null;
    }
    ok = result.status === 0 && !outsideChanged && status === test.status;
  } else {
    ok = result.status !== null && result.status !== 0 && output.includes(test.code) && JSON.stringify(before) === JSON.stringify(after);
  }
  if (ok) passed += 1;
  else {
    failed += 1;
    console.error(`${test.name}: exit=${result.status} signal=${result.signal ?? "-"} out=${output.trim()} outsideChanged=${outsideChanged}`);
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
const happy = spawnSync(process.execPath, [gateScript, "aggregate", "_base/male", "G0", "--auto", auto, "--verdict", verdictA, "--verdict-b", verdictB], { env, encoding: "utf8", timeout: 10_000 });
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
