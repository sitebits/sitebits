import { describe, expect, it } from 'vitest';
import proj4 from 'proj4';
import { konvergenzGrad, refraktionGrad, sonnenRichtung, sonnenstand, tagesbahn } from '../src/sonneMath';
import { PROJ4 } from '../src/crs';
// Referenz: pvlib.solarposition.spa_python (NREL SPA), erzeugt 21.10.2026, 4 Orte × 9 Zeitpunkte (2001–2030).
import referenz from './fixtures/sonne-spa-pvlib.json';

const winkel = (a: number, b: number): number => Math.abs(((a - b + 540) % 360) - 180);

describe('sonnenstand gegen NREL SPA', () => {
  for (const r of referenz) {
    if (r.hoehe < -2) continue;
    it(`${r.ort} ${r.utc}`, () => {
      const s = sonnenstand(new Date(r.utc), r.lat, r.lon);
      expect(Math.abs(s.hoeheGrad - r.hoehe)).toBeLessThan(0.05);
      // Azimut ist nahe am Zenit schlecht bestimmt; dort zählt er für Schatten kaum.
      if (r.hoehe < 85) expect(winkel(s.azimutGrad, r.azimut)).toBeLessThan(0.05 / Math.cos(r.hoehe * Math.PI / 180));
      // Refraktion: Druck/Temperatur weichen leicht ab, Schatten bei > 2° Höhe
      if (r.hoehe > 2) expect(Math.abs(s.hoeheScheinbarGrad - r.hoehe_schein)).toBeLessThan(0.1);
    });
  }
});

describe('Hilfen', () => {
  it('Refraktion: ~0,5° am Horizont, 0 im Zenit, fällt mit der Höhe', () => {
    expect(refraktionGrad(0)).toBeCloseTo(0.482, 2);
    expect(refraktionGrad(90)).toBe(0);
    expect(refraktionGrad(10)).toBeLessThan(refraktionGrad(2));
  });
  it('Konvergenz UTM 33 in Potsdam ≈ +1,5°: westlich des Mittelmeridians neigt sich der Meridian nach Osten; am Mittelmeridian 0', () => {
    const p = (ll: [number, number]) => proj4('WGS84', PROJ4['EPSG:25833']!, ll) as [number, number];
    expect(konvergenzGrad(52.4005, 13.0565, p)).toBeCloseTo((15 - 13.0565) * Math.sin(52.4005 * Math.PI / 180), 1);
    expect(konvergenzGrad(52.4, 15, p)).toBeCloseTo(0, 3);
  });
  it('Richtung: Süden mittags zeigt nach −Y und oben; Konvergenz dreht im Uhrzeigersinn', () => {
    const [x, y, z] = sonnenRichtung({ azimutGrad: 180, hoeheScheinbarGrad: 30 }, 0);
    expect(x).toBeCloseTo(0, 6); expect(y).toBeCloseTo(-Math.cos(Math.PI / 6), 6); expect(z).toBeCloseTo(0.5, 6);
    expect(sonnenRichtung({ azimutGrad: 0, hoeheScheinbarGrad: 0 }, 90)[0]).toBeCloseTo(1, 6);
  });
  it('Tagesbahn Potsdam 21.06.: ~16,7 h über dem Horizont, Mittag im Süden', () => {
    const b = tagesbahn(new Date('2026-06-21T12:00Z'), 52.4005, 13.0565, 5);
    expect(b.length * 5 / 60).toBeGreaterThan(16.2); expect(b.length * 5 / 60).toBeLessThan(17.2);
    const hoch = b.reduce((a, c) => (c.stand.hoeheGrad > a.stand.hoeheGrad ? c : a));
    expect(winkel(hoch.stand.azimutGrad, 180)).toBeLessThan(2);
    expect(hoch.stand.hoeheGrad).toBeCloseTo(90 - 52.4005 + 23.44, 0);
  });
});
