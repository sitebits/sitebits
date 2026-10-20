import { describe, expect, it } from 'vitest';
import { zielPfad } from '../src/portal/weiter';

const ORIGIN = 'http://localhost:5173';

describe('zielPfad', () => {
  it('akzeptiert einen normalen Pfad mit Query', () => {
    expect(zielPfad('/ansicht?p=/data/x/', ORIGIN)).toBe('/ansicht?p=/data/x/');
  });
  it('lehnt fehlenden Pfad ab', () => {
    expect(zielPfad(null, ORIGIN)).toBe('/');
  });
  it('lehnt protokollrelative Pfade ab (//)', () => {
    expect(zielPfad('//evil.example/', ORIGIN)).toBe('/');
  });
  it('lehnt den Backslash-Trick ab (Browser normalisiert \\ zu /)', () => {
    expect(zielPfad('/\\evil.example/', ORIGIN)).toBe('/');
  });
  it('lehnt absolute fremde URLs ab', () => {
    expect(zielPfad('https://evil.example/', ORIGIN)).toBe('/');
  });
  it('lehnt Pfade ab, die erst nach dem Normalisieren protokollrelativ werden', () => {
    for (const pfad of ['/.//evil.example', '/x/..//evil.example', '/%2e//evil.example', '/./\\evil.example', '/x/..\\/evil.example']) {
      const ziel = zielPfad(pfad, ORIGIN);
      expect(new URL(ziel, ORIGIN).origin, pfad).toBe(ORIGIN);
      expect(ziel, pfad).toBe('/');
    }
  });
  it('behält Hash und Query eines eigenen Pfads', () => {
    expect(zielPfad('/viewer/?p=/data/x/#b=P-1&rev=2', ORIGIN)).toBe('/viewer/?p=/data/x/#b=P-1&rev=2');
  });
  it('lehnt Pfade ohne führenden Schrägstrich ab', () => {
    expect(zielPfad('anmelden', ORIGIN)).toBe('/');
  });
});
