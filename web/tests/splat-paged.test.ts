import { afterEach, expect, it, vi } from 'vitest';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { SplatsLayer } from '../src/manifest';
import { createSplatLayer } from '../src/splatLayer';
const fake = vi.hoisted(() => ({
  rendererOptions: {} as Record<string, unknown>, meshOptions: {} as Record<string, unknown>,
  numSplats: 0, disposed: vi.fn(), rendererDisposed: vi.fn(),
  metadata: { lodTree: true, chunks: [{}, {}], count: 12_000_000, maxSh: 3 },
}));
vi.mock('@sparkjsdev/spark', () => ({
  SplatFileType: { SPZ: 'spz', PLY: 'ply' },
  SparkRenderer: class {
    constructor(options: Record<string, unknown>) { fake.rendererOptions = options; }
    dispose = fake.rendererDisposed;
  },
  SplatMesh: class {
    constructor(options: Record<string, unknown>) { fake.meshOptions = options; }
    initialized = Promise.resolve();
    paged = { getRadMeta: () => Promise.resolve({ meta: fake.metadata }),
      setMaxSh: vi.fn(), getNumSplats: () => fake.numSplats };
    position = { set: vi.fn() };
    opacity = 1;
    visible = true;
    dispose = fake.disposed;
  },
}));
const layer: SplatsLayer = { id: 'sicht', kind: 'splats', title: 'Sichtschicht', format: 'rad',
  url: 'layers/sicht/model.rad', splatCount: 10_000_000, sizeBytes: 800_000_000,
  shDegree: 3, chunkCount: 2, lodMethod: 'bhatt-lod', boundsWorld: [[0, 0, 0], [1, 1, 1]],
  frameCheck: { ok: true, samples: 1000, p50M: 0, p95M: 0 } };
function setup(weak: boolean) {
  vi.stubGlobal('matchMedia', () => ({ matches: weak }));
  vi.stubGlobal('navigator', { deviceMemory: 8 });
  const fetch = vi.fn(() => { throw new Error('No full-file fetch for RAD'); });
  vi.stubGlobal('fetch', fetch);
  let render: (() => void) | undefined;
  const instance = { renderer: { capabilities: { isWebGL2: true },
    domElement: { addEventListener: vi.fn(), removeEventListener: vi.fn() } },
    scene: { add: vi.fn(), remove: vi.fn() }, notifyChange: vi.fn(),
    addEventListener: (_name: string, callback: () => void) => { render = callback; },
    removeEventListener: vi.fn() };
  const notify = vi.fn();
  const loading = vi.fn();
  fake.numSplats = 0;
  fake.metadata.chunks = [{}, {}];
  return { instance, notify, loading, fetch, render: () => render?.(), options: {
    instance: instance as unknown as Instance, notify, loading, layer, originWorld: [10, 20, 30] as [number, number, number],
    pkgBase: 'https://example.test/data/fixture/' } };
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); vi.useRealTimers(); });
it('streams RAD with bounded weak-device pages and loading until a rendered page', async () => {
  const s = setup(true);
  const handle = await createSplatLayer(s.options);
  expect(s.fetch).not.toHaveBeenCalled();
  expect(fake.meshOptions).toMatchObject({ paged: true, raycastable: false,
    url: 'https://example.test/data/fixture/layers/sicht/model.rad' });
  expect(fake.meshOptions.fileBytes).toBeUndefined();
  expect(fake.rendererOptions).toMatchObject({ maxPagedSplats: 131_072, lodSplatCount: 100_000 });
  s.render();
  expect(s.notify).toHaveBeenLastCalledWith('Lade Sichtschicht …');
  expect(s.loading).toHaveBeenLastCalledWith(true);
  fake.numSplats = 100;
  s.render();
  expect(s.notify).toHaveBeenLastCalledWith(expect.stringContaining('Details werden nachgeladen'));
  expect(s.loading).toHaveBeenLastCalledWith(false);
  handle.dispose();
  expect(fake.disposed).toHaveBeenCalledOnce();
  expect(fake.rendererDisposed).toHaveBeenCalledOnce();
});
it('rejects inconsistent metadata and releases renderer and mesh', async () => {
  const s = setup(false);
  fake.metadata.chunks = [{}];
  await expect(createSplatLayer(s.options)).rejects.toThrow('RAD-Metadaten');
  expect(s.instance.scene.add).not.toHaveBeenCalled();
  expect(fake.rendererOptions.maxPagedSplats).toBe(1_048_576);
  expect(fake.rendererDisposed).toHaveBeenCalledOnce();
  expect(fake.disposed).toHaveBeenCalled();
});

it('restarts the first-image deadline when an initially hidden layer is enabled', async () => {
  vi.useFakeTimers();
  const s = setup(true);
  const handle = await createSplatLayer(s.options);
  handle.setVisible(false);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(s.notify).not.toHaveBeenCalledWith(expect.stringContaining('kein erstes Bild'));
  expect(s.loading).toHaveBeenLastCalledWith(false);
  handle.setVisible(true);
  expect(s.loading).toHaveBeenLastCalledWith(true);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(s.notify).toHaveBeenLastCalledWith(expect.stringContaining('kein erstes Bild'));
  expect(s.loading).toHaveBeenLastCalledWith(false);
  handle.dispose();
});
