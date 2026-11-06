import { inflateSync } from "node:zlib";

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

export function decodePng(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 8 || !buffer.subarray(0, 8).equals(SIGNATURE)) {
    throw new Error("PngDecode");
  }
  let offset = 8;
  let width;
  let height;
  let bitDepth;
  let colorType;
  const idat = [];
  let ended = false;
  while (offset + 8 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    offset += 4;
    const type = buffer.toString("ascii", offset, offset + 4);
    offset += 4;
    if (offset + length + 4 > buffer.length) throw new Error("PngDecode");
    const data = buffer.subarray(offset, offset + length);
    offset += length;
    offset += 4;
    if (type === "IHDR") {
      if (length < 13) throw new Error("PngDecode");
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      if (data[12] !== 0) throw new Error("PngDecode");
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      ended = true;
      break;
    }
  }
  if (!ended || !width || !height || bitDepth !== 8 || CHANNELS[colorType] === undefined || idat.length === 0) {
    throw new Error("PngDecode");
  }
  let inflated;
  try {
    inflated = inflateSync(Buffer.concat(idat));
  } catch {
    throw new Error("PngDecode");
  }
  const channels = CHANNELS[colorType];
  const stride = width * channels;
  if (inflated.length !== height * (stride + 1)) throw new Error("PngDecode");
  const pixels = Buffer.alloc(height * stride);
  const previous = Buffer.alloc(stride);
  const current = Buffer.alloc(stride);
  let source = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = inflated[source];
    source += 1;
    const row = inflated.subarray(source, source + stride);
    source += stride;
    if (filter > 4) throw new Error("PngDecode");
    for (let i = 0; i < stride; i += 1) {
      const raw = row[i];
      const left = i >= channels ? current[i - channels] : 0;
      const up = previous[i];
      const upLeft = i >= channels ? previous[i - channels] : 0;
      let value = raw;
      if (filter === 1) value = (raw + left) & 255;
      else if (filter === 2) value = (raw + up) & 255;
      else if (filter === 3) value = (raw + ((left + up) >> 1)) & 255;
      else if (filter === 4) value = (raw + paeth(left, up, upLeft)) & 255;
      current[i] = value;
    }
    current.copy(pixels, y * stride);
    current.copy(previous);
  }
  return { width, height, colorType, channels, pixels };
}
