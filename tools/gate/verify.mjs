#!/usr/bin/env node
import { mkdtemp, readFile, writeFile, rm, mkdir, copyFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { armsDownPng, scalePng } from "./fixtures/make-arms-down.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const gateScript = path.join(here, "gate.mjs");
const imageScript = path.join(here, "image-check.mjs");
const fixtureDir = path.join(here, "fixtures/lifecycle");
const { cases } = JSON.parse(await readFile(path.join(here, "cases.json"), "utf8"));
const jojaepyoBody = process.env.GATE_JOJAEPYO_BODY
  ?? "E:/git/Charakuru/characters/jojaepyo/sources/kit/body";
const iyeonBody = process.env.GATE_IYEON_BODY
  ?? "E:/git/Charakuru/characters/iyeon/sources/kit/body";

function matches(actual, expected) {
  return Object.entries(expected).every(([key, value]) => actual?.[key] === value);
}

function runNode(script, args, env) {
  return spawnSync(process.execPath, [script, ...args], { env, encoding: "utf8" });
}

async function writeVerdict(file, options) {
  const verdict = options.verdict ?? "PASS";
  await writeFile(file, `${JSON.stringify({
    asset: "fixture",
    gate: "G0",
    reviewer: options.reviewer ?? path.basename(file),
    renders: [],
    checklist: options.checklist ?? [{ id: "fixture", pass: verdict === "PASS", note: "fixture verdict, not a vision review" }],
    identity_score: options.identity_score ?? (verdict === "PASS" ? 5 : 1),
    verdict,
    blocking: options.blocking ?? (verdict === "PASS" ? [] : ["fixture"]),
  }, null, 2)}\n`);
}

async function materialize(source, dir) {
  if (source === "fixture-body") {
    await mkdir(dir, { recursive: true });
    await copyFile(path.join(fixtureDir, "body-front.png"), path.join(dir, "front.png"));
    await copyFile(path.join(fixtureDir, "body-front.png"), path.join(dir, "back.png"));
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "left.png"));
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "right.png"));
    return dir;
  }
  if (source === "fixture-head" || source === "fixture-neck") {
    const file = source === "fixture-head" ? "head-front.png" : "head-neck.png";
    await mkdir(dir, { recursive: true });
    for (const name of ["front", "left", "right", "back"]) {
      await copyFile(path.join(fixtureDir, file), path.join(dir, `${name}.png`));
    }
    return dir;
  }
  if (source === "corrupt") {
    await mkdir(dir, { recursive: true });
    await copyFile(path.join(fixtureDir, "tiny-corrupt.png"), path.join(dir, "front.png"));
    return dir;
  }
  if (source === "iyeon-body") return iyeonBody;
  if (source === "fixture-arms-down") {
    await mkdir(dir, { recursive: true });
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "front.png"));
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "back.png"));
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "left.png"));
    await copyFile(path.join(fixtureDir, "body-side.png"), path.join(dir, "right.png"));
    return dir;
  }
  if (source === "jojaepyo-body") return jojaepyoBody;
  if (source === "jojaepyo-archive") return path.join(jojaepyoBody, "archive");
  const armsMode = {
    "jojaepyo-arms-erased": "erase",
    "jojaepyo-arms-down": "down",
    "jojaepyo-arms-down-thick": "thick",
  }[source];
  if (armsMode) {
    await mkdir(dir, { recursive: true });
    const mutated = armsDownPng(await readFile(path.join(jojaepyoBody, "front.png")), armsMode);
    await writeFile(path.join(dir, "front.png"), mutated);
    await writeFile(path.join(dir, "back.png"), mutated);
    await copyFile(path.join(jojaepyoBody, "left.png"), path.join(dir, "left.png"));
    await copyFile(path.join(jojaepyoBody, "right.png"), path.join(dir, "right.png"));
    return dir;
  }
  if (source === "iyeon-body-scaled") {
    await mkdir(dir, { recursive: true });
    for (const name of ["front", "back", "left", "right"]) {
      const buffer = await readFile(path.join(iyeonBody, `${name}.png`));
      await writeFile(path.join(dir, `${name}.png`), scalePng(buffer, 0.5));
    }
    return dir;
  }
  return dir;
}

let passed = 0;
let failed = 0;

