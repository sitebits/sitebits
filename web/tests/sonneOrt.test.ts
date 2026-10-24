import { describe, expect, it } from 'vitest';
import { aufUntergang, ausOrtszeit, ortszeit, sonnenOrt, uhrzeitText, versatzMin } from '../src/sonneOrt';

const POTSDAM_UTM: [number, number, number] = [365809, 5806702, 42];

describe('sonnenOrt', () => {
  it('UTM 33 (Potsdam) → Potsdam, Konvergenz ≈ +1,54°', () => {
    const o = sonnenOrt(POTSDAM_UTM, 'EPSG:25833')!;
    expect(o.breiteGrad).toBeCloseTo(52.40, 1); expect(o.laengeGrad).toBeCloseTo(13.03, 1);
    expect(o.konvergenzGrad).toBeCloseTo(1.54, 1);
  });
  it('WKT wird direkt gelesen', () => {
    const wkt = 'PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89",DATUM["European_Terrestrial_Reference_System_1989",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",15],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]';
    const a = sonnenOrt(POTSDAM_UTM, wkt)!; const b = sonnenOrt(POTSDAM_UTM, 'EPSG:25833')!;
    expect(a.breiteGrad).toBeCloseTo(b.breiteGrad, 6); expect(a.laengeGrad).toBeCloseTo(b.laengeGrad, 6);
  });
  it('lokal, LOCAL_CS oder unbekannt → kein Ort', () => {
    expect(sonnenOrt(POTSDAM_UTM, null)).toBeNull();
    expect(sonnenOrt(POTSDAM_UTM, 'LOCAL_CS["x"]')).toBeNull();
    expect(sonnenOrt(POTSDAM_UTM, 'EPSG:4711')).toBeNull();
  });
});

describe('Ortszeit Europe/Berlin', () => {
  it('Sommer MESZ, Winter MEZ', () => {
    expect(ausOrtszeit(2026, 172, 13 * 60).toISOString()).toBe('2026-06-21T11:00:00.000Z');
    expect(ausOrtszeit(2026, 355, 13 * 60).toISOString()).toBe('2026-12-21T12:00:00.000Z');
    expect(ortszeit(new Date('2026-06-21T11:00Z'))).toEqual({ jahr: 2026, tagImJahr: 172, minuten: 780, kuerzel: 'MESZ' });
    expect(ortszeit(new Date('2026-12-21T12:00Z')).kuerzel).toBe('MEZ');
    expect(versatzMin(new Date('2026-06-21T11:00:30.500Z'))).toBe(120);
  });
  it('Umstellung: 29.03. 02:30 gibt es nicht (→ 03:30 MESZ), 25.10. 02:30 doppelt (→ die erste, MESZ)', () => {
    const luecke = ausOrtszeit(2026, 88, 150);
    expect(luecke.toISOString()).toBe('2026-03-29T01:30:00.000Z');
    expect(ortszeit(luecke).minuten).toBe(210);
    const doppelt = ausOrtszeit(2026, 298, 150);
    expect(doppelt.toISOString()).toBe('2026-10-25T00:30:00.000Z');
    expect(ortszeit(doppelt).kuerzel).toBe('MESZ');
    // Hin und zurück über das ganze Jahr in 7-Stunden-Schritten (trifft jede Tageszeit)
    for (let t = Date.UTC(2026, 0, 1); t < Date.UTC(2027, 0, 1); t += 7 * 3_600_000) {
      const o = ortszeit(new Date(t));
      const zurueck = ausOrtszeit(o.jahr, o.tagImJahr, o.minuten).getTime();
      // Nur die doppelte Stunde darf auf die erste abbilden
      if (zurueck !== t) expect(t - zurueck).toBe(3_600_000);
    }
  });
  it('Auf-/Untergang Potsdam gegen NREL SPA (pvlib sun_rise_set_transit_spa, 21.10.2026) ±30 s', () => {
    const ort = sonnenOrt(POTSDAM_UTM, 'EPSG:25833')!;
    const ab = (d: Date | null, soll: string): number => Math.abs(d!.getTime() - Date.parse(soll)) / 1000;
    const sommer = aufUntergang(2026, 172, ort);
    expect(ab(sommer.auf, '2026-06-21T02:45:16Z')).toBeLessThan(30);
    expect(ab(sommer.unter, '2026-06-21T19:34:05Z')).toBeLessThan(30);
    const winter = aufUntergang(2026, 355, ort);
    expect(ab(winter.auf, '2026-12-21T07:15:44Z')).toBeLessThan(30);
    expect(ab(winter.unter, '2026-12-21T14:56:05Z')).toBeLessThan(30);
    expect(aufUntergang(2026, 172, { breiteGrad: 78, laengeGrad: 15 })).toEqual({ auf: null, unter: null });
  });
  it('Uhrzeittext', () => { expect(uhrzeitText(5)).toBe('00:05'); expect(uhrzeitText(13 * 60 + 59.6)).toBe('14:00'); });
});
