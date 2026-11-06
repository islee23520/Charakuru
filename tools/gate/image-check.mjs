#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { decodePng } from "./png.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const profilesPath = process.env.GATE_PROFILES_FILE ?? path.join(root, "tools/gate/profiles.json");

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
    if (out[key] !== undefined) throw new Error(`DuplicateArgument:${key}`);
    out[key] = tokens[++i];
  }
  return out;
}

function backgroundOf(image, spreadLimit) {
  const { width, height, channels, pixels, colorType } = image;
  const step = Math.max(1, Math.floor(Math.min(width, height) / 48));
  const samples = [];
  const take = (x, y) => {
    const index = (y * width + x) * channels;
    samples.push([pixels[index], pixels[index + 1] ?? 0, pixels[index + 2] ?? pixels[index]]);
  };
  for (let x = 0; x < width; x += step) {
    take(x, 0);
    take(x, height - 1);
  }
  for (let y = 0; y < height; y += step) {
    take(0, y);
    take(width - 1, y);
  }
  const average = [0, 0, 0];
  for (const sample of samples) {
    average[0] += sample[0];
    average[1] += sample[1];
    average[2] += sample[2];
  }
  average[0] /= samples.length;
  average[1] /= samples.length;
  average[2] /= samples.length;
  let spread = 0;
  for (const sample of samples) {
    spread = Math.max(spread, Math.abs(sample[0] - average[0]) + Math.abs(sample[1] - average[1]) + Math.abs(sample[2] - average[2]));
  }
  const alpha = colorType === 4 || colorType === 6;
  return { average, spread, plain: spread <= spreadLimit, alpha };
}

