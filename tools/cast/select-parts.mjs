#!/usr/bin/env node
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const castFile = process.env.CAST_MANIFEST_FILE ?? path.join(root, "characters/_cast/cast.json");
const rulesFile = process.env.CAST_SELECTION_RULES_FILE ?? path.join(root, "characters/_cast/selection-rules.json");
const tokenDir = process.env.CAST_PORTRAIT_TOKEN_DIR ?? "E:/git/seoul-dengoku-wiki/public/portrait-tokens";
const detailDir = process.env.CAST_PERSON_DETAIL_DIR ?? "E:/git/seoul-dengoku-wiki/public/person-details";
const outputDir = process.env.CAST_SELECTION_OUTPUT_DIR ?? path.join(root, "characters/_cast/selection");

const personId = process.argv[2];
if (!personId) {
  console.error("Usage: node tools/cast/select-parts.mjs <personId>");
  process.exit(2);
}

const cast = await readJson(castFile);
const person = cast.people.find((entry) => entry.personId === personId);
if (!person) fail(`PersonUnknown:${personId}`, 2);
const rules = await readPersonInput(rulesFile);
const token = await readPersonInput(path.join(tokenDir, `${personId}.json`));
const detail = await readPersonInput(path.join(detailDir, `${personId}.json`));

const facts = {
  gender: person.gender,
  role: token.facts?.role,
  faction: detail.stateName,
  socialTier: rules.tierMap?.[detail.commonTier],
  workMode: rules.workModeByPerson?.[personId]
};
for (const fact of ["gender", "role", "faction", "socialTier", "workMode"]) {
  if (facts[fact] === undefined || facts[fact] === null || facts[fact] === "") {
    await emitBlocked(person, facts, `FactUnknown:${fact}`);
  }
}

const selections = [];
for (const [slot, family] of Object.entries(person.garmentFamilies)) {
  if (!family || slot === "carriedWeapons" || slot === "headwear") continue;
  const wikiText = person.slots[slot];
  if (!wikiText) await emitBlocked(person, facts, `ExplicitSlotMissing:${slot}`);
  if (slot === "hair") {
    const style = rules.hairDefaults[person.gender];
    selections.push({slot, family, source: style.source, palette: style.palette, material: style.material, wear: style.wear,
      ruleTrace: [{kind:"wiki-explicit", weight:rules.autoSelectWeight, text:wikiText, result:"wins"}]});
    continue;
  }
  const group = slot === "accessories" && family.startsWith("glasses_") ? "glasses" : slot === "accessories" ? "accessory" : slot;
  const candidates = rules.rules.filter((rule) => rule.group === group).map((rule, index) => score(rule, facts, index));
  const familyCandidates = candidates.filter((candidate) => candidate.family === family);
  if (!familyCandidates.length) await emitBlocked(person, facts, `RuleMissing:${group}:${family}`);
  const structureWords = await validateFamilyRules(family, familyCandidates, rules.familyStructureWords?.[family]);
  const styleGroups = groupStyleVariants(familyCandidates);
  const matchedGroups = styleGroups.filter((variant) => variant.keywords.some((keyword) => containsPhrase(wikiText, keyword)));
  if (matchedGroups.length > 1) await emitBlocked(person, facts, `VariantAmbiguous:${slot}:${family}`, {
    ruleTrace: [{kind:"variant-ambiguous", slot, family, text:wikiText, matched:matchedGroups.map((variant) => variant.keywords)}]
  });
  const matchedGroup = matchedGroups[0];
  const styleMatches = matchedGroup?.candidates ?? [];
  const styleMatch = styleMatches.sort((a, b) => b.weight - a.weight || b.priority - a.priority || a.index - b.index)[0];
  const styleCandidate = styleMatch
    ?? familyCandidates.filter((candidate) => !(candidate.wikiKeywords?.length)).sort((a, b) => b.weight - a.weight || b.priority - a.priority || a.index - b.index)[0]
    ?? familyCandidates.sort((a, b) => b.weight - a.weight || b.priority - a.priority || a.index - b.index)[0];
  const remainingTokens = remainingStyleTokens(wikiText, structureWords, styleCandidate.wikiKeywords);
  if (remainingTokens.length) await emitBlocked(person, facts, `StyleUnmatched:${slot}:${family}:${remainingTokens[0]}`, {
    ruleTrace: [{kind:"style-unmatched", slot, family, text:wikiText, selectedKeywords:styleCandidate.wikiKeywords, remainingTokens}]
  });
  if (!rules.allowedSources?.includes(styleCandidate.source)) await emitBlocked(person, facts, `SourceUnknown:${styleCandidate.source}`);
  const explicit = {family, weight: rules.autoSelectWeight, priority: Number.MAX_SAFE_INTEGER, index: -1};
  const winner = [explicit, ...candidates].find((candidate) => candidate.weight >= rules.autoSelectWeight)
    ?? candidates.sort((a, b) => b.priority - a.priority || b.weight - a.weight || a.index - b.index)[0];
  selections.push({
    slot,
    family: winner.family,
    source: styleCandidate.source,
    palette: styleCandidate.palette,
    material: styleCandidate.material,
    wear: styleCandidate.wear,
    cut: styleCandidate.cut,
    ruleTrace: [
      ...styleCandidate.trace,
      {kind:"style-variant", result:styleMatch ? "keyword-match" : (styleGroups.length > 1 ? "default-no-style-word" : "default-no-named-style"), matchedKeywords:matchedWikiKeywords(styleCandidate, wikiText)},
      {kind:"wiki-explicit", weight:explicit.weight, text:wikiText, family, result:"family-wins"},
      {kind:"selection", rule:"first-weight-at-least-100-else-max", selected: winner.family}
    ]
  });
}

