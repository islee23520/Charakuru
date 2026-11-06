#!/usr/bin/env node
// Builds the Blender auto-check fixtures in a temp dir and runs tools/gate/fixtures/auto-cases.json.
// Usage: node tools/gate/fixtures/make-fixtures.mjs [--keep] [--evidence <file>] [--only <substring>]
// Env: BLENDER (default: Blender 5.2 install path), RUN_TMP (default: a new dir under os.tmpdir()).
// Every Blender call is bounded by timeout_s (600 s); a timeout is recorded as exit "TIMEOUT".
// Prints "AUTO passed=N failed=M" and exits 1 when any case or fixture step fails.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const opt = (n) => (argv.includes(n) ? argv[argv.indexOf(n) + 1] : null);
const BLENDER = process.env.BLENDER || "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe";
const RUN_TMP = process.env.RUN_TMP || mkdtempSync(join(tmpdir(), "charakuru-auto-"));
const vars = { BLENDER, RUN_TMP: RUN_TMP.replaceAll("\\", "/"), MAIN: "E:/git/Charakuru", REF: "E:/git/seoul-dengoku-tools/ART-ASSETS/avatar-gen" };
const spec = JSON.parse(readFileSync(join(REPO, "tools/gate/fixtures/auto-cases.json"), "utf8"));
const sub = (s) => s.replace(/\$\{(\w+)\}/g, (_, k) => vars[k] ?? `\${${k}}`);
const evidence = opt("--evidence");
const only = opt("--only");
const log = (line) => {
  console.log(line);
  if (evidence) appendFileSync(evidence, line + "\n");
};
const sha = (p) => createHash("sha256").update(readFileSync(p)).digest("hex");

function run(cmd, env) {
  const t0 = Date.now();
  const r = spawnSync(cmd[0], cmd.slice(1), { cwd: REPO, encoding: "utf8", timeout: spec.timeout_s * 1000, maxBuffer: 1 << 28,
    env: env ? { ...process.env, ...env } : process.env });
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  const timedOut = r.error && r.error.code === "ETIMEDOUT";
  return { exit: timedOut ? "TIMEOUT" : r.status, secs, out: (r.stdout || "") + (r.stderr || ""), error: r.error && !timedOut ? String(r.error) : null };
}

const get = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
const codeMatch = (codes, want) => (want.endsWith("*") ? codes.some((c) => c.startsWith(want.slice(0, -1))) : codes.includes(want));

mkdirSync(join(RUN_TMP, "fixtures"), { recursive: true });
mkdirSync(join(RUN_TMP, "out"), { recursive: true });
log(`# RUN_TMP=${vars.RUN_TMP}`);
log(`# BLENDER=${BLENDER}`);
let passed = 0, failed = 0;
const fail = (msg) => { failed++; log(`FAIL ${msg}`); };

for (const f of spec.fixtures) {
  if (only && !f.name.includes(only) && !f.name.startsWith("reduce iyeon")) continue;
  const cmd = f.cmd.map(sub);
  log(`\n## fixture: ${f.name}\n$ ${cmd.map((c) => (c.includes(" ") ? `"${c}"` : c)).join(" ")}`);
  const r = run(cmd);
  log(`exit=${r.exit} secs=${r.secs}${r.error ? " error=" + r.error : ""}`);
  const tail = r.out.split(/\r?\n/).filter((l) => /REDUCE_|HEAD_FIXTURES|SYNTH_|Error|Traceback/.test(l)).slice(-6);
  tail.forEach((l) => log(`  | ${l}`));
  if (r.exit !== f.exit) { fail(`${f.name}: exit ${r.exit} != ${f.exit}`); continue; }
  if (f.json) {
    const p = sub(f.json);
    if (!existsSync(p)) { fail(`${f.name}: ${p} missing`); continue; }
    const j = JSON.parse(readFileSync(p, "utf8"));
    const a = f.assert || {};
    const miss = (a.fields || []).filter((k) => j[k] === undefined || j[k] === null);
    const keyFields = { content_sha: j.content_sha, src_sha: j.src_sha, blend_sha: j.blend_sha, glb_sha: j.glb_sha, quads: j.quads, tris: j.tris, faces: j.faces, bake_rmse: j.bake_rmse };
    log(`  reduce.json ${JSON.stringify(keyFields)}`);
    if (miss.length) { fail(`${f.name}: reduce.json missing ${miss}`); continue; }
    if (a.quads_max && !(j.quads <= a.quads_max)) { fail(`${f.name}: quads ${j.quads} > ${a.quads_max}`); continue; }
    if (a.tris_max && !(j.tris <= a.tris_max)) { fail(`${f.name}: tris ${j.tris} > ${a.tris_max}`); continue; }
    if (f.compare_to) {
      const b = JSON.parse(readFileSync(sub(f.compare_to), "utf8"));
      const glbSame = j.glb_sha === b.glb_sha, blendSame = j.blend_sha === b.blend_sha;
      const topoSame = j.quads === b.quads && j.tris === b.tris && j.faces === b.faces;
      const contentSame = j.content_sha === b.content_sha;
      log(`  determinism: content_sha same=${contentSame} src_sha same=${j.src_sha === b.src_sha} quads/tris same=${topoSame} glb_sha same=${glbSame} blend_sha same=${blendSame} bake_rmse ${b.bake_rmse} -> ${j.bake_rmse}`);
      if (!topoSame || !contentSame || !glbSame) { fail(`${f.name}: reduce output differs between runs`); continue; }
      if (!blendSame) log(`  note: .blend bytes differ while content_sha and glb_sha match: Blender writes runtime memory addresses (BHead old pointers) into .blend files`);
    }
  }
  passed++;
  log(`ok fixture ${f.name}`);
}

