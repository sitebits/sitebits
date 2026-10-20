import { describe, expect, it } from 'vitest';
import { befundEintraege, datumText, frageAus, koordinateText, leinwandName, nachbar } from '../src/empfangModell';
import type { Poi } from '../src/poiStore';

const poi = (id: string, over: Partial<Poi> = {}): Poi =>
  ({ id, title: `T ${id}`, category: 'Mangel', note: '', position: [1, 2, 3], station: null, source: 'browser', ...over });

describe('frageAus', () => {
  it('trennt die Frage ab der ersten Zeile „Frage:“', () => {
    expect(frageAus('Riss seit 2024.\nFrage: Ist das neu?\nBitte prüfen.')).toEqual({ notiz: 'Riss seit 2024.', frage: 'Ist das neu?\nBitte prüfen.' });
    expect(frageAus('frage: klein geschrieben')).toEqual({ notiz: '', frage: 'klein geschrieben' });
    expect(frageAus('Keine Frage hier: nur Text')).toEqual({ notiz: 'Keine Frage hier: nur Text', frage: null });
    expect(frageAus('Frage:')).toEqual({ notiz: '', frage: null });
  });
  it('Windows-Zeilenenden, Einrückung vor „Frage:“, nur die erste Frage-Zeile trennt', () => {
    expect(frageAus('Notiz\r\nFrage: Wer?\r\nFrage: Wann?')).toEqual({ notiz: 'Notiz', frage: 'Wer?\nFrage: Wann?' });
    expect(frageAus('Notiz\n  Frage: eingerückt')).toEqual({ notiz: 'Notiz', frage: 'eingerückt' });
    expect(frageAus('')).toEqual({ notiz: '', frage: null });
  });
});

describe('befundEintraege', () => {
  it('Reihenfolge der Liste, Metadaten wo vorhanden, Rückmeldung erkannt', () => {
    const l = befundEintraege([poi('a'), poi('b', { category: 'Rückmeldung', station: 'S001' })], (id) =>
      id === 'b' ? { serverId: 'B', paketRevision: 2, erstelltVonName: 'Anna', geaendertAm: 1_759_300_000, eigen: false } : null);
    expect(l.map((x) => x.id)).toEqual(['a', 'b']);
    expect(l[0]).toMatchObject({ urheber: null, datum: null, rueckmeldung: false });
    expect(l[1]).toMatchObject({ urheber: 'Anna', datum: 1_759_300_000, rueckmeldung: true, standpunkt: 'S001' });
  });
  it('Notiz und Frage getrennt, Koordinate als Kopie, zerlegte Schreibweise von „Rückmeldung“ gilt gleich', () => {
    const pos: [number, number, number] = [4, 5, 6];
    const [e] = befundEintraege([poi('a', { note: 'Text\nFrage: Warum?', position: pos, category: 'Rückmeldung' })], () => null);
    expect(e).toMatchObject({ notiz: 'Text', frage: 'Warum?', koordinate: [4, 5, 6], rueckmeldung: true, titel: 'T a', kategorie: 'Rückmeldung' });
    expect(e!.koordinate).not.toBe(pos);
  });
});

describe('befundEintraege mit Befund-Fäden', () => {
  const meta = (id: string) => (id === 'l' ? null : { serverId: id.toUpperCase(), paketRevision: 1, erstelltVonName: null, geaendertAm: null, eigen: true });
  it('Kennzeichen je Befund aus dem Stand des Servers; ohne Fäden keins', () => {
    const faden = { passt: () => true, kennzeichen: (sid: string | undefined) => (sid === 'A' ? 'Status: offen' : null) };
    const l = befundEintraege([poi('a'), poi('b')], meta, faden);
    expect(l.map((x) => x.kennzeichen)).toEqual(['Status: offen', null]);
    expect(befundEintraege([poi('a')], meta)[0]!.kennzeichen).toBeNull();
  });
  it('Filter: nur Treffer des Servers, lokale POIs ohne Server-ID fallen bei aktivem Filter weg', () => {
    const faden = { passt: (sid: string | undefined) => sid === 'B', kennzeichen: () => null };
    expect(befundEintraege([poi('a'), poi('b'), poi('l')], meta, faden).map((x) => x.id)).toEqual(['b']);
  });
});

describe('nachbar', () => {
  const l = befundEintraege([poi('a'), poi('b'), poi('c')], () => null);
  it('vor und zurück, an den Enden null, unbekannt → erster bzw. letzter', () => {
    expect(nachbar(l, 'a', 1)).toBe('b');
    expect(nachbar(l, 'c', 1)).toBeNull();
    expect(nachbar(l, 'a', -1)).toBeNull();
    expect(nachbar(l, null, 1)).toBe('a');
    expect(nachbar(l, 'x', -1)).toBe('c');
    expect(nachbar([], null, 1)).toBeNull();
  });
});

describe('Texte', () => {
  it('Leinwandname', () => {
    expect(leinwandName({ standpunkt: 'S012', befund: 'Riss Nordwand' })).toBe('3D-Ansicht, Standpunkt S012, Blick auf Befund „Riss Nordwand“');
    expect(leinwandName({ standpunkt: null, befund: null })).toBe('3D-Ansicht');
    expect(leinwandName({ standpunkt: 'S003', befund: null })).toBe('3D-Ansicht, Standpunkt S003');
    expect(leinwandName({ standpunkt: null, befund: 'Riss' })).toBe('3D-Ansicht, Blick auf Befund „Riss“');
  });
  it('Koordinate und Datum de-DE', () => {
    expect(koordinateText([412345.5, 5650000.25, 312.125])).toBe('Ost 412.345,500 m, Nord 5.650.000,250 m, Höhe 312,125 m');
    expect(koordinateText([-0, -0.0004, 0.0004])).toBe('Ost 0,000 m, Nord 0,000 m, Höhe 0,000 m');
    expect(koordinateText([-0.0006, 0, 0])).toBe('Ost -0,001 m, Nord 0,000 m, Höhe 0,000 m');
    expect(datumText(null)).toBe('');
    expect(datumText(0)).toBe('01.01.1970');            // feste Zeitzone Europe/Berlin
    expect(datumText(1_759_300_000)).toBe('01.10.2025');
  });
  it('Datum: Mitternacht in Berlin zählt zum neuen Tag, ungültige Werte ergeben leeren Text', () => {
    // 30.09.2025 22:30 UTC = 01.10.2025 00:30 Sommerzeit
    expect(datumText(Date.UTC(2025, 8, 30, 22, 30) / 1000)).toBe('01.10.2025');
    expect(datumText(Number.NaN)).toBe('');
    expect(datumText(Number.POSITIVE_INFINITY)).toBe('');
  });
});
