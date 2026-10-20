import { describe, expect, it } from 'vitest';
import type { PoiDaten, ServerEintrag } from '../src/plattformApi';
import { namenAus } from '../src/fadenModell';
import { FADEN_SPALTEN, SPALTEN, spaltenFuer, zeilen, zeitUTC } from '../src/portal/maengellisteModell';

const poi = (titel: string, zeit: number): ServerEintrag<PoiDaten> => ({
  id: `P-${zeit}`, version: 1, paketRevision: 7, erstelltVon: 1, erstelltVonName: 'Konto A',
  erstelltAm: zeit, geaendertVon: 2, geaendertVonName: 'Konto B', geaendertAm: 60,
  daten: { title: titel, category: 'Mangel', note: 'Tür\n' + 'ü'.repeat(1996),
    station: null, position: [-1.23456, 2, -0.0001] },
});

describe('Mängelliste', () => {
  it('sortiert und nummeriert ohne die Eingabe zu verändern; formatiert alle Spalten', () => {
    const eingabe = [poi('später', 2), poi('früher', 1)];
    const result = zeilen(eingabe);
    expect(result[0]).toEqual(['1', 'früher', 'Mangel', eingabe[1]!.daten.note, '',
      '-1,235', '2,000', '0,000', '1970-01-01 00:00', 'Konto A', '1970-01-01 00:01', 'Konto B']);
    expect(result[1]![0]).toBe('2');
    expect(result[1]![1]).toBe('später');
    expect(eingabe[0]!.daten.title).toBe('später');
  });
  it('leere Liste, fehlende Namen und Standpunkt', () => {
    expect(zeilen([])).toEqual([]);
    const p = poi('=HYPERLINK("https://example.org")', 0);
    p.erstelltVonName = null;
    p.geaendertVonName = null;
    p.daten.station = 'S001';
    expect(zeilen([p])[0]!.slice(1, 5)).toEqual([p.daten.title, 'Mangel', p.daten.note, 'S001']);
    expect(zeilen([p])[0]![9]).toBe('');
    expect(zeilen([p])[0]![11]).toBe('');
  });
  it('nutzt UTC auch bei Sommerzeit', () => {
    expect(zeitUTC(Date.parse('2026-07-01T12:34:00Z') / 1000)).toBe('2026-07-01 12:34');
  });
});

describe('Mängelliste mit Befund-Fäden', () => {
  const namen = namenAus([{ id: 4, name: 'Dach – Firma X', kurz: 'DA' }], [{ nutzerId: 9, nutzer: 'Konto F' }]);
  it('ohne Stand die bisherigen Spalten, mit Stand Status, Zuständig und Frist wie die XLSX des Servers', () => {
    const ohne = [poi('a', 1)];
    expect(spaltenFuer(ohne)).toEqual(SPALTEN);
    expect(zeilen(ohne, namen)[0]).toHaveLength(SPALTEN.length);
    const mit = { ...poi('b', 2), stand: { status: 'in_arbeit', zustaendig: { gewerk_id: 4, nutzer_id: 9 }, frist: '2026-10-02', version: 3 } };
    const leer = { ...poi('c', 3), stand: { status: 'offen', zustaendig: null, frist: null, version: 1 } };
    expect(spaltenFuer([mit, leer])).toEqual([...SPALTEN, ...FADEN_SPALTEN]);
    expect(FADEN_SPALTEN).toEqual(['Status', 'Zuständig', 'Frist']);
    const z = zeilen([mit, leer], namen);
    expect(z[0]!.slice(SPALTEN.length)).toEqual(['in Arbeit', 'DA / Konto F', '2026-10-02']);
    expect(z[1]!.slice(SPALTEN.length)).toEqual(['offen', '', '']);
  });
  it('unbekannte Person und unbekanntes Gewerk bleiben als Kennung erkennbar', () => {
    const p = { ...poi('d', 4), stand: { status: 'erledigt', zustaendig: { gewerk_id: 5, nutzer_id: 8 }, frist: null, version: 2 } };
    expect(zeilen([p], namen)[0]!.slice(SPALTEN.length)).toEqual(['erledigt (gemeldet)', 'Gewerk 5 / Konto 8', '']);
  });
});
