import { describe, expect, it } from 'vitest';
import { standText } from '../src/portal/freigaben';
import type { EbenenStand } from '../src/portal/freigaben';

const basis: EbenenStand = {
  ebene: 'panos', vorhanden: true, torAktiv: true, dateien: 48, fehler: null,
  anonymisierung: { status: 'reviewed', werkzeug: 'pano-anonym', manifestSha256: 'a'.repeat(64), geprueft: true },
  freigabe: null,
};

describe('standText (Freigabe F20)', () => {
  it('ohne Freigabe: nur Bearbeiter+', () => {
    expect(standText(basis)).toBe('Panoramen: nicht freigegeben – nur Bearbeiter, Projektleitung und Admins sehen sie.');
  });
  it('Altbestand ohne Tor ist ausdrücklich gekennzeichnet', () => {
    expect(standText({ ...basis, torAktiv: false })).toContain('Altbestand ohne Anonymisierung');
  });
  it('abweichende Dateien werden genannt', () => {
    const t = standText({ ...basis, freigabe: { art: 'anonymisiert', dateien: 48, abweichend: 2, freigegebenAm: 1 } });
    expect(t).toBe('Panoramen: freigegeben (anonymisiert und geprüft), aber 2 von 48 Dateien wurden danach verändert und sind gesperrt.');
  });
  it('ohne Nachprüfung ehrlich als nicht nachgeprüft', () => {
    expect(standText({ ...basis, freigabe: { art: 'anonymisiert', dateien: 48, freigegebenAm: 1 } }))
      .toBe('Panoramen: freigegeben (anonymisiert und geprüft), 48 Dateien (noch nicht nachgeprüft).');
  });
  it('personenfrei und Fotos', () => {
    expect(standText({ ...basis, ebene: 'fotos', freigabe: { art: 'personenfrei', dateien: 3, abweichend: 0, freigegebenAm: 1 } }))
      .toBe('Fotos: freigegeben (als personenfrei erklärt), 3 Dateien.');
  });
});
