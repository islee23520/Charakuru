#!/usr/bin/env node
import { createHash } from "node:crypto";
import { lstatSync, realpathSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const profilesPath = process.env.GATE_PROFILES_FILE ?? path.join(root, "tools/gate/profiles.json");
const charactersRoot = process.env.GATE_CHARACTERS_ROOT ?? path.join(root, "characters");

function options(tokens) {
  const out = { _: [] };
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (!token.startsWith("--")) {
      out._.push(token);
      continue;
    }
    if (i + 1 >= tokens.length || tokens[i + 1].startsWith("--")) throw new Error(`InvalidArgument:${token}`);
    const key = token.slice(2);
    const value = tokens[++i];
    if (key === "output") {
      out.output ??= [];
      out.output.push(value);
    } else if (out[key] !== undefined) {
      throw new Error(`DuplicateArgument:${key}`);
    } else {
      out[key] = value;
    }
  }
  return out;
}

function fail(message, code = 1) {
  console.error(message);
  process.exitCode = code;
}

async function readJson(file) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`InputMissing:${path.resolve(file)}`);
    throw new Error(`JsonInvalid:${path.resolve(file)}:${error.message}`);
  }
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function fileSha(file) {
  return sha256(await readFile(file));
}

const ASSET_NAME = /^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+$/;
const RESERVED_SEGMENT = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i;
const MAX_ASSET_LENGTH = 128;

function assetFailure(asset) {
  if (typeof asset !== "string" || asset.length === 0 || asset.length > MAX_ASSET_LENGTH) return "AssetNameInvalid";
  if (asset.includes("\0") || asset.includes("\\") || asset.includes(":") || asset.includes(".")) return "AssetNameInvalid";
  if (!ASSET_NAME.test(asset)) return "AssetNameInvalid";
  if (asset.split("/").some((segment) => RESERVED_SEGMENT.test(segment))) return "AssetNameReserved";
  return null;
}

function gatePath(asset, gate) {
  const failure = assetFailure(asset);
  if (failure) throw new Error(failure);
  const person = asset.split("/")[0].split("-")[0];
  const file = path.resolve(charactersRoot, person, "gates", `${gate}-${asset.replaceAll("/", "-")}.json`);
  const rootResolved = path.resolve(charactersRoot);
  const relative = path.relative(rootResolved, file);
  const inside = relative !== "" && !path.isAbsolute(relative) && !relative.split(path.sep).includes("..");
  const prefix = rootResolved.toLowerCase().endsWith(path.sep.toLowerCase()) ? rootResolved.toLowerCase() : `${rootResolved.toLowerCase()}${path.sep}`;
  if (!inside || !file.toLowerCase().startsWith(prefix)) throw new Error("AssetPathEscape");
  if (!realInside(rootResolved, file)) throw new Error("AssetPathEscape");
  return file;
}

// A junction or symlink inside the characters root must not carry a valid name outside it: resolve the
// deepest existing ancestor, re-join the missing remainder, and compare against the real root.
function realInside(rootResolved, file) {
  const fold = (value) => (process.platform === "win32" ? value.toLowerCase() : value);
  const relative = path.relative(fold(realPath(rootResolved)), fold(realPath(file)));
  return relative !== "" && !path.isAbsolute(relative) && !relative.split(path.sep).includes("..");
}

// realpath of the deepest existing ancestor plus the not-yet-created remainder. A dangling link or a
// loop (ELOOP) is refused: mkdir would follow it to wherever it points.
function realPath(target) {
  const missing = [];
  let existing = target;
  for (;;) {
    try {
      return path.join(realpathSync.native(existing), ...missing);
    } catch (error) {
      if (error.code !== "ENOENT" || lstatSync(existing, { throwIfNoEntry: false })) throw new Error("AssetPathEscape");
      const parent = path.dirname(existing);
      if (parent === existing) throw new Error("AssetPathEscape");
      missing.unshift(path.basename(existing));
      existing = parent;
    }
  }
}

function evidenceHash(parts) {
  const lines = parts
    .map((part) => `${part.role}\t${part.path}\t${part.sha256}`)
    .sort();
  return sha256(lines.join("\n"));
}

function verdictFailures(verdict, gate) {
  const failures = [];
  if (!verdict || typeof verdict !== "object") return ["SchemaMissing:verdict"];
  for (const field of ["verdict", "blocking", "checklist", "reviewer"]) {
    if (verdict[field] === undefined) failures.push(`SchemaMissing:${field}`);
  }
  if (verdict.verdict !== undefined && verdict.verdict !== "PASS" && verdict.verdict !== "FAIL") failures.push("SchemaMissing:verdict");
  if (verdict.blocking !== undefined && !Array.isArray(verdict.blocking)) failures.push("SchemaMissing:blocking");
  else if (Array.isArray(verdict.blocking) && verdict.blocking.length > 0) failures.push("BlockingNotEmpty");
  if (verdict.checklist !== undefined && !Array.isArray(verdict.checklist)) failures.push("SchemaMissing:checklist");
  else if (Array.isArray(verdict.checklist)) {
    if (verdict.checklist.length === 0) failures.push("SchemaMissing:checklist");
    for (const item of verdict.checklist) {
      if (item?.pass !== true) failures.push(`ChecklistItemFailed:${item?.id ?? "unknown"}`);
    }
  }
  if (gate === "G3" || gate === "G5") {
    if (!Number.isFinite(verdict.identity_score)) failures.push("SchemaMissing:identity_score");
    else if (verdict.identity_score < 4) failures.push(`IdentityScoreBelow4:${verdict.identity_score}`);
  }
  if (verdict.verdict === "FAIL" && failures.length === 0) failures.push("VerdictFail");
  return failures;
}

