import { describe, expect, it } from 'vitest';
import {
  befundlinkPfad, eintragInhalt, FILTER_LEER, kategorienAus, linkAdresse, kennzeichenKurz, standZeile, filterAktiv, filterQuery, fristText, kennzeichen, namenAus, nummeriert,
  statusText, ueberfaelligText, zustaendigText,
} from '../src/fadenModell';
import type { FadenEintrag, Stand } from '../src/fadenModell';

const stand = (o: Partial<Stand> = {}): Stand => ({ status: 'offen', zustaendig: null, frist: null, version: 1, ...o });
const eintrag = (o: Partial<FadenEintrag> = {}): FadenEintrag => ({
  schluessel: '0123456789abcdef', art: 'kommentar', text: 'Hallo', wert: null, intern: false,
  erstellt_von: { id: 3, name: 'Konto C' }, erstellt_am: 1_700_000_000, kanal: 'web', zurueckgezogen_am: null, ...o,
});
const namen = namenAus(
  [{ id: 7, name: 'Dach – Firma X', kurz: 'DA' }],
  [{ nutzerId: 3, nutzer: 'Konto C' }],
);

describe('Status und Frist', () => {
  it('beschriftet die fünf Status und lässt Unbekanntes roh', () => {
    expect(['offen', 'in_arbeit', 'erledigt', 'abgeschlossen', 'abgelehnt'].map(statusText))
      .toEqual(['offen', 'in Arbeit', 'erledigt (gemeldet)', 'abgeschlossen', 'abgelehnt']);
    expect(statusText('neu')).toBe('neu');
  });
  it('schreibt die Frist als deutsches Datum, ohne Frist leer', () => {
    expect(fristText('2026-10-02')).toBe('02.10.2026');
    expect(fristText(null)).toBe('');
    expect(fristText('kaputt')).toBe('kaputt');
  });
  it('Kennzeichen in Textform: Status, Frist, überfällig nur nach Angabe des Servers', () => {
    expect(kennzeichen(stand(), false)).toBe('Status: offen');
    expect(kennzeichen(stand({ status: 'in_arbeit', frist: '2020-01-01' }), true))
      .toBe('Status: in Arbeit · Frist: 01.01.2020 · überfällig');
    // Ein vergangenes Datum allein macht nichts überfällig — das entscheidet der Server.
    expect(kennzeichen(stand({ frist: '2020-01-01' }), false)).toBe('Status: offen · Frist: 01.01.2020');
  });
  it('Kurzform am Marker: Status und gegebenenfalls „überfällig“', () => {
    expect(kennzeichenKurz(stand({ status: 'erledigt', frist: '2020-01-01' }), false)).toBe('erledigt (gemeldet)');
    expect(kennzeichenKurz(stand({ status: 'in_arbeit' }), true)).toBe('in Arbeit · überfällig');
  });
  it('Projektkarte: Anzahl überfälliger Befunde nur, wenn es welche gibt', () => {
    expect(ueberfaelligText(0)).toBe('');
    expect(ueberfaelligText(undefined)).toBe('');
    expect(ueberfaelligText(1)).toBe('1 überfällig');
    expect(ueberfaelligText(3)).toBe('3 überfällig');
  });
});

describe('Zuständig', () => {
  it('Gewerk mit Kurzzeichen und Person aus der Beteiligtenliste', () => {
    expect(zustaendigText({ gewerk_id: 7, nutzer_id: 3 }, namen)).toBe('Dach – Firma X (DA) / Konto C');
    expect(zustaendigText({ gewerk_id: 7, nutzer_id: null }, namen)).toBe('Dach – Firma X (DA)');
    expect(zustaendigText(null, namen)).toBe('niemand');
  });
  it('unbekanntes Gewerk und ehemalige Person bleiben erkennbar', () => {
    expect(zustaendigText({ gewerk_id: 9, nutzer_id: 4 }, namen)).toBe('Gewerk 9 / Konto 4 (ehemalig)');
    expect(zustaendigText({ gewerk_id: null, nutzer_id: 3 }, namen)).toBe('Konto C');
  });
});

describe('Einträge', () => {
  it('nummeriert nach Position ab 1, nie nach Schlüssel', () => {
    const l = nummeriert([eintrag({ schluessel: 'ffffffffffffffff' }), eintrag({ schluessel: '0000000000000000' })]);
    expect(l.map((x) => x.nr)).toEqual([1, 2]);
    expect(l[1]!.eintrag.schluessel).toBe('0000000000000000');
    expect(nummeriert([])).toEqual([]);
  });
  it('Inhalt je Art', () => {
    expect(eintragInhalt(eintrag(), namen)).toBe('Hallo');
    expect(eintragInhalt(eintrag({ text: null, zurueckgezogen_am: 1 }), namen)).toBe('zurückgezogen');
    expect(eintragInhalt(eintrag({ art: 'status', wert: 'erledigt', text: null }), namen)).toBe('Status → erledigt (gemeldet)');
    expect(eintragInhalt(eintrag({ art: 'status', wert: 'abgelehnt', text: 'kein Mangel' }), namen)).toBe('Status → abgelehnt. Begründung: kein Mangel');
    expect(eintragInhalt(eintrag({ art: 'zustaendig', wert: { gewerk_id: 7, nutzer_id: null }, text: null }), namen)).toBe('Zuständig → Dach – Firma X (DA)');
    expect(eintragInhalt(eintrag({ art: 'zustaendig', wert: null, text: null }), namen)).toBe('Zuständigkeit entfernt');
    expect(eintragInhalt(eintrag({ art: 'frist', wert: '2026-11-30', text: null }), namen)).toBe('Frist → 30.11.2026');
    expect(eintragInhalt(eintrag({ art: 'frist', wert: null, text: null }), namen)).toBe('Frist entfernt');
    expect(eintragInhalt(eintrag({ art: 'link', wert: { url: 'https://example.org/a', titel: 'Plan' }, text: null }), namen)).toBe('Link: Plan');
    expect(eintragInhalt(eintrag({ art: 'link', wert: null, text: null, zurueckgezogen_am: 5 }), namen)).toBe('zurückgezogen');
  });
  it('Markierung: geänderte Felder mit Vorher und Nachher', () => {
    const e = eintrag({
      art: 'markierung', text: null,
      wert: { vorher: { title: 'Riss', position: [1, 2, 3.5] }, nachher: { title: 'Riss Wand', position: [1, 2, 4] } },
    });
    expect(eintragInhalt(e, namen)).toBe('Markierung geändert: Titel „Riss“ → „Riss Wand“; Position 1 / 2 / 3,5 → 1 / 2 / 4');
  });
});

