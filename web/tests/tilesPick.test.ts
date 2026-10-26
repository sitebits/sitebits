import { describe, expect, it, vi } from 'vitest';
import { PerspectiveCamera } from 'three';
import type { Mesh } from 'three';
import { CompositePickSource } from '../src/compositePick';
import { BlattSpeicher } from '../src/tilesBlatt';
import type { LadeBytes } from '../src/tilesBlatt';
import { pruefeTileset } from '../src/tilesMath';
import type { Tileset } from '../src/tilesMath';
import { KachelPickSource } from '../src/tilesPick';
import type { KachelMessEbene } from '../src/tilesPick';
import type { PickSource, RayPickResult } from '../src/rayPick';
import { ladeBlattGeo } from '../src/tilesBlatt';
import { EXTRAS, GROB_VERSATZ_M, MITTE, glb, flaeche, synthetischesTileset } from './tilesFixture';

const erwartet = { crs: 'EPSG:25833', heightSystem: 'DHHN2016' };
const rect = () => ({ left: 0, top: 0, width: 1000, height: 1000 });
// Kamera 3 m über der Mitte, senkrecht nach unten; Bildmitte (500,500) trifft (MITTE x, MITTE y).
const kamera = (dx = 0, dy = 0): PerspectiveCamera => {
  const c = new PerspectiveCamera(45, 1, 0.01, 100);
  c.position.set(MITTE[0] + dx, MITTE[1] + dy, MITTE[2] + 3); c.up.set(0, 1, 0); c.lookAt(MITTE[0] + dx, MITTE[1] + dy, MITTE[2]); c.updateMatrixWorld(true);
  return c;
};
// Pixel je Meter in 3 m Abstand: 1000 px / (2·3·tan 22,5°) ≈ 402,4 px/m
const PX_JE_M = 1000 / (2 * 3 * Math.tan(Math.PI / 8));
/** Strahlabstand bis zur waagerechten Ebene dz Meter unter der Kamera, für den Pixel zu (0,5 | 0,5) m auf Blatthöhe (3 m). */
const abstand = (dz: number): number => Math.hypot(0.5, 0.5, 3) * dz / 3;
const bildpunkt = (xm: number, ym: number): [number, number] => [500 + xm * PX_JE_M, 500 - ym * PX_JE_M];

interface Aufbau { picker: KachelPickSource; ebene: KachelMessEbene; abrufe: string[]; speicher: BlattSpeicher }
function aufbau(o: { tileset?: Tileset; dateien?: Map<string, ArrayBuffer>; lade?: LadeBytes; sichtbar?: boolean; deckkraft?: number;
  verdeckung?: Mesh[]; kamera?: PerspectiveCamera; clip?: { enabled: boolean; box: [[number, number, number], [number, number, number]] } } = {}): Aufbau {
  const syn = synthetischesTileset();
  const ts = o.tileset ?? pruefeTileset(syn.json, erwartet);
  const dateien = o.dateien ?? syn.dateien;
  const abrufe: string[] = [];
  const speicher = new BlattSpeicher({ lade: o.lade ?? (async (u) => { abrufe.push(u.split('/').pop()!); return dateien.get(u.split('/').pop()!)!; }) });
  const ebene: KachelMessEbene = { id: 'kacheln', visible: o.sichtbar ?? true, opacity: o.deckkraft ?? 1, tileset: ts, blaetter: speicher,
    blattUrl: (k) => `http://localhost/layers/kacheln/${k.uri}`, occluderMeshes: o.verdeckung ?? [] };
  const cam = o.kamera ?? kamera();
  return { picker: new KachelPickSource({ camera: () => cam, rect, layers: () => [ebene], clip: () => o.clip }), ebene, abrufe, speicher };
}
/** Grobe Wurzel-Fläche als gezeichnetes Netz (5 cm über den Blättern) — nur Verdeckung. */
async function grobeFlaeche(): Promise<Mesh[]> {
  const syn = synthetischesTileset(); const ts = pruefeTileset(syn.json, erwartet);
  return (await ladeBlattGeo(syn.dateien.get('wurzel.glb')!, ts.wurzel, 'wurzel.glb')).meshes;
}

