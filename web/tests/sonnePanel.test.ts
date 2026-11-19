import { afterEach, expect, it, vi } from 'vitest';
import type { Sonne } from '../src/sonne';
import { attachSonnePanel } from '../src/sonnePanel';
// Minimaler DOM-Adapter für Node (Vitest läuft ohne jsdom): geprüft wird der vom Panel aufgebaute Baum.
class Knoten {
  children: (Knoten | string)[] = [];
  attrs = new Map<string, string>();
  className = '';
  constructor(readonly tag: string) { }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  append(...xs: (Knoten | string)[]): void { this.children.push(...xs); }
  replaceChildren(...xs: (Knoten | string)[]): void { this.children = xs; }
  set textContent(s: string) { this.children = [s]; }
  get textContent(): string { return this.children.map((c) => typeof c === 'string' ? c : c.textContent).join(''); }
  addEventListener(): void { /* Bedienung prüft die e2e-Spezifikation */ }
  find(tag: string): Knoten[] { return [...(this.tag === tag ? [this] : []), ...this.children.flatMap((c) => typeof c === 'string' ? [] : c.find(tag))]; }
}
afterEach(() => vi.unstubAllGlobals());

const sonne = (mitOrt: boolean): Sonne => ({
  ort: mitOrt ? { breiteGrad: 50, laengeGrad: 10, konvergenzGrad: 0 } : null,
  an: false, bahn: true, spielt: false,
  zeit: new Date('2026-06-21T11:00:00Z'),
  stand: mitOrt ? { azimutGrad: 180, hoeheGrad: 60, hoeheScheinbarGrad: 60 } : null,
  aufUnter: null,
  setzen: () => undefined, zustand: () => { throw new Error('nicht gebraucht'); }, onChange: () => undefined, dispose: () => undefined,
}) as unknown as Sonne;

function panel(mitOrt: boolean): Knoten {
  vi.stubGlobal('document', { createElement: (tag: string) => new Knoten(tag), activeElement: null });
  const root = new Knoten('div');
  attachSonnePanel(root as unknown as HTMLElement, sonne(mitOrt));
  return root;
}

it.each([true, false])('zeigt den Experiment-Hinweis sichtbar als ersten Absatz (georeferenziert: %s)', (mitOrt) => {
  const p = panel(mitOrt).find('p')[0]!;
  expect(p.textContent).toBe('Experiment – Ergebnisse ohne Gewähr. Haftung nur im gesetzlich vorgeschriebenen Umfang.');
  expect(p.className).toBe('sv-sonne-experiment');
  // sichtbar im Panel, nicht nur als Tooltip und nicht vor Screenreadern versteckt
  expect(p.attrs.has('title')).toBe(false);
  expect(p.attrs.get('aria-hidden')).toBeUndefined();
});

it('ohne Georeferenz folgt der Nicht-verfügbar-Hinweis als einzige Notiz', () => {
  const ps = panel(false).find('p');
  expect(ps.map((p) => p.attrs.get('role'))).toEqual([undefined, 'note']);
  expect(ps[1]!.textContent).toContain('Sonne nicht verfügbar');
});
