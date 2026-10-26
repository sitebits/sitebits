import { describe, expect, it } from 'vitest';
import { Matrix4, Ray, Vector3 } from 'three';
import {
  blattKandidaten, epsgAusWkt, LAGE_TOLERANZ_M, pruefeCrs, pruefeLage, pruefeTileset, relativerPfad, strahlBoxIntervall,
  TILES_MAX_TIEFE, TilesetFehler,
} from '../src/tilesMath';
import { EXTRAS, MITTE, synthetischesTileset } from './tilesFixture';

const erwartet = { crs: 'EPSG:25833', heightSystem: 'DHHN2016' };
const fix = (): Record<string, unknown> => structuredClone(synthetischesTileset().json);
type Knoten = { children?: Knoten[]; [k: string]: unknown };
const wurzel = (j: Record<string, unknown>): Knoten => j.root as Knoten;
const ab = (j: Record<string, unknown>): Knoten => wurzel(j).children![0]!.children![0]!;

describe('pruefeTileset', () => {
  it('liest die Form der Kachler-Ausgabe: Wurzel ohne transform, absolute Kinder, relative Enkel', () => {
    const ts = pruefeTileset(fix(), erwartet);
    expect(ts.kachelzahl).toBe(5);
    expect(ts.blaetter.map((b) => b.uri).sort()).toEqual(['g1.glb', 'g2.glb', 'k2.glb']);
    const g1 = ts.blaetter.find((b) => b.uri === 'g1.glb')!;
    // Kind absolut (365000, 5805999.5, 70) + Enkel relativ (−0,5, 0, 0): die Enkel-Kachel liegt bei x = 364999,5.
    expect(new Vector3().setFromMatrixPosition(g1.welt).toArray()).toEqual([MITTE[0] - 0.5, MITTE[1] - 0.5, MITTE[2]]);
    expect(ts.weltBox[0]).toEqual([MITTE[0] - 1, MITTE[1] - 1, MITTE[2] - 0.2]);
    expect(ts.weltBox[1]).toEqual([MITTE[0] + 1, MITTE[1] + 1, MITTE[2] + 0.2]);
  });
  it('Lage der Gruppe verschiebt alles', () => {
    const ts = pruefeTileset(fix(), erwartet, new Matrix4().makeTranslation(10, 0, 0));
    expect(ts.weltBox[0][0]).toBe(MITTE[0] - 1 + 10);
  });
  it('Wurzel darf eine Verschiebung tragen (dann gilt sie für alle Kinder)', () => {
    const j = fix(); wurzel(j).transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1];
    const ts = pruefeTileset(j, erwartet);
    expect(ts.weltBox[0][0]).toBe(MITTE[0] - 1 + 5);
    expect(new Vector3().setFromMatrixPosition(ts.blaetter.find((b) => b.uri === 'k2.glb')!.welt).x).toBe(MITTE[0] + 5);
  });
  it.each([
    ['CRS fehlt', (j: Record<string, unknown>) => { delete j.extras; }, /coordinateReferenceSystem/],
    ['falsches CRS', (j: Record<string, unknown>) => { j.extras = { ...EXTRAS, coordinateReferenceSystem: 'EPSG:4326' }; }, /coordinateReferenceSystem/],
    ['falsches Höhensystem', (j: Record<string, unknown>) => { j.extras = { ...EXTRAS, heightSystem: 'DHHN92' }; }, /heightSystem/],
    ['Achse nicht Z', (j: Record<string, unknown>) => { j.extras = { ...EXTRAS, axis: 'Y' }; }, /axis/],
    ['Einheit nicht m', (j: Record<string, unknown>) => { j.extras = { ...EXTRAS, unit: 'ft' }; }, /unit/],
    ['asset.version 2', (j: Record<string, unknown>) => { j.asset = { version: '2.0' }; }, /asset\.version/],
    ['root fehlt', (j: Record<string, unknown>) => { delete j.root; }, /root fehlt/],
    ['Wurzel ohne refine', (j: Record<string, unknown>) => { delete wurzel(j).refine; }, /refine REPLACE/],
    ['refine ADD', (j: Record<string, unknown>) => { ab(j).refine = 'ADD'; }, /nur refine REPLACE/],
    ['Hüllquader fehlt', (j: Record<string, unknown>) => { delete (ab(j).boundingVolume as Record<string, unknown>).box; }, /boundingVolume\.box/],
    ['Hüllquader sphere', (j: Record<string, unknown>) => { ab(j).boundingVolume = { sphere: [0, 0, 0, 1] }; }, /boundingVolume\.box/],
    ['Hüllquader ohne Ausdehnung', (j: Record<string, unknown>) => { ab(j).boundingVolume = { box: [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1] }; }, /ohne Ausdehnung/],
    ['Blatt mit Fehler > 0', (j: Record<string, unknown>) => { ab(j).geometricError = 0.01; }, /feinste Stufe muss 0 sein/],
    ['Fehler nicht streng fallend', (j: Record<string, unknown>) => { wurzel(j).children![0]!.geometricError = 0.4; }, /streng fallen/],
    ['negativer Fehler', (j: Record<string, unknown>) => { ab(j).geometricError = -1; }, /geometricError/],
    ['Drehung im transform', (j: Record<string, unknown>) => { ab(j).transform = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }, /reine Verschiebung/],
    ['Maßstab im transform', (j: Record<string, unknown>) => { ab(j).transform = [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1]; }, /reine Verschiebung/],
    ['transform zu kurz', (j: Record<string, unknown>) => { ab(j).transform = [1, 0, 0]; }, /transform ungültig/],
    ['uri mit ..', (j: Record<string, unknown>) => { ab(j).content = { uri: '../fremd.glb' }; }, /relativer Pfad/],
    ['uri absolut', (j: Record<string, unknown>) => { ab(j).content = { uri: '/etc/x.glb' }; }, /relativer Pfad/],
    ['uri http', (j: Record<string, unknown>) => { ab(j).content = { uri: 'https://fremd.example/x.glb' }; }, /relativer Pfad/],
    ['uri data', (j: Record<string, unknown>) => { ab(j).content = { uri: 'data:model/gltf-binary;base64,AAAA' }; }, /relativer Pfad/],
    ['externes Tileset', (j: Record<string, unknown>) => { ab(j).content = { uri: 'extern/tileset.json' }; }, /externe Tilesets/],
    ['children keine Liste', (j: Record<string, unknown>) => { (wurzel(j) as Record<string, unknown>).children = 'x'; }, /children/],
  ])('lehnt ab: %s', (_n, aendern, muster) => {
    const j = fix(); aendern(j);
    expect(() => pruefeTileset(j, erwartet)).toThrow(TilesetFehler);
    expect(() => pruefeTileset(j, erwartet)).toThrow(muster);
  });
  it('Baum tiefer als die Grenze wird abgelehnt', () => {
    let k: Knoten = { boundingVolume: { box: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1] }, geometricError: 0, content: { uri: 'x.glb' } };
    for (let i = 1; i <= TILES_MAX_TIEFE + 1; i++) k = { boundingVolume: { box: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1] }, geometricError: i, content: { uri: 'x.glb' }, children: [k] };
    k.refine = 'REPLACE';
    expect(() => pruefeTileset({ asset: { version: '1.1' }, extras: EXTRAS, root: k }, erwartet)).toThrow(/tiefer/);
  });
  it('Tileset ohne Blatt mit Inhalt wird abgelehnt', () => {
    const j = fix(); delete wurzel(j).children;
    wurzel(j).geometricError = 0; delete wurzel(j).content;
    expect(() => pruefeTileset(j, erwartet)).toThrow(/kein Blatt mit Inhalt/);
  });
});