const result = {schemaVersion:1, personId, name:person.name, folder:person.folder, facts, selections};
await mkdir(outputDir, {recursive:true});
await writeFile(path.join(outputDir, `${person.folder}.json`), `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result));

function score(rule, currentFacts, index) {
  let weight = rule.base;
  const trace = [{kind:"base", value:rule.base, priority:rule.priority, family:rule.family}];
  for (const modifier of rule.modifiers ?? []) {
    const { fact, value } = modifier.when;
    const matched = currentFacts[fact] === value;
    if (matched) {
      if (modifier.factor !== undefined) weight *= modifier.factor;
      if (modifier.add !== undefined) weight += modifier.add;
    }
    trace.push({kind:"modifier", fact, expected:value, actual:currentFacts[fact], matched, factor:modifier.factor ?? 1, add:modifier.add ?? 0, weight});
  }
  return {...rule, weight, index, trace};
}

async function validateFamilyRules(family, candidates, structureWords) {
  if (!Array.isArray(structureWords)) await emitBlocked(person, facts, `RulesInvalid:${family}:structureWords`);
  for (const candidate of candidates) {
    if (!Array.isArray(candidate.wikiKeywords)) await emitBlocked(person, facts, `RulesInvalid:${family}:wikiKeywords`);
  }
  return structureWords;
}

function matchedWikiKeywords(candidate, wikiText) {
  if (!candidate.wikiKeywords?.length) return [];
  return candidate.wikiKeywords.filter((keyword) => containsPhrase(wikiText, keyword));
}

function groupStyleVariants(candidates) {
  const groups = new Map();
  for (const candidate of candidates) {
    if (!candidate.wikiKeywords.length) continue;
    const signature = candidate.wikiKeywords.map((keyword) => tokenize(keyword).join(" ")).sort().join("|");
    const existing = groups.get(signature) ?? {keywords:candidate.wikiKeywords, candidates:[]};
    existing.candidates.push(candidate);
    groups.set(signature, existing);
  }
  return [...groups.values()];
}

function tokenize(text) {
  return (text.normalize("NFKC").toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).map(normalizeToken);
}

function normalizeToken(token) {
  if (!/[가-힣]/u.test(token)) return token;
  for (const suffix of ["으로", "의", "을", "를", "은", "는", "과", "와", "로"]) {
    if (token.length > suffix.length + 1 && token.endsWith(suffix)) return token.slice(0, -suffix.length);
  }
  return token;
}

function containsPhrase(text, phrase) {
  const tokens = tokenize(text);
  const wanted = tokenize(phrase);
  if (!wanted.length || wanted.length > tokens.length) return false;
  const compact = wanted.join("");
  return tokens.some((token, index) => token === compact || wanted.every((part, offset) => tokens[index + offset] === part));
}

function remainingStyleTokens(text, structureWords, selectedKeywords) {
  let tokens = tokenize(text);
  for (const phrase of [...selectedKeywords, ...structureWords]) tokens = removePhrase(tokens, tokenize(phrase));
  return tokens;
}

function removePhrase(tokens, phrase) {
  if (!phrase.length) return tokens;
  const compact = phrase.join("");
  const result = [];
  for (let index = 0; index < tokens.length;) {
    if (tokens[index] === compact) { index += 1; continue; }
    if (phrase.every((part, offset) => tokens[index + offset] === part)) { index += phrase.length; continue; }
    result.push(tokens[index]);
    index += 1;
  }
  return result;
}

async function emitBlocked(currentPerson, currentFacts, blocked, extra = {}) {
  const result = {schemaVersion:1, personId, name:currentPerson.name, folder:currentPerson.folder, facts:currentFacts, blocked, ...extra};
  await mkdir(outputDir, {recursive:true});
  await writeFile(path.join(outputDir, `${currentPerson.folder}.json`), `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify(result));
  process.exit(1);
}

async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

async function readPersonInput(file) {
  try { return await readJson(file); }
  catch (error) { await emitBlocked(person, {}, `InputReadFailed:${file}:${error.message}`); }
}
function fail(message, code = 1) { console.error(message); process.exit(code); }
