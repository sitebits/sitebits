import { expect, it } from 'vitest';
import { transcodeSpz, SplatFileType } from '@sparkjsdev/spark';
import { gunzipSync } from 'node:zlib';
import { syntheticSplatRad } from './fixtures/splats.mjs';

it('Spark independently decodes synthetic RAD parent and leaves in package axes', async () => {
  const files = syntheticSplatRad();
  const out = await transcodeSpz({ inputs: [{
    fileBytes: Buffer.concat([files['model.rad']!, files['model-0.radc']!]),
    fileType: SplatFileType.RAD }], maxSh: 3 });
  const bytes: unknown = out.fileBytes;
  if (!(bytes instanceof Uint8Array)) throw new Error('No decoded output');
  const raw = gunzipSync(bytes);
  expect(raw.readUInt32LE(8)).toBe(1025);
  const point = (index: number) => [0, 1, 2].map((axis) => {
    const v = raw.readUIntLE(16 + index * 9 + axis * 3, 3);
    return ((v & 0x800000) ? v - 0x1000000 : v) / 4096;
  });
  expect(point(0)).toEqual([0, 0, 0]);
  expect(point(1)).toEqual([-1, -1, 0]);
  expect(point(1024)).toEqual([0.9375, 0.9375, 0]);
});
