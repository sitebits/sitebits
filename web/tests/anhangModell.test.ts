import { describe, expect, it } from 'vitest';
import { anhangText, aufnahmeText, eintragInhalt, OHNE_NAMEN } from '../src/fadenModell';
import type { AnhangStand, FadenEintrag } from '../src/fadenModell';

const bild: AnhangStand = {
  id: 'a'.repeat(32), typ: 'bild', status: 'bereit', name: 'Riss.jpg', groesse: 120_000,
  breite: 4000, hoehe: 3000, aufnahme: '2026-10-21T14:05:09', fehler: null,
};

describe('Anhänge am Faden (F7)', () => {
  it('Foto mit Maßen und Aufnahmezeit', () => {
    expect(anhangText(bild)).toBe('Foto „Riss.jpg“, 4000 × 3000 px, aufgenommen 21.10.2026 14:05');
  });
  it('in Prüfung, abgelehnt, entfernt', () => {
    expect(anhangText({ ...bild, status: 'pruefung' })).toBe('Foto „Riss.jpg“ wird geprüft …');
    expect(anhangText({ ...bild, status: 'abgelehnt', fehler: 'Bild hat zu viele Pixel' })).toBe('Datei „Riss.jpg“ abgelehnt: Bild hat zu viele Pixel');
    expect(anhangText(null)).toBe('Anhang entfernt');
  });
  it('PDF nur zum Herunterladen', () => {
    expect(anhangText({ ...bild, typ: 'pdf', name: null, groesse: 2048, breite: null, hoehe: null, aufnahme: null }))
      .toBe('PDF aus externer Quelle (2 KB) – nur zum Herunterladen');
  });
  it('Aufnahmezeit nur in erwarteter Form', () => {
    expect(aufnahmeText('2026-10-21T14:05:09')).toBe('21.10.2026 14:05');
    expect(aufnahmeText('gestern')).toBeNull();
    expect(aufnahmeText(null)).toBeNull();
  });
  it('Eintragsinhalt nutzt den Anhangsstand, zurückgezogen bleibt zurückgezogen', () => {
    const e: FadenEintrag = {
      schluessel: 'b'.repeat(16), art: 'anhang', text: null, wert: null, intern: false,
      erstellt_von: { id: 1, name: 'A' }, erstellt_am: 0, kanal: 'web', zurueckgezogen_am: null, anhang: bild,
    };
    expect(eintragInhalt(e, OHNE_NAMEN)).toContain('Foto „Riss.jpg“');
    expect(eintragInhalt({ ...e, zurueckgezogen_am: 1 }, OHNE_NAMEN)).toBe('zurückgezogen');
  });
});
