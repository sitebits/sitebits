import { describe, expect, it } from 'vitest';
import { FadenStaende } from '../src/fadenStaende';
import { FILTER_LEER } from '../src/fadenModell';
import type { Befunde, Filter } from '../src/fadenModell';
import { PlattformFehler } from '../src/plattformApi';

const zeile = (id: string, status: string, frist: string | null, ueberfaellig: boolean): Befunde['befunde'][number] => ({
  poi: { id, version: 1, daten: { title: id, category: '', note: '', position: [0, 0, 0], station: null }, paketRevision: null,
    erstelltVon: null, erstelltAm: 0, geaendertVon: null, geaendertVonName: null, geaendertAm: 0 },
  stand: { status, zustaendig: null, frist, version: 1 }, ueberfaellig, letzter_eintrag_am: null,
});

function server(): { lies: (f: Filter) => Promise<Befunde>; abfragen: Filter[] } {
  const abfragen: Filter[] = [];
  return {
    abfragen,
    lies: (f) => {
      abfragen.push(f);
      const alle = [zeile('A', 'in_arbeit', '2020-01-01', true), zeile('B', 'offen', null, false)];
      const befunde = f.frist === 'ueberfaellig' ? alle.filter((b) => b.ueberfaellig) : alle;
      return Promise.resolve({ befunde, zaehler: { ueberfaellig_meine: 1 } });
    },
  };
}

describe('Stände der Befund-Fäden im Viewer', () => {
  it('Kennzeichen je Server-ID, überfällig nur nach Angabe des Servers', async () => {
    const s = new FadenStaende(server().lies);
    expect(s.kennzeichen('A')).toBeNull();
    expect(await s.laden()).toBeNull();
    expect(s.kennzeichen('A')).toBe('Status: in Arbeit · Frist: 01.01.2020 · überfällig');
    expect(s.kennzeichen('B')).toBe('Status: offen');
    expect(s.kennzeichen(undefined)).toBeNull();
    expect(s.kurz('A')).toBe('in Arbeit · überfällig');
    expect(s.kurz('X')).toBeNull();
  });
  it('ohne Filter passt alles, mit Filter nur die Treffer des Servers', async () => {
    const sv = server();
    const s = new FadenStaende(sv.lies);
    await s.laden();
    expect(s.passt('B')).toBe(true);
    expect(s.passt(undefined)).toBe(true);
    let gemeldet = 0;
    s.onAenderung(() => gemeldet++);
    expect(await s.setzeFilter({ ...FILTER_LEER, frist: 'ueberfaellig' })).toBeNull();
    expect(gemeldet).toBe(1);
    expect(s.passt('A')).toBe(true);
    expect(s.passt('B')).toBe(false);
    expect(s.passt(undefined)).toBe(false);
    expect(sv.abfragen.at(-1)).toEqual({ ...FILTER_LEER, frist: 'ueberfaellig' });
  });
  it('Fehler des Servers: Text zurück, bisheriger Stand bleibt', async () => {
    let fehler = false;
    const sv = server();
    const s = new FadenStaende((f) => (fehler ? Promise.reject(new PlattformFehler(422, 'Eingabe ungültig.')) : sv.lies(f)));
    await s.laden();
    fehler = true;
    expect(await s.setzeFilter({ ...FILTER_LEER, status: 'offen' })).toBe('Eingabe ungültig.');
    expect(s.kennzeichen('A')).toBe('Status: in Arbeit · Frist: 01.01.2020 · überfällig');
  });
});