async function aggregate(asset, gate, flags, profile) {
  if (!flags.auto) throw new Error("AutoJsonRequired");
  const verdictArgs = [flags.verdict, flags["verdict-b"]].filter(Boolean);
  const verdictPaths = verdictArgs.map((file) => path.resolve(file));
  if (new Set(verdictPaths).size !== verdictPaths.length) throw new Error("ReviewerNotIndependent");
  const outputs = [].concat(flags.output ?? []).flatMap((item) => String(item).split(",")).map((item) => item.trim()).filter(Boolean);
  const auto = await readJson(flags.auto);
  const verdicts = [];
  for (const file of verdictArgs) verdicts.push({ file, data: await readJson(file) });
  const blocking = [];
  if (auto.ok !== true || !Array.isArray(auto.blocking)) {
    blocking.push("AutoContractInvalid");
  } else if (auto.blocking.length > 0) {
    blocking.push("AutoFail", ...auto.blocking);
  }
  const reviewers = verdicts.map((entry) => entry.data?.reviewer);
  if (reviewers.some((reviewer) => typeof reviewer !== "string" || !reviewer.trim()) || new Set(reviewers).size !== reviewers.length) {
    blocking.push("ReviewerNotIndependent");
  }
  const verdictReports = verdicts.map((entry) => ({
    file: path.resolve(entry.file),
    reviewer: typeof entry.data?.reviewer === "string" ? entry.data.reviewer : null,
    failures: verdictFailures(entry.data, gate),
  }));
  const passCount = verdictReports.filter((entry) => entry.failures.length === 0 && verdicts.find((item) => path.resolve(item.file) === entry.file)?.data?.verdict === "PASS").length;
  if (passCount < profile.verdictCount) blocking.push(`VisionVerdicts:${passCount}<${profile.verdictCount}`);
  for (const report of verdictReports) {
    for (const failure of report.failures) blocking.push(`Verdict:${report.reviewer ?? path.basename(report.file)}:${failure}`);
  }
  const parts = [];
  for (const file of outputs) parts.push({ role: "output", path: path.resolve(file), sha256: await fileSha(file) });
  parts.push({ role: "auto", path: path.resolve(flags.auto), sha256: await fileSha(flags.auto) });
  for (const entry of verdicts) parts.push({ role: "verdict", path: path.resolve(entry.file), sha256: await fileSha(entry.file) });
  const fingerprint = evidenceHash(parts);
  const file = gatePath(asset, gate);
  let previous = null;
  try {
    previous = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (previous?.status === "PASS" && previous.evidence_sha256 === fingerprint && blocking.length === 0) {
    console.log(JSON.stringify({ status: "PASS", asset, gate, evidence_sha256: fingerprint, unchanged: true }));
    return;
  }
  const checkpoint = profile.checkpoints.includes(gate);
  const status = blocking.length > 0 ? "FAIL" : (checkpoint ? "REVIEW_PASS" : "PASS");
  const record = {
    asset,
    gate,
    status,
    checkpoint,
    evidence_sha256: fingerprint,
    evidence: parts.map(({ role, path: evidencePath, sha256: digest }) => ({ role, path: evidencePath, sha256: digest })),
    auto: path.resolve(flags.auto),
    verdicts: verdictReports,
    blocking,
    userConfirmation: null,
    supersededConfirmations: previous?.supersededConfirmations ?? [],
  };
  if (previous?.userConfirmation) record.supersededConfirmations = [...record.supersededConfirmations, previous.userConfirmation];
  if (status === "PASS" && previous?.userConfirmation && !checkpoint) record.userConfirmation = previous.userConfirmation;
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(record, null, 2)}\n`);
  console.log(JSON.stringify({ status, asset, gate, evidence_sha256: fingerprint, blocking }));
  if (status === "FAIL") process.exitCode = 1;
}

async function confirm(asset, gate, flags) {
  if (!flags.note || !flags.note.trim()) throw new Error("NoteRequired");
  const file = gatePath(asset, gate);
  let record;
  try {
    record = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error("NotReviewed");
    throw error;
  }
  if (record.status !== "REVIEW_PASS") throw new Error("NotReviewed");
  record.userConfirmation = { at: new Date().toISOString(), note: flags.note, checkpoint: gate };
  record.status = "PASS";
  await writeFile(file, `${JSON.stringify(record, null, 2)}\n`);
  console.log(JSON.stringify({ status: "PASS", asset, gate, userConfirmation: record.userConfirmation }));
}

async function requireGate(asset, gate) {
  const file = gatePath(asset, gate);
  let record;
  try {
    record = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`GateMissing:${gate}`);
    throw error;
  }
  if (record.status === "PASS") {
    console.log(JSON.stringify({ status: "PASS", asset, gate }));
    return;
  }
  if (record.status === "REVIEW_PASS") {
    fail(`AwaitingUserConfirmation:${gate}`);
    return;
  }
  fail(`GateNotPass:${gate}:${record.status}`);
}

async function main() {
  const [command, asset, gate, ...rest] = process.argv.slice(2);
  const flags = options(rest);
  if (!command || asset === undefined || !gate) throw new Error("Usage:gate <aggregate|confirm|require> <asset> <gate>");
  if (!/^G[0-5]$/.test(gate)) throw new Error("GateNameInvalid");
  const named = assetFailure(asset);
  if (named) throw new Error(named);
  const profile = JSON.parse(await readFile(profilesPath, "utf8"));
  if (command === "aggregate") await aggregate(asset, gate, flags, profile);
  else if (command === "confirm") await confirm(asset, gate, flags);
  else if (command === "require") await requireGate(asset, gate);
  else throw new Error(`UnknownCommand:${command}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