describe('KachelPickSource — Messen nur auf Blättern', () => {
  it('trifft das Blatt (nicht die 5 cm höhere grobe Stufe), mit Quelle, Ebene und Kachel-URI', async () => {
    const { picker, abrufe } = aufbau({ verdeckung: await grobeFlaeche() });
    const r = await picker.pickAtScreen(...bildpunkt(0.5, 0.5));
    expect(r.info.level).toBe('fine'); expect(r.info.source).toBe('mesh'); expect(r.info.layerId).toBe('kacheln');
    expect(r.info.triangle?.tile).toBe('k2.glb'); expect(r.info.triangle?.index).toBeGreaterThanOrEqual(0);
    expect(r.point![2]).toBeCloseTo(MITTE[2], 9);            // Blatt, nicht MITTE[2] + 0,05
    expect(r.point![2]).not.toBeCloseTo(MITTE[2] + GROB_VERSATZ_M, 3);
    expect(r.point![0]).toBeCloseTo(MITTE[0] + 0.5, 3); expect(r.point![1]).toBeCloseTo(MITTE[1] + 0.5, 3);
    expect(r.info.tiefeM).toBeCloseTo(abstand(3), 6);
    expect(abrufe).toEqual(['k2.glb']);                          // nur das Blatt unter dem Strahl, sonst nichts
  });
  it('findet Blätter über zwei Hierarchietiefen (Enkel mit relativer Kachel-Verschiebung)', async () => {
    const { picker } = aufbau();
    const l = await picker.pickAtScreen(...bildpunkt(-0.5, -0.5)); const rr = await picker.pickAtScreen(...bildpunkt(0.5, -0.5));
    expect(l.info.triangle?.tile).toBe('g1.glb'); expect(rr.info.triangle?.tile).toBe('g2.glb');
    expect(l.point![0]).toBeCloseTo(MITTE[0] - 0.5, 3); expect(rr.point![0]).toBeCloseTo(MITTE[0] + 0.5, 3);
  });
  it('Strahl neben dem Modell: leer, ohne Netzabruf', async () => {
    const { picker, abrufe } = aufbau({ verdeckung: await grobeFlaeche() });
    const r = await picker.pickAtScreen(...bildpunkt(3, 0));
    expect(r.point).toBeNull(); expect(r.info.reason).toBe('empty'); expect(abrufe).toEqual([]);
  });
  it('ausgeblendete oder durchsichtige Ebene liefert nichts', async () => {
    expect((await aufbau({ sichtbar: false }).picker.pickAtScreen(500, 500)).info.reason).toBe('hidden');
    expect((await aufbau({ deckkraft: 0 }).picker.pickAtScreen(500, 500)).info.reason).toBe('hidden');
  });
  it('Schnittbox verwirft den Blatttreffer außerhalb, innerhalb bleibt er', async () => {
    const innen = aufbau({ clip: { enabled: true, box: [[MITTE[0] - 2, MITTE[1] - 2, MITTE[2] - 1], [MITTE[0] + 2, MITTE[1] + 2, MITTE[2] + 1]] } });
    expect((await innen.picker.pickAtScreen(...bildpunkt(0.5, 0.5))).info.level).toBe('fine');
    const aussen = aufbau({ clip: { enabled: true, box: [[MITTE[0] + 0.8, MITTE[1] - 2, MITTE[2] - 1], [MITTE[0] + 2, MITTE[1] + 2, MITTE[2] + 1]] } });
    const r = await aussen.picker.pickAtScreen(...bildpunkt(0.5, 0.5));
    expect(r.point).toBeNull(); expect(r.info.level).toBe('none');
  });
  it('Tiefenfenster: Blatt vor dem Fenster zählt nicht', async () => {
    const { picker } = aufbau();
    const r = await picker.pickAtScreen(...bildpunkt(0.5, 0.5), { tiefenFenster: { min: 3.5, max: 10 } });
    expect(r.point).toBeNull();
  });
  it('Vertex-Fang nur mit eingeschaltetem Fang', async () => {
    const { picker } = aufbau();
    const [x, y] = bildpunkt(0.99, 0.99);
    const ohne = await picker.pickAtScreen(x, y); expect(ohne.info.snap?.result).toBe('none');
    const mit = await picker.pickAtScreen(x, y, { snapEdges: true });
    expect(mit.info.snap?.result).toBe('vertex');
    expect(mit.point![0]).toBeCloseTo(MITTE[0] + 1, 6); expect(mit.point![1]).toBeCloseTo(MITTE[1] + 1, 6);
  });
});

