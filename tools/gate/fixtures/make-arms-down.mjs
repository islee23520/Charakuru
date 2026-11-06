import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { deflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { decodePng } from "../png.mjs";

const DISTANCE = 48;

function crc32(buffer) {
  let crc = 0xffffffff;
  for (let index = 0; index < buffer.length; index += 1) {
    crc ^= buffer[index];
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const name = Buffer.from(type);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, crc]);
}

export function encodePng(width, height, rgb) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const raw = Buffer.alloc(height * (width * 3 + 1));
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 3 + 1)] = 0;
    rgb.copy(raw, y * (width * 3 + 1) + 1, y * width * 3, (y + 1) * width * 3);
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function borderAverage(image) {
  const { width, height, channels, pixels } = image;
  const step = Math.max(1, Math.floor(Math.min(width, height) / 48));
  const samples = [];
  const take = (x, y) => {
    const index = (y * width + x) * channels;
    samples.push([pixels[index], pixels[index + 1], pixels[index + 2]]);
  };
  for (let x = 0; x < width; x += step) {
    take(x, 0);
    take(x, height - 1);
  }
  for (let y = 0; y < height; y += step) {
    take(0, y);
    take(width - 1, y);
  }
  return [0, 1, 2].map((channel) => samples.reduce((sum, sample) => sum + sample[channel], 0) / samples.length);
}

function silhouette(image, background) {
  const { width, height, channels, pixels } = image;
  const foregroundAt = (index) => Math.abs(pixels[index] - background[0])
    + Math.abs(pixels[index + 1] - background[1])
    + Math.abs(pixels[index + 2] - background[2]) > DISTANCE;
  const rows = [];
  let minY = height;
  let maxY = -1;
  for (let y = 0; y < height; y += 1) {
    let left = -1;
    let right = -1;
    const row = y * width * channels;
    for (let x = 0; x < width; x += 1) {
      if (!foregroundAt(row + x * channels)) continue;
      if (left < 0) left = x;
      right = x;
    }
    rows.push(left < 0 ? null : [left, right]);
    if (left >= 0) {
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  return { rows, minY, maxY, foregroundAt };
}

function armsOf(image, background) {
  const { width, channels, pixels } = image;
  const { rows, minY, maxY, foregroundAt } = silhouette(image, background);
  const span = maxY - minY + 1;
  const torsoStart = minY + Math.floor(span * 0.45);
  const torsoEnd = minY + Math.floor(span * 0.75);
  const torsoWidths = [];
  let centerSum = 0;
  let centerCount = 0;
  for (let y = torsoStart; y <= torsoEnd; y += 1) {
    if (!rows[y]) continue;
    torsoWidths.push(rows[y][1] - rows[y][0] + 1);
    centerSum += (rows[y][0] + rows[y][1]) / 2;
    centerCount += 1;
  }
  torsoWidths.sort((a, b) => a - b);
  const torsoWidth = torsoWidths[Math.floor(torsoWidths.length / 2)];
  const center = centerSum / centerCount;
  const cut = torsoWidth / 2 * 0.9;
  const arms = { left: [], right: [] };
  for (let y = minY; y < torsoStart; y += 1) {
    if (!rows[y] || rows[y][1] - rows[y][0] + 1 <= torsoWidth * 1.5) continue;
    const row = y * width * channels;
    for (let x = 0; x < width; x += 1) {
      if (!foregroundAt(row + x * channels)) continue;
      if (x < center - cut) arms.left.push([x, y]);
      else if (x > center + cut) arms.right.push([x, y]);
    }
  }
  return { arms, pixels };
}

// The arms of a T-pose extend horizontally out from the torso, so they are the
// foreground of the wide rows above the torso. `erase` paints them out with the
// border colour. `down` and `thick` rotate each arm down beside the torso; `thick`
// repeats the sleeve outward so a heavy sleeve still reads as arms down.
export function armsDownPng(buffer, mode) {
  const image = decodePng(buffer);
  if (image.colorType !== 2) throw new Error(`ArmsDownSource:${image.colorType}`);
  const { width, height, channels, pixels } = image;
  const background = borderAverage(image);
  const { arms } = armsOf(image, background);
  const rgb = Buffer.from(pixels);
  const fill = background.map((value) => Math.round(value));
  const clear = (x, y) => {
    const index = (y * width + x) * channels;
    rgb[index] = fill[0];
    rgb[index + 1] = fill[1];
    rgb[index + 2] = fill[2];
  };
  if (mode === "erase") {
    for (const point of [...arms.left, ...arms.right]) clear(point[0], point[1]);
    return encodePng(width, height, rgb);
  }
  const extra = mode === "thick" ? 0.6 : 0;
  const paste = [];
  for (const side of ["left", "right"]) {
    const points = arms[side];
    if (points.length === 0) continue;
    const attachX = side === "left" ? Math.max(...points.map((point) => point[0])) : Math.min(...points.map((point) => point[0]));
    const armTop = Math.min(...points.map((point) => point[1]));
    const armBottom = Math.max(...points.map((point) => point[1]));
    const grow = Math.round((armBottom - armTop + 1) * extra);
    for (const [x, y] of points) {
      const outward = y - armTop;
      const down = side === "left" ? attachX - x : x - attachX;
      const base = side === "left" ? attachX - outward : attachX + outward;
      for (let step = 0; step <= grow; step += 1) {
        const rx = side === "left" ? base - step : base + step;
        const ry = armBottom + down;
        if (rx >= 0 && rx < width && ry >= 0 && ry < height) paste.push([rx, ry, x, y]);
      }
    }
  }
  for (const point of [...arms.left, ...arms.right]) clear(point[0], point[1]);
  for (const [rx, ry, sx, sy] of paste) {
    const source = (sy * width + sx) * channels;
    const target = (ry * width + rx) * channels;
    rgb[target] = pixels[source];
    rgb[target + 1] = pixels[source + 1];
    rgb[target + 2] = pixels[source + 2];
  }
  return encodePng(width, height, rgb);
}

export function scalePng(buffer, factor) {
  const image = decodePng(buffer);
  const { width, height, channels, pixels } = image;
  const scaledWidth = Math.round(width * factor);
  const scaledHeight = Math.round(height * factor);
  const rgb = Buffer.alloc(scaledWidth * scaledHeight * 3);
  for (let y = 0; y < scaledHeight; y += 1) {
    for (let x = 0; x < scaledWidth; x += 1) {
      const sx = Math.min(width - 1, Math.floor(x / factor));
      const sy = Math.min(height - 1, Math.floor(y / factor));
      const source = (sy * width + sx) * channels;
      const target = (y * scaledWidth + x) * 3;
      rgb[target] = pixels[source];
      rgb[target + 1] = pixels[source + 1] ?? pixels[source];
      rgb[target + 2] = pixels[source + 2] ?? pixels[source];
    }
  }
  return encodePng(scaledWidth, scaledHeight, rgb);
}

async function main() {
  const [source, outFile, mode] = process.argv.slice(2);
  if (!source || !outFile || !mode) throw new Error("Usage:make-arms-down <source.png> <out.png> <erase|down|thick|scale>");
  const buffer = await readFile(source);
  const output = mode === "scale" ? scalePng(buffer, 0.5) : armsDownPng(buffer, mode);
  await mkdir(path.dirname(path.resolve(outFile)), { recursive: true });
  await writeFile(outFile, output);
  console.log(JSON.stringify({ mode, out: path.resolve(outFile), bytes: output.length }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
