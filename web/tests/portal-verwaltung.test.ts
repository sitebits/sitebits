import { describe, expect, it } from 'vitest';
import { datumText, einladungsText, istEigenesKonto, istRolle, rechteAusAuswahl, ROLLEN_AUSWAHL, sparkline, statusText, tagText, zelle } from '../src/portal/verwaltungModell';
import type { AdminNutzer } from '../src/portal/verwaltungModell';

const n = (teil: Partial<AdminNutzer>): AdminNutzer => ({
  id: 1, name: 'T', email: 't@b.de', admin: false, gesperrt: false, letzteAnmeldung: null, rechte: {}, anonymisiert: false, ...teil,
});

describe('Verwaltung', () => {
  it('Zelle der Rechte-Matrix', () => {
    expect(zelle(n({ rechte: { ost: 'bearbeiter' } }), 'ost')).toBe('bearbeiter');
    expect(zelle(n({}), 'ost')).toBe('');
    expect(zelle(n({ admin: true }), 'ost')).toBe('admin');
  });
  it('Status und Datum', () => {
    expect(statusText(n({ gesperrt: true }))).toBe('gesperrt');
    expect(statusText(n({}))).toBe('aktiv');
    expect(statusText(n({ gesperrt: true, anonymisiert: true }))).toBe('anonymisiert');
    expect(datumText(null)).toBe('–');
    expect(datumText(0)).toContain('1970');
    expect(tagText('2026-09-27')).toBe('27.09.2026');
    expect(tagText(null)).toBe('–');
  });
  it('Rechte aus der Auswahl: leere Felder entfallen', () => {
    expect(rechteAusAuswahl({ ost: 'bearbeiter', halle: '', b: 'betrachter', x: 'admin' })).toEqual({ ost: 'bearbeiter', b: 'betrachter' });
  });
  it('Rückmelder ist wählbar und wird übernommen, Fremdwerte nicht', () => {
    expect(ROLLEN_AUSWAHL).toEqual([
      { wert: '', text: '—' },
      { wert: 'betrachter', text: 'Betrachter' },
      { wert: 'rueckmelder', text: 'Rückmelder' },
      { wert: 'bearbeiter', text: 'Bearbeiter' },
      { wert: 'projektleitung', text: 'Projektleitung' },
    ]);
    expect(rechteAusAuswahl({ a: 'rueckmelder', b: 'Rueckmelder', c: 'rückmelder', d: 'chef', e: 'constructor' })).toEqual({ a: 'rueckmelder' });
    expect(['betrachter', 'rueckmelder', 'bearbeiter', 'projektleitung'].every(istRolle)).toBe(true);
    for (const w of ['', 'admin', 'Bearbeiter', 'betrachter ']) expect(istRolle(w)).toBe(false);
  });
  it('Sparkline', () => {
    expect(sparkline([0, 0, 0])).toBe('▁▁▁');
    expect(sparkline([0, 1, 2])).toBe('▁▅█');
    expect(sparkline([])).toBe('');
  });
  it('eigenes Konto: Vergleich ohne Groß-/Kleinschreibung und Leerzeichen', () => {
    expect(istEigenesKonto(n({ email: 'Admin@B.de' }), ' admin@b.de ')).toBe(true);
    expect(istEigenesKonto(n({ email: 'andere@b.de' }), 'admin@b.de')).toBe(false);
  });
});

describe('einladungsText', () => {
  const link = 'https://x.test/einladung#t=abc';
  it('Vorname aus dem Namen, Link auf eigener Zeile, Gültigkeit genannt', () => {
    const t = einladungsText(link, '  Erika Mustermann ');
    expect(t.startsWith('Hallo Erika,')).toBe(true);
    expect(t.split('\n')).toContain(link);
    expect(t).toContain('7 Tage');
  });
  it('ohne Namen neutrale Anrede; Zurücksetzen mit eigenem Satz', () => {
    expect(einladungsText(link, '').startsWith('Hallo,')).toBe(true);
    expect(einladungsText(link, 'A', true)).toContain('neu einrichtest');
  });
});