describe('KachelPickSource — Zeitgrenze: kein Ersatzwert', () => {
  const langsam = (): { lade: LadeBytes; frei: () => void } => {
    const wartende: (() => void)[] = [];
    const syn = synthetischesTileset();
    return { lade: (u) => new Promise((ok) => { wartende.push(() => ok(syn.dateien.get(u.split('/').pop()!)!)); }), frei: () => wartende.splice(0).forEach((f) => f()) };
  };
  it('Blatt nicht rechtzeitig: kein Punkt, imprecise mit Tiefe der sichtbaren groben Fläche (nicht empty)', async () => {
    const { lade } = langsam();
    const { picker } = aufbau({ lade, verdeckung: await grobeFlaeche() });
    const r = await picker.pickAtScreen(...bildpunkt(0.5, 0.5), { budgetMs: 20 });
    expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise'); expect(r.info.fineReason).toBe('timeout');
    expect(r.info.source).toBe('mesh'); expect(r.info.level).toBe('none');
    expect(r.info.frontDepthM).toBeCloseTo(abstand(3 - GROB_VERSATZ_M), 6);
  });
  it('ohne gezeichnete Fläche steht der Eintritt des ersten ungeladenen Blattes als Tiefe da', async () => {
    const { lade } = langsam();
    const { picker } = aufbau({ lade });
    const r = await picker.pickAtScreen(...bildpunkt(0.5, 0.5), { budgetMs: 20 });
    expect(r.info.reason).toBe('imprecise'); expect(r.info.frontDepthM).toBeCloseTo(abstand(3 - 0.2), 6);
  });
  it('Abruf nach dem Zeitlimit liegt beim nächsten Klick fertig bereit', async () => {
    const { lade, frei } = langsam();
    const { picker, speicher } = aufbau({ lade });
    const erst = await picker.pickAtScreen(...bildpunkt(0.5, 0.5), { budgetMs: 20 });
    expect(erst.point).toBeNull(); expect(speicher.laeuft('k2.glb')).toBe(true);
    frei(); await vi.waitFor(() => expect(speicher.vorhanden('k2.glb')).not.toBeNull());
    const zweit = await picker.pickAtScreen(...bildpunkt(0.5, 0.5), { budgetMs: 20 });
    expect(zweit.info.level).toBe('fine'); expect(zweit.point![2]).toBeCloseTo(MITTE[2], 9);
  });
  it('Netzfehler: kein Punkt, fineReason error, Folgeversuch lädt neu', async () => {
    let n = 0; const syn = synthetischesTileset();
    const { picker } = aufbau({ lade: async (u) => { if (n++ === 0) throw new Error('HTTP 503'); return syn.dateien.get(u.split('/').pop()!)!; }, verdeckung: await grobeFlaeche() });
    const r = await picker.pickAtScreen(...bildpunkt(0.5, 0.5));
    expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise'); expect(r.info.fineReason).toBe('error');
    expect((await picker.pickAtScreen(...bildpunkt(0.5, 0.5))).info.level).toBe('fine');
  });
  it('gezeichnete Fläche ohne Blatt unter dem Strahl verdeckt, statt „leer“ zu melden (kein Ersatzwert dahinter)', async () => {
    // Netz außerhalb aller Blatt-Hüllquader (x + 3 … 4 m neben dem Tileset), vom Renderer gezeichnet:
    const syn = synthetischesTileset(); const ts = pruefeTileset(syn.json, erwartet);
    const daneben = (await ladeBlattGeo(glb({ ...flaeche(3, 4, -1, 1, 0), knoten: [MITTE[0], MITTE[2], -MITTE[1]] }), ts.wurzel, 'x')).meshes;
    const { picker, abrufe } = aufbau({ verdeckung: daneben, kamera: kamera(3.5, 0) });
    const r = await picker.pickAtScreen(500, 500);
    expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise'); expect(r.info.frontDepthM).toBeCloseTo(3, 6);
    expect(abrufe).toEqual([]);
    // Ohne gezeichnetes Netz an derselben Stelle ist es schlicht leer.
    expect((await aufbau({ kamera: kamera(3.5, 0) }).picker.pickAtScreen(500, 500)).info.reason).toBe('empty');
  });
  it('Blatt hinter einer schwebenden gezeichneten Fläche wird trotzdem gemessen (Verdeckung zählt nur ohne Messtreffer)', async () => {
    const syn = synthetischesTileset(); const ts = pruefeTileset(syn.json, erwartet);
    const schwebend = (await ladeBlattGeo(glb({ ...flaeche(-1, 1, -1, 1, 1), knoten: [MITTE[0], MITTE[2], -MITTE[1]] }), ts.wurzel, 'x')).meshes;
    const r = await aufbau({ verdeckung: schwebend }).picker.pickAtScreen(...bildpunkt(0.5, 0.5));
    expect(r.info.level).toBe('fine'); expect(r.point![2]).toBeCloseTo(MITTE[2], 9);
  });
});