for (const test of cases) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "gate-case-"));
  const characters = path.join(dir, "characters");
  const images = path.join(dir, "images");
  const autoPath = path.join(dir, "auto.json");
  const verdictA = path.join(dir, "verdict-a.json");
  const verdictB = path.join(dir, "verdict-b.json");
  await mkdir(characters, { recursive: true });
  const defaultAuto = { ok: true, gate: "fixture", blocking: [], source: "fixture auto JSON, not tools/gate/auto-check.py" };
  await writeFile(autoPath, `${JSON.stringify(defaultAuto, null, 2)}\n`);
  await writeVerdict(verdictA, {});
  await writeVerdict(verdictB, {});
  const env = { ...process.env, GATE_CHARACTERS_ROOT: characters, GATE_PROFILES_FILE: path.join(here, "profiles.json") };
  let casePassed = true;
  let detail = "";
  const imageOut = path.join(dir, "image.json");
  let checkedDir = images;

  for (let index = 0; index < test.steps.length; index += 1) {
    const step = test.steps[index];
    if (step.seed === "review-pass-g0") {
      const person = path.join(characters, "lifecycle", "gates");
      await mkdir(person, { recursive: true });
      await writeFile(path.join(person, "G0-lifecycle-seed.json"), `${JSON.stringify({
        asset: "lifecycle-seed", gate: "G0", status: "REVIEW_PASS", evidence_sha256: "seed", blocking: [], userConfirmation: null, supersededConfirmations: [],
      }, null, 2)}\n`);
      continue;
    }
    if (step.flipByte) {
      const file = path.join(checkedDir, step.flipByte);
      const bytes = await readFile(file);
      bytes[bytes.length - 1] ^= 0xff;
      await writeFile(file, bytes);
      continue;
    }
    let result;
    if (step.imageCheck) {
      if (step.imageCheck.from !== "current") {
        await rm(images, { recursive: true, force: true });
        checkedDir = await materialize(step.imageCheck.from, images);
      }
      let stepEnv = env;
      if (step.imageCheck.profile) {
        const base = JSON.parse(await readFile(path.join(here, "profiles.json"), "utf8"));
        const profilePath = path.join(dir, `profile-${index}.json`);
        await writeFile(profilePath, JSON.stringify({ ...base, ...step.imageCheck.profile }));
        stepEnv = { ...env, GATE_PROFILES_FILE: profilePath };
      }
      result = runNode(imageScript, ["--kind", step.imageCheck.kind, checkedDir, imageOut], stepEnv);
    } else if (step.aggregate) {
      const args = ["aggregate", step.aggregate.asset, step.aggregate.gate, "--auto", autoPath];
      if (step.aggregate.useImage) {
        for (const name of ["front.png", "left.png", "right.png", "back.png"]) {
          args.push("--output", path.join(checkedDir, name));
        }
      }
      if (step.auto) await writeFile(autoPath, `${JSON.stringify(step.auto, null, 2)}\n`);
      if (step.aggregate.verdicts === "pass") args.push("--verdict", verdictA, "--verdict-b", verdictB);
      else if (step.aggregate.verdicts === "one") args.push("--verdict", verdictA);
      else if (step.aggregate.verdicts === "missing") args.push("--verdict", verdictA, "--verdict-b", path.join(dir, "absent-verdict.json"));
      else if (step.aggregate.verdicts === "same-path") args.push("--verdict", verdictA, "--verdict-b", verdictA);
      else if (step.aggregate.verdicts === "same-reviewer") {
        const twin = path.join(dir, "verdict-twin.json");
        await writeVerdict(twin, { reviewer: "verdict-a.json" });
        args.push("--verdict", verdictA, "--verdict-b", twin);
      } else if (step.aggregate.verdicts === "failing-checklist") {
        const bad = path.join(dir, "verdict-checklist.json");
        await writeVerdict(bad, { reviewer: "reviewer-checklist", checklist: [{ id: "fixture", pass: false, note: "fails" }] });
        args.push("--verdict", verdictA, "--verdict-b", bad);
      } else if (step.aggregate.verdicts === "low-score") {
        const low = path.join(dir, "verdict-score.json");
        await writeVerdict(low, { reviewer: "reviewer-score", identity_score: 1 });
        args.push("--verdict", verdictA, "--verdict-b", low);
      } else if (step.aggregate.verdicts === "blocking") {
        const blocked = path.join(dir, "verdict-blocking.json");
        await writeVerdict(blocked, { reviewer: "reviewer-blocking", blocking: ["arm-count"] });
        args.push("--verdict", verdictA, "--verdict-b", blocked);
      } else if (step.aggregate.verdicts === "one-bad") {
        const bad = path.join(dir, "verdict-one-bad.json");
        await writeVerdict(bad, { reviewer: "reviewer-checklist", checklist: [{ id: "fixture", pass: false, note: "fails" }] });
        args.push("--verdict", bad);
      }
      result = runNode(gateScript, args, env);
    } else if (step.gate) {
      result = runNode(gateScript, step.gate, env);
    } else if (step.assertImage) {
      const report = JSON.parse(await readFile(imageOut, "utf8"));
      const entry = report.files.find((file) => file.name === step.assertImage.file);
      const ratio = entry?.armSpan?.ratio ?? 0;
      const spanOk = step.assertImage.span === undefined || entry?.armSpan?.span === step.assertImage.span;
      const minOk = step.assertImage.ratioMin === undefined || ratio >= step.assertImage.ratioMin;
      const maxOk = step.assertImage.ratioMax === undefined || ratio <= step.assertImage.ratioMax;
      if (!spanOk || !minOk || !maxOk) {
        casePassed = false;
        detail = `${test.name} step ${index + 1}: arm span ${JSON.stringify(entry?.armSpan)} outside span ${step.assertImage.span} ratio ${step.assertImage.ratioMin}..${step.assertImage.ratioMax}`;
        break;
      }
      continue;
    } else if (step.assertBlocking) {
      const person = step.assertBlocking.asset.split("-")[0];
      const record = JSON.parse(await readFile(path.join(characters, person, "gates", `${step.assertBlocking.gate}-${step.assertBlocking.asset}.json`), "utf8"));
      const missing = step.assertBlocking.contains.filter((text) => !record.blocking.some((item) => item.includes(text)));
      if (missing.length) {
        casePassed = false;
        detail = `${test.name} step ${index + 1}: blocking missing ${missing.join(",")} in ${JSON.stringify(record.blocking)}`;
        break;
      }
      continue;
    } else if (step.assertGate) {
      const person = step.assertGate.asset.split("-")[0];
      const record = JSON.parse(await readFile(path.join(characters, person, "gates", `${step.assertGate.gate}-${step.assertGate.asset}.json`), "utf8"));
      const noteOk = step.assertGate.note === undefined || record.userConfirmation?.note === step.assertGate.note;
      const supersededOk = step.assertGate.superseded === undefined || record.supersededConfirmations.length === step.assertGate.superseded;
      if (record.status !== step.assertGate.status || !noteOk || !supersededOk) {
        casePassed = false;
        detail = `${test.name} step ${index + 1}: gate record ${record.status} note=${record.userConfirmation?.note} superseded=${record.supersededConfirmations.length}`;
      }
      continue;
    }
    if (!result) continue;
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
    if (result.status !== step.exit) {
      casePassed = false;
      detail = `${test.name} step ${index + 1}: expected exit ${step.exit}, got ${result.status}: ${output.trim()}`;
      break;
    }
    if (step.contains && !output.includes(step.contains)) {
      casePassed = false;
      detail = `${test.name} step ${index + 1}: missing ${step.contains}: ${output.trim()}`;
      break;
    }
    if (step.error && !output.includes(step.error)) {
      casePassed = false;
      detail = `${test.name} step ${index + 1}: missing error ${step.error}: ${output.trim()}`;
      break;
    }
    if (step.auto) await writeFile(autoPath, `${JSON.stringify(defaultAuto, null, 2)}\n`);
    if (step.json) {
      const raw = step.imageCheck ? await readFile(imageOut, "utf8") : result.stdout;
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch {
        casePassed = false;
        detail = `${test.name} step ${index + 1}: not JSON: ${String(raw).trim()}`;
        break;
      }
      if (!matches(parsed, step.json)) {
        casePassed = false;
        detail = `${test.name} step ${index + 1}: JSON mismatch ${JSON.stringify(step.json)}: ${JSON.stringify(parsed)}`;
        break;
      }
    }
  }

  if (casePassed) passed += 1;
  else {
    failed += 1;
    console.error(detail);
  }
  await rm(dir, { recursive: true, force: true });
}

console.log(`GATE passed=${passed} failed=${failed}`);
if (failed) process.exitCode = 1;
