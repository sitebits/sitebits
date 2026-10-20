import { expect, it, vi } from 'vitest';
import { PlattformFehler } from '../src/plattformApi';
import { gesammelt, ergebnisText, anzahl } from '../src/portal/zuPruefenModell';
it('entscheidet nacheinander und setzt nach gemischten 409/422 fort', async () => {
  const reihenfolge: number[] = [];
  const f = vi.fn(async (x: number) => { reihenfolge.push(x); await Promise.resolve(); if (x === 2)
    throw new PlattformFehler(409, 'Schon erledigt'); if (x === 3)
    throw new PlattformFehler(422, 'Kein Vorschlag'); });
  const r = await gesammelt([1, 2, 3, 4], f);
  expect(reihenfolge).toEqual([1, 2, 3, 4]);
  expect(r).toEqual({ erledigt: 2, gruende: ['Schon erledigt', 'Kein Vorschlag'] });
  expect(ergebnisText(r, 'annehmen')).toBe('2 angenommen, 2 übersprungen (Schon erledigt; Kein Vorschlag).');
  expect(anzahl({ pois: [], messungen: [], faden: [] })).toBe(0);
});
import { pruefgruppen } from '../src/portal/zuPruefenModell';
it('gruppiert Faden-Vorschläge mit lesbarem Inhalt, Urheber und präzisem Eintragslink', () => {
  const g = pruefgruppen({ pois: [], messungen: [], faden: [{ poi: 'P1', eintrag: { schluessel: '0123456789abcdef', art: 'vorschlag', wert: { art: 'frist', wert: '2026-11-01' }, text: null, erstellt_von: { id: 1, name: 'Prüfkonto' }, erstellt_am: 1, kanal: 'api', intern: false, zurueckgezogen_am: null } }] }, 'p');
  expect(g.map(x => [x.name, x.zeilen.length])).toEqual([['POIs', 0], ['Messungen', 0], ['Befund-Faden', 1]]);
  expect(g[2]?.zeilen[0]).toEqual({ text: 'KI schlägt vor: Frist → 01.11.2026', wer: 'Prüfkonto', wann: 1, link: '/ansicht?p=/data/p/#b=P1&e=0123456789abcdef', ziel: { tabelle: 'faden', poi: 'P1', schluessel: '0123456789abcdef' } });
});
