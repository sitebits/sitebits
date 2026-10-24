// Lader-Lebenszyklus ohne WebGL: Decoder liefern reale three-Geometrie, Netz und GPU werden ersetzt.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BufferAttribute, BufferGeometry, CompressedTexture, Group, LineSegments, Mesh, MeshBasicMaterial, MeshLambertMaterial, MeshNormalMaterial, Plane, Raycaster, Vector3 } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { MeshLayer } from '../src/manifest';
import { createMeshLayer, PREVIEW_TIMEOUT_MS } from '../src/meshLayer';
import { meshFeatureEdges } from '../src/meshPickMath';
import { DESKTOP } from '../src/geraet';
const MESH_BUDGET_BYTES = DESKTOP.meshBytes;
const mock = vi.hoisted(() => ({ parse: vi.fn(), compressed: true, dispose: vi.fn() }));
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({ GLTFLoader: class {
  setKTX2Loader() { return this; } setMeshoptDecoder() { return this; } register() { return this; }
  // Wie GLTFLoader: Ergebnis trägt den Parser mit dem glTF-JSON (hier ohne deklarierte Texturen).
  async parseAsync(...args: unknown[]) { return { parser: { json: {} }, ...(await mock.parse(...args) as object) }; }
} }));
vi.mock('three/examples/jsm/loaders/KTX2Loader.js', () => ({ KTX2Loader: class {
  workerConfig = { bptcSupported: mock.compressed };
  setTranscoderPath() { return this; } detectSupport() { return this; } dispose() { mock.dispose(); }
} }));
const layer: MeshLayer = { id: 'mesh', kind: 'mesh', title: 'Mesh', format: 'gltf', url: 'model.glb',
  previewUrl: 'preview.glb', previewColor: 'vertex', triangleCount: 1, boundsWorld: [[30, 40, 10], [32, 41, 10.5]],
  positionStepM: .001, provenance: { kind: 'other', interpolated: true, scaleVerified: false, scaleSource: 'cli' } };
function scene(bytes?: number): Group {
  const group = new Group(); const g = new BufferGeometry();
  const attribute = new BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3);
  if (bytes) Object.defineProperty(attribute.array, 'buffer', { value: { byteLength: bytes } });
  g.setAttribute('position', attribute); group.add(new Mesh(g, new MeshBasicMaterial())); return group;
}
function glb(): ArrayBuffer {
  const b = new ArrayBuffer(24); const v = new DataView(b);
  [0x46546c67, 2, 24, 4, 0x4e4f534a].forEach((x, i) => v.setUint32(i * 4, x, true));
  new Uint8Array(b, 20).set(new TextEncoder().encode('{}  ')); return b;
}
const instance = () => ({ threeObjects: new Group(), renderer: {}, notifyChange: vi.fn() } as unknown as Instance);
const make = (i = instance()) => createMeshLayer({ instance: i, layer, originWorld: [31, 40, 10],
  pkgBase: 'http://localhost/data/pkg/', wasmBase: '/wasm/', notify: vi.fn() });
