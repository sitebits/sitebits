import { describe, expect, it } from 'vitest';
import { alleLoeschenErlaubt, alleLoeschenFrage } from '../src/measureList';

describe('„Alle löschen“', () => {
  it('Einzelmodus bzw. nicht gemeinsam: keine Rückfrage', () => {
    const fragen: string[] = [];
    expect(alleLoeschenErlaubt(3, false, (t) => (fragen.push(t), false))).toBe(true);
    expect(fragen).toEqual([]);
  });
  it('Plattformmodus (Bearbeiter): Rückfrage mit Anzahl, Abbrechen löscht nicht', () => {
    const fragen: string[] = [];
    expect(alleLoeschenErlaubt(4, true, (t) => (fragen.push(t), false))).toBe(false);
    expect(alleLoeschenErlaubt(4, true, () => true)).toBe(true);
    expect(fragen).toEqual(['Löscht alle 4 Messungen des Projekts – für alle Beteiligten. Wirklich löschen?']);
    expect(alleLoeschenFrage(4)).toBe(fragen[0]);
  });
  it('nichts zu löschen: keine Rückfrage', () => {
    expect(alleLoeschenErlaubt(0, true, () => false)).toBe(true);
  });
});
