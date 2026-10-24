import { describe, expect, it } from 'vitest';
import {
  dekodiere, exportName, idProblem, KontrollpunktFehler, kontrollpunkteCsv, leseKontrollpunkte, paketLageEpsg, SPALTEN,
} from '../src/kontrollpunkte';
import type { IstWert } from '../src/kontrollpunkte';
import type { Frame } from '../src/manifest';

const UTM33: Frame['crs'] = { wkt: 'EPSG:25833', verticalDatum: 'dhhn2016', declaredBy: 'file' };
const LISTE = [
  '# Synthetische Kontrollpunkte',
  '# crs = EPSG:25833+7837',
  'id;rolle;e;n;h;sigma_lage;sigma_hoehe',
  'K1;check;367000,125;5807000,5;40,25;0,01;0,015',
  'K2;Kontrollpunkt;367010.000;5807000.000;40.300;;',
  'P1;passpunkt;367005;5807005;+40.1;0.005;0.005',
].join('\r\n');

const fehler = (text: string, crs: Frame['crs'] = UTM33): string => {
  try {
    leseKontrollpunkte(text, crs);
  } catch (e) {
    expect(e).toBeInstanceOf(KontrollpunktFehler);
    return (e as Error).message;
  }
  throw new Error('keine Ablehnung');
};

