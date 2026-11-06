#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ledgerScript = path.join(root, "tools/cast/credit-ledger.mjs");
const preload = path.join(root, "tools/cast/fixtures/adversarial-argv.mjs");
const ledgerSource = path.join(root, "characters/_cast/credits.json");
const profilesSource = path.join(root, "characters/_cast/gen-profiles.json");
const { cases } = JSON.parse(await readFile(path.join(root, "tools/cast/stage-cases.json"), "utf8"));

let passed = 0;
let failed = 0;
for (const test of cases) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "cast-stage-"));
  const ledger = path.join(dir, "credits.json");
  await writeFile(ledger, await readFile(ledgerSource));
  const env = { ...process.env, CAST_CREDITS_FILE: ledger, CAST_PROFILES_FILE: profilesSource };
  const spec = test.stage_reject;
  const encoded = spec.stage === "" || spec.stage.includes("\0");
  const args = [spec.command];
  if (spec.command === "record") {
    const taskPath = path.join(dir, "task.json");
    await writeFile(taskPath, JSON.stringify(spec.task));
    args.push("--task-json", taskPath);
  }
  args.push("--stage", encoded ? "placeholder" : spec.stage);
  if (spec.person) args.push("--person", spec.person);
  if (spec.reserve) args.push("--reserve", spec.reserve);
  if (spec.cost !== undefined) args.push("--cost", String(spec.cost));
  const stepEnv = encoded ? { ...env, ADVERSARIAL_ARG: spec.stage === "" ? "-" : "nul" } : env;
  const launch = encoded ? ["--import", preload] : [];
  const before = createHash("sha256").update(await readFile(ledger)).digest("hex");
  const result = spawnSync(process.execPath, [...launch, ledgerScript, ...args], { env: stepEnv, encoding: "utf8" });
  const after = createHash("sha256").update(await readFile(ledger)).digest("hex");
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  const ok = result.status === test.exit && output.includes(test.contains) && after === before;
  if (ok) passed += 1;
  else {
    failed += 1;
    console.error(`${test.name}: exit ${result.status}, ledger ${after === before ? "unchanged" : "CHANGED"}: ${output.trim()}`);
  }
  await rm(dir, { recursive: true, force: true });
}
console.log(`CAST_STAGE passed=${passed} failed=${failed}`);
if (failed) process.exitCode = 1;