describe('relativerPfad', () => {
  it.each(['a.glb', 'texturen/1.jpg', 'a/b/c.glb'])('akzeptiert %s', (p) => expect(relativerPfad(p)).toBe(true));
  it.each(['', '/a.glb', '../a.glb', 'a/../b.glb', './a.glb', 'a//b.glb', 'a\\b.glb', 'http://x/a.glb', 'file:///a.glb', 'a%2e%2e/b.glb', 'a.glb?x=1', 'a.glb#x', 5, null])('lehnt %j ab', (p) => expect(relativerPfad(p)).toBe(false));
});

describe('Lage und CRS', () => {
  const ts = pruefeTileset(fix(), erwartet);
  it('Hüllquader passt zu boundsWorld innerhalb der Toleranz', () => {
    expect(() => pruefeLage(ts.weltBox, ts.weltBox)).not.toThrow();
    expect(() => pruefeLage(ts.weltBox, [[ts.weltBox[0][0] - LAGE_TOLERANZ_M, ...ts.weltBox[0].slice(1)] as [number, number, number], ts.weltBox[1]])).not.toThrow();
  });
  it('Versatz > Toleranz (falsch gelegtes Modell) wird abgelehnt', () => {
    const b: [number, number, number][] = [[...ts.weltBox[0]], [...ts.weltBox[1]]];
    b[0]![1] += LAGE_TOLERANZ_M + 0.5;
    expect(() => pruefeLage(ts.weltBox, [b[0]!, b[1]!])).toThrow(/Lage/);
    expect(() => pruefeLage(ts.weltBox, [[0, 0, 0], [1, 1, 1]])).toThrow(/Lage/);
  });
  const wkt = 'PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89",DATUM["ETRS_1989",SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],AUTHORITY["EPSG","6258"]],AUTHORITY["EPSG","4258"]],AUTHORITY["EPSG","25833"]]';
  it('EPSG-Code aus WKT (letzte AUTHORITY, WKT1 und WKT2)', () => {
    expect(epsgAusWkt(wkt)).toBe(25833);
    expect(epsgAusWkt('PROJCRS["x",ID["EPSG",25832]]')).toBe(25832);
    expect(epsgAusWkt(null)).toBeNull(); expect(epsgAusWkt('unbekannt')).toBeNull();
  });
  it('CRS der Kacheln darf dem Paketrahmen nicht widersprechen; ohne erkennbares CRS kein Einspruch', () => {
    expect(() => pruefeCrs('EPSG:25833', wkt)).not.toThrow();
    expect(() => pruefeCrs('EPSG:25832', wkt)).toThrow(/passt nicht/);
    expect(() => pruefeCrs('lokal', wkt)).toThrow(/passt nicht/);
    expect(() => pruefeCrs('EPSG:25832', null)).not.toThrow();
  });
});

