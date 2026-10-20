import { it, expect } from 'vitest';
import { gzipSync, gunzipSync } from 'node:zlib';
import { transcodeSpz, SplatFileType } from '@sparkjsdev/spark';

it('Spark dekodiert SPZ-RUB direkt, einschließlich SH3 und v3 Quaternion', async () => {
  const header = Buffer.alloc(16);
  header.writeUInt32LE(0x5053474e, 0);
  header.writeUInt32LE(3, 4);
  header.writeUInt32LE(1, 8);
  header[12] = 3; header[13] = 12;
  const body = Buffer.alloc(65);
  [4096, -8192, -12288].forEach((v, i) => body.writeUIntLE(v & 0xffffff, i * 3, 3));
  body[9] = 230; body.fill(141, 10, 13); body.fill(86, 13, 16);
  body.writeUInt32LE(0, 16); // RUB Rx(pi): largest quaternion component x=1.
  body.fill(128, 20); body[20] = 154;
  const out = await transcodeSpz({ inputs: [{ fileBytes: gzipSync(Buffer.concat([header, body])),
    fileType: SplatFileType.SPZ }], maxSh: 3 });
  const encoded: unknown = out.fileBytes;
  if (!(encoded instanceof Uint8Array)) throw new Error('Spark returned no bytes');
  const raw = gunzipSync(encoded);
  const position = [0, 1, 2].map((axis) => {
    const v = raw.readUIntLE(16 + axis * 3, 3);
    return ((v & 0x800000) ? v - 0x1000000 : v) / 4096;
  });
  expect(position).toEqual([1, -2, -3]);
  expect(raw[12]).toBe(3);
  const shStart = 16 + 9 + 1 + 3 + 3 + (raw.readUInt32LE(4) === 2 ? 3 : 4);
  expect(Math.abs(raw[shStart]! - 154)).toBeLessThanOrEqual(4);
  expect(raw[shStart + 44]).toBe(128);
});
