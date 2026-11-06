#!/usr/bin/env node
import { mkdir, mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ledgerScript = path.join(root, "tools/cast/credit-ledger.mjs");
const manifestScript = path.join(root, "tools/cast/manifest-verify.mjs");
const contractScript = path.join(root, "tools/cast/contract-check.mjs");
const selectScript = path.join(root, "tools/cast/select-parts.mjs");
const manifestSource = path.join(root, "characters/_cast/cast.json");
const selectionRulesSource = path.join(root, "characters/_cast/selection-rules.json");
const profilesSource = path.join(root, "characters/_cast/gen-profiles.json");
const ledgerSource = path.join(root, "characters/_cast/credits.json");
const { cases } = JSON.parse(await readFile(path.join(root, "tools/cast/cases.json"), "utf8"));
let passed = 0;
let failed = 0;
let manifestShapePassed = false;
let manifestFailed = 0;
let contractPassed = 0;
let contractFailed = 0;
let selectPassed = 0;
let selectFailed = 0;

function contractFixture(boneName, slots, secondary = null) {
  const binary = Buffer.alloc(secondary ? 60 : 36);
  binary.writeUInt16LE(0, 12);
  binary.writeFloatLE(1, 20);
  if (secondary) {
    binary.writeUInt16LE(1, 36);
    binary.writeFloatLE(0.5, 44);
  }
  const attributes = { POSITION: 0, JOINTS_0: 1, WEIGHTS_0: 2 };
  const bufferViews = [
    { buffer: 0, byteOffset: 0, byteLength: 12 },
    { buffer: 0, byteOffset: 12, byteLength: 8 },
    { buffer: 0, byteOffset: 20, byteLength: 16 }
  ];
  const accessors = [
    { bufferView: 0, componentType: 5126, count: 1, type: "VEC3" },
    { bufferView: 1, componentType: 5123, count: 1, type: "VEC4" },
    { bufferView: 2, componentType: 5126, count: 1, type: "VEC4" }
  ];
  if (secondary) {
    attributes.JOINTS_1 = 3;
    bufferViews.push({ buffer: 0, byteOffset: 36, byteLength: 8 });
    accessors.push({ bufferView: 3, componentType: 5123, count: 1, type: "VEC4" });
    if (secondary !== "unpaired") {
      attributes.WEIGHTS_1 = 4;
      bufferViews.push({ buffer: 0, byteOffset: 44, byteLength: 16 });
      accessors.push({ bufferView: 4, componentType: 5126, count: 1, type: "VEC4" });
    }
  }
  const document = {
    asset: { version: "2.0", extras: { cast_slots: slots, hide_groups: [] } },
    nodes: secondary ? [{ name: "head.x" }, { mesh: 0, skin: 0 }, { name: boneName }] : [{ name: boneName }, { mesh: 0, skin: 0 }],
    skins: [{ joints: secondary ? [0, 2] : [0] }],
    meshes: [{ primitives: [{ attributes }] }],
    bufferViews,
    accessors,
    buffers: [{ byteLength: binary.length }]
  };
  const rawJson = Buffer.from(JSON.stringify(document));
  const jsonPadding = (4 - (rawJson.length % 4)) % 4;
  const json = Buffer.concat([rawJson, Buffer.alloc(jsonPadding, 0x20)]);
  const header = Buffer.alloc(12);
  header.write("glTF");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + json.length + 8 + binary.length, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(json.length, 0);
  jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binary.length, 0);
  binHeader.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([header, jsonHeader, json, binHeader, binary]);
}

function matchesObject(actual, expected) {
  return Object.entries(expected).every(([key, value]) => actual?.[key] === value);
}

function readPath(value, dottedPath) {
  return dottedPath.split(".").reduce((current, key) => current?.[key], value);
}

