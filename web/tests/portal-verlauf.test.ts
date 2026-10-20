import { describe, expect, it } from 'vitest';
import { artText, bezeichnung, unterschiede, werText, wertText, zeitText } from '../src/portal/verlaufText';
import type { Ereignis } from '../src/portal/verlaufText';

const e = (teil: Partial<Ereignis>): Ereignis => ({
  id: 1, zeit: 0, nutzerId: 1, nutzer: 'Anna', kanal: 'web', projekt: 'p1', art: 'poi.angelegt', objekt: 'P-1', vorher: null, nachher: null, ...teil,
});

describe('Verlauf', () => {
  it('zeigt nur geänderte Felder', () => {
    expect(unterschiede({ title: 'Tür', note: '' }, { title: 'Tür', note: 'innen' })).toEqual([{ feld: 'Notiz', vorher: '–', nachher: 'innen' }]);
  });
  it('Anlegen: alle Felder mit „–“ vorher, Zahlen deutsch', () => {
    expect(unterschiede(null, { title: 'A', position: [1, 2.5, 3] })).toEqual([
      { feld: 'Titel', vorher: '–', nachher: 'A' },
      { feld: 'Position', vorher: '–', nachher: '1 / 2,5 / 3' },
    ]);
  });
  it('Messungen: Art und Eckpunktzahl lesbar', () => {
    const u = unterschiede({ kind: 'area', points: [[0, 0, 0], [1, 0, 0], [1, 1, 0]] }, { kind: 'area', points: [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]] });
    expect(u).toEqual([{ feld: 'Eckpunkte', vorher: '3 Punkte', nachher: '4 Punkte' }]);
    expect(bezeichnung(e({ art: 'messung.angelegt', nachher: { kind: 'area', points: [] } }))).toBe('Fläche');
  });
  it('Rechte: einfacher Wert statt Objekt', () => {
    expect(unterschiede('betrachter', null)).toEqual([{ feld: 'Wert', vorher: 'betrachter', nachher: '–' }]);
    expect(unterschiede('bearbeiter', 'bearbeiter')).toEqual([]);
  });
  it('Rechte-Ereignisse zeigen die Rolle mit Anzeigenamen', () => {
    expect(unterschiede(null, 'rueckmelder', 'recht.vergeben')).toEqual([{ feld: 'Wert', vorher: '–', nachher: 'Rückmelder' }]);
    expect(unterschiede('rueckmelder', 'bearbeiter', 'recht.vergeben')).toEqual([{ feld: 'Wert', vorher: 'Rückmelder', nachher: 'Bearbeiter' }]);
    expect(unterschiede('betrachter', null, 'recht.entzogen')).toEqual([{ feld: 'Wert', vorher: 'Betrachter', nachher: '–' }]);
    expect(unterschiede(null, 'chef', 'recht.vergeben')).toEqual([{ feld: 'Wert', vorher: '–', nachher: 'chef' }]);
  });
  it('Einladung zeigt Rechte und Zweck lesbar, ohne JSON', () => {
    expect(unterschiede(null, { rechte: { p: 'rueckmelder', q: 'bearbeiter' }, zweck: 'neu' }, 'einladung.angelegt')).toEqual([
      { feld: 'Rechte', vorher: '–', nachher: 'p: Rückmelder, q: Bearbeiter' },
      { feld: 'Zweck', vorher: '–', nachher: 'Neuer Zugang' },
    ]);
    expect(wertText('zweck', 'zuruecksetzen')).toBe('Zugang zurückgesetzt');
    expect(wertText('rechte', {})).toBe('–');
  });
  it('Texte für Art, Bezeichnung, Wer, Zeit', () => {
    expect(artText('poi.angelegt')).toBe('POI angelegt');
    expect(artText('meta.geaendert')).toBe('Titel/Beschreibung geändert');
    expect(artText('neu.unbekannt')).toBe('neu.unbekannt');
    expect(bezeichnung(e({ nachher: { title: 'Tür' } }))).toBe('„Tür“');
    expect(werText(e({ nutzer: null, kanal: 'cli' }))).toBe('Kommandozeile');
    expect(werText(e({}))).toBe('Anna');
    expect(werText(e({ kanal: 'api', token: 'Claude Desktop' }))).toBe('Anna (per API, Token „Claude Desktop“)');
    expect(werText(e({ kanal: 'api' }))).toBe('Anna (per API)');
    expect(zeitText(0)).toContain('1970');
  });
});

