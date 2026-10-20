import { describe, expect, it } from 'vitest';
import { loadManifest, parseManifest, resolveUrl, ManifestError } from '../src/manifest';
import v1 from './fixtures/manifest-v1.json';
import v2 from './fixtures/manifest-v2.json';
import v2m from './fixtures/manifest-v2-migrated.json';

const good = {
  schemaVersion: 1, projectId: 'p', revision: 1,
  source: { filename: 'a.e57', sha256: 'x', sizeBytes: 1 },
  crs: { wkt: null, verticalDatum: null, unit: 'm', declaredBy: 'file' },
  originWorld: [1469, 7107, 1],
  cloud: { url: 'cloud.copc.laz', pointCount: 10, boundsWorld: [[0, 0, 0], [1, 1, 1]], attributes: ['rgb'], scale: 0.001 },
  stations: { url: 'stations.json', count: 0 },
  initialView: { target: [0.5, 0.5, 0.5], distance: 2 },
};

describe('parseManifest', () => {
  it('cloud.pointSourceIsScan: true bleibt, fehlt → undefined, kein Boolean → Fehler', () => {
    expect(parseManifest({ ...good, cloud: { ...good.cloud, pointSourceIsScan: true } }).cloud!.pointSourceIsScan).toBe(true);
    expect(parseManifest(good).cloud!.pointSourceIsScan).toBeUndefined();
    expect(() => parseManifest({ ...good, cloud: { ...good.cloud, pointSourceIsScan: 'ja' } })).toThrow(/pointSourceIsScan/);
  });
  it('cloud.classes (F30): Histogramm bleibt, fehlt → undefined, kaputt → Fehler', () => {
    expect(parseManifest({ ...good, cloud: { ...good.cloud, classes: { '2': 5, '6': 0 } } }).cloud!.classes).toEqual({ '2': 5, '6': 0 });
    expect(parseManifest(good).cloud!.classes).toBeUndefined();
    for (const classes of [[1, 2], { boden: 3 }, { '2': -1 }, { '2': 1.5 }, { '256': 1 }, null])
      expect(() => parseManifest({ ...good, cloud: { ...good.cloud, classes } })).toThrow(/classes/);
  });
  it('akzeptiert ein gültiges Manifest', () => {
    expect(parseManifest(good).cloud!.pointCount).toBe(10);
  });
  it('lehnt falsche Schema-Version mit deutscher Meldung ab', () => {
    expect(() => parseManifest({ ...good, schemaVersion: 5 })).toThrow(ManifestError);
    expect(() => parseManifest({ ...good, schemaVersion: 5 })).toThrow(/Schema-Version 5/);
  });
  it('verlangt originWorld mit drei Zahlen', () => {
    expect(() => parseManifest({ ...good, originWorld: [1, 2] })).toThrow(/originWorld/);
  });

  it('akzeptiert cloud.offset, wenn LAS-Offset vom originWorld abweicht', () => {
    const withOffset = { ...good, cloud: { ...good.cloud, offset: [1470, 7108, 1] } };
    const m = parseManifest(withOffset);
    expect(m.cloud!.offset).toEqual([1470, 7108, 1]);
  });

  it('akzeptiert ein Manifest ohne cloud.offset (Regelfall: Offset == originWorld)', () => {
    const m = parseManifest(good);
    expect(m.cloud!.offset).toBeUndefined();
  });

  it('akzeptiert stations.count > 0', () => {
    const withStations = { ...good, stations: { url: 'stations.json', count: 12 } };
    expect(parseManifest(withStations).stations!.count).toBe(12);
  });

  it('akzeptiert conversion mit mode direct/staged', () => {
    const withConversion = { ...good, conversion: { seconds: 12.5, peakRssMb: 512, mode: 'staged' as const } };
    const m = parseManifest(withConversion);
    expect(m.conversion).toEqual({ seconds: 12.5, peakRssMb: 512, mode: 'staged' });
  });

  it('akzeptiert ein Manifest ohne conversion-Block', () => {
    const m = parseManifest(good);
    expect(m.conversion).toBeUndefined();
  });

  it('toleriert zusätzliche cloud-Felder (readerPointCount, invalidDropped), ohne sie ins Innenmodell zu übernehmen', () => {
    // scanview_import/convert.py schreibt zusätzlich readerPointCount/invalidDropped in cloud;
    // parseManifest prüft nur die Kernfelder. Fremdfelder gehören nicht zum Innenmodell.
    const real = {
      ...good,
      cloud: { ...good.cloud, readerPointCount: 11, invalidDropped: 1 },
    };
    const m = parseManifest(real);
    expect(m.cloud!.pointCount).toBe(10);
    expect((m.cloud as unknown as { readerPointCount?: number }).readerPointCount).toBeUndefined();
  });

  const topview = {
    url: 'topview.png', widthPx: 815, heightPx: 608, pixelSizeM: 0.05,
    upperLeftWorld: [1464.522, 7127.14], pixelAnchor: 'edge', zMaxWorld: null,
  };
  it('akzeptiert einen gültigen topview-Block (Scheibe 3, additiv zu Schema 1)', () => {
    expect(parseManifest({ ...good, topview }).topview?.widthPx).toBe(815);
  });
  it('Manifest ohne topview bleibt gültig (Pakete aus Scheibe 0/1)', () => {
    expect(parseManifest(good).topview).toBeUndefined();
  });
  it('lehnt einen kaputten topview-Block ab', () => {
    expect(() => parseManifest({ ...good, topview: { ...topview, pixelSizeM: 0 } })).toThrow(/topview/);
    expect(() => parseManifest({ ...good, topview: { ...topview, upperLeftWorld: [1] } })).toThrow(/topview/);
    expect(() => parseManifest({ ...good, topview: { ...topview, pixelAnchor: 'center' } })).toThrow(/topview/);
  });
});

