import { describe, expect, it } from 'vitest';
import { beschriftung, einheitKopf, koordinateAnzeige, koordinateKopie, zahlAnzeige } from '../src/koordinaten';
import { CSV_BOM, measurementsCsv } from '../src/measureCsv';
import { MeasureStore } from '../src/measureStore';

const NB = ' ';
const crs = (wkt: string | null, verticalDatum: string | null = null) => ({ wkt, verticalDatum, declaredBy: 'test' });

describe('Koordinaten beschriften (F23)', () => {
  it('Zahlen: Tausender, 3 Nachkommastellen, Dezimalkomma, kein „-0,000“', () => {
    expect(zahlAnzeige(367334.4041)).toBe(`367${NB}334,404`);
    expect(zahlAnzeige(5807317.8169)).toBe(`5${NB}807${NB}317,817`);
    expect(zahlAnzeige(59.8374)).toBe('59,837');
    expect(zahlAnzeige(-0.0004)).toBe('0,000');
    expect(zahlAnzeige(-1234.5)).toBe(`-1${NB}234,500`);
    expect(zahlAnzeige(999.9996)).toBe(`1${NB}000,000`);
  });

  it('Höhensystem: Werte aus hoehen.py, ohne Angabe, unbekannter Rohtext', () => {
    expect(beschriftung(crs('EPSG:25833', 'dhhn2016')).hoehe).toBe('NHN');
    expect(beschriftung(crs('EPSG:25833', 'dhhn92')).hoehe).toBe('NHN (DHHN92)');
    expect(beschriftung(crs('EPSG:25833', 'ellipsoidal-etrs89')).hoehe).toBe('ellips.');
    expect(beschriftung(crs('EPSG:25833', null)).hoehe).toBeNull();
    expect(beschriftung(crs('EPSG:25833', 'Amsterdam')).hoehe).toBe('Amsterdam');
  });

  it('Koordinatensystem: EPSG, WKT-Name, lokal, nicht auswertbar = lokal', () => {
    expect(beschriftung(crs('EPSG:25833', 'dhhn2016'))).toEqual({ lokal: false, crs: 'ETRS89/UTM33', hoehe: 'NHN' });
    expect(beschriftung(crs('EPSG:25832')).crs).toBe('ETRS89/UTM32');
    expect(beschriftung(crs('PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89"]]')).crs).toBe('ETRS89 / UTM zone 33N');
    expect(beschriftung(crs(`PROJCRS["${'x'.repeat(50)}"]`)).crs).toBe(`${'x'.repeat(32)}…`);
    // Lokaler Rahmen: kein Höhensystem, auch wenn eins eingetragen ist — nichts wird geraten.
    expect(beschriftung(crs(null, 'dhhn2016'))).toEqual({ lokal: true, crs: 'lokal', hoehe: null });
    expect(beschriftung(crs('LOCAL_CS["Kirche"]'))).toEqual({ lokal: true, crs: 'lokal', hoehe: null });
    expect(beschriftung(crs('irgendwas')).lokal).toBe(true);
  });

  it('Anzeige und Kopiertext', () => {
    const p: [number, number, number] = [367334.4041, 5807317.8169, 59.8374];
    const utm = beschriftung(crs('EPSG:25833', 'dhhn2016'));
    expect(koordinateAnzeige(p, utm)).toBe(`E 367${NB}334,404  N 5${NB}807${NB}317,817  H 59,837 m NHN · ETRS89/UTM33`);
    expect(koordinateKopie(p, utm)).toBe('367334.404;5807317.817;59.837;ETRS89/UTM33;NHN');
    expect(koordinateAnzeige(p, beschriftung(crs('EPSG:25833')))).toContain('H 59,837 m Höhe ? · ETRS89/UTM33');
    const lokal = beschriftung(crs(null));
    expect(koordinateAnzeige([1.5, -0.0001, 2], lokal)).toBe('x 1,500  y 0,000  z 2,000 m (lokal)');
    expect(koordinateKopie([1.5, -0.0001, 2], lokal)).toBe('1.500;0.000;2.000;lokal;');
  });

  it('CSV-Kopf nennt Koordinatensystem und Höhensystem; ohne Rahmen bleibt „[m]“', () => {
    expect(einheitKopf(beschriftung(crs('EPSG:25833', 'dhhn2016')))).toBe('[m, ETRS89/UTM33, NHN]');
    expect(einheitKopf(beschriftung(crs(null)))).toBe('[m, lokal]');
    const s = new MeasureStore();
    s.add([1, 2, 3], [4, 6, 3]);
    const kopf = (csv: string): string[] => csv.slice(CSV_BOM.length).split('\r\n')[0]!.split(';');
    expect(kopf(measurementsCsv(s.list(), [], beschriftung(crs('EPSG:25833', 'ellipsoidal-etrs89'))))[11]).toBe(
      'Koordinaten X Y Z [m, ETRS89/UTM33, ellips.]',
    );
    expect(kopf(measurementsCsv(s.list()))[11]).toBe('Koordinaten X Y Z [m]');
  });
});

describe('Zweithöhe (F23 §2 Nr. 5)', () => {
  // BKG-Rechner Potsdam: 100,000 m ellipsoidisch = 59,837 m NHN, also ζ = 40,163 m.
  const P: [number, number, number] = [367334.404, 5807317.817, 59.837];
  const u = { atOriginM: 40.163, maxDeviationM: 0.004, grid: 'GCG2016' };
  const crs = (vd: string | null) => ({ wkt: 'EPSG:25833', verticalDatum: vd, declaredBy: 'cli' });

  it('NHN-Paket zeigt die ellipsoidische Höhe daneben, ellipsoidisches Paket die NHN-Höhe', () => {
    expect(koordinateAnzeige(P, beschriftung(crs('dhhn2016'), u))).toBe(
      `E ${zahlAnzeige(P[0])}  N ${zahlAnzeige(P[1])}  H 59,837 m NHN (ellips. 100,000 m ±0,4 cm) · ETRS89/UTM33`,
    );
    const ellips: [number, number, number] = [P[0], P[1], 100];
    expect(koordinateAnzeige(ellips, beschriftung(crs('ellipsoidal-etrs89'), u))).toContain('H 100,000 m ellips. (NHN 59,837 m ±0,4 cm)');
  });

  it('keine Zweithöhe ohne ζ, bei zu großer Schwankung, DHHN92 oder lokal; Kopiertext bleibt einfach', () => {
    for (const b of [
      beschriftung(crs('dhhn2016')),
      beschriftung(crs('dhhn2016'), { ...u, maxDeviationM: 0.011 }),
      beschriftung(crs('dhhn92'), u),
      beschriftung({ wkt: null, verticalDatum: 'dhhn2016', declaredBy: 'cli' }, u),
    ]) expect(b.zweit).toBeUndefined();
    expect(koordinateKopie(P, beschriftung(crs('dhhn2016'), u))).toBe('367334.404;5807317.817;59.837;ETRS89/UTM33;NHN');
  });
});