describe('Filter', () => {
  it('ohne Auswahl keine Abfrage und nicht aktiv', () => {
    expect(filterQuery(FILTER_LEER)).toBe('');
    expect(filterAktiv(FILTER_LEER)).toBe(false);
  });
  it('setzt nur gewählte Filter, Kategorie kodiert', () => {
    const f = { status: 'in_arbeit', zustaendig: 'gewerk', frist: 'ueberfaellig', kategorie: 'Mängel & Co' } as const;
    expect(filterQuery(f)).toBe('status=in_arbeit&zustaendig=gewerk&frist=ueberfaellig&kategorie=M%C3%A4ngel+%26+Co');
    expect(filterAktiv(f)).toBe(true);
    expect(filterQuery({ ...FILTER_LEER, zustaendig: 'ich' })).toBe('zustaendig=ich');
  });
});

describe('Befundlink', () => {
  it('öffnet Viewer und Befund, optional mit Eintrag', () => {
    expect(befundlinkPfad('proj-1', 'P-1')).toBe('/ansicht?p=/data/proj-1/#b=P-1');
    expect(befundlinkPfad('proj-1', 'P-1', '0123456789abcdef')).toBe('/ansicht?p=/data/proj-1/#b=P-1&e=0123456789abcdef');
    expect(befundlinkPfad('proj-1', 'P-1', 'kein-schluessel')).toBe('/ansicht?p=/data/proj-1/#b=P-1');
  });
});

describe('Kategorien des Filters', () => {
  it('alle Projektkategorien (auch ausgeschaltete: sie stehen an Befunden) und „Rückmeldung“, ohne Doppelte', () => {
    expect(kategorienAus([{ name: 'Mangel', aktiv: true }, { name: 'Alt', aktiv: false }, { name: 'Rückmeldung', aktiv: true }, { name: 3 }]))
      .toEqual(['Mangel', 'Alt', 'Rückmeldung']);
    expect(kategorienAus(undefined)).toEqual(['Rückmeldung']);
  });
});

describe('Stand-Zeile des Verlaufs', () => {
  it('überfällig nach Angabe des Servers im Stand, mit Zuständigkeit', () => {
    expect(standZeile({ ...stand({ frist: '2020-01-01', zustaendig: { gewerk_id: 7, nutzer_id: null } }), ueberfaellig: true }, namen))
      .toBe('Status: offen · Frist: 01.01.2020 · überfällig · Zuständig: Dach – Firma X (DA)');
    expect(standZeile({ ...stand({ frist: '2020-01-01' }), ueberfaellig: false }, namen)).toBe('Status: offen · Frist: 01.01.2020');
    // Ältere Server ohne das Feld: nicht überfällig.
    expect(standZeile(stand({ frist: '2020-01-01' }), namen)).toBe('Status: offen · Frist: 01.01.2020');
  });
});

describe('Link-Adresse eines Eintrags', () => {
  const link = (url: string) => eintrag({ art: 'link', text: null, wert: { url, titel: 'Stelle' } });
  it('relative Befundlinks dieser Instanz und https werden Links, anderes nicht', () => {
    const befundlink = '/ansicht?p=/data/proj-1/#cam=-4.000,0.000,-0.500&tgt=1,1,1&b=P-1&e=0123456789abcdef';
    expect(linkAdresse(link(befundlink))).toBe(befundlink);
    expect(linkAdresse(link('https://example.org/plan'))).toBe('https://example.org/plan');
    expect(linkAdresse(link('//evil.test/x'))).toBeNull();
    expect(linkAdresse(link('javascript:alert(1)'))).toBeNull();
    // Browser lesen „/\“ wie „//“ (fremder Host).
    expect(linkAdresse(link('/\\evil.test/x'))).toBeNull();
    expect(linkAdresse(link('/a\\b'))).toBeNull();
  });
});

describe('KI-Vorschlag',() => {
  it.each([
    ['frist','2026-11-01','schlägt vor: Frist → 01.11.2026'],
    ['frist',null,'schlägt vor: Frist entfernt'],
    ['status','in_arbeit','schlägt vor: Status → in Arbeit'],
    ['zustaendig',{gewerk_id:7,nutzer_id:3},'schlägt vor: Zuständig → Dach – Firma X (DA) / Konto C'],
    ['zustaendig',null,'schlägt vor: Zuständigkeit entfernt'],
  ])('%s %j', (art,wert,text) => {
    expect(eintragInhalt(eintrag({art:'vorschlag',text:null,wert:{art,wert},offen:true,herkunft:'ki'}),namen)).toBe(text);
  });
});
