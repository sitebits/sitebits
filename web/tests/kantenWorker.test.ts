import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BoxGeometry, BufferGeometry, Float32BufferAttribute, InterleavedBuffer, InterleavedBufferAttribute, Matrix4, Mesh, PerspectiveCamera, Vector3 } from 'three';
import { berechneKanten, kopiereKantenTeile } from '../src/kantenDaten';
import type { KantenAnfrage } from '../src/kantenDaten';
import { kantenKern } from '../src/kantenKern';
import { featureEdges, meshFeatureEdges, prepareMeshFeatureEdges, vormerkenMeshFeatureEdges } from '../src/meshPickMath';
import { MeshPickSource } from '../src/meshPick';
import type { MeshLayerHandle } from '../src/meshLayer';

class TestWorker {
  static alle: TestWorker[] = [];
  onmessage: ((ev: MessageEvent<Uint32Array[]>) => void) | null = null;
  onerror: ((ev: ErrorEvent) => void) | null = null;
  onmessageerror: (() => void) | null = null;
  terminate = vi.fn();
  anfrage!: KantenAnfrage;
  constructor() { TestWorker.alle.push(this); }
  postMessage(o: KantenAnfrage, transfer: ArrayBuffer[]): void {
    this.anfrage = structuredClone(o, { transfer });
  }
  antworten(): void { this.onmessage?.({ data: berechneKanten(this.anfrage) } as MessageEvent<Uint32Array[]>); }
}
beforeEach(() => { TestWorker.alle = []; vi.stubGlobal('Worker', TestWorker); });
afterEach(() => { vi.unstubAllGlobals(); });
const teil = (g: BufferGeometry, matrix: Matrix4 | null = null) => ({ pos: g.getAttribute('position'), index: g.index, matrix: matrix?.elements ?? null });

it.each([true, false])('Worker-Kern liefert bitgleiche sortierte Indizes (verschweißt: %s)', (geschweisst) => {
  const a = new BoxGeometry().toNonIndexed(), b = new BoxGeometry();
  // Interleaved Float64 bleibt einschließlich Präzision und Getter-Semantik erhalten.
  const pos = a.getAttribute('position');
  const werte = new Float64Array(pos.count * 4);
  for (let i = 0; i < pos.count; i++) werte.set([pos.getX(i) + 1e-10, pos.getY(i), pos.getZ(i), 99], i * 4);
  a.setAttribute('position', new InterleavedBufferAttribute(new InterleavedBuffer(werte, 4), 3, 0));
  const teile = geschweisst ? [teil(a)] : [teil(a, new Matrix4().makeRotationZ(.3)), teil(b, new Matrix4().makeTranslation(2, 0, 0))];
  const erwartet = kantenKern(teile, geschweisst);
  const ergebnis = berechneKanten({ teile: kopiereKantenTeile(teile), geschweisst });
  expect(erwartet.flat().length).toBeGreaterThan(10);
  expect(ergebnis).toEqual(erwartet.map((liste) => Uint32Array.from(liste.flatMap((e) => [e.a, e.b]))));
});

it('Einzelgeometrie startet einmal, transferiert nur Kopien und befüllt den Cache', () => {
  const g = new BoxGeometry(), vorher = g.getAttribute('position').array.slice();
  expect(featureEdges(g)).toEqual([]); expect(featureEdges(g)).toEqual([]);
  expect(TestWorker.alle).toHaveLength(1);
  expect(g.getAttribute('position').array).toEqual(vorher); expect(g.index!.array.byteLength).toBeGreaterThan(0);
  const worker = TestWorker.alle[0]!; worker.antworten();
  expect(featureEdges(g)).toEqual(kantenKern([teil(g)], true)[0]);
  expect(worker.terminate).toHaveBeenCalledOnce(); expect(worker.onmessage).toBeNull();
  g.dispose();
  expect(featureEdges(g)).toEqual([]); expect(TestWorker.alle).toHaveLength(2);
  g.dispose();
});

