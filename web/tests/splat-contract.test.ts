import { describe, it, expect } from 'vitest';
import { parseManifest } from '../src/manifest';
import { initialVisibility, visibleBounds } from '../src/layers';

const layer = { id: 'sicht', kind: 'splats', title: 'Sichtschicht', format: 'ply',
  url: 'layers/sicht/model.ply', splatCount: 10, sizeBytes: 1000,
  boundsWorld: [[10, 20, 30], [12, 22, 32]], shDegree: 0,
  frameCheck: { ok: true, samples: 1000, p50M: 0, p95M: 0 },
};
const raw = () => ({ schemaVersion: 2, projectId: 'fixture', revision: 1,
  frame: { crs: { wkt: null, verticalDatum: null, declaredBy: 'cli' }, unit: 'm', originWorld: [10, 20, 30] },
  layers: [layer], initialView: { target: [11, 21, 31], distance: 8, layers: ['sicht'] },
});
describe('Sichtschicht-Vertrag', () => {
  it('liest Splats ohne Wolke und rahmt deren Weltgrenzen', () => {
    const p = parseManifest(raw());
    expect(p.cloud).toBeNull();
    expect(p.splats[0]?.url).toBe(layer.url);
    expect(p.initialView.layers).toEqual(['sicht']);
    expect(visibleBounds(p, () => true)).toEqual(layer.boundsWorld);
    expect(initialVisibility(p.layers, p.initialView.layers)).toEqual({ sicht: true });
  });
  it('verwirft falschen Rahmenbeleg und unbekannte Startebene', () => {
    expect(() => parseManifest({ ...raw(), layers: [{ ...layer, frameCheck: { ok: false } }] })).toThrow();
    expect(() => parseManifest({ ...raw(), initialView: { ...raw().initialView, layers: ['falsch'] } })).toThrow();
  });
  it('alter Platzhalter ohne format bleibt „nicht unterstützt“, der Rest lädt', () => {
    const p = parseManifest({ ...raw(), layers: [layer, { id: 'alt', kind: 'splats', title: 'Splat (Test)' }] });
    expect(p.layers.find((l) => l.id === 'alt')).toMatchObject({ unsupported: true });
    expect(p.splats.map((s) => s.id)).toEqual(['sicht']);
  });
});

it('liest SPZ mit SH-Grad 3 und expliziter Startposition', () => {
  const p = parseManifest({ ...raw(), layers: [{ ...layer, format: 'spz', shDegree: 3, chunkCount: 1 }],
    initialView: { ...raw().initialView, position: [11, 22, 40] } });
  expect(p.splats[0]).toMatchObject({ format: 'spz', shDegree: 3, chunkCount: 1 });
  expect(p.initialView.position).toEqual([11, 22, 40]);
});

it('verwirft unerzeugtes RAD, ungültigen Grad, Chunks und Kamera am Ziel', () => {
  for (const change of [{ format: 'rad' }, { shDegree: 4 }, { shDegree: 1.5 }, { chunkCount: 2 }]) {
    expect(() => parseManifest({ ...raw(), layers: [{ ...layer, ...change }] })).toThrow();
  }
  expect(() => parseManifest({ ...raw(), initialView: { ...raw().initialView, position: [11, 21, 31] } })).toThrow();
});

it('liest gestuftes RAD und verwirft RAD ohne Chunk-/LoD-Vertrag', () => {
  const rad = { ...layer, format: 'rad', chunkCount: 200, lodMethod: 'bhatt-lod', splatCount: 10_000_000 };
  expect(parseManifest({ ...raw(), layers: [rad] }).splats[0]).toMatchObject({ format: 'rad', chunkCount: 200, lodMethod: 'bhatt-lod' });
  for (const change of [{ chunkCount: 0 }, { chunkCount: 1.5 }, { lodMethod: undefined }]) {
    expect(() => parseManifest({ ...raw(), layers: [{ ...rad, ...change }] })).toThrow();
  }
});
