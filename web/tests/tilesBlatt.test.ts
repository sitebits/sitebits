import { describe, expect, it, vi } from 'vitest';
import { DoubleSide, FrontSide, Mesh, Vector3 } from 'three';
import { BlattSpeicher, ladeBlattGeo } from '../src/tilesBlatt';
import { pruefeTileset } from '../src/tilesMath';
import { flaeche, glb, MITTE, synthetischesTileset } from './tilesFixture';

const erwartet = { crs: 'EPSG:25833', heightSystem: 'DHHN2016' };
const { json, dateien } = synthetischesTileset();
const ts = pruefeTileset(json, erwartet);
const blatt = (uri: string) => ts.blaetter.find((b) => b.uri === uri)!;
const eckenWelt = (m: Mesh): number[][] => {
  const p = m.geometry.attributes.position!; const out: number[][] = [];
  for (let i = 0; i < p.count; i++) out.push(new Vector3().fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld).toArray());
  return out;
};

describe('ladeBlattGeo', () => {
  it('legt Blatt-Ecken in die Welt: Kachelkette + Y→Z + Knotentransformation, auf Zehntelmillimeter', async () => {
    const g = await ladeBlattGeo(dateien.get('g1.glb')!, blatt('g1.glb'), 'g1.glb');
    expect(g.dreiecke).toBe(2);
    const ecken = eckenWelt(g.meshes[0]!);
    // G1: x ∈ [−1,0], y ∈ [−1,0] relativ zur Mitte, Höhe = Mitte.
    const erw = [[-1, -1], [0, -1], [0, 0], [-1, 0]].map(([x, y]) => [MITTE[0] + x!, MITTE[1] + y!, MITTE[2]]);
    ecken.forEach((e, i) => e.forEach((v, k) => expect(v).toBeCloseTo(erw[i]![k]!, 9)));
  });
  it('Wurzel-GLB: Ursprung als Knoten-Translation (glTF Y oben) ergibt dieselben Weltkoordinaten', async () => {
    const g = await ladeBlattGeo(dateien.get('wurzel.glb')!, ts.wurzel, 'wurzel.glb');
    const ecken = eckenWelt(g.meshes[0]!);
    for (const [i, x, y] of [[0, -1, -1], [2, 1, 1]] as const) {
      expect(ecken[i]![0]).toBeCloseTo(MITTE[0] + x, 9); expect(ecken[i]![1]).toBeCloseTo(MITTE[1] + y, 9);
      expect(ecken[i]![2]).toBeCloseTo(MITTE[2] + 0.05, 9);
    }
  });
  it('lädt keine Texturen (kein Bild-Abruf, in Node gäbe es keinen Bild-Lader) und behält nur Positionen', async () => {
    const f = flaeche(-0.5, 0.5, -0.5, 0.5, 0);
    const g = await ladeBlattGeo(glb({ ...f, mitTextur: true }), blatt('g1.glb'), 'x.glb');
    expect(Object.keys(g.meshes[0]!.geometry.attributes)).toEqual(['position']);
    expect(g.bytes).toBe(4 * 3 * 4 + 6 * 2);
  });
  it('Seitigkeit bleibt wie in der Darstellung (doppelseitig bzw. nur Vorderseite)', async () => {
    const f = flaeche(-0.5, 0.5, -0.5, 0.5, 0);
    const zwei = await ladeBlattGeo(glb({ ...f, zweiseitig: true }), blatt('g1.glb'), 'a');
    const ein = await ladeBlattGeo(glb({ ...f, zweiseitig: false }), blatt('g1.glb'), 'b');
    expect((zwei.meshes[0]!.material as { side: number }).side).toBe(DoubleSide);
    expect((ein.meshes[0]!.material as { side: number }).side).toBe(FrontSide);
  });
  it('wirft bei kaputtem GLB und bei GLB ohne Mesh', async () => {
    await expect(ladeBlattGeo(new ArrayBuffer(20), blatt('g1.glb'), 'x')).rejects.toThrow('Ungültiges GLB');
  });
});

describe('BlattSpeicher', () => {
  const knoten = blatt('g1.glb');
  it('ein Abruf je Blatt, gemeinsame Abrufe, spätere Abfrage aus dem Speicher', async () => {
    const lade = vi.fn(async (u: string) => dateien.get(u.split('/').pop()!)!);
    const s = new BlattSpeicher({ lade });
    const [a, b] = await Promise.all([s.hole('http://x/g1.glb', 'g1.glb', knoten), s.hole('http://x/g1.glb', 'g1.glb', knoten)]);
    expect(a).toBe(b); expect(lade).toHaveBeenCalledTimes(1);
    expect(await s.hole('http://x/g1.glb', 'g1.glb', knoten)).toBe(a); expect(lade).toHaveBeenCalledTimes(1);
    expect(s.anzahl).toBe(1); expect(s.belegt).toBe(a.bytes); expect(s.laeuft('g1.glb')).toBe(false);
  });
  it('Fehler wird nicht gemerkt: der nächste Versuch lädt neu', async () => {
    let n = 0;
    const s = new BlattSpeicher({ lade: async () => { if (n++ === 0) throw new Error('HTTP 503'); return dateien.get('g1.glb')!; } });
    await expect(s.hole('u', 'g1.glb', knoten)).rejects.toThrow('503');
    expect(s.laeuft('g1.glb')).toBe(false);
    expect((await s.hole('u', 'g1.glb', knoten)).dreiecke).toBe(2);
  });
  it('Abruf über das Zeitlimit hinaus läuft zu Ende und liegt dann bereit', async () => {
    let frei!: () => void;
    const s = new BlattSpeicher({ lade: () => new Promise((ok) => { frei = () => ok(dateien.get('g1.glb')!); }) });
    const p = s.hole('u', 'g1.glb', knoten);
    expect(s.vorhanden('g1.glb')).toBeNull(); expect(s.laeuft('g1.glb')).toBe(true);
    frei(); await p;
    expect(s.vorhanden('g1.glb')).not.toBeNull();
  });
  it('Byte-Grenze wirft zuletzt Benutztes zuerst hinaus, das neueste bleibt', async () => {
    const s = new BlattSpeicher({ lade: async (u) => dateien.get(u)!, maxBytes: 100 });
    const g1 = await s.hole('g1.glb', 'g1.glb', blatt('g1.glb'));
    expect(g1.bytes).toBe(4 * 3 * 4 + 6 * 2); // 60 Byte
    await s.hole('g2.glb', 'g2.glb', blatt('g2.glb'));
    expect(s.anzahl).toBe(1); expect(s.vorhanden('g1.glb')).toBeNull(); expect(s.vorhanden('g2.glb')).not.toBeNull();
    const s2 = new BlattSpeicher({ lade: async (u) => dateien.get(u)!, maxBytes: 1000 });
    await s2.hole('g1.glb', 'g1.glb', blatt('g1.glb')); await s2.hole('g2.glb', 'g2.glb', blatt('g2.glb'));
    s2.vorhanden('g1.glb'); // g1 zuletzt benutzt → g2 ist das älteste
    await s2.hole('k2.glb', 'k2.glb', blatt('k2.glb'));
    expect(s2.anzahl).toBe(3);
    s2.leeren(); expect(s2.anzahl).toBe(0); expect(s2.belegt).toBe(0);
  });
});
