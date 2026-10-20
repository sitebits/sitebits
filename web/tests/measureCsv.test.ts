import { describe, expect, it } from 'vitest';
import { CSV_BOM, measurementsCsv, num } from '../src/measureCsv';
import { MeasureStore } from '../src/measureStore';

describe('CSV für deutsches Excel', () => {
  it('num: 3 Nachkommastellen, Dezimalkomma, kein „-0,000“', () => {
    expect(num(2.5176)).toBe('2,518');
    expect(num(-0.0004)).toBe('0,000');
    expect(num(-1.5)).toBe('-1,500');
    expect(num(500000.1234)).toBe('500000,123');
  });
  it('BOM, Kopfzeile, „;“, CRLF, je Messung eine Zeile mit Werten und Weltkoordinaten', () => {
    const s = new MeasureStore();
    s.add([1485, 7112, 22], [1488, 7116, 22]);
    s.addGeometry('area', [[0, 0, 0], [4, 0, 0], [4, 2.5, 0], [0, 2.5, 0]]);
    s.addGeometry('height', [[1, 1, 1], [1.3, 1.4, -0.5]]);
    s.addGeometry('polyline', [[0, 0, 0], [3, 4, 0], [3, 4, 2]]);
    const csv = measurementsCsv(s.list());
    expect(csv.startsWith(CSV_BOM)).toBe(true);
    const lines = csv.slice(1).split('\r\n');
    expect(lines).toEqual([
      'ID;Art;Länge [m];Fläche [m²];Umfang [m];Δz 2.−1. Punkt [m];Horizontal [m];Schräg [m];Ebenheit max. [m];Hinweis;Eckpunkte;Koordinaten X Y Z [m];Quelle',
      'M1;Strecke;5,000;;;;;;;;2;P1: 1485,000 7112,000 22,000 | P2: 1488,000 7116,000 22,000;P1: unbekannt | P2: unbekannt',
      'M2;Fläche;;10,000;13,000;;;;0,000;;4;P1: 0,000 0,000 0,000 | P2: 4,000 0,000 0,000 | P3: 4,000 2,500 0,000 | P4: 0,000 2,500 0,000;P1: unbekannt | P2: unbekannt | P3: unbekannt | P4: unbekannt',
      'M3;Höhe;;;;-1,500;0,500;1,581;;;2;P1: 1,000 1,000 1,000 | P2: 1,300 1,400 -0,500;P1: unbekannt | P2: unbekannt',
      'M4;Polylinie;7,000;;;;;;;;3;P1: 0,000 0,000 0,000 | P2: 3,000 4,000 0,000 | P3: 3,000 4,000 2,000;P1: unbekannt | P2: unbekannt | P3: unbekannt',
      '',
    ]);
  });
  it('nicht ebene Fläche trägt den Hinweis', () => {
    const s = new MeasureStore();
    s.addGeometry('area', [[0, 0, 0], [2, 0, 0], [2, 2, 0.1], [0, 2, 0]]);
    expect(measurementsCsv(s.list()).split('\r\n')[1]).toContain(';nicht eben;');
  });
  it('keine Zelle beginnt als Formel: Nicht-Zahlen nie mit = + - @, auch bei negativem X', () => {
    const s = new MeasureStore();
    s.add([-1485, -7112, -22], [-1488, 7116, 22]);
    s.addGeometry('height', [[-1, 1, 1], [1.3, 1.4, -0.5]]);
    const cells = measurementsCsv(s.list())
      .slice(1)
      .split('\r\n')
      .flatMap((l) => l.split(';'));
    const number = /^-?\d+,\d{3}$/;
    for (const cell of cells) if (!number.test(cell)) expect(cell, cell).not.toMatch(/^[=+\-@]/);
    expect(cells).toContain('P1: -1485,000 -7112,000 -22,000 | P2: -1488,000 7116,000 22,000');
  });
});