beforeEach(() => {
  mock.compressed = true; mock.parse.mockReset(); mock.dispose.mockReset();
  mock.parse.mockImplementation(async () => ({ scene: scene() }));
  vi.stubGlobal('fetch', vi.fn(async () => new Response(glb())));
});
afterEach(() => vi.unstubAllGlobals());
it('Vollstufe 404 lässt Vorschau stehen und wird nicht erneut versucht', async () => {
  const fetch = vi.mocked(globalThis.fetch);
  fetch.mockResolvedValueOnce(new Response(glb())).mockResolvedValue(new Response('', { status: 404 }));
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.failure).toBe('load-error'));
  expect(h.stage).toBe('preview'); expect(h.bytes).toBeGreaterThan(0);
  h.update([31, 40.5, 10.25]); expect(fetch).toHaveBeenCalledTimes(2); h.dispose();
});
it('nachgeladene Stufe übernimmt Sichtbarkeit, Deckkraft und Schnittbox; Ressourcen werden freigegeben', async () => {
  let release!: (value: { scene: Group }) => void;
  const preview = scene(); const final = scene();
  const disposePreview = vi.spyOn((preview.children[0] as Mesh).geometry, 'dispose');
  const disposeFull = vi.spyOn((final.children[0] as Mesh).geometry, 'dispose');
  mock.parse.mockResolvedValueOnce({ scene: preview }).mockImplementationOnce(() => new Promise((r) => { release = r; }));
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(release).toBeTypeOf('function'));
  h.setVisible(false); h.setOpacity(.4); const planes = Array.from({ length: 6 }, () => new Plane(new Vector3(1, 0, 0), 0));
  h.setClippingPlanes(planes); release({ scene: final });
  await vi.waitFor(() => expect(h.stage).toBe('full'));
  expect(h.visible).toBe(false); expect(h.clippingPlaneCounts).toEqual([6]);
  expect(((final.children[0] as Mesh).material as MeshBasicMaterial).opacity).toBe(.4);
  expect(disposePreview).toHaveBeenCalledOnce(); h.dispose(); h.dispose();
  expect(disposeFull).toHaveBeenCalledOnce(); expect(mock.dispose).toHaveBeenCalledOnce(); expect(h.bytes).toBe(0);
});
it('Budgetüberschreitung verwirft nur die Vollstufe', async () => {
  const tooLarge = scene(MESH_BUDGET_BYTES);
  const dispose = vi.spyOn((tooLarge.children[0] as Mesh).geometry, 'dispose');
  mock.parse.mockResolvedValueOnce({ scene: scene() }).mockResolvedValueOnce({ scene: tooLarge });
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.failure).toBe('budget'));
  expect(h.stage).toBe('preview'); expect(dispose).toHaveBeenCalledOnce(); h.dispose();
});
it('Budget ist je Instance gemeinsam, nicht je Ebene; Dispose gibt es frei', async () => {
  mock.parse.mockImplementation(async () => ({ scene: scene(MESH_BUDGET_BYTES / 2 + 1) }));
  const i = instance(); const a = await make(i); const b = await make(i);
  expect(a.stage).toBe('preview'); expect(b.failure).toBe('budget'); expect(b.stage).toBe('none');
  a.dispose(); const c = await make(i); expect(c.stage).toBe('preview'); b.dispose(); c.dispose();
});
it('Normalen entstehen erst auf Wunsch und beim zweiten Einschalten nicht erneut', async () => {
  const g = scene(); const geometry = (g.children[0] as Mesh).geometry;
  const compute = vi.spyOn(geometry, 'computeVertexNormals'); mock.parse.mockResolvedValue({ scene: g });
  const h = await make(); const bytes = h.bytes;
  expect(geometry.attributes.normal).toBeUndefined(); h.setShading(true);
  expect(compute).toHaveBeenCalledOnce(); expect(h.bytes).toBeGreaterThan(bytes);
  h.setShading(false); h.setShading(true); expect(compute).toHaveBeenCalledOnce(); h.dispose();
});
it('ohne GPU-Kompression: Vorschau bleibt, Vollstufe wird nicht angefragt', async () => {
  mock.compressed = false; const h = await make(); h.update([31, 40.5, 10.25]);
  expect(h.stage).toBe('preview'); expect(h.failure).toBe('no-compressed-texture-support');
  expect(globalThis.fetch).toHaveBeenCalledOnce(); h.dispose();
});
it('Panorama-Verlassen stellt die gewünschte Ebenensichtbarkeit wieder her', async () => {
  const h = await make(); h.setPanoramaOverlay(1); expect(h.visible).toBe(false);
  h.setPanoramaOverlay(.5); expect(h.visible).toBe(true); h.setVisible(false); h.setPanoramaOverlay(0);
  expect(h.visible).toBe(false); h.dispose();
});

it('Decoder-RangeError ist ein Ladefehler, kein Budgetfehler', async () => {
  mock.parse.mockResolvedValueOnce({ scene: scene() }).mockRejectedValueOnce(new RangeError('Kaputter Decoderpuffer'));
  const h = await make(); h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.failure).toBe('load-error'));
  expect(h.stage).toBe('preview'); h.dispose();
});
it('Lichter einmal je Instanz: zwei schattierte Ebenen teilen sie, Abbau räumt sie weg', async () => {
  const i = instance();
  const a = await make(i); const b = await make(i);
  const lichter = () => i.threeObjects.children.filter((c) => c.name === 'scanview-mesh-licht');
  expect(lichter()).toHaveLength(1); expect(lichter()[0]!.visible).toBe(false);
  a.setShading(true); b.setShading(true); expect(lichter()[0]!.visible).toBe(true);
  a.setShading(false); expect(lichter()[0]!.visible).toBe(true);
  b.dispose(); expect(lichter()[0]!.visible).toBe(false);
  a.dispose(); expect(lichter()).toHaveLength(0);
});
it('hängende Vorschau bricht nach dem Zeitlimit ab, der Viewer bekommt trotzdem eine Ebene', async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(globalThis.fetch).mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('abgebrochen', 'AbortError')));
    }));
    const pending = make();
    await vi.advanceTimersByTimeAsync(PREVIEW_TIMEOUT_MS);
    const h = await pending;
    expect(h.stage).toBe('none'); expect(h.failure).toBe('load-error'); h.dispose();
  } finally { vi.useRealTimers(); }
});

