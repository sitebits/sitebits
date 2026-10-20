import { describe, expect, it } from 'vitest';
import { standardPaketPfad } from '../src/standardpaket';

// Das Standardpaket des Einzelmodus kommt beim Bau aus VITE_STANDARDPAKET (private Demo-/
// Entwicklungsumgebung); ohne Angabe gilt der neutrale Name „beispiel“.
describe('standardPaketPfad', () => {
  it('ohne Angabe: /data/beispiel/', () => {
    expect(standardPaketPfad(undefined)).toBe('/data/beispiel/');
    expect(standardPaketPfad('')).toBe('/data/beispiel/');
    expect(standardPaketPfad('   ')).toBe('/data/beispiel/');
  });
  it('gültige projectId wird übernommen', () => {
    expect(standardPaketPfad('paket-a-v2')).toBe('/data/paket-a-v2/');
    expect(standardPaketPfad(' paket-a ')).toBe('/data/paket-a/');
  });
  it('ungültige Angabe fällt auf beispiel zurück (kein Pfadtrick)', () => {
    for (const falsch of ['../x', 'a/b', 'A', 'https://x.example/', '-x', 'a'.repeat(65)]) {
      expect(standardPaketPfad(falsch)).toBe('/data/beispiel/');
    }
  });
});