describe('leseKontrollpunkte', () => {
  it('liest „;“ mit Dezimalkomma und -punkt, Rollen deutsch/englisch, σ optional', () => {
    const l = leseKontrollpunkte(LISTE, UTM33);
    expect(l.crs).toBe('EPSG:25833+7837');
    expect(l.lageEpsg).toBe(25833);
    expect(l.hoehe).toBe('dhhn2016');
    expect(l.punkte.map((p) => [p.id, p.rolle])).toEqual([['K1', 'check'], ['K2', 'check'], ['P1', 'control']]);
    expect(l.punkte[0]!.soll).toEqual([367000.125, 5807000.5, 40.25]);
    expect(l.punkte[0]!.sollText).toEqual(['367000.125', '5807000.5', '40.25']);
    expect(l.punkte[0]!.sigmaLage).toBe('0.01');
    expect(l.punkte[1]!.sigmaLage).toBeNull();
    // Führendes „+“ entfällt (Formelschutz), der Wert bleibt.
    expect(l.punkte[2]!.sollText[2]).toBe('40.1');
    expect(l.istVerworfen).toBe(0);
  });

  it('liest „,“ nur mit Dezimalpunkt, BOM und Spalten in anderer Reihenfolge', () => {
    const l = leseKontrollpunkte('﻿# crs = epsg:25833+7837\nrolle,id,h,e,n\ncontrol,A,1.5,2,3\n', UTM33);
    expect(l.punkte[0]).toMatchObject({ id: 'A', rolle: 'control', soll: [2, 3, 1.5] });
    expect(fehler('# crs = EPSG:25833+7837\nid,rolle,e,n,h\nA,check,1,5,2,3\n')).toMatch(/6 statt 5 Felder/);
  });

  it('übernimmt Ist-Werte des Lieferanten nicht und zählt sie', () => {
    const l = leseKontrollpunkte('# crs = EPSG:25833+7837\nid;rolle;e;n;h;ist_e;ist_n;ist_h\nA;check;1;2;3;1,01;2;3\nB;check;1;2;3;;;\n', UTM33);
    expect(l.istVerworfen).toBe(1);
    expect(kontrollpunkteCsv(l, new Map(), 't')).toContain('\nA;check;1;2;3;;;;;\n');
  });

  it.each([
    ['ohne crs', 'id;rolle;e;n;h\nA;check;1;2;3', /nicht deklariert/],
    ['crs widersprüchlich', '# crs = EPSG:25833+7837\n# crs = EPSG:25833+5783\nid;rolle;e;n;h\nA;check;1;2;3', /widersprüchlich/],
    ['crs ohne Höhe', '# crs = EPSG:25833\nid;rolle;e;n;h\nA;check;1;2;3', /nicht auswertbar/],
    ['unbekannte Höhe', '# crs = EPSG:25833+5555\nid;rolle;e;n;h\nA;check;1;2;3', /EPSG:5555.*nicht hinterlegt/],
    ['unbekannte Spalte', '# crs = EPSG:25833+7837\nid;rolle;e;n;h;quelle\nA;check;1;2;3;x', /unbekannt oder doppelt: quelle/],
    ['doppelte Spalte', '# crs = EPSG:25833+7837\nid;rolle;e;n;h;h\nA;check;1;2;3;3', /unbekannt oder doppelt/],
    ['Pflichtspalte fehlt', '# crs = EPSG:25833+7837\nid;rolle;e;n\nA;check;1;2', /Pflichtspalten fehlen: h/],
    ['kein Trenner', '# crs = EPSG:25833+7837\nid rolle e n h', /Trenner/],
    ['leer', '# crs = EPSG:25833+7837\n\n', /ohne Kopf/],
    ['doppelte ID', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;check;1;2;3\nA;check;4;5;6', /leer oder doppelt/],
    ['leere ID', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\n ;check;1;2;3', /leer oder doppelt/],
    ['Rolle unbekannt', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;tie;1;2;3', /Rolle „tie“ unbekannt/],
    ['keine Zahl', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;check;1,2,3;2;3', /Zeile 3: „1,2,3“ ist keine Zahl/],
    ['Hex ist keine Zahl', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;check;0x10;2;3', /keine Zahl/],
    ['nicht endlich', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;check;1e999;2;3', /nicht endlich/],
    ['Soll fehlt', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\nA;check;1;;3', /Sollkoordinate fehlt/],
    ['Formel-ID', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\n=HYPERLINK("x");check;1;2;3', /Formelzeichen/],
    ['@-ID', '# crs = EPSG:25833+7837\nid;rolle;e;n;h\n@A;check;1;2;3', /Formelzeichen/],
    ['ID mit „;“ aus „,“-Liste', '# crs = EPSG:25833+7837\nid,rolle,e,n,h\nA;1,check,1,2,3', /enthält „;“/],
    ['ID mit „#“ in anderer Spaltenfolge', '# crs = EPSG:25833+7837\nrolle;id;e;n;h\ncheck;#7;1;2;3', /beginnt mit „#“/],
  ])('lehnt ab: %s', (_name, text, muster) => {
    expect(fehler(text)).toMatch(muster);
  });

  it('lehnt eine Liste ab, deren Bezugssystem nicht zum Paket passt — keine Umrechnung', () => {
    expect(fehler(LISTE, { wkt: 'EPSG:25832', verticalDatum: 'dhhn2016', declaredBy: 'file' })).toMatch(/Lagesystem passt nicht: Liste EPSG:25833, Paket EPSG:25832/);
    expect(fehler(LISTE, { wkt: 'EPSG:25833', verticalDatum: 'ellipsoidal-etrs89', declaredBy: 'file' })).toMatch(/Höhensystem passt nicht/);
    expect(fehler(LISTE, { wkt: 'EPSG:25833', verticalDatum: null, declaredBy: 'file' })).toMatch(/Höhensystem des Pakets nicht angegeben/);
    expect(fehler(LISTE, { wkt: null, verticalDatum: null, declaredBy: 'none' })).toMatch(/lokalen Koordinaten/);
    expect(fehler(LISTE, { wkt: 'Freitext', verticalDatum: 'dhhn2016', declaredBy: 'cli' })).toMatch(/ohne EPSG-Kennung/);
    // Schreibweisen des Höhensystems wie hoehen.py; Achsfolge-Variante gilt als gleich.
    expect(leseKontrollpunkte(LISTE, { wkt: 'EPSG:25833', verticalDatum: 'NHN', declaredBy: 'file' }).punkte).toHaveLength(3);
    expect(leseKontrollpunkte(LISTE.replace('25833+', '3045+'), UTM33).lageEpsg).toBe(3045);
  });
});

describe('paketLageEpsg / idProblem', () => {
  it('liest den EPSG-Code aus Kurzform und WKT1/WKT2 (letzte Kennung)', () => {
    expect(paketLageEpsg('EPSG:25833')).toBe(25833);
    expect(paketLageEpsg('PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89",AUTHORITY["EPSG","4258"]],AUTHORITY["EPSG","25833"]]')).toBe(25833);
    expect(paketLageEpsg('PROJCRS["ETRS89 / UTM zone 33N",BASEGEOGCRS["ETRS89",ID["EPSG",4258]],ID["EPSG",25833]]')).toBe(25833);
    expect(paketLageEpsg('PROJCS["ohne Kennung"]')).toBeNull();
    expect(paketLageEpsg(null)).toBeNull();
  });
  it('lässt gewöhnliche IDs durch', () => {
    for (const id of ['K1', '1001', 'KP 3', 'Ä-7', 'P1-a']) expect(idProblem(id)).toBeNull();
    for (const id of ['-1', '+1', '=1', '@1', '\t1', 'a b']) expect(idProblem(id)).not.toBeNull();
  });
});

describe('kontrollpunkteCsv', () => {
  const liste = leseKontrollpunkte(LISTE, UTM33);
  const ist = new Map<string, IstWert>([
    ['K1', { punkt: [367000.1234, 5807000.4996, 40.2504], quelle: 'Mesh: Fassade\nzweite Zeile' }],
    ['P1', { punkt: [367005, 5807005, -0.0001], quelle: 'Punktwolke: Punktwolke', grob: true }],
  ]);
  const text = kontrollpunkteCsv(liste, ist, '0.10.3+abc');

  it('schreibt P3-Schema: Kommentare, fester Kopf, Soll/σ durchgereicht, Ist mit 3 Stellen, ungepickt leer', () => {
    expect(text).toBe([
      '# crs = EPSG:25833+7837',
      '# quelle = siteBits-Viewer 0.10.3+abc, am Modell gepickt',
      '# ebene K1 = Mesh: Fassade zweite Zeile',
      '# ebene P1 = Punktwolke: Punktwolke (grobe Stufe)',
      'id;rolle;e;n;h;sigma_lage;sigma_hoehe;ist_e;ist_n;ist_h',
      'K1;check;367000.125;5807000.5;40.25;0.01;0.015;367000.123;5807000.500;40.250',
      'K2;check;367010.000;5807000.000;40.300;;;;;',
      'P1;control;367005;5807005;40.1;0.005;0.005;367005.000;5807005.000;0.000',
      '',
    ].join('\n'));
    expect(text.startsWith('﻿')).toBe(false);
  });

  it('keine Datenzelle beginnt als Formel (= + @), jede Zeile hat genau 10 Felder', () => {
    const daten = text.split('\n').filter((z) => z && !z.startsWith('#'));
    for (const z of daten) {
      const zellen = z.split(';');
      expect(zellen).toHaveLength(SPALTEN.length);
      for (const c of zellen) expect(c).not.toMatch(/^[=+@\t\r]/);
    }
  });

  it('Rundlauf: der Export liest sich wieder (gleiche Punkte, Soll und Bezugssystem)', () => {
    const zurueck = leseKontrollpunkte(text, UTM33);
    expect(zurueck.crs).toBe(liste.crs);
    expect(zurueck.punkte).toEqual(liste.punkte);
    // Die Ist-Werte aus dem eigenen Export zählen beim erneuten Laden als „nicht übernommen“.
    expect(zurueck.istVerworfen).toBe(2);
  });

  it('Dateiname passt zum Suchmuster des Prüfers, Dekodierung ist streng', () => {
    expect(exportName('proj/1 x')).toMatch(/kontrollpunkt.*\.csv$/);
    expect(exportName('proj/1 x')).toBe('kontrollpunkte-ist-proj_1_x.csv');
    expect(() => dekodiere(new Uint8Array([0x4b, 0xe4, 0x3b]).buffer)).toThrow(/kein gültiges UTF-8/);
    expect(dekodiere(new TextEncoder().encode('﻿id').buffer)).toBe('﻿id');
  });
});