it('glTF-Primitiv ohne Gruppen bekommt ein Einzelmaterial (ein Array zeichnet nichts)', async () => {
  const vorschau = scene(); mock.parse.mockResolvedValueOnce({ scene: vorschau });
  const h = await make();
  expect(h.stage).toBe('preview');
  const mesh = vorschau.children[0] as Mesh;
  expect(Array.isArray(mesh.material)).toBe(false);
  expect(mesh.geometry.groups).toHaveLength(0);
  h.dispose();
});

it('texturierte Materialien zeichnen ohne Vertexfarben (sonst doppelt abgedunkelt)', async () => {
  const vorschau = scene(); const voll = scene();
  const quelle = (voll.children[0] as Mesh).material as MeshBasicMaterial;
  quelle.map = new CompressedTexture([], 4, 4); quelle.vertexColors = true;
  ((vorschau.children[0] as Mesh).material as MeshBasicMaterial).vertexColors = true;
  mock.parse.mockResolvedValueOnce({ scene: vorschau }).mockResolvedValueOnce({ scene: voll });
  const h = await make();
  expect(((vorschau.children[0] as Mesh).material as MeshBasicMaterial).vertexColors).toBe(true);
  h.update([31, 40.5, 10.25]);
  await vi.waitFor(() => expect(h.stage).toBe('full'));
  const m = (voll.children[0] as Mesh).material as MeshBasicMaterial;
  expect(m.map).toBe(quelle.map); expect(m.vertexColors).toBe(false);
  h.dispose();
});

it('Messwerkzeug lädt die Vollstufe auch außerhalb der Raumdiagonale, nur einmal', async () => {
  const h = await make();
  expect(h.pickMeshes).toHaveLength(0);
  h.setMeasuring(true); h.setMeasuring(true);
  await vi.waitFor(() => expect(h.stage).toBe('full'));
  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(h.pickMeshes).toHaveLength(1);
  h.setVisible(false); expect(h.pickMeshes).toHaveLength(0); h.dispose();
});

it('glTF-Primitiv ohne Gruppen bleibt mit Einzelmaterial raycastbar', async () => {
  const h = await make(); h.setMeasuring(true);
  await vi.waitFor(() => expect(h.stage).toBe('full'));
  const mesh = h.pickMeshes[0]!;
  expect(Array.isArray(mesh.material)).toBe(false);
  const caster = new Raycaster(new Vector3(31.2,39,10.2),new Vector3(0,1,0));
  expect(caster.intersectObject(mesh)).toHaveLength(1); h.dispose();
});

it('Vollstufenlader bereitet Kanten über Material-Primitiven hinweg vor', async () => {
 const full=new Group();
 for (const points of [[0,0,0,1,0,0,0,1,0],[1,0,0,0,0,0,0,0,1]]) {
  const g=new BufferGeometry();g.setAttribute('position',new BufferAttribute(new Float32Array(points),3));full.add(new Mesh(g,new MeshBasicMaterial()));
 }
 mock.parse.mockResolvedValueOnce({scene:scene()}).mockResolvedValueOnce({scene:full});
 const h=await make();h.setMeasuring(true);await vi.waitFor(()=>expect(h.stage).toBe('full'));
 expect(h.pickMeshes).toHaveLength(2);for(const mesh of h.pickMeshes) expect(meshFeatureEdges(mesh)).toHaveLength(1);
 h.dispose();
});

