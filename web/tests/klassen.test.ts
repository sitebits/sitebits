import { describe, expect, it } from 'vitest';
import {
  OHNE_FILTER, dekodierfilter, filterAusLink, filterKennung, filterVonLink, filterZuLink, klassenName, vorhandeneKlassen,
} from '../src/klassen';
import { decodeHash, encodeHash } from '../src/hash';
import { availableColorModes } from '../src/viewMath';
import type { Vec3 } from '../src/manifest';


const VOLL = { attributes: ['classification', 'returns', 'returnsLast'], classes: { 5: 3, 18: 2 } };
describe('Punktklassen (F30)', () => {
  it('Legende: nur vorhandene Klassen, aufsteigend, deutsche ASPRS-Namen, sonst „Klasse n“', () => {
    expect(vorhandeneKlassen({ '6': 10, '2': 500, '0': 0, '64': 3, x: 5, '300': 1 })).toEqual([
      { klasse: 2, name: 'Boden', punkte: 500 },
      { klasse: 6, name: 'Gebäude', punkte: 10 },
      { klasse: 64, name: 'Klasse 64', punkte: 3 },
    ]);
    expect(vorhandeneKlassen(undefined)).toEqual([]);
    expect(klassenName(18)).toBe('Rauschen (hoch)');
  });

  it('Dekodierfilter: Klassen per not_in, erste Echos per ReturnNumber = 1, ohne Filter leer', () => {
    expect(dekodierfilter(OHNE_FILTER)).toEqual([]);
    expect(dekodierfilter({ aus: new Set([5, 3]), echo: 'erste' })).toEqual([
      { dimension: 'Classification', operator: 'not_in', values: new Set([3, 5]) },
      { dimension: 'ReturnNumber', operator: 'equal', value: 1 },
    ]);
  });

  it('Nur letzte Echos: UserData = 1 (vom Import markiert)', () => {
    expect(dekodierfilter({ aus: new Set(), echo: 'letzte' })).toEqual([{ dimension: 'UserData', operator: 'equal', value: 1 }]);
    expect(filterKennung({ aus: new Set(), echo: 'letzte' })).not.toBe(filterKennung({ aus: new Set(), echo: 'erste' }));
  });

  it('Kennung: gleicher Zustand gleiche Kennung, ohne Filter leer', () => {
    expect(filterKennung(OHNE_FILTER)).toBe('');
    expect(filterKennung({ aus: new Set([5, 3]), echo: 'alle' })).toBe(filterKennung({ aus: new Set([3, 5]), echo: 'alle' }));
    expect(filterKennung({ aus: new Set(), echo: 'erste' })).not.toBe('');
    expect(filterKennung({ aus: new Set([3]), echo: 'alle' })).not.toBe(filterKennung({ aus: new Set([3]), echo: 'erste' }));
  });

  it('Ansichtslink kx/ec: Rundreise, ungültige Werte fallen einzeln heraus, Pose bleibt', () => {
    const o: Vec3 = [0, 0, 0];
    const f = { aus: new Set([18, 5]), echo: 'erste' as const };
    const h = encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ...filterZuLink(f) }, o);
    expect(h).toContain('kx=5,18');
    expect(h).toContain('ec=1');
    const d = decodeHash(h, o);
    expect(filterVonLink(d, VOLL)).toEqual(f);
    const kaputt = decodeHash('#cam=1,2,3&tgt=0,0,0&kx=2,abc,999,7&ec=3', o);
    expect(kaputt.kx).toEqual([2, 7]);
    expect(kaputt.ec).toBeUndefined();
    expect(kaputt.cam).toEqual([1, 2, 3]);
    expect(encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ...filterZuLink(OHNE_FILTER) }, o)).not.toMatch(/kx=|ec=/);
    expect(filterAusLink(null, null)).toEqual(OHNE_FILTER);
    const letzte = encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ...filterZuLink({ aus: new Set(), echo: 'letzte' }) }, o);
    expect(letzte).toContain('ec=2');
    expect(filterVonLink(decodeHash(letzte, o), VOLL)).toEqual({ aus: new Set(), echo: 'letzte' });
  });

  it('Link setzt nur Filter, für die es Schalter gibt (sonst leere Wolke ohne Ausweg)', () => {
    const link = { kx: [0, 1, 5, 18], ec: 'erste' as const };
    expect(filterVonLink(link, VOLL)).toEqual({ aus: new Set([5, 18]), echo: 'erste' });
    expect(filterVonLink(link, { attributes: ['intensity'], classes: { 1: 10 } })).toEqual(OHNE_FILTER);
    expect(filterVonLink({ ec: 'letzte' }, { attributes: ['returns'] })).toEqual(OHNE_FILTER);
    expect(filterVonLink({ ec: 'erste' }, { attributes: ['returnsLast'] })).toEqual(OHNE_FILTER);
  });

  it('Farbmodus „Klassen“ nur mit Attribut classification', () => {
    const fmt7 = ['Color', 'Intensity', 'Z', 'Classification'];
    expect(availableColorModes(['rgb', 'classification'], fmt7)).toEqual(['rgb', 'elevation', 'classification']);
    expect(availableColorModes(['rgb'], fmt7)).toEqual(['rgb', 'elevation']);
    expect(availableColorModes(['classification'], ['Z'])).toEqual(['elevation']);
  });
});
