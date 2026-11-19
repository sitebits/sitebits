import { describe, expect, it } from 'vitest';
import { markerPosition, nearestStation, nearestStation3d, neighbourInDirection, neighbours, pickScreenMarker } from '../src/stationMath';
import type { Station } from '../src/stations';

const st = (id: string, x: number, y: number, z = 1.5, pano = true, groundZ: number | null = 0): Station => ({
  id, name: id, pose: { translation: [x, y, z], rotationQuat: [1, 0, 0, 0] }, groundZ, pointSourceId: null,
  panorama: pano ? ({ kind: 'cube', faceSize: 64, levels: [64], levelFile: '{level}.jpg', faces: [] }) : null,
  panoramaIssue: pano ? 'none' : 'absent',
});

describe('markerPosition', () => {
  it('am Boden, wenn groundZ belegt ist', () => expect(markerPosition(st('A', 1, 2, 1.5, true, 0.2))).toEqual([1, 2, 0.2]));
  it('sonst auf Scannerhöhe (nichts geraten)', () => expect(markerPosition(st('A', 1, 2, 1.5, true, null))).toEqual([1, 2, 1.5]));
});

describe('nearestStation', () => {
  const list = [st('A', 0, 0), st('B', 10, 0, 1.5, false), st('C', 20, 0)];
  it('nächster in XY', () => expect(nearestStation(list, [9, 1])?.id).toBe('B'));
  it('nur mit Panorama', () => expect(nearestStation(list, [9, 1], { requirePanorama: true })?.id).toBe('A'));
  it('leere Liste → null', () => expect(nearestStation([], [0, 0])).toBeNull());
  it('3D mit Höchstabstand', () => {
    expect(nearestStation3d(list, [19, 0, 1.5])?.id).toBe('C');
    expect(nearestStation3d(list, [100, 0, 0], { maxDistM: 30 })).toBeNull();
  });
});

describe('neighbours', () => {
  const list = [st('A', 0, 0), st('B', 5, 0), st('C', 30, 0), st('D', 3, 0, 1.5, false), st('E', 1, 0)];
  it('sortiert, ohne sich selbst, ohne Panorama-lose, innerhalb 25 m', () =>
    expect(neighbours(list, 'A').map((s) => s.id)).toEqual(['E', 'B']));
  it('Einzelstandpunkt → keine Nachbarn', () => expect(neighbours([st('A', 0, 0)], 'A')).toEqual([]));
  it('unbekannte ID → keine Nachbarn', () => expect(neighbours(list, 'Z')).toEqual([]));
  it('Höchstzahl', () => expect(neighbours(list, 'A', 100, 1).map((s) => s.id)).toEqual(['E']));
});

describe('pickScreenMarker', () => {
  const ms = [
    { id: 'A', x: 100, y: 100, behind: false },
    { id: 'B', x: 110, y: 100, behind: false },
    { id: 'C', x: 101, y: 100, behind: true },
  ];
  it('nächster sichtbarer im Radius', () => expect(pickScreenMarker(ms, { x: 104, y: 100 })).toBe('A'));
  it('hinter der Kamera zählt nicht', () => expect(pickScreenMarker([ms[2]!], { x: 101, y: 100 })).toBeNull());
  it('außerhalb des Radius → null', () => expect(pickScreenMarker(ms, { x: 300, y: 300 })).toBeNull());
});

describe('neighbours bei weiten Abständen', () => {
  it('ohne Nachbarn ≤ 25 m: die nächsten drei bis 100 m (Panorama-Serien im Gehen liegen weiter auseinander)', () => {
    const weit = [st('A', 0, 0), st('B', 40, 0), st('C', 0, 60), st('D', 90, 0), st('E', 150, 0)];
    expect(neighbours(weit, 'A').map((s) => s.id)).toEqual(['B', 'C', 'D']);
  });
  it('mit nahen Nachbarn bleibt die 25-m-Regel', () => {
    const nah = [st('A', 0, 0), st('B', 10, 0), st('C', 40, 0)];
    expect(neighbours(nah, 'A').map((s) => s.id)).toEqual(['B']);
  });
});

describe('neighbourInDirection (Doppelklick im Panorama)', () => {
  const list = [st('A', 0, 0), st('B', 10, 0), st('C', 0, 10), st('D', -8, 0)];
  it('Nachbar in Blickrichtung (Markerposition, nicht Scannerzentrum)', () => {
    // Blick schräg nach unten Richtung B (Marke am Boden bei z = 0, Kamera auf 1,5 m).
    expect(neighbourInDirection(list, 'A', [10, 0, -1.5])?.id).toBe('B');
    expect(neighbourInDirection(list, 'A', [0, 1, -0.15])?.id).toBe('C');
  });
  it('nichts innerhalb von 15° → null', () => {
    expect(neighbourInDirection(list, 'A', [1, 1, -0.2])).toBeNull();
    expect(neighbourInDirection(list, 'A', [0, 0, 1])).toBeNull();
  });
  it('kleinster Winkel gewinnt', () => {
    const zwei = [st('A', 0, 0), st('B', 10, 0), st('C', 10, 2)];
    expect(neighbourInDirection(zwei, 'A', [10, 1.5, -1.5])?.id).toBe('C');
  });
});

describe('neighbours nach Richtungssektoren (dichter Rundgang)', () => {
  // Eine dichte Gruppe von zehn Standpunkten östlich von A (alle näher als 7 m) und ein Standpunkt 15 m nördlich:
  // Mit nur den 8 nächsten läge kein Nachbar im Norden, der Standpunkt hinter der Gruppe wäre unerreichbar.
  const gruppe = Array.from({ length: 10 }, (_, i) => st(`G${i}`, 2 + (i % 5), i < 5 ? -0.8 : 0.8));
  const list = [st('A', 0, 0), ...gruppe, st('N', 0, 15)];
  it('der Standpunkt in der leeren Richtung bleibt Nachbar, die nächsten acht bleiben auch', () => {
    const ids = neighbours(list, 'A').map((s) => s.id);
    expect(ids).toContain('N');
    expect(ids.slice(0, 8).every((id) => id.startsWith('G'))).toBe(true);
    expect(ids).toHaveLength(9);
  });
  it('Doppelklick in seine Richtung erreicht ihn', () =>
    expect(neighbourInDirection(list, 'A', [0, 15, -1.5])?.id).toBe('N'));
  it('jenseits von 25 m zählt auch ein leerer Sektor nicht', () =>
    expect(neighbours([...list.slice(0, -1), st('N', 0, 30)], 'A').map((s) => s.id)).not.toContain('N'));
});
