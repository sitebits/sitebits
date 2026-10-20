import { describe, expect, it } from 'vitest';
import { einladungHinweis } from '../src/portal/seiten';

describe('einladungHinweis', () => {
  it('ohne Fragment (nach Neuladen): Link erneut öffnen', () => {
    expect(einladungHinweis(null)).toBe('Bitte öffnen Sie den Einladungslink erneut aus Ihrer Nachricht.');
  });
  it('kaputter oder zu kurzer Token: ungültig', () => {
    expect(einladungHinweis('')).toBe('Einladung ungültig, abgelaufen oder bereits eingelöst.');
    expect(einladungHinweis('abc')).toBe('Einladung ungültig, abgelaufen oder bereits eingelöst.');
  });
  it('plausibler Token: Server fragen', () => {
    expect(einladungHinweis('A'.repeat(43))).toBeNull();
  });
});
