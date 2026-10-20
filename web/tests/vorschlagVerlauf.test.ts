import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import fixture from './fixtures/vorschlag-ereignisse.json';
import { artText } from '../src/portal/verlaufText';
it('beschriftet alle Vorschlagsereignisse für POIs, Messungen und Faden', () => {
  for (const art of [...fixture.inhalt.flatMap(a => [`poi.${a}`, `messung.${a}`]), ...fixture.faden]) {
    expect(artText(art)).not.toBe(art);
    expect(artText(art)).not.toContain('_');
  }
  expect(artText('poi.vorgeschlagen')).toBe('POI vorgeschlagen (KI)');
});
it('Fixture hält mit den Ereignissen und Prüfschritten des Servers Schritt', () => {
  const inhalte = readFileSync(new URL('../../server/src/scanview_server/inhalte.py', import.meta.url), 'utf8');
  const faden = readFileSync(new URL('../../server/src/scanview_server/faden.py', import.meta.url), 'utf8');
  const schritte = /Pruefschritt = Literal\[([^\]]+)\]/.exec(inhalte)?.[1] ?? '';
  const pruefschritte = [...schritte.matchAll(/"([^"]+)"/g)].map(m => m[1]);
  expect(pruefschritte.length).toBeGreaterThan(0);
  for (const a of pruefschritte)
    expect(fixture.inhalt).toContain(a);
  const weitere = [...inhalte.matchAll(/"(vorschlag_[a-z_]+|vorgeschlagen)"/g)].map(m => m[1]);
  for (const a of weitere)
    expect(fixture.inhalt).toContain(a);
  const fadenarten = [...new Set([...faden.matchAll(/ereignis = "(eintrag\.[^"]+)"/g)].map(m => m[1]).filter(a => /vorschlag|vorgeschlag/.test(a ?? '')))];
  expect(fadenarten.sort()).toEqual([...fixture.faden].sort());
  for (const a of fixture.inhalt)
    expect(inhalte).toContain(a);
});