describe('Strahl gegen Kachelbaum', () => {
  const ts = pruefeTileset(fix(), erwartet);
  const von = (x: number, y: number, z = MITTE[2] + 3): Ray => new Ray(new Vector3(x, y, z), new Vector3(0, 0, -1));
  it('Intervall: Eintritt/Austritt entlang des Strahls, aus dem Inneren ab 0', () => {
    const wurz = ts.wurzel;
    const iv = strahlBoxIntervall(von(MITTE[0], MITTE[1]), wurz)!;
    expect(iv[0]).toBeCloseTo(3 - 0.2, 9); expect(iv[1]).toBeCloseTo(3 + 0.2, 9);
    expect(strahlBoxIntervall(von(MITTE[0], MITTE[1], MITTE[2]), wurz)![0]).toBe(0);
    expect(strahlBoxIntervall(von(MITTE[0] + 5, MITTE[1]), wurz)).toBeNull();
    expect(strahlBoxIntervall(new Ray(new Vector3(MITTE[0], MITTE[1], MITTE[2] + 3), new Vector3(0, 0, 1)), wurz)).toBeNull();
    // waagerechter Strahl durch die Box (Richtungsanteil 0 in z, innerhalb der Platte)
    expect(strahlBoxIntervall(new Ray(new Vector3(MITTE[0] - 5, MITTE[1], MITTE[2]), new Vector3(1, 0, 0)), wurz)![0]).toBeCloseTo(4, 9);
    expect(strahlBoxIntervall(new Ray(new Vector3(MITTE[0] - 5, MITTE[1], MITTE[2] + 1), new Vector3(1, 0, 0)), wurz)).toBeNull();
  });
  it('findet genau das Blatt unter dem Strahl, auch wenn die Eltern-Hierarchie tiefer reicht', () => {
    expect(blattKandidaten(ts, von(MITTE[0] - 0.7, MITTE[1] - 0.5)).map((k) => k.knoten.uri)).toEqual(['g1.glb']);
    expect(blattKandidaten(ts, von(MITTE[0] + 0.7, MITTE[1] - 0.5)).map((k) => k.knoten.uri)).toEqual(['g2.glb']);
    expect(blattKandidaten(ts, von(MITTE[0], MITTE[1] + 0.5)).map((k) => k.knoten.uri)).toEqual(['k2.glb']);
    expect(blattKandidaten(ts, von(MITTE[0] + 9, MITTE[1])).length).toBe(0);
  });
  it('Strahl auf der Nahtkante trifft beide Blätter, sortiert nach Eintritt dann Nummer', () => {
    const k = blattKandidaten(ts, von(MITTE[0], MITTE[1] - 0.5));
    expect(k.map((x) => x.knoten.uri).sort()).toEqual(['g1.glb', 'g2.glb']);
    expect(k[0]!.ein).toBeCloseTo(k[1]!.ein, 9);
  });
  it('maxT kappt Blätter jenseits des Tiefenfensters', () => {
    expect(blattKandidaten(ts, von(MITTE[0] - 0.7, MITTE[1] - 0.5), 1).length).toBe(0);
  });
});
