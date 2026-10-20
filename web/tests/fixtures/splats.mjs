import { gzipSync } from 'node:zlib';

// Synthetische 3DGS-Punkte: keine externen Daten. In Tests zur Laufzeit erzeugen.
export function syntheticSplatPly() {
  const names = ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
    'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3'];
  const header = Buffer.from(`ply\nformat binary_little_endian 1.0\nelement vertex 1024\n${names.map((n) => `property float ${n}\n`).join('')}end_header\n`);
  const body = Buffer.alloc(1024 * 56);
  for (let i = 0; i < 1024; i++) {
    const values = [(i % 32) / 16 - 1, Math.floor(i / 32) / 16 - 1, 0,
      1.4, 0.2, -0.5, 3, Math.log(0.06), Math.log(0.06), Math.log(0.02), 1, 0, 0, 0];
    values.forEach((v, j) => body.writeFloatLE(v, i * 56 + j * 4));
  }
  return Buffer.concat([header, body]);
}

export function syntheticSplatSpz() {
  const n = 1024;
  const header = Buffer.alloc(16);
  header.writeUInt32LE(0x5053474e); header.writeUInt32LE(3, 4); header.writeUInt32LE(n, 8);
  header[12] = 3; header[13] = 12;
  const positions = Buffer.alloc(n * 9);
  for (let i = 0; i < n; i++) {
    const xyz = [(i % 32) / 16 - 1, 1 - Math.floor(i / 32) / 16, 0];
    xyz.forEach((v, axis) => positions.writeUIntLE(Math.round(v * 4096) & 0xffffff, i * 9 + axis * 3, 3));
  }
  const sh = Buffer.alloc(n * 45, 128);
  for (let i = 0; i < n; i++) sh[i * 45] = 154;
  return gzipSync(Buffer.concat([header, positions, Buffer.alloc(n, 230),
    Buffer.alloc(n * 3, 141), Buffer.alloc(n * 3, 112), Buffer.alloc(n * 4), sh]));
}

// One synthetic LoD parent and 1024 leaves; RAD v1 property layout from Spark rad.rs.
export function syntheticSplatRad() {
  const n = 1025, props = [], parts = [];
  let offset = 0;
  function property(name, encoding, raw) {
    props.push({ property: name, encoding, offset, bytes: raw.length });
    parts.push(raw, Buffer.alloc((-raw.length >>> 0) % 8));
    offset += Math.ceil(raw.length / 8) * 8;
  }
  function floats(name, dims, fn) {
    const raw = Buffer.alloc(n * dims * 4);
    for (let axis = 0; axis < dims; axis++) for (let i = 0; i < n; i++)
      raw.writeFloatLE(fn(i, axis), (axis * n + i) * 4);
    property(name, 'f32', raw);
  }
  floats('center', 3, (i, axis) => i === 0 || axis === 2 ? 0 :
    axis === 0 ? ((i - 1) % 32) / 16 - 1 : Math.floor((i - 1) / 32) / 16 - 1);
  floats('alpha', 1, () => 0.95);
  floats('rgb', 3, (_i, axis) => [0.9, 0.4, 0.1][axis]);
  floats('scales', 3, (i, axis) => i === 0 ? 1 : axis === 2 ? 0.02 : 0.06);
  floats('orientation', 3, () => 0);
  floats('sh1', 9, (_i, axis) => axis === 0 ? 0.2 : 0);
  floats('sh2', 15, () => 0);
  floats('sh3', 21, () => 0);
  const counts = Buffer.alloc(n * 2); counts.writeUInt16LE(1024);
  const starts = Buffer.alloc(n * 4); starts.writeUInt32LE(1);
  property('child_count', 'u16', counts); property('child_start', 'u32', starts);
  function header(magic, meta) {
    const json = Buffer.from(JSON.stringify(meta));
    const prefix = Buffer.alloc(8); prefix.write(magic); prefix.writeUInt32LE(json.length, 4);
    return Buffer.concat([prefix, json, Buffer.alloc((-json.length >>> 0) % 8)]);
  }
  const payloadSize = Buffer.alloc(8); payloadSize.writeBigUInt64LE(BigInt(offset));
  const chunk = Buffer.concat([header('RADC', { version: 1, base: 0, count: n, maxSh: 3,
    lodTree: true, payloadBytes: offset, properties: props }), payloadSize, ...parts]);
  const root = header('RAD0', { version: 1, type: 'gsplat', count: n, maxSh: 3, lodTree: true,
    chunkSize: 65536, allChunkBytes: chunk.length,
    chunks: [{ filename: 'model-0.radc', bytes: chunk.length, offset: 0 }] });
  return { 'model.rad': root, 'model-0.radc': chunk };
}