it('Gruppen verschweißen Materialnähte mit Matrizen und bleiben bis zur Antwort leer', () => {
  const a = new BufferGeometry(), b = new BufferGeometry();
  a.setAttribute('position', new Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
  b.setAttribute('position', new Float32BufferAttribute([0,0,0, -1,0,0, -1,0,1], 3));
  const ma = new Mesh(a), mb = new Mesh(b); mb.position.x = 1; mb.updateMatrixWorld(true);
  vormerkenMeshFeatureEdges([ma, mb], new Matrix4());
  expect(TestWorker.alle).toHaveLength(0);
  expect(meshFeatureEdges(ma)).toEqual([]); expect(meshFeatureEdges(mb)).toEqual([]);
  expect(TestWorker.alle).toHaveLength(1); TestWorker.alle[0]!.antworten();
  expect(meshFeatureEdges(ma)).toEqual([{ a: 0, b: 1 }]);
  expect(meshFeatureEdges(mb)).toEqual([{ a: 0, b: 1 }]);
  a.dispose(); b.dispose();
});

it.each(['dispose', 'vormerken'])('verwirft laufende Generationen und verspätete Antworten bei %s', (aktion) => {
  const a = new Mesh(new BoxGeometry()), b = new Mesh(new BoxGeometry());
  prepareMeshFeatureEdges([a, b], new Matrix4());
  const alt = TestWorker.alle[0]!, antwort = alt.onmessage!;
  if (aktion === 'dispose') a.geometry.dispose();
  vormerkenMeshFeatureEdges([a, b], new Matrix4());
  expect(alt.terminate).toHaveBeenCalledOnce(); expect(meshFeatureEdges(a)).toEqual([]);
  antwort({ data: [new Uint32Array([999, 1000]), new Uint32Array([999, 1000])] } as MessageEvent<Uint32Array[]>);
  expect(meshFeatureEdges(a)).toEqual([]); TestWorker.alle[1]!.antworten();
  expect(meshFeatureEdges(a)).toEqual(kantenKern([teil(a.geometry), teil(b.geometry)], false)[0]);
  a.geometry.dispose(); b.geometry.dispose();
});

it.each(['fehlend', 'konstruktor', 'postMessage', 'error', 'messageerror'])('synchroner Rückfall bei %s', (fehler) => {
  if (fehler === 'fehlend') vi.stubGlobal('Worker', undefined);
  if (fehler === 'konstruktor') vi.stubGlobal('Worker', class { constructor() { throw new Error('nicht verfügbar'); } });
  if (fehler === 'postMessage') vi.spyOn(TestWorker.prototype, 'postMessage').mockImplementationOnce(() => { throw new Error('nicht verfügbar'); });
  const g = new BoxGeometry(); const kanten = featureEdges(g);
  if (fehler === 'error' || fehler === 'messageerror') {
    expect(kanten).toEqual([]);
    const w = TestWorker.alle[0]!;
    if (fehler === 'error') w.onerror!({ preventDefault: vi.fn() } as unknown as ErrorEvent);
    else w.onmessageerror!();
  }
  expect(featureEdges(g)).toEqual(kantenKern([teil(g)], true)[0]);
  for (const w of TestWorker.alle) expect(w.terminate).toHaveBeenCalledOnce();
  g.dispose();
});

it('warm wartet nicht; Messen und Vertex-Fang gehen sofort, Kantenfang nach der Antwort', async () => {
  const mesh = new Mesh(new BoxGeometry()); mesh.updateMatrixWorld(true);
  const camera = new PerspectiveCamera(45, 1, .01, 100); camera.position.z = 3; camera.updateMatrixWorld(true);
  const layer = { id: 'synthetisch', stage: 'full', visible: true, pickMeshes: [mesh] } as unknown as MeshLayerHandle;
  const picker = new MeshPickSource({ camera: () => camera, rect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }), layers: () => [layer], clip: () => undefined });
  vormerkenMeshFeatureEdges([mesh], new Matrix4());
  expect(picker.warm()).toBeUndefined(); expect(TestWorker.alle).toHaveLength(1);
  const rand = new Vector3(.497, .1, .5).project(camera);
  const x = (rand.x + 1) * 500, y = (1 - rand.y) * 500;
  const davor = await picker.pickAtScreen(x, y, { snapEdges: true });
  expect(davor.point).not.toBeNull(); expect(davor.info.snap?.result).toBe('none');
  expect((await picker.pickAtScreen(737, 263, { snapEdges: true })).info.snap?.result).toBe('vertex');
  TestWorker.alle[0]!.antworten();
  expect((await picker.pickAtScreen(x, y, { snapEdges: true })).info.snap?.result).toBe('edge');
  mesh.geometry.dispose();
});

it('verwirft auch vorgemerkte und laufende Einzelgeometrien beim Entladen', () => {
  const mesh = new Mesh(new BoxGeometry());
  vormerkenMeshFeatureEdges([mesh], new Matrix4());
  mesh.geometry.dispose();
  // Die Vormerkung ist weg: direkter Geometriepfad startet einen neuen Auftrag.
  expect(featureEdges(mesh.geometry)).toEqual([]);
  const alt = TestWorker.alle[0]!, antwort = alt.onmessage!;
  mesh.geometry.dispose();
  expect(alt.terminate).toHaveBeenCalledOnce();
  antwort({ data: [new Uint32Array([999, 1000])] } as MessageEvent<Uint32Array[]>);
  expect(featureEdges(mesh.geometry)).toEqual([]);
  TestWorker.alle[1]!.antworten();
  expect(featureEdges(mesh.geometry)).toHaveLength(12);
  mesh.geometry.dispose();
});