for (const c of spec.cases) {
  if (only && !c.name.includes(only)) continue;
  const slug = c.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60);
  const slugOf = (n) => n.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60);
  const out = join(vars.RUN_TMP, "out", `${c.reuse_out_of ? slugOf(c.reuse_out_of) : slug}.json`).replaceAll("\\", "/");
  const before = c.reuse_out_of && existsSync(out) ? JSON.parse(readFileSync(out, "utf8")) : null;
  if (c.reuse_out_of) log(`  pre-existing JSON at out: ok=${before?.ok} blocking=${JSON.stringify(before?.blocking)}`);
  const cmd = [BLENDER, "-b", "--factory-startup", "-P", "tools/gate/auto-check.py", "--", sub(c.in), c.gate, out, ...c.args.map(sub)];
  log(`\n## case: ${c.name}\n$ ${cmd.map((x) => (x.includes(" ") ? `"${x}"` : x)).join(" ")}`);
  const r = run(cmd, c.env);
  let j = null;
  try { j = JSON.parse(readFileSync(out, "utf8")); } catch { /* reported below */ }
  if (c.exit === "nonzero") {
    const problems = [];
    if (c.reuse_out_of && !(before && before.ok === true && before.blocking.length === 0)) problems.push("precondition: no passing JSON at out before the run");
    if (r.exit === 0) problems.push("exit 0 for an aborted run");
    if (!j) problems.push("no JSON");
    else {
      if (j.ok !== c.expect_ok) problems.push(`ok=${j.ok}`);
      if (!j.blocking.includes(c.expect_code)) problems.push(`blocking lacks ${c.expect_code}`);
      if (j.input_sha256 == null) problems.push("input_sha256 missing");
    }
    log(`exit=${r.exit} secs=${r.secs} ok=${j?.ok} blocking=${JSON.stringify(j?.blocking)} input_sha256=${j?.input_sha256}`);
    if (problems.length) fail(`${c.name}: ${problems.join("; ")}`);
    else { passed++; log(`ok ${c.name}`); }
    continue;
  }
  log(`exit=${r.exit} secs=${r.secs} ok=${j?.ok} blocking=${JSON.stringify(j?.blocking)}${j?.error ? " error=" + j.error : ""}`);
  const problems = [];
  if (r.exit !== c.exit) problems.push(`exit ${r.exit} != ${c.exit}`);
  if (!j) problems.push("no JSON written");
  else {
    if (j.exit_code !== r.exit) problems.push(`JSON exit_code ${j.exit_code} != process exit ${r.exit}`);
    if (j.pass !== (c.exit === 0)) problems.push(`JSON pass=${j.pass} contradicts expected exit ${c.exit}`);
    if (j.ok !== (r.exit === 0 || r.exit === 1) || !Array.isArray(j.blocking)) problems.push("gate contract fields ok/blocking wrong");
    else if (r.exit !== 2 && JSON.stringify(j.blocking) !== JSON.stringify(j.codes)) problems.push("blocking != codes");
    if (c.expect_codes_empty && j.codes.length) problems.push(`codes not empty: ${j.codes}`);
    for (const w of [c.expect_code, ...(c.expect_also || [])].filter(Boolean)) if (!codeMatch(j.codes, w)) problems.push(`code ${w} absent`);
    for (const w of c.expect_absent || []) if (j.codes.some((x) => x.startsWith(w))) problems.push(`unexpected code ${w}`);
    for (const [k, v] of Object.entries(c.assert || {})) {
      const [path, kind] = k.endsWith("_max") ? [k.slice(0, -4), "max"] : k.endsWith("_min") ? [k.slice(0, -4), "min"] : [k, "eq"];
      const got = get(j, path);
      const ok = kind === "max" ? got <= v : kind === "min" ? got >= v : got === v;
      log(`  assert ${path} ${kind} ${v}: got ${got} ${ok ? "ok" : "FAIL"}`);
      if (!ok) problems.push(`${path}=${got}`);
    }
    log(`  input_sha256=${j.input_sha256}`);
  }
  if (problems.length) fail(`${c.name}: ${problems.join("; ")}`);
  else { passed++; log(`ok ${c.name}`); }
}

