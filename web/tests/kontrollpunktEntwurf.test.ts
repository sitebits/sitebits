import { describe, expect, it } from 'vitest';
import { ENTWURF_MAX_ZEICHEN, entwurfJson, entwurfSchluessel, kpEntwurfSpeicher, listenSha, pruefeKpEntwurf } from '../src/kontrollpunktEntwurf';
import { KP_ENTWURF_PRAEFIX, kpEntwuerfeLoeschen } from '../src/entwurfKonstanten';
import { entwuerfeLoeschen } from '../src/portal/rahmen';
import type { IstWert } from '../src/kontrollpunkte';

/** Nachgebauter localStorage (Map); `sperre` lässt jeden Zugriff werfen wie ein gesperrter Browser-Speicher. */
function speicher(sperre = false): Storage & { daten: Map<string, string> } {
  const daten = new Map<string, string>();
  const pruef = (): void => { if (sperre) throw new DOMException('gesperrt', 'SecurityError'); };
  return {
    daten,
    get length() { pruef(); return daten.size; },
    key: (i: number) => { pruef(); return [...daten.keys()][i] ?? null; },
    getItem: (k: string) => { pruef(); return daten.get(k) ?? null; },
    setItem: (k: string, v: string) => { pruef(); daten.set(k, v); },
    removeItem: (k: string) => { pruef(); daten.delete(k); },
    clear: () => { pruef(); daten.clear(); },
  };
}

const PICKS = new Map<string, IstWert>([
  ['K1', { punkt: [367000.123, 5807000.5, 40.25], quelle: 'Mesh: Fassade' }],
  ['P1', { punkt: [1, 2, 3], quelle: 'Punktwolke: Wolke', grob: true }],
]);
const IDS = new Set(['K1', 'K2', 'P1']);

describe('kontrollpunktEntwurf', () => {
  it('SHA-256 wie der Prüfer (Text ohne BOM, UTF-8), Schlüssel je Projekt und Liste', async () => {
    // sha256("abc")
    expect(await listenSha('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(await listenSha('﻿abc')).toBe(await listenSha('abc'));
    expect(entwurfSchluessel('p1', 'ab')).toBe(`${KP_ENTWURF_PRAEFIX}p1:ab`);
  });

  it('Rundlauf: schreiben, lesen, gleiche Picks; leere Picks löschen den Entwurf', () => {
    const s = speicher(); const e = kpEntwurfSpeicher(() => s);
    expect(e.schreibe('k', PICKS)).toBe(true);
    expect(e.lies('k', IDS)).toEqual(PICKS);
    expect(e.schreibe('k', new Map())).toBe(true);
    expect(s.daten.has('k')).toBe(false);
    expect(e.lies('k', IDS)).toBeNull();
  });

  it.each([
    ['kein JSON', '{'],
    ['falsches Schema', JSON.stringify({ schema: 2, picks: {} })],
    ['fremde ID', JSON.stringify({ schema: 1, picks: { X: { p: [1, 2, 3], quelle: 'q' } } })],
    ['Koordinate nicht endlich', '{"schema":1,"picks":{"K1":{"p":[1,2,1e999],"quelle":"q"}}}'],
    ['zwei Koordinaten', JSON.stringify({ schema: 1, picks: { K1: { p: [1, 2], quelle: 'q' } } })],
    ['Zusatzfeld', JSON.stringify({ schema: 1, picks: { K1: { p: [1, 2, 3], quelle: 'q', x: 1 } } })],
    ['grob falsch', JSON.stringify({ schema: 1, picks: { K1: { p: [1, 2, 3], quelle: 'q', grob: false } } })],
    ['zu lange Quelle', JSON.stringify({ schema: 1, picks: { K1: { p: [1, 2, 3], quelle: 'q'.repeat(201) } } })],
    ['zu groß', 'x'.repeat(ENTWURF_MAX_ZEICHEN + 1)],
  ])('verwirft einen unbrauchbaren Entwurf und löscht ihn: %s', (_name, roh) => {
    expect(pruefeKpEntwurf(roh, IDS)).toBeNull();
    const s = speicher(); s.setItem('k', roh);
    expect(kpEntwurfSpeicher(() => s).lies('k', IDS)).toBeNull();
    expect(s.daten.has('k')).toBe(false);
  });

  it('gesperrter oder fehlender Speicher: kein Entwurf, kein Fehler', () => {
    for (const e of [kpEntwurfSpeicher(() => speicher(true)), kpEntwurfSpeicher(() => null),
      kpEntwurfSpeicher(() => { throw new DOMException('gesperrt'); })]) {
      expect(e.schreibe('k', PICKS)).toBe(false);
      expect(e.lies('k', IDS)).toBeNull();
      expect(() => e.loesche('k')).not.toThrow();
    }
  });

  it('Größenlimit: ein zu großer Entwurf wird nicht geschrieben', () => {
    const s = speicher(); const viele = new Map<string, IstWert>();
    for (let i = 0; i < 12_000; i++) viele.set(`K${i}`, { punkt: [367000.123, 5807000.5, 40.25], quelle: 'M'.repeat(150) });
    expect(entwurfJson(viele).length).toBeGreaterThan(ENTWURF_MAX_ZEICHEN);
    expect(kpEntwurfSpeicher(() => s).schreibe('k', viele)).toBe(false);
    expect(s.daten.size).toBe(0);
  });

  it('Abmelden im Portal räumt nur die Kontrollpunkt-Entwürfe weg', async () => {
    const s = speicher();
    s.setItem(`${KP_ENTWURF_PRAEFIX}p1:aa`, '{}'); s.setItem(`${KP_ENTWURF_PRAEFIX}p2:bb`, '{}'); s.setItem('sv-willkommen', '1');
    await entwuerfeLoeschen(undefined, () => s);
    expect([...s.daten.keys()]).toEqual(['sv-willkommen']);
    expect(() => kpEntwuerfeLoeschen(speicher(true))).not.toThrow();
    await expect(entwuerfeLoeschen(undefined, () => { throw new DOMException('gesperrt'); })).resolves.toBeUndefined();
  });
});
