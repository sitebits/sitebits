import { describe, expect, it } from 'vitest';
import { filtere, formatAusdehnung, formatGroesse, formatPunkte, formatStandpunkte, trefferText, viewerLink } from '../src/portal/karten';
import type { Projekt } from '../src/portal/api';

const p = (id: string, titel: string, beschreibung = ''): Projekt => ({
  id, titel, beschreibung, punkte: 1, standpunkte: 1, ausdehnung: null, crs: 'lokal', scandatum: null,
  groesseBytes: 1, draufsicht: false, rolle: 'betrachter',
  version: 0,
});

describe('karten', () => {
  it('formatiert Punkte', () => {
    expect(formatPunkte(578_422_748)).toBe('578,4 Mio. Punkte');
    expect(formatPunkte(1234)).toBe('1.234 Punkte');
    expect(formatPunkte(null)).toBe('–');
  });
  it('formatiert Größe', () => {
    expect(formatGroesse(4_200_000_000)).toBe('4,2 GB');
    expect(formatGroesse(630_000_000)).toBe('630 MB');
  });
  it('formatiert Ausdehnung', () => {
    expect(formatAusdehnung([40.7, 30.4, 46.2])).toBe('41 × 30 × 46 m');
    expect(formatAusdehnung([4.25, 3.1, 2.04])).toBe('4,3 × 3,1 × 2,0 m');
    expect(formatAusdehnung(null)).toBe('–');
  });
  it('sucht ohne Groß-/Kleinschreibung und Umlaute', () => {
    const l = [p('a', 'Musterhalle Nord'), p('b', 'Fassade Ost', 'Außenhülle'), p('c', 'Halle 4')];
    expect(filtere(l, 'musterhalle').map((x) => x.id)).toEqual(['a']);
    expect(filtere(l, 'aussen').map((x) => x.id)).toEqual([]);
    expect(filtere(l, 'außen').map((x) => x.id)).toEqual(['b']);
    expect(filtere(l, 'AUSSENHULLE').map((x) => x.id)).toEqual([]);
    expect(filtere(l, 'hülle').map((x) => x.id)).toEqual(['b']);
    expect(filtere(l, 'hulle').map((x) => x.id)).toEqual(['b']);
    expect(filtere(l, '  ').length).toBe(3);
  });
  it('baut Viewer-Link', () => {
    expect(viewerLink('musterhalle-nord-v2')).toBe('/ansicht?p=/data/musterhalle-nord-v2/');
  });
  it('formatiert Standpunkte', () => {
    expect(formatStandpunkte(null)).toBe('–');
    expect(formatStandpunkte(1)).toBe('1 Standpunkte');
    expect(formatStandpunkte(45)).toBe('45 Standpunkte');
  });
});

describe('trefferText', () => {
  it('Einzahl, Mehrzahl, keiner', () => {
    expect(trefferText(0)).toBe('Kein Projekt gefunden.');
    expect(trefferText(1)).toBe('1 Projekt gefunden.');
    expect(trefferText(2)).toBe('2 Projekte gefunden.');
  });
});