// Integration with part A: the exact auto-check JSON is fed to gate.mjs aggregate.
const verdict = (reviewer) => ({ asset: "fixture", gate: "G1", reviewer, renders: [],
  checklist: [{ id: "fixture", pass: true, note: "fixture verdict, not a vision review" }], identity_score: 5,
  verdict: "PASS", blocking: [] });
for (const g of spec.integration || []) {
  if (only && !g.name.includes(only)) continue;
  const caseOut = join(vars.RUN_TMP, "out", `${g.auto_case.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60)}.json`);
  const root = join(vars.RUN_TMP, "gate-root");
  mkdirSync(root, { recursive: true });
  const va = join(vars.RUN_TMP, "verdict-a.json"), vb = join(vars.RUN_TMP, "verdict-b.json");
  writeFileSync(va, JSON.stringify(verdict("fixture-reviewer-a"), null, 2));
  writeFileSync(vb, JSON.stringify(verdict("fixture-reviewer-b"), null, 2));
  const cmd = [process.execPath, "tools/gate/gate.mjs", "aggregate", g.asset, g.gate, "--auto", caseOut, "--verdict", va, "--verdict-b", vb];
  log(`\n## integration: ${g.name}\n$ GATE_CHARACTERS_ROOT=${root} ${cmd.join(" ")}`);
  const t0 = Date.now();
  const r = spawnSync(cmd[0], cmd.slice(1), { cwd: REPO, encoding: "utf8", env: { ...process.env, GATE_CHARACTERS_ROOT: root }, timeout: 120000 });
  const gateFile = join(root, g.asset.split("-")[0], "gates", `${g.gate}-${g.asset}.json`);
  let gj = null;
  try { gj = JSON.parse(readFileSync(gateFile, "utf8")); } catch { /* reported below */ }
  log(`exit=${r.status} secs=${((Date.now() - t0) / 1000).toFixed(1)} gate_file=${gateFile}`);
  log(`  gate.status=${gj?.status} gate.blocking=${JSON.stringify(gj?.blocking)}`);
  (r.stdout + r.stderr).trim().split(/\r?\n/).slice(-3).forEach((l) => log(`  | ${l}`));
  const problems = [];
  if (r.status !== g.exit) problems.push(`exit ${r.status} != ${g.exit}`);
  if (!gj) problems.push("gate file not written");
  else {
    if (gj.status !== g.status) problems.push(`status ${gj.status} != ${g.status}`);
    for (const c of g.blocking_includes || []) if (!(gj.blocking || []).includes(c)) problems.push(`blocking lacks ${c}`);
    for (const c of g.blocking_excludes || []) if ((gj.blocking || []).includes(c)) problems.push(`blocking has ${c}`);
  }
  if (problems.length) fail(`${g.name}: ${problems.join("; ")}`);
  else { passed++; log(`ok ${g.name}`); }
}

log(`\nAUTO passed=${passed} failed=${failed}`);
if (!flag("--keep")) {
  rmSync(RUN_TMP, { recursive: true, force: true });
  log(`# cleanup: removed ${vars.RUN_TMP} exists_after=${existsSync(RUN_TMP)}`);
} else log(`# kept ${vars.RUN_TMP}`);
process.exit(failed ? 1 : 0);