for (const test of cases) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "cast-ledger-"));
  try {
  const profiles = path.join(dir, "gen-profiles.json");
  const ledger = path.join(dir, "credits.json");
  const profileData = JSON.parse(await readFile(profilesSource, "utf8"));
  const ledgerData = JSON.parse(await readFile(ledgerSource, "utf8"));
  await writeFile(profiles, JSON.stringify(profileData));
  await writeFile(ledger, JSON.stringify(ledgerData));
  const env = { ...process.env, CAST_PROFILES_FILE: profiles, CAST_CREDITS_FILE: ledger };
  let result;
  if (test.select === "canonical_all") {
    const output = path.join(dir, "canonical-selection");
    await mkdir(output, {recursive:true});
    const manifest = JSON.parse(await readFile(manifestSource, "utf8"));
    let canonicalPassed = true;
    for (const person of manifest.people) {
      const run = spawnSync(process.execPath, [selectScript, person.personId], {env:{...env, CAST_SELECTION_OUTPUT_DIR:output}, encoding:"utf8"});
      if (run.status !== 0) { canonicalPassed = false; break; }
      const generated = await readFile(path.join(output, `${person.folder}.json`), "utf8");
      const canonical = await readFile(path.join(root, `characters/_cast/selection/${person.folder}.json`), "utf8");
      if (generated !== canonical) { canonicalPassed = false; break; }
    }
    result = {status:canonicalPassed ? 0 : 1, stdout:canonicalPassed ? "CanonicalSelectionsByteIdentical" : "", stderr:canonicalPassed ? "" : "CanonicalSelectionMismatch"};
  } else if (test.selectMutation) {
    const source = await readFile(selectScript, "utf8");
    const mutated = source.replace("if (remainingTokens.length) await emitBlocked", "if (familyCandidates.length > 1 && remainingTokens.length) await emitBlocked");
    const mutationScript = path.join(dir, "select-parts-mutated.mjs");
    await writeFile(mutationScript, mutated);
    const fixtureCast = path.join(dir, "cast.json");
    const fixtureTokens = path.join(dir, "tokens");
    const fixtureDetails = path.join(dir, "details");
    const fixtureOutput = path.join(dir, "selection");
    await mkdir(fixtureTokens, { recursive: true });
    await mkdir(fixtureDetails, { recursive: true });
    const manifest = JSON.parse(await readFile(manifestSource, "utf8"));
    const person = manifest.people.find((entry) => entry.personId === "person-0998");
    person.slots.lower = "maroon high-waisted pleated trousers";
    await writeFile(fixtureCast, JSON.stringify({people:[person]}));
    await writeFile(path.join(fixtureTokens, "person-0998.json"), JSON.stringify({facts:{role:"제1분공방 이씨 가문 후계"}}));
    await writeFile(path.join(fixtureDetails, "person-0998.json"), JSON.stringify({stateName:"규격맹", commonTier:"T2"}));
    const mutationEnv = {...env, CAST_MANIFEST_FILE:fixtureCast, CAST_SELECTION_RULES_FILE:selectionRulesSource,
      CAST_PORTRAIT_TOKEN_DIR:fixtureTokens, CAST_PERSON_DETAIL_DIR:fixtureDetails, CAST_SELECTION_OUTPUT_DIR:fixtureOutput};
    const mutationRun = spawnSync(process.execPath, [mutationScript, "person-0998"], {env:mutationEnv, encoding:"utf8"});
    result = mutationRun.status === 0
      ? {status:1, stdout:"", stderr:"MutationSurvived:single_iilseop_lower_maroon"}
      : {status:0, stdout:"MutationKilled", stderr:""};
  } else if (test.select) {
    const fixtureCast = path.join(dir, "cast.json");
    const fixtureRules = path.join(dir, "selection-rules.json");
    const fixtureTokens = path.join(dir, "tokens");
    const fixtureDetails = path.join(dir, "details");
    const fixtureOutput = path.join(dir, "selection");
    await mkdir(fixtureTokens, { recursive: true });
    await mkdir(fixtureDetails, { recursive: true });
    const manifest = JSON.parse(await readFile(manifestSource, "utf8"));
    const fixturePersonId = ["procedural_props","single_jeongyura_upper_crimson"].includes(test.select) ? "person-0399"
      : test.select === "minunggi_lower" ? "person-1008"
      : test.select === "single_sinjongmok_lower_navy" ? "person-1009"
      : test.select === "single_bakseongsu_lower_khaki" ? "person-1019"
      : ["gimyeongyu_lower","single_gimyeongyu_upper_maroon"].includes(test.select) ? "person-1007" : "person-0998";
    const person = manifest.people.find((entry) => entry.personId === fixturePersonId);
    if (test.select === "wiki_wins") {
      person.garmentFamilies.upper = "upper_gimyeongyu";
      person.slots.upper = "plain high-collared civilian coat";
    }
    const variantTexts = {
      variant_unmatched_file: "olive wool breeches with buttoned cuffs",
      empty_keywords_no_match: "olive wool breeches with buttoned cuffs",
      oov_maroon: "maroon breeches with buttoned cuffs",
      oov_khaki: "khaki breeches with buttoned cuffs",
      oov_bamsaek: "밤색 breeches with buttoned cuffs",
      substring_highlight: "highlight gray breeches with buttoned cuffs",
      hyphen_dark_brown: "dark-brown wool breeches with buttoned cuffs",
      variant_ambiguous: "light gray dark brown breeches with buttoned cuffs",
      plain_default_variant: "breeches with buttoned cuffs",
      rules_invalid_keywords: "dark brown breeches with buttoned cuffs",
      rules_invalid_structure: "dark brown breeches with buttoned cuffs",
      source_unknown_file: "olive wool breeches with buttoned cuffs"
    };
    if (variantTexts[test.select]) {
      person.garmentFamilies.lower = "male_breeches_buttoned_cuffs";
      person.slots.lower = variantTexts[test.select];
    }
    const singleTexts = {
      single_iilseop_lower_maroon: {slot:"lower", text:"maroon high-waisted pleated trousers"},
      single_jeongyura_upper_crimson: {slot:"upper", text:"로고 없는 탁한 진홍색 작업 재킷"},
      single_bakseongsu_lower_khaki: {slot:"lower", text:"khaki wool trousers bloused over boots"},
      single_sinjongmok_lower_navy: {slot:"lower", text:"navy high-waisted pleated trousers"},
      single_gimyeongyu_upper_maroon: {slot:"upper", text:"Plain deep maroon high-collared civilian coat over beige shirt"},
      extra_colour_trailing: {slot:"lower", text:"dark brown and maroon breeches with buttoned cuffs", family:"male_breeches_buttoned_cuffs"},
      extra_colour_leading: {slot:"lower", text:"maroon breeches with dark brown buttoned cuffs", family:"male_breeches_buttoned_cuffs"}
    };
    if (singleTexts[test.select]) {
      const fixture = singleTexts[test.select];
      person.slots[fixture.slot] = fixture.text;
      if (fixture.family) person.garmentFamilies[fixture.slot] = fixture.family;
    }
    await writeFile(fixtureCast, JSON.stringify({ people: [person] }));
    const fixtureRuleData = JSON.parse(await readFile(selectionRulesSource, "utf8"));
    if (test.select === "empty_keywords_no_match") {
      fixtureRuleData.rules.find((rule) => rule.family === "male_breeches_buttoned_cuffs").wikiKeywords = [];
    }
    if (test.select === "rules_invalid_keywords") fixtureRuleData.rules.find((rule) => rule.family === "male_breeches_buttoned_cuffs").wikiKeywords = "dark brown";
    if (test.select === "rules_invalid_structure") delete fixtureRuleData.familyStructureWords.male_breeches_buttoned_cuffs;
    if (test.select === "source_unknown_file") {
      const rule = fixtureRuleData.rules.find((entry) => entry.family === "male_breeches_buttoned_cuffs" && entry.wikiKeywords?.includes("olive"));
      if (rule) rule.source = "invalid-source";
      else fixtureRuleData.rules.push({...fixtureRuleData.rules.find((entry) => entry.family === "male_breeches_buttoned_cuffs"), wikiKeywords:["olive"], source:"invalid-source"});
    }
    await writeFile(fixtureRules, JSON.stringify(fixtureRuleData));
    const fixtureRole = fixturePersonId === "person-1008" ? "규격맹 봉신 · 통신 정비·배분 담당(전 대전선 군주, 왕격 보존)"
      : fixturePersonId === "person-0399" ? "위원장" : fixturePersonId === "person-1007" ? "온민의 수장" : "제1분공방 이씨 가문 후계";
    const fixtureState = fixturePersonId === "person-1008" ? { stateName: "규격맹", commonTier: "T1" }
      : fixturePersonId === "person-0399" ? { stateName: "정동노총", commonTier: "T1" } : fixturePersonId === "person-1007" ? { stateName: "대한민국정부", commonTier: "T2" }
      : { stateName: test.select === "unknown_faction" ? null : "규격맹", commonTier: "T2" };
    await writeFile(path.join(fixtureTokens, `${fixturePersonId}.json`), JSON.stringify({ facts: test.select === "blocked_file" ? {} : { role: fixtureRole } }));
    await writeFile(path.join(fixtureDetails, `${fixturePersonId}.json`), JSON.stringify(fixtureState));
    const selectEnv = { ...env, CAST_MANIFEST_FILE: fixtureCast, CAST_SELECTION_RULES_FILE: fixtureRules,
      CAST_PORTRAIT_TOKEN_DIR: fixtureTokens, CAST_PERSON_DETAIL_DIR: fixtureDetails, CAST_SELECTION_OUTPUT_DIR: fixtureOutput };
    if (variantTexts[test.select] || singleTexts[test.select]) {
      await mkdir(fixtureOutput, { recursive: true });
      await writeFile(path.join(fixtureOutput, `${person.folder}.json`), JSON.stringify({selections:[{slot:"lower", family:"stale"}]}));
    }
    result = spawnSync(process.execPath, [selectScript, fixturePersonId], { env: selectEnv, encoding: "utf8" });
    if (singleTexts[test.select]) {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (!stored.blocked?.startsWith("StyleUnmatched:") || stored.selections !== undefined || stored.ruleTrace?.[0]?.kind !== "style-unmatched") result.status = 2;
    }
    if (test.select === "blocked_file") {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (stored.blocked !== "FactUnknown:role" || stored.selections !== undefined) result.status = 2;
    }
    if (["variant_unmatched_file", "empty_keywords_no_match", "oov_maroon", "oov_khaki", "oov_bamsaek", "substring_highlight"].includes(test.select)) {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (!stored.blocked?.startsWith("StyleUnmatched:lower:male_breeches_buttoned_cuffs:") || stored.selections !== undefined || stored.ruleTrace?.[0]?.kind !== "style-unmatched") result.status = 2;
    }
    if (test.select === "variant_ambiguous") {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (stored.blocked !== "VariantAmbiguous:lower:male_breeches_buttoned_cuffs" || stored.selections !== undefined || stored.ruleTrace?.[0]?.kind !== "variant-ambiguous") result.status = 2;
    }
    if (["rules_invalid_keywords", "rules_invalid_structure"].includes(test.select)) {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (!stored.blocked?.startsWith("RulesInvalid:male_breeches_buttoned_cuffs:") || stored.selections !== undefined) result.status = 2;
    }
    if (test.select === "source_unknown_file") {
      const stored = JSON.parse(await readFile(path.join(fixtureOutput, `${person.folder}.json`), "utf8"));
      if (stored.blocked !== "SourceUnknown:invalid-source" || stored.selections !== undefined) result.status = 2;
    }
    if (result.status === 0) {
      const selected = JSON.parse(result.stdout);
      const upper = selected.selections.find((entry) => entry.slot === "upper");
      if (test.select === "iilseop_upper" && !(upper?.family === "upper_iilseop" && upper?.palette?.name.includes("clay-brown"))) result.status = 1;
      if (test.select === "wiki_wins" && !(upper?.family === "upper_gimyeongyu" && upper?.ruleTrace.some((entry) => entry.kind === "wiki-explicit" && entry.result === "family-wins"))) result.status = 1;
      if (test.select === "minunggi_lower") {
        const lower = selected.selections.find((entry) => entry.slot === "lower");
        if (!(lower?.family === "male_breeches_buttoned_cuffs" && lower?.palette?.name === "dark brown" && lower?.wear === "worn" && lower?.source === "library")) result.status = 1;
      }
      if (test.select === "gimyeongyu_lower") {
        const lower = selected.selections.find((entry) => entry.slot === "lower");
        if (!(lower?.family === "male_breeches_buttoned_cuffs" && lower?.palette?.name === "light gray" && lower?.wear === "maintained" && lower?.source === "new")) result.status = 1;
      }
      if (test.select === "hyphen_dark_brown") {
        const lower = selected.selections.find((entry) => entry.slot === "lower");
        if (!(lower?.palette?.name === "dark brown" && lower?.ruleTrace.some((entry) => entry.kind === "style-variant" && entry.result === "keyword-match"))) result.status = 1;
      }
      if (test.select === "plain_default_variant") {
        const lower = selected.selections.find((entry) => entry.slot === "lower");
        if (!lower?.ruleTrace.some((entry) => entry.kind === "style-variant" && entry.result === "default-no-style-word")) result.status = 1;
      }
      if (test.select === "procedural_props") {
        const accessory = selected.selections.find((entry) => entry.slot === "accessories");
        if (accessory?.source !== "procedural") result.status = 1;
      }
    }
  } else if (test.contract) {
    const fixture = path.join(dir, `${test.contract}.glb`);
    const secondary = test.contract.startsWith("secondary_")
      ? (test.contract === "secondary_unpaired_joints" ? "unpaired" : "paired") : null;
    const bone = test.contract === "unknown_bone" || test.contract === "secondary_unknown_bone"
      ? "UpperArm.L.001" : test.contract === "secondary_outside_whitelist" ? "root.x" : "head.x";
    const slots = test.contract === "duplicate_slot" ? ["face_glasses", "face_glasses"] : ["face_glasses"];
    await writeFile(fixture, contractFixture(bone, slots, secondary));
    result = spawnSync(process.execPath, [contractScript, fixture, "--type", "male", "--slot", "face_glasses"], { encoding: "utf8" });
  } else if (test.assert_budget) {
    const budget = profileData.budget;
    const expected = { assembled_cap: 60000, components: { head: 12000, body: 24000, hair_cards: 8000, garments: 24000, props: 3000 } };
    result = { status: JSON.stringify(budget) === JSON.stringify(expected) ? 0 : 1, stdout: "", stderr: "" };
  }
  if (!test.assert_budget && test.setup_spend) {
    ledgerData.stages[test.setup_spend.stage].spent = test.setup_spend.cost;
    await writeFile(ledger, JSON.stringify(ledgerData));
  }
  if (!test.assert_budget && test.mutate_stage) {
    profileData.stages[test.mutate_stage.stage].allocation = test.mutate_stage.allocation;
    await writeFile(profiles, JSON.stringify(profileData));
  }
  let casePassed = false;
  if (test.select || test.selectMutation || test.contract) {
    // The contract case was executed while its fixture was prepared above.
  } else if (test.assert_budget) {
    // The canonical profile budget is checked directly above.
    casePassed = result.status === test.exit;
  } else if (test.manifest) {
    const args = [];
    if (test.manifest === "missing_gender") {
      const fixture = path.join(dir, "cast-missing-gender.json");
      const manifest = JSON.parse(await readFile(manifestSource, "utf8"));
      delete manifest.people[0].gender;
      await writeFile(fixture, JSON.stringify(manifest));
      args.push(fixture);
    } else if (test.manifest !== "canonical") {
      throw new Error(`UnknownManifestCase:${test.manifest}`);
    }
    result = spawnSync(process.execPath, [manifestScript, ...args], { encoding: "utf8" });
  } else if (test.sequence) {
    casePassed = true;
    for (let index = 0; index < test.sequence.length; index += 1) {
      const step = test.sequence[index];
      const command = step.command ?? ["record"];
      const args = [...command];
      if (step.task_json) {
        const taskPath = path.join(dir, `task-${index}.json`);
        await writeFile(taskPath, JSON.stringify(step.task_json));
        args.push("--task-json", taskPath);
      }
      if (step.stage) args.push("--stage", step.stage);
      if (step.person) args.push("--person", step.person);
      if (step.reserve) args.push("--reserve", step.reserve);
      const stepResult = spawnSync(process.execPath, [ledgerScript, ...args], { env, encoding: "utf8" });
      result = stepResult;
      const output = `${stepResult.stdout ?? ""}${stepResult.stderr ?? ""}`;
      if (stepResult.status !== step.exit) {
        casePassed = false;
        console.error(`${test.name} step ${index + 1}: expected exit ${step.exit}, got ${stepResult.status}: ${output.trim()}`);
        break;
      }
      if (step.json) {
        let parsed;
        try {
          parsed = JSON.parse(stepResult.stdout);
        } catch {
          casePassed = false;
          console.error(`${test.name} step ${index + 1}: stdout was not JSON: ${stepResult.stdout.trim()}`);
          break;
        }
        if (!matchesObject(parsed, step.json)) {
          casePassed = false;
          console.error(`${test.name} step ${index + 1}: JSON did not match ${JSON.stringify(step.json)}: ${stepResult.stdout.trim()}`);
          break;
        }
      }
      if (step.contains && !output.includes(step.contains)) {
        casePassed = false;
        console.error(`${test.name} step ${index + 1}: output missing ${step.contains}: ${output.trim()}`);
        break;
      }
    }
    if (casePassed && test.expected_state) {
      const saved = JSON.parse(await readFile(ledger, "utf8"));
      for (const [key, value] of Object.entries(test.expected_state)) {
        if (key === "total_spent") {
          const total = Object.values(saved.stages).reduce((sum, stage) => sum + stage.spent, 0);
          if (total !== value) casePassed = false;
        } else if (readPath(saved, key) !== value) {
          casePassed = false;
        }
        if (!casePassed) console.error(`${test.name}: persisted ${key} did not equal ${value}`);
      }
    }
  } else if (test.record_twice || test.task_json) {
    const taskPath = path.join(dir, "task.json");
    await writeFile(taskPath, JSON.stringify(test.record_twice ?? test.task_json));
    const stage = (test.record_twice ?? test.task_json).stage ?? "iilseop";
    spawnSync(process.execPath, [ledgerScript, "record", "--task-json", taskPath, "--stage", stage], { env, encoding: "utf8" });
    result = spawnSync(process.execPath, [ledgerScript, "record", "--task-json", taskPath, "--stage", stage], { env, encoding: "utf8" });
  } else {
    result = spawnSync(process.execPath, [ledgerScript, ...test.command], { env, encoding: "utf8" });
  }
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  if (!test.sequence && !test.assert_budget && result.status === test.exit && (!test.contains || output.includes(test.contains))) casePassed = true;
  if (test.manifest) {
    if (test.manifest === "canonical" && casePassed) manifestShapePassed = true;
    if (!casePassed) {
      manifestFailed += 1;
      console.error(`${test.name}: expected exit ${test.exit}${test.contains ? ` containing ${test.contains}` : ""}; got ${result.status}: ${output.trim()}`);
    }
  } else if (test.select || test.selectMutation) {
    if (casePassed) selectPassed += 1;
    else {
      selectFailed += 1;
      console.error(`${test.name}: expected exit ${test.exit}${test.contains ? ` containing ${test.contains}` : ""}; got ${result.status}: ${output.trim()}`);
    }
  } else if (test.contract) {
    if (casePassed) contractPassed += 1;
    else {
      contractFailed += 1;
      console.error(`${test.name}: expected exit ${test.exit}${test.contains ? ` containing ${test.contains}` : ""}; got ${result.status}: ${output.trim()}`);
    }
  } else if (casePassed) passed += 1;
  else {
    failed += 1;
    console.error(`${test.name}: expected exit ${test.exit}${test.contains ? ` containing ${test.contains}` : ""}; got ${result.status}: ${output.trim()}`);
  }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

if (manifestShapePassed) console.log("CAST_MANIFEST passed=1");
if (manifestFailed) process.exitCode = 1;
console.log(`CAST_LEDGER passed=${passed} failed=${failed}`);
console.log(`CAST_CONTRACT passed=${contractPassed} failed=${contractFailed}`);
console.log(`CAST_SELECT passed=${selectPassed} failed=${selectFailed}`);
if (failed || contractFailed || selectFailed) process.exitCode = 1;
