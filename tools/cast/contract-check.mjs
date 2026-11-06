#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function fail(code) {
  console.error(code);
  process.exit(1);
}

function option(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

const input = process.argv[2];
const type = option("--type");
const requestedSlot = option("--slot");
if (!input || !type || !requestedSlot) {
  console.error("usage: node tools/cast/contract-check.mjs <glb|vrm> --type male|female --slot <id>");
  process.exit(2);
}

let contract;
try {
  contract = JSON.parse(await readFile(path.join(root, "characters/_cast/contract.json"), "utf8"));
} catch (error) {
  fail(`ContractUnreadable:${error.message}`);
}

if (!contract.portrait_types?.[type]) fail(`PortraitTypeMissing:${type}`);

const boneNames = contract.bones?.map((bone) => bone.node_name) ?? [];
const duplicateBone = boneNames.find((name, index) => !name || boneNames.indexOf(name) !== index);
if (duplicateBone !== undefined) fail(`BoneDeclaredTwice:${duplicateBone ?? ""}`);
const knownBones = new Set(boneNames);

const slotRows = contract.slots ?? [];
const duplicateContractSlot = slotRows.find((slot, index) => !slot.id || slotRows.findIndex((candidate) => candidate.id === slot.id) !== index);
if (duplicateContractSlot) fail(`SlotDeclaredTwice:${duplicateContractSlot.id ?? ""}`);
const slot = requestedSlot === "body" ? null : slotRows.find((candidate) => candidate.id === requestedSlot);
if (requestedSlot !== "body" && !slot) fail(`SlotMissing:${requestedSlot}`);

let bytes;
try {
  bytes = await readFile(input);
} catch (error) {
  fail(`InputUnreadable:${error.message}`);
}
if (bytes.length < 12) fail("TruncatedGLB");
if (bytes.toString("utf8", 0, 4) !== "glTF") fail("InvalidGLBMagic");
if (bytes.readUInt32LE(4) !== 2) fail(`UnsupportedGLBVersion:${bytes.readUInt32LE(4)}`);
if (bytes.readUInt32LE(8) !== bytes.length) fail(`GLBLengthMismatch:${bytes.readUInt32LE(8)}!=${bytes.length}`);

let json;
let binary;
let offset = 12;
while (offset < bytes.length) {
  if (offset + 8 > bytes.length) fail("TruncatedGLBChunkHeader");
  const length = bytes.readUInt32LE(offset);
  const kind = bytes.readUInt32LE(offset + 4);
  const start = offset + 8;
  const end = start + length;
  if (end > bytes.length) fail("TruncatedGLBChunk");
  if (kind === 0x4e4f534a) {
    if (json) fail("DuplicateJSONChunk");
    try {
      json = JSON.parse(bytes.subarray(start, end).toString("utf8"));
    } catch (error) {
      fail(`InvalidJSONChunk:${error.message}`);
    }
  } else if (kind === 0x004e4942) {
    if (binary) fail("DuplicateBINChunk");
    binary = bytes.subarray(start, end);
  }
  offset = end;
}
if (!json) fail("MissingJSONChunk");

if (requestedSlot !== "body") {
  const declarations = json.asset?.extras?.cast_slots ?? json.extras?.cast_slots;
  if (!Array.isArray(declarations)) fail(`SlotMissing:${requestedSlot}`);
  const matches = declarations.filter((value) => value === requestedSlot);
  if (matches.length === 0) fail(`SlotMissing:${requestedSlot}`);
  if (matches.length > 1 || new Set(declarations).size !== declarations.length) fail(`DuplicateSlot:${requestedSlot}`);
  const hideGroups = new Set(json.asset?.extras?.hide_groups ?? json.extras?.hide_groups ?? []);
  for (const group of slot.hides) if (!hideGroups.has(group)) fail(`HideGroupMissing:${group}`);
}

const componentBytes = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
const componentCounts = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

function readComponent(buffer, position, componentType) {
  if (componentType === 5120) return buffer.readInt8(position);
  if (componentType === 5121) return buffer.readUInt8(position);
  if (componentType === 5122) return buffer.readInt16LE(position);
  if (componentType === 5123) return buffer.readUInt16LE(position);
  if (componentType === 5125) return buffer.readUInt32LE(position);
  if (componentType === 5126) return buffer.readFloatLE(position);
  fail(`AccessorComponentTypeUnsupported:${componentType}`);
}

function accessor(index) {
  const value = json.accessors?.[index];
  if (!value) fail(`AccessorMissing:${index}`);
  if (value.sparse) fail(`SparseAccessorUnsupported:${index}`);
  const view = json.bufferViews?.[value.bufferView];
  if (!view) fail(`BufferViewMissing:${value.bufferView}`);
  if (view.buffer !== 0 || !binary) fail(`AccessorBufferUnsupported:${index}`);
  const width = componentBytes[value.componentType];
  const count = componentCounts[value.type];
  if (!width || !count) fail(`AccessorShapeUnsupported:${index}`);
  const stride = view.byteStride ?? width * count;
  const start = (view.byteOffset ?? 0) + (value.byteOffset ?? 0);
  const end = start + Math.max(0, value.count - 1) * stride + width * count;
  if (end > binary.length) fail(`AccessorOutOfBounds:${index}`);
  return Array.from({ length: value.count }, (_, row) =>
    Array.from({ length: count }, (_, column) => readComponent(binary, start + row * stride + column * width, value.componentType)),
  );
}

const meshSkins = new Map();
for (const node of json.nodes ?? []) {
  if (node.mesh === undefined || node.skin === undefined) continue;
  if (meshSkins.has(node.mesh) && meshSkins.get(node.mesh) !== node.skin) fail(`MeshSkinAmbiguous:${node.mesh}`);
  meshSkins.set(node.mesh, node.skin);
}

const allowed = requestedSlot === "body" ? knownBones : new Set(slot.bone_whitelist);
let skinnedPrimitiveCount = 0;
for (let meshIndex = 0; meshIndex < (json.meshes?.length ?? 0); meshIndex += 1) {
  const skinIndex = meshSkins.get(meshIndex) ?? (json.skins?.length === 1 ? 0 : undefined);
  for (const primitive of json.meshes[meshIndex].primitives ?? []) {
    const attributes = primitive.attributes ?? {};
    const influenceSets = new Set();
    for (const name of Object.keys(attributes)) {
      const match = /^(JOINTS|WEIGHTS)_(\d+)$/.exec(name);
      if (match) influenceSets.add(Number(match[2]));
      else if (name.startsWith("JOINTS_") || name.startsWith("WEIGHTS_")) fail(`InfluenceSetLayout:${name}`);
    }
    if (influenceSets.size === 0) continue;
    if (skinIndex === undefined) fail(`MeshSkinMissing:${meshIndex}`);
    const skin = json.skins?.[skinIndex];
    if (!skin) fail(`SkinMissing:${skinIndex}`);
    skinnedPrimitiveCount += 1;
    for (const set of [...influenceSets].sort((a, b) => a - b)) {
      const jointsIndex = attributes[`JOINTS_${set}`];
      const weightsIndex = attributes[`WEIGHTS_${set}`];
      if (jointsIndex === undefined || weightsIndex === undefined) fail(`InfluenceSetMismatch:${set}`);
      const jointsAccessor = json.accessors?.[jointsIndex];
      const weightsAccessor = json.accessors?.[weightsIndex];
      if (!jointsAccessor || !weightsAccessor
        || ![5121, 5123].includes(jointsAccessor.componentType)
        || ![5121, 5123, 5126].includes(weightsAccessor.componentType)
        || jointsAccessor.type !== "VEC4" || weightsAccessor.type !== "VEC4"
        || jointsAccessor.count !== weightsAccessor.count) fail(`InfluenceSetMismatch:${set}`);
      const joints = accessor(jointsIndex);
      const weights = accessor(weightsIndex);
      if (joints.length !== weights.length || joints.some((row, index) => row.length !== weights[index].length)) fail(`InfluenceSetMismatch:${set}`);
      for (let vertex = 0; vertex < joints.length; vertex += 1) {
        for (let influence = 0; influence < joints[vertex].length; influence += 1) {
          if (!(weights[vertex][influence] > 0)) continue;
          const jointNode = skin.joints?.[joints[vertex][influence]];
          const name = json.nodes?.[jointNode]?.name;
          if (!name || !knownBones.has(name)) fail(`UnknownBone:${name ?? jointNode ?? ""}`);
          if (!allowed.has(name)) fail(`BoneOutsideWhitelist:${name}`);
        }
      }
    }
  }
}
if (skinnedPrimitiveCount === 0) fail("MeshSkinMissing");

console.log(`CAST_CONTRACT_OK type=${type} slot=${requestedSlot} skinned_primitives=${skinnedPrimitiveCount}`);
