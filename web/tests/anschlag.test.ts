import { describe, expect, it } from 'vitest';
import { anschlagMoeglich, kennung, trifftAnschlag } from '../src/anschlag';
import { entpacke, TAFEL } from '../src/anschlagTafel';

describe('Tafel am Westportal', () => {
  it('Kennung FNV-1a, nur das synthetische Testpaket', () => {
    expect(kennung('')).toBe(0x811c9dc5);
    expect(kennung('a')).toBe(0xe40c292c);
    expect(anschlagMoeglich('e2e-kirche')).toBe(true);
    expect(anschlagMoeglich('e2e-kirche-alt')).toBe(false);
    expect(anschlagMoeglich('e2e-halle')).toBe(false);
  });
  it('Treffer nur auf der Türfläche', () => {
    expect(trifftAnschlag([0.2, 0, 1.5])).toBe(true);
    expect(trifftAnschlag([0.2, 1.2, 1.5])).toBe(false);
    expect(trifftAnschlag([0, 0, 4])).toBe(false);
    expect(trifftAnschlag([5, 0, 1])).toBe(false);
    expect(trifftAnschlag(null)).toBe(false);
  });
  it('Inhalt lässt sich entpacken und hat beide Sprachen', () => {
    const inhalt = entpacke(TAFEL) as { de: { text: string[] }; en: { text: string[] }; figur: object; musik: object };
    expect(inhalt.de.text.length).toBeGreaterThan(5);
    expect(inhalt.en.text.length).toBe(inhalt.de.text.length);
    expect(Object.keys(inhalt)).toEqual(['de', 'en', 'figur', 'musik']);
  });
});
