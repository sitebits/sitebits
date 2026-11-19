import { afterEach, describe, expect, it, vi } from 'vitest';
import { alleLoeschenErlaubt, alleLoeschenFrage, attachMeasureList } from '../src/measureList';
import type { MeasureTool } from '../src/measure';
import type { MeasureStore } from '../src/measureStore';

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

// Minimaler DOM-Adapter für Node (wie fadenAnsicht.test.ts): geprüft wird der vom Produktionscode aufgebaute Baum.
class Knoten {
  children: (Knoten | string)[] = [];
  hidden = false;
  className = '';
  type = '';
  title = '';
  constructor(readonly tag: string) { }
  setAttribute(): void { }
  addEventListener(): void { }
  append(...xs: (Knoten | string)[]): void { this.children.push(...xs); }
  replaceChildren(...xs: (Knoten | string)[]): void { this.children = xs; }
  set textContent(s: string) { this.children = [s]; }
  get textContent(): string { return this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  find(tag: string): Knoten[] { return [...(this.tag === tag ? [this] : []), ...this.children.flatMap(c => typeof c === 'string' ? [] : c.find(tag))]; }
}

describe('Panel „Messungen“', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('ohne eigene Überschrift: der Rahmen trägt den Titel, im Inhalt kein strong-Knoten', () => {
    vi.stubGlobal('document', { createElement: (tag: string) => new Knoten(tag) });
    const root = new Knoten('div');
    attachMeasureList({
      root: root as unknown as HTMLElement,
      store: { list: () => [] } as unknown as MeasureStore,
      tool: { selection: () => null } as unknown as MeasureTool,
      manifest: { projectId: 'p', revision: 1 },
      onChange: () => undefined,
      onToggle: () => undefined,
      notify: () => undefined,
    });
    expect(root.find('strong')).toEqual([]);
    expect(root.find('p').map((p) => p.textContent)).toEqual(['Noch keine Messungen']); // Inhalt ist aufgebaut
    expect(root.children.some((c) => typeof c !== 'string' && c.textContent === 'Messungen')).toBe(false);
  });
});