it('Darstellung: Grau mit/ohne Licht, Normalen, Aus — Normalen nur für Licht, Rückweg auf das Originalmaterial', async () => {
  const g = scene(); const mesh = g.children[0] as Mesh;
  const compute = vi.spyOn(mesh.geometry, 'computeVertexNormals'); mock.parse.mockResolvedValue({ scene: g });
  const h = await make(); const original = mesh.material;
  h.setFlaeche('grau'); expect(mesh.material).toBeInstanceOf(MeshLambertMaterial); expect(compute).not.toHaveBeenCalled();
  expect((mesh.material as MeshLambertMaterial).flatShading).toBe(true); // ohne Normalen flach, kein Budget
  expect((mesh.material as MeshLambertMaterial).map).toBeNull();
  h.setShading(true); expect(compute).toHaveBeenCalledOnce(); // jetzt mit Normalen: glatt
  expect((mesh.material as MeshLambertMaterial).flatShading).toBe(false);
  h.setFlaeche('normalen'); expect(mesh.material).toBeInstanceOf(MeshNormalMaterial);
  h.setFlaeche('aus'); expect((mesh.material as MeshBasicMaterial).visible).toBe(false);
  h.setShading(false); h.setFlaeche('textur'); expect(mesh.material).toBe(original);
  expect((mesh.material as MeshBasicMaterial).visible).toBe(true); expect(compute).toHaveBeenCalledOnce();
  expect(h.flaeche).toBe('textur'); h.dispose();
});
it('Drahtgitter: Linien als Kind, teilen Positionen, zählen im Budget, messen nicht; Abschalten lässt die Fläche heil', async () => {
  const g = scene(); const mesh = g.children[0] as Mesh; mock.parse.mockResolvedValue({ scene: g });
  const h = await make(); const bytes = h.bytes;
  h.setDraht(true);
  const linien = mesh.children.filter((c): c is LineSegments => c instanceof LineSegments);
  expect(linien).toHaveLength(1); expect(linien[0]!.geometry.attributes.position).toBe(mesh.geometry.attributes.position);
  expect([...linien[0]!.geometry.index!.array]).toEqual([0, 1, 1, 2, 2, 0]);
  expect(h.bytes).toBe(bytes + 24); expect((mesh.material as MeshBasicMaterial).polygonOffset).toBe(true);
  const ray = new Raycaster(new Vector3(.2, .2, 5), new Vector3(0, 0, -1)); const treffer: unknown[] = [];
  linien[0]!.raycast(ray, treffer as never); expect(treffer).toHaveLength(0);
  const dispose = vi.spyOn(linien[0]!.geometry, 'dispose');
  h.setDraht(false); expect(mesh.children).toHaveLength(0); expect(dispose).toHaveBeenCalledOnce();
  expect(mesh.geometry.attributes.position).toBeDefined(); expect(h.bytes).toBe(bytes); expect(h.draht).toBe(false); h.dispose();
});
it('Drahtgitter über dem Budget bleibt aus und meldet sich', async () => {
  const groß = scene(MESH_BUDGET_BYTES - 10); mock.parse.mockResolvedValue({ scene: groß });
  const notify = vi.fn();
  const h = await createMeshLayer({ instance: instance(), layer, originWorld: [31, 40, 10], pkgBase: 'http://localhost/data/pkg/', wasmBase: '/wasm/', notify });
  h.setDraht(true); expect(h.draht).toBe(false); expect(notify).toHaveBeenCalledWith('Drahtgitter überschreitet das Grafikbudget'); h.dispose();
});
it('Fläche „aus“: weder messbar noch verdeckend, auch mit Drahtgitter', async () => {
  const h = await make(); expect(h.occluderMeshes).toHaveLength(1);
  h.setFlaeche('aus'); h.setDraht(true); expect(h.occluderMeshes).toHaveLength(0); expect(h.pickMeshes).toHaveLength(0);
  h.setFlaeche('grau'); expect(h.occluderMeshes).toHaveLength(1); h.dispose();
});
it('Budget: Grau/Normalen tragen keine Textur, sie bleibt aber im Budget; ohne Normalen kein Zuwachs', async () => {
  const g = scene(); const quelle = (g.children[0] as Mesh).material as MeshBasicMaterial;
  quelle.map = new CompressedTexture([{ data: new Uint8Array(4096), width: 64, height: 64 }], 64, 64);
  mock.parse.mockResolvedValue({ scene: g });
  const h = await make(); const vorher = h.bytes;
  h.setFlaeche('normalen'); expect(h.bytes).toBe(vorher); // flach, keine Normalen; Textur bleibt im Budget
  h.setFlaeche('grau'); expect(h.bytes).toBe(vorher); h.dispose();
});
it('Grau schaltet das Licht an; Schattierung über dem Budget wird flach statt abgelehnt', async () => {
  const i = instance(); const groß = scene(MESH_BUDGET_BYTES - 10); mock.parse.mockResolvedValue({ scene: groß });
  const notify = vi.fn(); const mesh = groß.children[0] as Mesh;
  const h = await createMeshLayer({ instance: i, layer, originWorld: [31, 40, 10], pkgBase: 'http://localhost/data/pkg/', wasmBase: '/wasm/', notify });
  const licht = () => i.threeObjects.children.find((c) => c.name === 'scanview-mesh-licht')!;
  h.setFlaeche('grau'); expect(licht().visible).toBe(true); h.setFlaeche('textur'); expect(licht().visible).toBe(false);
  h.setShading(true); expect(h.shading).toBe(true); expect(mesh.geometry.attributes.normal).toBeUndefined();
  expect((mesh.material as MeshLambertMaterial).flatShading).toBe(true);
  expect(notify).toHaveBeenCalledWith('Schattierung flach – Normalen passen nicht ins Grafikbudget'); h.dispose();
});
