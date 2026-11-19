import { describe, expect, it } from 'vitest';
import { amLetztenPunkt, HANDLE_HIT_PX, klickAusgang, nearestHandle, SCHLIESSEN_PX, schliesstFlaeche } from '../src/handleMath';
import type { Handle } from '../src/handleMath';

const h = (id: string, index: number, kind: Handle['kind'], x: number, y: number): Handle => ({ id, index, kind, x, y });

describe('nearestHandle', () => {
  it('nächster Eckpunkt im Umkreis, sonst null', () => {
    const hs = [h('M1', 0, 'vertex', 100, 100), h('M1', 1, 'vertex', 110, 100)];
    expect(nearestHandle(hs, 108, 101)).toMatchObject({ id: 'M1', index: 1 });
    expect(nearestHandle(hs, 100, 100 + HANDLE_HIT_PX + 0.5)).toBeNull();
  });
  it('Eckpunkt hat Vorrang vor einem näheren Mittelpunkt-Griff', () => {
    const hs = [h('M1', 1, 'midpoint', 100, 100), h('M1', 0, 'vertex', 106, 100)];
    expect(nearestHandle(hs, 101, 100)).toMatchObject({ kind: 'vertex', index: 0 });
  });
  it('Mittelpunkt-Griff, wenn kein Eckpunkt im Umkreis liegt', () => {
    const hs = [h('M2', 2, 'midpoint', 50, 50), h('M2', 0, 'vertex', 80, 50)];
    expect(nearestHandle(hs, 52, 51)).toMatchObject({ kind: 'midpoint', index: 2 });
  });
});

describe('nearestHandle mit sichtbarem Markerradius', () => {
  it('große Kugel (r > Fangradius): ganzer sichtbarer Marker greift', () => {
    const hs: Handle[] = [{ ...h('M1', 0, 'vertex', 100, 100), r: 18 }];
    expect(nearestHandle(hs, 100 + 15, 100)).toMatchObject({ id: 'M1', index: 0 });
    expect(nearestHandle(hs, 100 + 18.5, 100)).toBeNull();
  });
  it('kleine Kugel (r < Fangradius): Fangradius gilt weiter', () => {
    const hs: Handle[] = [{ ...h('M1', 0, 'vertex', 100, 100), r: 5 }];
    expect(nearestHandle(hs, 100 + HANDLE_HIT_PX, 100)).not.toBeNull();
  });
  it('bei Überlappung gewinnt der nähere Griff', () => {
    const hs: Handle[] = [{ ...h('A', 0, 'vertex', 100, 100), r: 20 }, { ...h('B', 0, 'vertex', 120, 100), r: 5 }];
    expect(nearestHandle(hs, 116, 100)).toMatchObject({ id: 'B' });
    expect(nearestHandle(hs, 108, 100)).toMatchObject({ id: 'A' });
  });
});

describe('schliesstFlaeche', () => {
  it('schließt erst ab drei Eckpunkten, mit dem Finger großzügiger als mit der Maus', () => {
    const start = { x: 100, y: 100 };
    expect(schliesstFlaeche(start, 110, 100, 2, true)).toBe(false);
    expect(schliesstFlaeche(start, 110, 100, 3, false)).toBe(true);
    expect(schliesstFlaeche(start, 100, 100 + SCHLIESSEN_PX.maus + 1, 3, false)).toBe(false);
    expect(schliesstFlaeche(start, 100, 100 + SCHLIESSEN_PX.maus + 1, 3, true)).toBe(true);
    expect(schliesstFlaeche(start, 100, 100 + SCHLIESSEN_PX.touch + 1, 5, true)).toBe(false);
    expect(schliesstFlaeche(null, 100, 100, 5, true)).toBe(false);
  });
});

describe('amLetztenPunkt', () => {
  const letzter = { x: 100, y: 100 };
  it('16 px Maus ja, 17 px nein; mit dem Finger bis 36 px', () => {
    expect(amLetztenPunkt(letzter, 116, 100, false)).toBe(true);
    expect(amLetztenPunkt(letzter, 117, 100, false)).toBe(false);
    expect(amLetztenPunkt(letzter, 100, 136, true)).toBe(true);
    expect(amLetztenPunkt(letzter, 100, 137, true)).toBe(false);
  });
  it('hinter der Kamera (null) nie', () => expect(amLetztenPunkt(null, 100, 100, true)).toBe(false));
});

describe('klickAusgang', () => {
  it('letzter Punkt der Polylinie beendet ab zwei Punkten, bei einem wird verworfen', () => {
    expect(klickAusgang('polyline', { openIndex: 2, beenden: true }, 3)).toBe('beenden');
    expect(klickAusgang('polyline', { openIndex: 1, beenden: true }, 2)).toBe('beenden');
    expect(klickAusgang('polyline', { openIndex: 0, beenden: true }, 1)).toBe('zu-wenig-linie');
  });
  it('inzwischen angekommener Punkt: der angeklickte ist nicht mehr der letzte → Eckpunkt-Fang', () =>
    expect(klickAusgang('polyline', { openIndex: 1, beenden: true }, 3)).toBe('setzen'));
  it('erster Eckpunkt der Fläche schließt ab drei Punkten', () => {
    expect(klickAusgang('area', { openIndex: 0 }, 3)).toBe('schliessen');
    expect(klickAusgang('area', { openIndex: 0 }, 2)).toBe('zu-wenig-flaeche');
  });
  it('sonst setzen (auch Eckpunkt-Fang ohne beenden, auch ohne Ziel)', () => {
    expect(klickAusgang('polyline', { openIndex: 0 }, 3)).toBe('setzen');
    expect(klickAusgang('polyline', null, 0)).toBe('setzen');
    expect(klickAusgang('distance', { openIndex: 0, beenden: true }, 1)).toBe('setzen');
  });
});
