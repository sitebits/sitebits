import { expect, it } from 'vitest';
import { grenzen, schritt, anteilAusZeiger } from '../src/aufteilung';
import { loadFotoLayout, saveFotoLayout } from '../src/layoutStore';
import type { KV } from '../src/layoutStore';

it('verallgemeinerte Aufteilung: Foto 25–75 %, Tasten und Finger quer/hoch', () => {
  const anteile = { hoch: { min: 0.25, max: 0.75 }, quer: { min: 0.25, max: 0.75 } };
  for (const lage of ['hoch', 'quer'] as const) {
    const g = grenzen(lage, 1000, anteile);
    expect(g).toEqual({ min: 0.25, max: 0.75 });
    expect(schritt(lage, 'Home', 0.5, g)).toBe(0.25);
    expect(schritt(lage, 'End', 0.5, g)).toBe(0.75);
    expect(anteilAusZeiger(lage, 388, { anfang: 0, laenge: 1000 })).toBe(0.6);
  }
  const klein = grenzen('hoch', 200, anteile); expect(klein.min).toBe(klein.max);
});
it('Foto-Layout ist projektweise gespeichert, validiert und gegen Speicherfehler geschützt', () => {
  const daten = new Map<string, string>();
  const s: KV = { getItem: (k) => daten.get(k) ?? null, setItem: (k, v) => { daten.set(k, v); }, removeItem: (k) => { daten.delete(k); } };
  saveFotoLayout(s, 'projekt-a', { tiefe: 20, hoch: 0.3, quer: 0.7 });
  expect(loadFotoLayout(s, 'projekt-a')).toEqual({ tiefe: 20, hoch: 0.3, quer: 0.7 });
  expect(loadFotoLayout(s, 'projekt-b').tiefe).toBe(1.5);
  const key = [...daten.keys()][0]!;
  daten.set(key, '{'); expect(loadFotoLayout(s, 'projekt-a').tiefe).toBe(1.5);
  daten.set(key, JSON.stringify({ tiefe: -1, hoch: 1, quer: 'kaputt' }));
  expect(loadFotoLayout(s, 'projekt-a')).toEqual({ tiefe: 0.1, hoch: 0.75, quer: 0.5 });
  const kaputt: KV = { getItem() { throw Error('gesperrt'); }, setItem() { throw Error('voll'); }, removeItem() {} };
  expect(() => saveFotoLayout(kaputt, 'a', { tiefe: 1, hoch: 0.5, quer: 0.5 })).not.toThrow();
  expect(loadFotoLayout(kaputt, 'a').tiefe).toBe(1.5);
});
