#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const manifestPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root, "characters/_cast/cast.json");
const requiredFields = ["personId", "name", "gender", "folder", "portrait", "slots", "garmentFamilies"];
const requiredSlots = ["hair", "upper", "lower", "footwear", "headwear", "accessories", "carriedWeapons"];

function fail(reason) {
  console.error(reason);
  process.exitCode = 1;
}

try {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  if (!Array.isArray(manifest.people)) throw new Error("CastPeopleRequired");
  if (manifest.people.length !== 7) throw new Error(`CastEntryCount:${manifest.people.length}`);
  const genders = { male: 0, female: 0 };
  const folders = new Set();
  for (const person of manifest.people) {
    for (const field of requiredFields) {
      if (!(field in person)) throw new Error(`CastFieldMissing:${field}`);
    }
    if (person.gender !== "male" && person.gender !== "female") throw new Error(`CastGenderInvalid:${person.personId}`);
    genders[person.gender] += 1;
    if (folders.has(person.folder)) throw new Error(`CastFolderDuplicate:${person.folder}`);
    folders.add(person.folder);
    for (const slot of requiredSlots) {
      if (!Object.hasOwn(person.slots, slot)) throw new Error(`CastSlotMissing:${slot}:${person.personId}`);
      const value = person.slots[slot];
      if (value !== null && (typeof value !== "string" || value.trim() === "")) {
        throw new Error(`CastSlotInvalid:${slot}:${person.personId}`);
      }
      if (!(slot in person.garmentFamilies)) throw new Error(`CastFamilyMissing:${slot}:${person.personId}`);
    }
    if (!path.isAbsolute(person.portrait)) throw new Error(`CastPortraitNotAbsolute:${person.personId}`);
    await readFile(person.portrait);
  }
  if (genders.male !== 5 || genders.female !== 2) throw new Error(`CastGenderCounts:${genders.male}male/${genders.female}female`);
  const pilot = manifest.people.find((person) => person.personId === "person-0998");
  if (!pilot?.slots.accessories?.includes("Large perfectly round clear eyeglass lenses in thin copper metal frames")) {
    throw new Error("CastPilotGlassesMissing");
  }
  console.log("CAST_MANIFEST passed=1");
} catch (error) {
  fail(error.message);
}
