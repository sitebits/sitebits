// Touch-Geräte: Mobilstufe statt Vollstufe; zum Messen wird die Vollstufe nachgeladen (Mobilstufe hat keinen Messfang).
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BufferAttribute, BufferGeometry, Group, Mesh, MeshBasicMaterial } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { MeshLayer } from '../src/manifest';
import { createMeshLayer } from '../src/meshLayer';
vi.mock('../src/geraet', async (orig) => {
  const echt = await orig<typeof import('../src/geraet')>();
  return { ...echt, GRENZEN: echt.TOUCH };
});
const mock = vi.hoisted(() => ({ parse: vi.fn() }));
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({ GLTFLoader: class {
  setKTX2Loader() { return this; } setMeshoptDecoder() { return this; } register() { return this; }
  async parseAsync(...args: unknown[]) { return { parser: { json: {} }, ...(await mock.parse(...args) as object) }; }
} }));
vi.mock('three/examples/jsm/loaders/KTX2Loader.js', () => ({ KTX2Loader: class {
  workerConfig = { bptcSupported: true };
  setTranscoderPath() { return this; } detectSupport() { return this; } dispose() { /* nichts */ }
} }));
const layer: MeshLayer = { id: 'mesh', kind: 'mesh', title: 'Mesh', format: 'gltf', url: 'model.glb',
  previewUrl: 'preview.glb', previewColor: 'vertex', triangleCount: 1, boundsWorld: [[30, 40, 10], [32, 41, 10.5]],
  mobile: { url: 'mobile.glb', sizeBytes: 24, triangleCount: 1 },
  positionStepM: .001, provenance: { kind: 'other', interpolated: true, scaleVerified: false, scaleSource: 'cli' } };
function scene(): Group {
  const group = new Group(); const g = new BufferGeometry();
  g.setAttribute('position', new BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  group.add(new Mesh(g, new MeshBasicMaterial())); return group;
}
function glb(): ArrayBuffer {
  const b = new ArrayBuffer(24); const v = new DataView(b);
  [0x46546c67, 2, 24, 4, 0x4e4f534a].forEach((x, i) => v.setUint32(i * 4, x, true));
  new Uint8Array(b, 20).set(new TextEncoder().encode('{}  ')); return b;
}
const notify = vi.fn();
const make = () => createMeshLayer({ instance: { threeObjects: new Group(), renderer: {}, notifyChange: vi.fn() } as unknown as Instance,
  layer, originWorld: [31, 40, 10], pkgBase: 'http://localhost/data/pkg/', wasmBase: '/wasm/', notify });
const geholt = (): string[] => vi.mocked(globalThis.fetch).mock.calls.map(([u]) => String(u).split('/').pop()!);
beforeEach(() => {
  notify.mockReset(); mock.parse.mockReset(); mock.parse.mockImplementation(async () => ({ scene: scene() }));
  vi.stubGlobal('fetch', vi.fn(async () => new Response(glb())));
});
afterEach(() => vi.unstubAllGlobals());

it('ohne Messen bleibt es bei der Mobilstufe, ohne Messfang', async () => {
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.stage).toBe('mobil'));
  expect(geholt()).toEqual(['preview.glb', 'mobile.glb']);
  expect(h.pickMeshes).toHaveLength(0); h.dispose();
});

it('Messen lädt aus der Mobilstufe einmal die Vollstufe nach und macht die Ebene messbar', async () => {
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.stage).toBe('mobil'));
  h.setMeasuring(true); h.setMeasuring(true); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.stage).toBe('full'));
  expect(geholt()).toEqual(['preview.glb', 'mobile.glb', 'model.glb']);
  expect(h.pickMeshes).toHaveLength(1);
  expect(notify).toHaveBeenCalledWith(expect.stringContaining('voller Auflösung zum Messen'));
  h.dispose();
});

it('scheitert die Vollstufe, bleibt die Mobilstufe sichtbar und es wird nicht erneut geladen', async () => {
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.stage).toBe('mobil'));
  vi.mocked(globalThis.fetch).mockResolvedValue(new Response('', { status: 404 }));
  h.setMeasuring(true);
  await vi.waitFor(() => expect(h.failure).toBe('load-error'));
  expect(h.stage).toBe('mobil'); expect(h.occluderMeshes).toHaveLength(1);
  h.update([31, 40.5, 10.25]); h.setMeasuring(true);
  expect(geholt()).toEqual(['preview.glb', 'mobile.glb', 'model.glb']); h.dispose();
});
