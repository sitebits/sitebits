import { describe, expect, it } from 'vitest';
import { FACES, levelUrl, loadStations, ohnePanoramen, parseStations, StationsError } from '../src/stations';
import type { FaceName, Quat } from '../src/stations';
import type { PanoramasLayer } from '../src/manifest';

const S = 0.7071067811865476;
const FACE_ROT: Record<FaceName, Quat> = {
  nz: [1, 0, 0, 0], pz: [0, 1, 0, 0], nx: [S, 0, S, 0], px: [S, 0, -S, 0], py: [S, S, 0, 0], ny: [S, -S, 0, 0],
};

function v2Station(id = 'S001'): Record<string, unknown> {
  const images = FACES.map((f) => ({
    guid: '0000000000000000', kind: 'pinhole', face: f,
    pose: { translation: [10, 20, 1.5], rotationQuat: FACE_ROT[f] },
    focalLength: 0.01, pixelWidth: 0.01 * 2 / 64, pixelHeight: 0.01 * 2 / 64, principalPoint: [31.5, 31.5],
    imageWidth: 64, imageHeight: 64,
  }));
  return {
    id, scanGuid: 'g', name: 'Scan 1',
    pose: { translation: [10, 20, 1.5], rotationQuat: [1, 0, 0, 0] },
    groundZ: 0.2,
    panorama: {
      kind: 'cube', faceSize: 64, levels: [32, 64], levelFile: '{level}.jpg',
      faces: Object.fromEntries(FACES.map((f) => [f, `panos/${id}/${f}`])),
      faceImages: Object.fromEntries(FACES.map((f, i) => [f, i])),
    },
    images,
  };
}

describe('parseStations', () => {
  it('Schema 2: Panorama mit Seiten in fester Reihenfolge und Bild je Seite', () => {
    const set = parseStations({ schemaVersion: 2, stations: [v2Station()] });
    const s = set.stations[0]!;
    expect(set.schemaVersion).toBe(2);
    expect(s.panoramaIssue).toBe('none');
    expect(s.groundZ).toBe(0.2);
    expect(s.panorama!.faces.map((f) => f.name)).toEqual([...FACES]);
    expect(s.panorama!.faces[0]!.image.face).toBe('px');
    expect(s.panorama!.faces[0]!.image.imageWidth).toBe(64);
  });
  it('Schema 1 (ohne schemaVersion): Standpunkt ja, Panorama nein, Grund "legacy"', () => {
    const old = v2Station();
    delete (old.panorama as Record<string, unknown>).faceImages;
    const s = parseStations({ stations: [{ ...old, groundZ: undefined }] }).stations[0]!;
    expect(s.panorama).toBeNull();
    expect(s.panoramaIssue).toBe('legacy');
    expect(s.groundZ).toBeNull();
    expect(s.pose.translation).toEqual([10, 20, 1.5]);
  });
  it('panorama: null → Grund "absent" (Halle)', () => {
    const s = parseStations({ schemaVersion: 2, stations: [{ ...v2Station(), panorama: null, images: [] }] }).stations[0]!;
    expect(s.panorama).toBeNull();
    expect(s.panoramaIssue).toBe('absent');
  });
  it('kaputte faceImages machen nur diese Station unbrauchbar', () => {
    const bad = v2Station('S002');
    (bad.panorama as { faceImages: Record<string, number> }).faceImages.px = 99;
    const set = parseStations({ schemaVersion: 2, stations: [v2Station('S001'), bad] });
    expect(set.stations[0]!.panoramaIssue).toBe('none');
    expect(set.stations[1]!.panorama).toBeNull();
    expect(set.stations[1]!.panoramaIssue).toBe('invalid');
  });
  it('Seite verweist auf ein Bild einer anderen Seite → ungültig', () => {
    const bad = v2Station();
    (bad.panorama as { faceImages: Record<string, number> }).faceImages.px = 1; // Bild 1 ist nx
    expect(parseStations({ schemaVersion: 2, stations: [bad] }).stations[0]!.panoramaIssue).toBe('invalid');
  });
  it('levelFile ohne {level} → ungültig', () => {
    const bad = v2Station();
    (bad.panorama as { levelFile: string }).levelFile = 'bild.jpg';
    expect(parseStations({ schemaVersion: 2, stations: [bad] }).stations[0]!.panoramaIssue).toBe('invalid');
  });
  it('unbekannte Schema-Version, doppelte oder unsichere IDs, fehlende Pose → Fehler', () => {
    expect(() => parseStations({ schemaVersion: 3, stations: [] })).toThrow(StationsError);
    expect(() => parseStations({ schemaVersion: 2, stations: [v2Station('S1'), v2Station('S1')] })).toThrow(/doppelt/);
    expect(() => parseStations({ schemaVersion: 2, stations: [v2Station('S 1<')] })).toThrow(/ID/);
    expect(() => parseStations({ schemaVersion: 2, stations: [{ ...v2Station(), pose: {} }] })).toThrow(/Pose/);
    expect(() => parseStations({ schemaVersion: 2 })).toThrow(/stations/);
  });
});