describe('Nächstes Blatt zuerst', () => {
  /** Acht übereinander gestapelte Blätter, Flächen bei z = 7 … 0 (oben zuerst getroffen). */
  function stapel(): { ts: Tileset; dateien: Map<string, ArrayBuffer> } {
    const dateien = new Map<string, ArrayBuffer>(); const kinder: unknown[] = [];
    for (let i = 0; i < 8; i++) {
      const z = 7 - i;
      dateien.set(`s${i}.glb`, glb({ ...flaeche(-1, 1, -1, 1, 0) }));
      kinder.push({ boundingVolume: { box: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.3] }, geometricError: 0, refine: 'REPLACE', content: { uri: `s${i}.glb` },
        transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, MITTE[0], MITTE[1], z, 1] });
    }
    dateien.set('w.glb', glb({ ...flaeche(-1, 1, -1, 1, 0) }));
    const json = { asset: { version: '1.1' }, extras: EXTRAS, root: { boundingVolume: { box: [MITTE[0], MITTE[1], 3.5, 1, 0, 0, 0, 1, 0, 0, 0, 4] },
      geometricError: 1, refine: 'REPLACE', content: { uri: 'w.glb' }, children: kinder } };
    return { ts: pruefeTileset(json, erwartet), dateien };
  }
  it('lädt Wellen von vorn nach hinten und hört auf, sobald ein Treffer vor dem nächsten Eintritt liegt', async () => {
    const { ts, dateien } = stapel();
    const cam = new PerspectiveCamera(45, 1, 0.01, 100);
    cam.position.set(MITTE[0], MITTE[1], 12); cam.up.set(0, 1, 0); cam.lookAt(MITTE[0], MITTE[1], 0); cam.updateMatrixWorld(true);
    const a = aufbau({ tileset: ts, dateien, kamera: cam });
    const r = await a.picker.pickAtScreen(500, 500);
    expect(r.info.level).toBe('fine'); expect(r.point![2]).toBeCloseTo(7, 9);
    // Erste Welle (6 Blätter, die obersten) reicht: die zwei untersten wurden nie angefragt.
    expect(a.abrufe.length).toBe(6); expect(a.abrufe).not.toContain('s6.glb'); expect(a.abrufe).not.toContain('s7.glb');
  });
});

describe('Verbund mit der Punktwolke (CompositePickSource)', () => {
  const wolke = (tiefe: number): PickSource => ({
    pickAtEvent: vi.fn(), warm: vi.fn(),
    pickAtScreen: vi.fn(async (): Promise<RayPickResult> => ({ point: [0, 0, 0], info: { level: 'fine', ms: 0, wideCandidates: 1, nearCandidates: 1, tiefeM: tiefe } })),
    vorschauAtScreen: () => ({ punktWelt: null, tiefeM: null, mitteTiefen: [], fensterTiefen: [], ms: 0 }),
  });
  it('Blatt nicht rechtzeitig + Wolkenpunkt dahinter → kein Wert, „imprecise“ (nie die Wolke hinter der sichtbaren Fläche)', async () => {
    const lade: LadeBytes = () => new Promise(() => undefined);
    const { picker } = aufbau({ lade, verdeckung: await grobeFlaeche() });
    const verbund = new CompositePickSource({ pointcloud: wolke(abstand(3.4)), cloudLayerId: 'punkte', meshes: [picker], preference: () => 'auto', panorama: () => false });
    const r = await verbund.pickAtScreen(...bildpunkt(0.5, 0.5), { budgetMs: 20 });
    expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise'); expect(r.info.source).toBe('mesh');
  });
  it('Blatt da: der nähere Treffer gewinnt, Messen-auf „Mesh“ bevorzugt das Blatt bei Gleichstand', async () => {
    const { picker } = aufbau();
    const verbund = new CompositePickSource({ pointcloud: wolke(abstand(3) + 0.004), cloudLayerId: 'punkte', meshes: [picker], preference: () => 'mesh', panorama: () => false });
    const r = await verbund.pickAtScreen(...bildpunkt(0.5, 0.5));
    expect(r.info.source).toBe('mesh'); expect(r.info.layerId).toBe('kacheln');
  });
});

describe('Vorschau', () => {
  it('zeigt die gezeichnete Fläche (nur Anzeige), ohne Netzabruf', async () => {
    const { picker, abrufe } = aufbau({ verdeckung: await grobeFlaeche() });
    const v = picker.vorschauAtScreen(...bildpunkt(0.5, 0.5));
    expect(v.tiefeM).toBeCloseTo(abstand(3 - GROB_VERSATZ_M), 6); expect(abrufe).toEqual([]);
    expect(picker.vorschauAtScreen(...bildpunkt(3, 0)).punktWelt).toBeNull();
  });
});