describe('Verwaltungsereignisse im Verlauf', () => {
  it('Arten deutsch', () => {
    expect(artText('gewerk.angelegt')).toBe('Gewerk angelegt');
    expect(artText('gewerk.geaendert')).toBe('Gewerk geändert');
    expect(artText('gewerk.geloescht')).toBe('Gewerk gelöscht');
    expect(artText('beteiligter.gewerk')).toBe('Gewerk zugeordnet');
    expect(artText('kategorie.angelegt')).toBe('Kategorie angelegt');
    expect(artText('kategorie.geaendert')).toBe('Kategorie geändert');
    expect(artText('einstellung.geaendert')).toBe('Schalter geändert');
    expect(artText('projekt.eingerichtet')).toBe('Projekt eingerichtet');
  });
  it('Felder und Werte', () => {
    expect(unterschiede({ rueckmeldungen: true, exportBearbeiter: true }, { rueckmeldungen: false, exportBearbeiter: true }))
      .toEqual([{ feld: 'Rückmeldungen erlaubt', vorher: 'ja', nachher: 'nein' }]);
    expect(unterschiede(null, { vorlage: 'bauprojekt' })).toEqual([{ feld: 'Vorlage', vorher: '–', nachher: 'Bauprojekt' }]);
    expect(unterschiede({ name: 'Raum', aktiv: true, reihenfolge: 0 }, { name: 'Raum', aktiv: false, reihenfolge: 0 }))
      .toEqual([{ feld: 'Aktiv', vorher: 'ja', nachher: 'nein' }]);
    expect(unterschiede('bearbeiter', 'projektleitung', 'recht.vergeben')).toEqual([{ feld: 'Wert', vorher: 'Bearbeiter', nachher: 'Projektleitung' }]);
    expect(unterschiede({ kategorien: ['Raum', 'Anlage'] }, { kategorien: ['Raum'] })).toEqual([{ feld: 'Kategorien', vorher: 'Raum, Anlage', nachher: 'Raum' }]);
  });
  it('Konto-Bezug mit Namen, sonst Konto-Nummer', () => {
    const ev = e({ nutzerId: 1, nutzer: 'Lea', art: 'recht.vergeben', objekt: '7', nachher: 'betrachter' });
    expect(bezeichnung(ev, new Map([[7, 'Tom']]))).toBe('Tom');
    expect(bezeichnung(ev)).toBe('Konto 7');
    expect(bezeichnung({ ...ev, art: 'beteiligter.gewerk' })).toBe('Konto 7');
    expect(bezeichnung(e({ art: 'gewerk.angelegt', objekt: '3', nachher: { name: 'Elektro' } }))).toBe('„Elektro“');
  });
});

describe('Befund-Fäden im Verlauf', () => {
  it('Arten deutsch', () => {
    expect(['eintrag.angelegt', 'eintrag.zurueckgezogen', 'poi.status', 'poi.zustaendig', 'poi.frist'].map(artText)).toEqual([
      'Eintrag im Befund-Faden', 'Eintrag zurückgezogen', 'Status geändert', 'Zuständigkeit geändert', 'Frist geändert',
    ]);
  });
  it('Status und Frist lesbar, Zuständigkeit je Feld', () => {
    expect(unterschiede('offen', 'erledigt', 'poi.status')).toEqual([{ feld: 'Wert', vorher: 'offen', nachher: 'erledigt (gemeldet)' }]);
    expect(unterschiede(null, '2026-10-02', 'poi.frist')).toEqual([{ feld: 'Wert', vorher: '–', nachher: '02.10.2026' }]);
    expect(unterschiede(null, { gewerk_id: 4, nutzer_id: null }, 'poi.zustaendig')).toEqual([{ feld: 'Gewerk', vorher: '–', nachher: '4' }]);
  });
  it('Eintrag: Art, Text und intern ohne technische Felder', () => {
    const nachher = {
      schluessel: '0123456789abcdef', poi: 'P-1', art: 'kommentar', text: 'Dach undicht', wert: null, intern: true,
      erstellt_von: 3, erstellt_am: 5, kanal: 'web', zurueckgezogen_am: null,
    };
    expect(unterschiede(null, nachher, 'eintrag.angelegt')).toEqual([
      { feld: 'Art', vorher: '–', nachher: 'Kommentar' },
      { feld: 'Text', vorher: '–', nachher: 'Dach undicht' },
      { feld: 'Intern', vorher: '–', nachher: 'ja' },
    ]);
    const link = { ...nachher, art: 'link', text: null, intern: false, wert: { url: 'https://example.org/plan', titel: 'Plan' } };
    expect(unterschiede(null, link, 'eintrag.angelegt')).toEqual([
      { feld: 'Art', vorher: '–', nachher: 'Link' },
      { feld: 'Wert', vorher: '–', nachher: 'Plan – https://example.org/plan' },
      { feld: 'Intern', vorher: '–', nachher: 'nein' },
    ]);
    expect(bezeichnung(e({ art: 'eintrag.angelegt', objekt: 'P-1/0123456789abcdef', nachher }))).toBe('Befund P-1');
  });
  it('Schalter Befund-Fäden', () => {
    expect(unterschiede({ befundFaeden: false }, { befundFaeden: true }, 'einstellung.geaendert')).toEqual([{ feld: 'Befund-Fäden', vorher: 'nein', nachher: 'ja' }]);
  });
});