describe('resolveUrl', () => {
  it('löst relative Pfade gegen die Paketbasis auf', () => {
    expect(resolveUrl('http://h/data/p/', 'cloud.copc.laz')).toBe('http://h/data/p/cloud.copc.laz');
    expect(resolveUrl('http://h/data/p', 'cloud.copc.laz')).toBe('http://h/data/p/cloud.copc.laz');
  });
});

describe('parseManifest: Pflichtfelder (I3)', () => {
  it.each([
    ['projectId', { ...good, projectId: 3 }, /projectId/],
    ['revision', { ...good, revision: '1' }, /revision/],
    ['cloud.pointCount', { ...good, cloud: { ...good.cloud, pointCount: undefined } }, /cloud\.pointCount/],
    ['cloud.attributes fehlt', { ...good, cloud: { ...good.cloud, attributes: undefined } }, /cloud\.attributes/],
    ['cloud.attributes kein String', { ...good, cloud: { ...good.cloud, attributes: [1] } }, /cloud\.attributes/],
  ])('%s ungültig → ManifestError', (_name, bad, re) => {
    expect(() => parseManifest(bad)).toThrow(ManifestError);
    expect(() => parseManifest(bad)).toThrow(re);
  });
});

describe('loadManifest (I3: Fehlerpfade mit klarer Meldung)', () => {
  const BASE = 'http://h/data/nope/';
  const json = (body: unknown, init: ResponseInit = {}) =>
    Promise.resolve(new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' }, ...init }));

  it('lädt ein gültiges Paket', async () => {
    let asked = '';
    const m = await loadManifest(BASE, (u) => {
      asked = String(u);
      return json(good);
    });
    expect(asked).toBe('http://h/data/nope/project.json');
    expect(m.projectId).toBe('p');
  });
  it('HTML statt JSON (falscher ?p=, Server liefert index.html) → „Kein siteBits-Paket unter …“', async () => {
    const html = () =>
      Promise.resolve(new Response('<!doctype html><html></html>', { headers: { 'Content-Type': 'text/html' } }));
    await expect(loadManifest(BASE, html)).rejects.toThrow(
      /^Kein siteBits-Paket unter http:\/\/h\/data\/nope\/ \(Server liefert eine HTML-Seite/,
    );
  });
  it('kein gültiges JSON (ohne Content-Type) → „Kein siteBits-Paket unter …“', async () => {
    const junk = () => Promise.resolve(new Response('<html>'));
    await expect(loadManifest(BASE, junk)).rejects.toThrow(/Kein siteBits-Paket unter .*kein gültiges JSON/);
  });
  it('404 → „Kein siteBits-Paket unter …: project.json nicht gefunden“', async () => {
    const nf = () => Promise.resolve(new Response('nope', { status: 404 }));
    await expect(loadManifest(BASE, nf)).rejects.toThrow(/Kein siteBits-Paket unter .*project\.json nicht gefunden/);
  });
  it('Netzfehler → „Server nicht erreichbar …“', async () => {
    const down = () => Promise.reject(new TypeError('Failed to fetch'));
    await expect(loadManifest(BASE, down)).rejects.toThrow(/^Server nicht erreichbar \(http:\/\/h\/data\/nope\/project\.json\)/);
  });
  it('ungültiges Manifest → Meldung nennt das Feld', async () => {
    await expect(loadManifest(BASE, () => json({ ...good, cloud: { ...good.cloud, attributes: null } }))).rejects.toThrow(
      /project\.json ungültig: cloud\.attributes/,
    );
  });
});

// Draft-Typ für die destruktiven Mutationen im it.each unten: kein `any`, damit ESLint grün bleibt.
interface V2DraftLayer {
  id?: string;
  provenance?: unknown;
  [key: string]: unknown;
}
interface V2Draft {
  frame?: { unit?: string; [key: string]: unknown };
  layers: V2DraftLayer[];
  [key: string]: unknown;
}

describe('Schema 2 und Innenmodell', () => {
  it('Schema 1 → Ebenen punkte + standpunkte, Scan-Provenienz', () => {
    const p = parseManifest(structuredClone(v1));
    expect(p.schemaVersion).toBe(1);
    expect(p.layers.map((l) => [l.id, l.kind])).toEqual([['punkte', 'pointcloud'], ['standpunkte', 'panoramas']]);
    expect(p.cloud).toBe(p.layers[0]);
    expect(p.cloud!.provenance).toEqual({ kind: 'scan', interpolated: false, scaleVerified: true, scaleSource: 'scanner' });
    expect(p.stations).toMatchObject({ url: 'stations.json', count: 2, pointcloudLayer: 'punkte' });
    expect(p.originWorld).toEqual([100, 200, 10]);
    expect(p.frame.originWorld).toBe(p.originWorld);
  });
  it('migriertes Schema 2 ergibt dasselbe Innenmodell wie Schema 1 (Abnahme A6)', () => {
    const a = parseManifest(structuredClone(v1));
    const b = parseManifest(structuredClone(v2m));
    const strip = (p: ReturnType<typeof parseManifest>) => JSON.parse(JSON.stringify({ ...p, schemaVersion: 0 }));
    // Extra-Felder der Ebene (source, readerPointCount …) gehören nicht zum Innenmodell.
    expect(strip(b)).toEqual(strip(a));
  });
  it('Schema 1 ohne Standpunkte → stations null', () => {
    const j = structuredClone(v1) as Record<string, unknown>;
    j.stations = { url: 'stations.json', count: 0 };
    expect(parseManifest(j).stations).toBeNull();
  });
  it('unbekannte Ebenenarten laden als „nicht unterstützt“', () => {
    const p = parseManifest(structuredClone(v2));
    expect(p.layers.map((l) => ('unsupported' in l ? `${l.id}:x` : l.id))).toEqual(['punkte', 'mesh:x', 'splat:x']);
    expect(p.cloud!.provenance.scaleVerified).toBe(false);
    expect(p.stations).toBeNull();
    expect(p.title).toBe('Beispielskulptur');
  });
  it('Schema 2 ohne Punktwolke ist gültig, cloud null', () => {
    const j = structuredClone(v2) as { layers: unknown[] };
    j.layers = j.layers.slice(1);
    expect(parseManifest(j).cloud).toBeNull();
  });
  it.each([
    ['fehlendes frame', (j: V2Draft) => delete j.frame, 'frame'],
    ['leere layers', (j: V2Draft) => (j.layers = []), 'layers'],
    ['doppelte Ebenen-ID', (j: V2Draft) => (j.layers[1]!.id = 'punkte'), 'doppelt'],
    ['ungültige Ebenen-ID', (j: V2Draft) => (j.layers[0]!.id = 'Punkte Wolke'), 'Ebenen-ID'],
    ['Punktwolke ohne provenance', (j: V2Draft) => delete j.layers[0]!.provenance, 'provenance'],
    [
      'panoramas auf fehlende Wolke',
      (j: V2Draft) => j.layers.push({ id: 'st', kind: 'panoramas', url: 's.json', count: 1, pointcloudLayer: 'nix' }),
      'pointcloudLayer',
    ],
    ['Einheit nicht m', (j: V2Draft) => (j.frame!.unit = 'mm'), 'unit'],
    [
      'crs.wkt weder Text noch null',
      (j: V2Draft) => ((j.frame as { crs: { wkt: unknown } }).crs.wkt = 5),
      'wkt',
    ],
  ])('Schema 2 abgelehnt: %s', (_n, mutate, fragment) => {
    const j = structuredClone(v2) as V2Draft;
    mutate(j);
    expect(() => parseManifest(j)).toThrow(new RegExp(fragment));
  });
  it('Schema 3 abgelehnt mit Meldung', () => {
    expect(() => parseManifest({ ...structuredClone(v2), schemaVersion: 3 })).toThrow(
      'Schema-Version 3 wird nicht unterstützt (erwartet 1 oder 2).',
    );
  });
});

const mesh = {
  id: 'mesh', kind: 'mesh', title: 'Reiter', format: 'gltf',
  url: 'layers/mesh/model.glb', previewUrl: 'layers/mesh/preview.glb', previewColor: 'vertex',
  triangleCount: 952394, boundsWorld: [[-1.3, -1.9, -2.3], [7.7, 7.1, 4]], positionStepM: 0.000137,
  provenance: { kind: 'other', interpolated: true, scaleVerified: false, scaleSource: 'unit:cli' },
};
const schema2 = (extra: object) => ({ ...v2, layers: [v2.layers[0], extra] });
describe('Mesh-Ebene', () => {
  it('liest glTF und behält dieselbe Ebene im Bequemlichkeitsverweis', () => {
    const p = parseManifest(schema2(mesh));
    expect(p.meshes.map((m) => m.id)).toEqual(['mesh']);
    expect(p.meshes[0]).toBe(p.layers[1]);
    expect(parseManifest(good).meshes).toEqual([]);
  });
  it('andere Formate bleiben nicht unterstützt', () => {
    expect(parseManifest(schema2({ ...mesh, format: '3dtiles' })).layers[1]).toMatchObject({ unsupported: true });
  });
  it.each([
    { url: 5 }, { previewUrl: undefined }, { boundsWorld: [[0, 0, 0]] },
    { boundsWorld: [[0, 0, 0], [-1, 1, 1]] }, { provenance: undefined },
    { triangleCount: -1 }, { positionStepM: Infinity }, { previewColor: 'png' },
  ])('lehnt kaputte Felder ab: %j', (patch) => {
    expect(() => parseManifest(schema2({ ...mesh, ...patch }))).toThrow(ManifestError);
  });
  it('previewColor fehlt → texture (ältere Pakete)', () => {
    expect(parseManifest(schema2({ ...mesh, previewColor: undefined })).meshes[0]?.previewColor).toBe('texture');
  });
  const mobil = { mobileUrl: 'layers/mesh/mobile.glb', mobileSizeBytes: 41_000_000, mobileTriangleCount: 400_000 };
  it('Mobilstufe wird gelesen; ohne sie fehlt das Feld (ältere Pakete)', () => {
    expect(parseManifest(schema2({ ...mesh, ...mobil })).meshes[0]?.mobile)
      .toEqual({ url: 'layers/mesh/mobile.glb', sizeBytes: 41_000_000, triangleCount: 400_000 });
    expect(parseManifest(schema2(mesh)).meshes[0]).not.toHaveProperty('mobile');
  });
  it.each([
    { mobileUrl: '' }, { mobileUrl: 3 }, { mobileSizeBytes: 0 }, { mobileSizeBytes: undefined }, { mobileTriangleCount: -1 },
  ])('lehnt eine kaputte Mobilstufe ab: %j', (patch) => {
    expect(() => parseManifest(schema2({ ...mesh, ...mobil, ...patch }))).toThrow(ManifestError);
  });
});

it('fehlende Startansicht wird als solche markiert; ungültige bleibt ein Fehler', () => {
  expect(parseManifest({ ...good, initialView: undefined }).initialViewDeclared).toBe(false);
  expect(parseManifest(good).initialViewDeclared).toBe(true);
  expect(() => parseManifest({ ...good, initialView: null })).toThrow(ManifestError);
});

describe('frame.undulation (F23)', () => {
  it('wird gelesen, wenn vollständig, sonst ignoriert', () => {
    const j = structuredClone(v2) as { frame: Record<string, unknown> };
    j.frame.undulation = { atOriginM: 40.163, maxDeviationM: 0.004, grid: 'GCG2016' };
    expect(parseManifest(j).frame.undulation).toEqual({ atOriginM: 40.163, maxDeviationM: 0.004, grid: 'GCG2016' });
    j.frame.undulation = { atOriginM: 'x', maxDeviationM: 0.004, grid: 'GCG2016' };
    expect(parseManifest(j).frame.undulation).toBeUndefined();
  });
});