function subjectOf(image, background, distance) {
  const { width, height, channels, pixels, colorType } = image;
  const alpha = colorType === 4 || colorType === 6;
  const left = new Int32Array(height);
  const right = new Int32Array(height);
  left.fill(-1);
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  let count = 0;
  for (let y = 0; y < height; y += 1) {
    let rowLeft = -1;
    let rowRight = -1;
    const row = y * width * channels;
    for (let x = 0; x < width; x += 1) {
      const index = row + x * channels;
      let foreground = false;
      if (alpha) {
        foreground = pixels[index + channels - 1] > 16;
      } else {
        const delta = Math.abs(pixels[index] - background[0])
          + Math.abs(pixels[index + 1] - background[1])
          + Math.abs((pixels[index + 2] ?? pixels[index]) - background[2]);
        foreground = delta > distance;
      }
      if (!foreground) continue;
      count += 1;
      if (rowLeft < 0) rowLeft = x;
      rowRight = x;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    left[y] = rowLeft;
    right[y] = rowRight;
  }
  if (count === 0) return null;
  return { minX, minY, maxX, maxY, width: maxX - minX + 1, height: maxY - minY + 1, count, left, right };
}

function rowWidth(subject, y) {
  return subject.left[y] < 0 ? 0 : subject.right[y] - subject.left[y] + 1;
}

function armSpanOf(subject) {
  let span = 0;
  let spanAt = subject.minY;
  for (let y = subject.minY; y <= subject.maxY; y += 1) {
    const width = rowWidth(subject, y);
    if (width > span) {
      span = width;
      spanAt = y;
    }
  }
  const height = subject.maxY - subject.minY + 1;
  return { span, height, spanAt, ratio: height > 0 ? span / height : 0 };
}

function checkView(name, image, profile) {
  const blocking = [];
  const longSide = Math.max(image.width, image.height);
  if (longSide < profile.longSideMin) blocking.push(`LongSide:${name}:${longSide}<${profile.longSideMin}`);
  const background = backgroundOf(image, profile.plainBackgroundSpread);
  if (!background.alpha && !background.plain) blocking.push(`BackgroundNotPlain:${name}`);
  const subject = subjectOf(image, background.average, profile.backgroundDistance);
  if (!subject) {
    blocking.push(`SubjectMissing:${name}`);
    return { name, blocking, sha256: null };
  }
  const heightFraction = subject.height / image.height;
  if (heightFraction < profile.subjectHeightMin || heightFraction > profile.subjectHeightMax) {
    blocking.push(`SubjectHeight:${name}:${heightFraction.toFixed(3)}`);
  }
  const center = (subject.minX + subject.maxX) / 2 / image.width;
  const offset = Math.abs(center - 0.5);
  if (offset > profile.centerTolerance) blocking.push(`SubjectOffCenter:${name}:${offset.toFixed(3)}`);
  return {
    name,
    blocking,
    width: image.width,
    height: image.height,
    colorType: image.colorType,
    alpha: background.alpha,
    plainBackground: background.plain,
    bbox: [subject.minX, subject.minY, subject.maxX, subject.maxY],
    heightFraction: Number(heightFraction.toFixed(4)),
    centerOffset: Number(offset.toFixed(4)),
    subject,
  };
}

function pairWidth(views, a, b, tolerance) {
  const left = views.find((view) => view.name === a);
  const right = views.find((view) => view.name === b);
  if (!left?.bbox || !right?.bbox) return [];
  const leftWidth = left.bbox[2] - left.bbox[0] + 1;
  const rightWidth = right.bbox[2] - right.bbox[0] + 1;
  const delta = Math.abs(leftWidth - rightWidth) / Math.max(leftWidth, rightWidth);
  if (delta > tolerance) return [`BboxWidth:${a}/${b}:${delta.toFixed(3)}`];
  return [];
}

export async function checkImages(dir, kind, profile) {
  const expected = profile.kinds[kind];
  if (!expected) throw new Error(`UnknownKind:${kind}`);
  const entries = await readdir(dir);
  const present = new Set();
  for (const name of entries) {
    if ((await stat(path.join(dir, name))).isFile()) present.add(name);
  }
  const blocking = [];
  const missing = expected.files.filter((name) => !present.has(name));
  const extra = [...present].filter((name) => !expected.files.includes(name)).sort();
  if (missing.length || extra.length) {
    blocking.push(`ViewSetIncomplete:${missing.join(",") || "-"}:${extra.join(",") || "-"}`);
  }
  const files = [];
  const views = [];
  for (const name of expected.files) {
    if (!present.has(name)) continue;
    const filePath = path.join(dir, name);
    const bytes = await readFile(filePath);
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    let image;
    try {
      image = decodePng(bytes);
    } catch (error) {
      if (error.message !== "PngDecode") throw error;
      blocking.push(`PngDecode:${name}`);
      files.push({ name, sha256, decoded: false });
      continue;
    }
    const view = checkView(name, image, profile);
    blocking.push(...view.blocking);
    const { subject, ...reported } = view;
    files.push({ ...reported, sha256, decoded: true });
    views.push({ ...view, sha256 });
  }
  if (views.length === expected.files.length && expected.files.length === 4) {
    blocking.push(...pairWidth(views, "front.png", "back.png", profile.pairWidthTolerance));
    blocking.push(...pairWidth(views, "left.png", "right.png", profile.pairWidthTolerance));
  }
  if ((kind === "body" || kind === "dressed") && views.length === expected.files.length) {
    for (const name of ["front.png", "back.png"]) {
      const view = views.find((entry) => entry.name === name);
      if (!view?.subject) continue;
      const measured = armSpanOf(view.subject);
      files.find((entry) => entry.name === name).armSpan = {
        span: measured.span,
        height: measured.height,
        spanAt: measured.spanAt,
        ratio: Number(measured.ratio.toFixed(3)),
      };
      if (measured.ratio < profile.armSpanRatio) blocking.push(`ArmSpan:${name}:${measured.ratio.toFixed(3)}<${profile.armSpanRatio}`);
    }
  }
  if (kind === "head") {
    for (const view of views) {
      if (!view.subject) continue;
      const chin = Math.floor(view.height * profile.headChinFraction);
      const limit = chin + profile.headChinSlackPx;
      let below = 0;
      for (let y = limit + 1; y < view.height; y += 1) {
        if (view.subject.left[y] >= 0) below += 1;
      }
      files.find((entry) => entry.name === view.name).chin = { line: chin, rowsBelow: below };
      if (below > 0) blocking.push(`ChinLine:${view.name}:${below}`);
    }
  }
  return {
    ok: blocking.length === 0,
    kind,
    dir: path.resolve(dir),
    decoder: profile.decoder,
    files,
    blocking,
  };
}

async function main() {
  const parsed = options(process.argv.slice(2));
  const [dir, outPath] = parsed._;
  if (!parsed.kind || !dir || !outPath) throw new Error("Usage:image-check --kind head|body|dressed|single_part <dir> <out.json>");
  const profile = JSON.parse(await readFile(profilesPath, "utf8"));
  const report = await checkImages(dir, parsed.kind, profile);
  await writeFile(outPath, `${JSON.stringify(report, null, 2)}\n`);
  if (!report.ok) {
    console.error(report.blocking.join("\n"));
    process.exitCode = 1;
    return;
  }
  console.log(JSON.stringify({ ok: true, kind: report.kind, files: report.files.length }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
