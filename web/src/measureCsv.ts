import { exportGueltig } from './vorschlagApi';
import { measurementNotes, sourceLabel } from './pointSources';
import type { SourceLayer } from './pointSources';
// CSV-Export der Messliste für deutsches Excel: Trennzeichen „;“, Dezimalkomma, UTF-8 mit BOM
// (ohne BOM liest Excel UTF-8 als Windows-1252 → „FlÃ¤che“), Zeilenende CRLF. Keine „sep=;“-Zeile — mit ihr
// ignoriert Excel das BOM. Ebenentitel (aus dem Paket) und Hinweise werden als CSV-Zellen maskiert. Formeln: Excel wertet eine Zelle, die mit = + - @ beginnt und keine
// Zahl ist, als Formel aus. Zahlzellen wie „-1,500“ bleiben Zahlen; die Koordinatenzelle ist keine Zahl und
// beginnt deshalb mit der Punktkennung („P1: x y z | P2: …“), nie mit dem Vorzeichen von X. Beleg:
// tests/measureCsv.test.ts („keine Zelle beginnt als Formel“). Koordinaten in Weltkoordinaten, 3 Nachkommastellen.
import type { Measurement } from './measureStore';
import { MEASURE_LABEL } from './measureStore';
import { einheitKopf } from './koordinaten';
import type { Beschriftung } from './koordinaten';

export const CSV_BOM = '\uFEFF';
export const CSV_HEADER = [
  'ID',
  'Art',
  'Länge [m]',
  'Fläche [m²]',
  'Umfang [m]',
  'Δz 2.−1. Punkt [m]',
  'Horizontal [m]',
  'Schräg [m]',
  'Ebenheit max. [m]',
  'Hinweis',
  'Eckpunkte',
  'Koordinaten X Y Z [m]',
  'Quelle',
];

/** 3 Nachkommastellen, Dezimalkomma, kein „-0,000“. */
export function num(x: number): string {
  const t = x.toFixed(3);
  return (t === '-0.000' ? '0.000' : t).replace('.', ',');
}

function row(m: Measurement, layers: readonly SourceLayer[]): string[] {
  const cells = Array<string>(CSV_HEADER.length).fill('');
  cells[0] = m.id;
  cells[1] = MEASURE_LABEL[m.kind];
  if (m.kind === 'distance') cells[2] = num(m.distanceM);
  if (m.kind === 'polyline') cells[2] = num(m.lengthM);
  if (m.kind === 'area') {
    cells[3] = num(m.areaM2);
    cells[4] = num(m.perimeterM);
    cells[8] = num(m.planarityM);
    if (m.nonPlanar) cells[9] = 'nicht eben';
  }
  if (m.kind === 'height') {
    cells[5] = num(m.dzM);
    cells[6] = num(m.horizontalM);
    cells[7] = num(m.slantM);
  }
  cells[10] = String(m.points.length);
  cells[11] = m.points.map((p, i) => `P${i + 1}: ${p.map(num).join(' ')}`).join(' | ');
  cells[12] = m.points.map((_,i) => `P${i+1}: ${sourceLabel(m.pointSources?.[i],layers)}`).join(' | ');
  cells[9] = [cells[9],...measurementNotes(m,layers)].filter(Boolean).join(' · ');
  return cells;
}

/** `rahmen`: Koordinatensystem und Höhensystem im Kopf der Koordinatenspalte (F23); ohne Angabe „[m]“. */
export function measurementsCsv(list: readonly Measurement[], layers: readonly SourceLayer[] = [], rahmen?: Beschriftung): string {
  const kopf = rahmen ? CSV_HEADER.map((h, i) => (i === 11 ? `Koordinaten X Y Z ${einheitKopf(rahmen)}` : h)) : CSV_HEADER;
  const cell = (text: string): string => /[;"\r\n]/.test(text) ? `"${text.replace(/"/g,'""')}"` : text;
  return CSV_BOM + [kopf, ...list.filter(exportGueltig).map((m) => row(m,layers))].map((r) => r.map(cell).join(';')).join('\r\n') + '\r\n';
}

/** Anzeige „x,xxx m“ (Beschriftungen, Messliste). */
export const formatM = (x: number): string => `${num(x)} m`;
