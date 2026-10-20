// Standpunkt-Marker ohne WebGL: Bildschirm-Marken im Panorama, flache Scheiben im Orbit.
import { describe, expect, it, vi } from 'vitest';
import { Group, Mesh, PerspectiveCamera, Sprite } from 'three';
import { attachStationLayer } from '../src/stationLayer';
import type { Station } from '../src/stations';
import type { Viewer } from '../src/viewer';

const st = (id: string, x: number, groundZ: number | null): Station => ({
  id, name: id, pose: { translation: [x, 0, 1.6], rotationQuat: [1, 0, 0, 0] }, groundZ, pointSourceId: null,
  panorama: { kind: 'cube', faceSize: 64, levels: [64], levelFile: '{level}.jpg', faces: [] }, panoramaIssue: 'none',
});

function viewer(): { v: Viewer; root: Group } {
  const root = new Group();
  const camera = new PerspectiveCamera(60, 1, 0.1, 1000);
  const v = { instance: { threeObjects: root, notifyChange: vi.fn(), view: { camera },
    domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }) } } } as unknown as Viewer;
  return { v, root };
}

const sichtbar = <T,>(root: Group, typ: new (...a: never[]) => T): T[] => {
  const out: T[] = [];
  root.traverseVisible((o) => { if (o instanceof typ) out.push(o as T); });
  return out;
};

describe('Standpunkt-Marker', () => {
  const list = [st('A', 0, null), st('B', 6, null), st('C', 12, 0)];
  it('Orbit: flache Scheiben, keine Bildschirm-Marken', () => {
    const { v, root } = viewer();
    attachStationLayer(v, list).showAll();
    expect(sichtbar(root, Mesh).filter((m) => !(m instanceof Sprite))).toHaveLength(3);
    expect(sichtbar(root, Sprite)).toHaveLength(0);
  });
  it('Panorama: Nachbarn als Bildschirm-Marken fester Größe, auch ohne Bodenhöhe', () => {
    const { v, root } = viewer();
    const layer = attachStationLayer(v, list);
    layer.showNeighboursOf('A');
    const marken = sichtbar(root, Sprite);
    expect(marken.map((m) => m.name).sort()).toEqual(['station-marke-B', 'station-marke-C']);
    for (const m of marken) expect(m.material.sizeAttenuation).toBe(false);
    // Ohne groundZ auf Scannerhöhe, mit groundZ am Boden — wie die Scheiben.
    expect(marken.find((m) => m.name === 'station-marke-B')!.position.z).toBeCloseTo(1.6, 1); // + 2 cm Anhebung wie die Scheiben
    expect(marken.find((m) => m.name === 'station-marke-C')!.position.z).toBeCloseTo(0, 1);
    expect(sichtbar(root, Mesh).filter((m) => !(m instanceof Sprite))).toHaveLength(0);
    layer.showAll();
    expect(sichtbar(root, Sprite)).toHaveLength(0);
  });
});
