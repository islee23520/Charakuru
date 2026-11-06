#!/usr/bin/env node
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ledgerPath = process.env.CAST_CREDITS_FILE ?? path.join(root, "characters/_cast/credits.json");
const profilesPath = process.env.CAST_PROFILES_FILE ?? path.join(root, "characters/_cast/gen-profiles.json");
const [command, ...args] = process.argv.slice(2);

function options(tokens) {
  const out = {};
  for (let i = 0; i < tokens.length; i += 1) {
    if (!tokens[i].startsWith("--") || i + 1 >= tokens.length || tokens[i + 1].startsWith("--")) {
      throw new Error(`InvalidArgument:${tokens[i]}`);
    }
    const key = tokens[i].slice(2);
    if (out[key] !== undefined) throw new Error(`DuplicateArgument:${key}`);
    out[key] = tokens[++i];
  }
  return out;
}

async function json(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

async function saveLedger(ledger) {
  await writeFile(ledgerPath, `${JSON.stringify(ledger, null, 2)}\n`);
}

function ensureAccounting(ledger) {
  if (!ledger.people) {
    ledger.people = {};
    for (const stage of Object.values(ledger.stages)) {
      if (!stage.person) continue;
      ledger.people[stage.person] ??= { spent: 0 };
      ledger.people[stage.person].spent += stage.spent;
    }
  }
  ledger.reserve_draws ??= {};
  for (const stage of Object.values(ledger.stages)) {
    if (!stage.person) continue;
    ledger.people[stage.person] ??= { spent: 0 };
    ledger.reserve_draws[stage.person] ??= 0;
  }
}

function fail(reason) {
  console.error(reason);
  process.exitCode = 4;
}

try {
  if (command === "check") {
    const o = options(args);
    const stageKey = o.stage;
    const cost = Number(o.cost);
    if (!stageKey || !Number.isSafeInteger(cost) || cost < 0) throw new Error("InvalidCost");
    const [ledger, profileData] = await Promise.all([json(ledgerPath), json(profilesPath)]);
    const stage = ledger.stages[stageKey];
    const planStage = profileData.stages[stageKey];
    if (!stage || !planStage) throw new Error(`UnknownStage:${stageKey}`);
    ensureAccounting(ledger);
    const reserveReason = o.reserve?.trim();
    const person = o.person ?? stage.person;
    if (o.reserve !== undefined && !reserveReason) throw new Error("ReserveReasonRequired");
    if (!reserveReason && o.step && !planStage.required.some((entry) => entry.step === o.step)) {
      fail(`UnlistedStep:${o.step}`);
    } else {
      const totalSpent = Object.values(ledger.stages).reduce((sum, entry) => sum + entry.spent, 0);
      if (totalSpent + cost > ledger.global_cap) fail("GlobalCapExceeded");
      else if (person && (ledger.people[person]?.spent ?? 0) + cost > ledger.person_cap) {
        fail(`PersonCapExceeded:${person}`);
      }
      if (!process.exitCode && reserveReason && person && (ledger.reserve_draws[person] ?? 0) + cost > ledger.reserve_draw_cap) {
        fail(`ReserveDrawCap:${person}:${ledger.reserve_draws[person] ?? 0}+${cost}>${ledger.reserve_draw_cap}`);
      }
      if (!process.exitCode && (stageKey === "reserve" || !reserveReason) && stage.spent + cost > stage.allocation) fail(`StageCapExceeded:${stageKey}`);
    }
  } else if (command === "record") {
    const o = options(args);
    if (!o["task-json"]) throw new Error("TaskJsonRequired");
    const task = await json(path.resolve(o["task-json"]));
    if (typeof task.task_id !== "string" || !task.task_id.trim()) throw new Error("TaskIdRequired");
    if (!Number.isSafeInteger(task.credits_consumed) || task.credits_consumed < 0) throw new Error("InvalidCreditsConsumed");
    const stageKey = o.stage ?? task.stage;
    if (!stageKey) throw new Error("RecordStageRequired");
    const ledger = await json(ledgerPath);
    ensureAccounting(ledger);
    if (ledger.tasks.some((entry) => entry.task_id === task.task_id)) throw new Error(`DuplicateTaskId:${task.task_id}`);
    const stage = ledger.stages[stageKey];
    if (!stage) throw new Error(`UnknownStage:${stageKey}`);
    const person = stage.person ?? o.person ?? task.person ?? null;
    if (stageKey === "reserve" && !person) throw new Error("ReservePersonRequired");
    if (stage.person && person !== stage.person) throw new Error(`StagePersonMismatch:${stageKey}`);
    const reserveReason = o.reserve?.trim() ?? task.reserve_reason?.trim();
    if (o.reserve !== undefined && !o.reserve.trim()) throw new Error("ReserveReasonRequired");
    if (stageKey === "reserve" && !reserveReason) throw new Error("ReserveReasonRequired");
    const cost = task.credits_consumed;
    const totalSpent = Object.values(ledger.stages).reduce((sum, entry) => sum + entry.spent, 0);
    if (totalSpent + cost > ledger.global_cap) throw new Error("GlobalCapExceeded");
    if (person && ledger.people[person] && ledger.people[person].spent + cost > ledger.person_cap) {
      throw new Error(`PersonCapExceeded:${person}`);
    }
    const isReserveDraw = Boolean(reserveReason && person);
    if (isReserveDraw && ledger.reserve_draws[person] + cost > ledger.reserve_draw_cap) {
      throw new Error(`ReserveDrawCap:${person}:${ledger.reserve_draws[person]}+${cost}>${ledger.reserve_draw_cap}`);
    }
    if ((stageKey === "reserve" || !reserveReason) && stage.spent + cost > stage.allocation) {
      throw new Error(`StageCapExceeded:${stageKey}`);
    }
    stage.spent += cost;
    if (person) {
      ledger.people[person] ??= { spent: 0 };
      ledger.reserve_draws[person] ??= 0;
      ledger.people[person].spent += cost;
      if (isReserveDraw) ledger.reserve_draws[person] += cost;
    }
    ledger.tasks.push({ task_id: task.task_id, credits_consumed: cost, stage: stageKey, person, reserve_reason: reserveReason ?? null });
    await saveLedger(ledger);
    console.log(JSON.stringify({ task_id: task.task_id, credits_consumed: cost, stage: stageKey, person, stage_spent: stage.spent, person_spent: person ? ledger.people[person].spent : null, reserve_draw: person ? ledger.reserve_draws[person] : null }));
  } else if (command === "report") {
    const ledger = await json(ledgerPath);
    ensureAccounting(ledger);
    console.log("Stage\tPerson\tAllocation\tSpent");
    for (const [key, stage] of Object.entries(ledger.stages)) console.log(`${key}\t${stage.person ?? "-"}\t${stage.allocation}\t${stage.spent}`);
    console.log("Person\tAllocation\tSpent");
    for (const [person, item] of Object.entries(ledger.people)) {
      const allocation = Object.values(ledger.stages).filter((stage) => stage.person === person).reduce((sum, stage) => sum + stage.allocation, 0);
      console.log(`${person}\t${allocation}\t${item.spent}`);
    }
  } else if (command === "plan-check") {
    const [ledger, profileData] = await Promise.all([json(ledgerPath), json(profilesPath)]);
    ensureAccounting(ledger);
    const issues = [];
    for (const [key, stage] of Object.entries(profileData.stages)) {
      const required = stage.required.reduce((sum, entry) => {
        const price = profileData.prices[entry.price_key];
        if (!Number.isSafeInteger(price) || price < 0) throw new Error(`UnknownPrice:${entry.price_key}`);
        return sum + price * entry.count;
      }, 0);
      if (required > stage.allocation) issues.push(`StageAllocationExceeded:${key}:${required}>${stage.allocation}`);
    }
    const people = new Map();
    for (const stage of Object.values(profileData.stages)) if (stage.person) people.set(stage.person, (people.get(stage.person) ?? 0) + stage.allocation);
    for (const [person, allocation] of people) if (allocation + ledger.reserve_draw_cap > ledger.person_cap) issues.push(`PersonCapExceeded:${person}:${allocation}+${ledger.reserve_draw_cap}>${ledger.person_cap}`);
    const total = Object.values(profileData.stages).reduce((sum, stage) => sum + stage.allocation, 0);
    if (total > ledger.global_cap) issues.push(`GlobalAllocationExceeded:${total}>${ledger.global_cap}`);
    if (issues.length) for (const issue of issues) console.error(issue), process.exitCode = 4;
  } else {
    throw new Error("Usage: credit-ledger.mjs <check|record|report|plan-check>");
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = command === "check" || command === "plan-check" ? 4 : 1;
}
