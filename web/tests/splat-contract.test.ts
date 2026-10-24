import { describe, it, expect } from 'vitest';
import { KEINE_WOLKE, parseManifest } from '../src/manifest';
import { createLayerRegistry, initialVisibility, visibleBounds } from '../src/layers';

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

describe('Splats ohne Punktwolke (Paket aus „neu-ohne-wolke“)', () => {
  const ohne = () => ({ ...raw(), layers: [{ ...layer, format: 'spz', chunkCount: 1, frameCheck: 'keine Wolke' }] });
  it('wird gelesen und behält die Kennzeichnung', () => {
    const p = parseManifest(ohne());
    expect(p.cloud).toBeNull();
    expect(p.splats[0]?.frameCheck).toBe(KEINE_WOLKE);
  });
  it('„keine Wolke“ gilt nicht, wenn das Paket doch eine Punktwolke hat', () => {
    const wolke = { id: 'punkte', kind: 'pointcloud', title: 'P', url: 'c.laz', pointCount: 1, attributes: [], scale: 0.001,
      boundsWorld: [[0, 0, 0], [1, 1, 1]], provenance: { kind: 'scan', interpolated: false, scaleVerified: true, scaleSource: 'scanner' } };
    expect(() => parseManifest({ ...ohne(), layers: [wolke, ohne().layers[0]] })).toThrow(/Rahmenbeleg/);
  });
  it('andere Texte als Rahmenbeleg bleiben ungültig', () => {
    expect(() => parseManifest({ ...raw(), layers: [{ ...layer, frameCheck: 'keine wolke' }] })).toThrow();
    expect(() => parseManifest({ ...raw(), layers: [{ ...layer, frameCheck: '' }] })).toThrow();
  });
  it('die Ebene sagt „Modellmaß – Lage und Maßstab nicht belegt“, mit Wolkenbeleg nicht', () => {
    const v = { cloud: null, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: () => {} };
    const notes = (m: unknown): string[] | undefined => createLayerRegistry({ project: parseManifest(m),
      viewer: v as unknown as import('../src/viewer').Viewer, stationLayer: null, initial: { sicht: true } })
      .entries().find((e) => e.id === 'sicht')?.notes;
    expect(notes(ohne())).toEqual(['Modellmaß – Lage und Maßstab nicht belegt', 'Sichtschicht – Messen und Fangen auf Punktwolke oder Mesh']);
    expect(notes(raw())).toEqual(['Sichtschicht – Messen und Fangen auf Punktwolke oder Mesh']);
  });
});
