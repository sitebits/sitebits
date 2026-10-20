import { describe, expect, it } from 'vitest';
import { datumText, HINWEIS_ERKLAERUNG, HINWEIS_KONTAKT, KONTAKT_NICHT_ABRUFBAR, LADEFEHLER, standText } from '../src/portal/barrierefreiheit';

describe('Seite Barrierefreiheit — Texte', () => {
  it('Stand der Vereinbarkeit in Worten', () => {
    expect(standText('vollstaendig')).toBe('vollständig vereinbar');
    expect(standText('teilweise')).toBe('teilweise vereinbar');
    expect(standText('nicht')).toBe('nicht vereinbar');
  });
  it('Datum deutsch', () => {
    expect(datumText('2026-10-01')).toBe('01.10.2026');
  });
  it('Hinweise nennen Betreiber und Einstellung, Ladefehler sagt was zu tun ist', () => {
    expect(HINWEIS_ERKLAERUNG).toContain('Betreiber');
    expect(HINWEIS_KONTAKT).toContain('SCANVIEW_BARRIEREFREIHEIT_KONTAKT');
    expect(LADEFEHLER).toMatch(/später erneut/);
  });
  it('Hinweis bei Ladefehler nennt die Betreiberin/den Betreiber als Kontaktweg', () => {
    expect(KONTAKT_NICHT_ABRUFBAR).toContain('derzeit nicht abrufbar');
    expect(KONTAKT_NICHT_ABRUFBAR).toContain('Betreiberin/den Betreiber');
  });
});