describe('parseStations: pointSourceId', () => {
  it('liest die Scan-ID der Station', () => {
    const s = parseStations({ schemaVersion: 2, stations: [{ ...v2Station(), pointSourceId: 3 }] }).stations[0]!;
    expect(s.pointSourceId).toBe(3);
  });
  it.each([undefined, null, 0, 2.5, 70000, '3'])('%s → unbekannt (null)', (x) => {
    const s = parseStations({ schemaVersion: 2, stations: [{ ...v2Station(), pointSourceId: x }] }).stations[0]!;
    expect(s.pointSourceId).toBeNull();
  });
});

describe('levelUrl', () => {
  it('setzt die Stufe in levelFile ein', () => {
    const p = parseStations({ schemaVersion: 2, stations: [v2Station()] }).stations[0]!.panorama!;
    expect(levelUrl('http://h/data/pkg/', p, p.faces[2]!, 64)).toBe('http://h/data/pkg/panos/S001/py/64.jpg');
  });
});

const panoramasLayer = (overrides: Partial<PanoramasLayer> = {}): PanoramasLayer => ({
  id: 'standpunkte',
  kind: 'panoramas',
  title: 'Standpunkte',
  url: 'stations.json',
  count: 1,
  pointcloudLayer: 'punkte',
  ...overrides,
});

describe('loadStations', () => {
  it('keine Panorama-Ebene → leere Menge ohne Anfrage', async () => {
    const set = await loadStations('http://h/p/', { stations: null }, () => {
      throw new Error('darf nicht laden');
    });
    expect(set.stations).toEqual([]);
  });
  it('count 0 → leere Menge ohne Anfrage', async () => {
    const set = await loadStations('http://h/p/', { stations: panoramasLayer({ count: 0 }) }, () => {
      throw new Error('darf nicht laden');
    });
    expect(set.stations).toEqual([]);
  });
  it('404 → StationsError mit verständlicher Meldung', async () => {
    const f = (async () => new Response('x', { status: 404 })) as typeof fetch;
    await expect(loadStations('http://h/p/', { stations: panoramasLayer({ count: 3 }) }, f)).rejects.toThrow(
      /Standpunkte nicht lesbar/,
    );
  });
  it('lädt und prüft', async () => {
    const f = (async () => Response.json({ schemaVersion: 2, stations: [v2Station()] })) as typeof fetch;
    const set = await loadStations('http://h/p/', { stations: panoramasLayer({ count: 1 }) }, f);
    expect(set.stations[0]!.id).toBe('S001');
  });
});

describe('ohnePanoramen (Server-Tor F20)', () => {
  it('Standpunkte bleiben, Panoramen gelten als gesperrt, ohne Panorama bleibt unverändert', () => {
    const ohne = { ...v2Station('S002'), panorama: null };
    const set = ohnePanoramen(parseStations({ schemaVersion: 2, stations: [v2Station(), ohne] }));
    expect(set.stations.map((s) => s.id)).toEqual(['S001', 'S002']);
    expect(set.stations[0]!.panorama).toBeNull();
    expect(set.stations[0]!.panoramaIssue).toBe('gesperrt');
    expect(set.stations[1]!.panoramaIssue).toBe('absent');
    expect(set.stations[0]!.pose.translation).toEqual([10, 20, 1.5]);
  });
});
