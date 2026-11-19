import { describe, expect, it } from 'vitest';
import { pruefZeit, standText } from '../src/portal/freigaben';
import type { EbenenStand } from '../src/portal/freigaben';

const basis: EbenenStand = {
  ebene: 'panos', vorhanden: true, torAktiv: true, dateien: 48, fehler: null,
  anonymisierung: { status: 'reviewed', werkzeug: 'pano-anonym', manifestSha256: 'a'.repeat(64), geprueft: true },
  freigabe: null,
};
const frei = { art: 'anonymisiert' as const, dateien: 48, freigegebenAm: 1, zuletztGeprueftAm: null, zuletztAbweichend: null };
// 11.11.2026 09:05 deutscher Zeit (MEZ, UTC+1)
const GEPRUEFT = Date.UTC(2026, 10, 11, 8, 5) / 1000;

describe('standText (Freigabe F20)', () => {
  it('ohne Freigabe: nur Bearbeiter+', () => {
    expect(standText(basis)).toBe('Panoramen: nicht freigegeben – nur Bearbeiter, Projektleitung und Admins sehen sie.');
  });
  it('nie geprüft: ehrlich als nicht nachgeprüft', () => {
    expect(standText({ ...basis, freigabe: frei }))
      .toBe('Panoramen: freigegeben (anonymisiert und geprüft), 48 Dateien (noch nicht nachgeprüft).');
  });
  it('geprüft, 0 abweichend: gespeicherter Stand samt Datum (nach dem Neuladen)', () => {
    expect(standText({ ...basis, freigabe: { ...frei, zuletztGeprueftAm: GEPRUEFT, zuletztAbweichend: 0 } }))
      .toBe('Panoramen: freigegeben (anonymisiert und geprüft), alle 48 Dateien unverändert (geprüft 11.11. 09:05).');
  });
  it('geprüft, 2 abweichend: frische Prüfung', () => {
    expect(standText({ ...basis, freigabe: { ...frei, abweichend: 2, zuletztGeprueftAm: GEPRUEFT, zuletztAbweichend: 2 } }))
      .toBe('Panoramen: freigegeben (anonymisiert und geprüft), aber 2 von 48 Dateien abweichend – für Betrachter gesperrt (geprüft 11.11. 09:05).');
  });
  it('Altbestand mit bewusst abgeschaltetem Schalter: Warnhinweis', () => {
    expect(standText({ ...basis, torAktiv: false, anonymisierung: { ...basis.anonymisierung, status: 'unreviewed', geprueft: false } }))
      .toBe('Panoramen: Achtung, für alle Beteiligten sichtbar, auch ohne Anonymisierung – „Panoramen nur freigegeben zeigen“ ist bewusst aus.');
  });
  it('personenfrei und Fotos', () => {
    expect(standText({ ...basis, ebene: 'fotos', freigabe: { ...frei, art: 'personenfrei', dateien: 3, abweichend: 0, zuletztGeprueftAm: GEPRUEFT, zuletztAbweichend: 0 } }))
      .toBe('Fotos: freigegeben (als personenfrei erklärt), alle 3 Dateien unverändert (geprüft 11.11. 09:05).');
  });
});

describe('pruefZeit', () => {
  it('deutsche Zeit auch im Sommer und über Mitternacht', () => {
    // 01.07.2026 22:30 UTC = 02.07. 00:30 MESZ
    expect(pruefZeit(Date.UTC(2026, 6, 1, 22, 30) / 1000)).toBe('02.07. 00:30');
  });
});
