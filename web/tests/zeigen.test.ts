import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sichereAusgeblendete, ZEIGEN_BLEIBT, zeigenAn } from '../src/zeigen';

describe('zeigenAn', () => {
  it('nur genau zeigen=1 schaltet den Show-Modus ein', () => {
    expect(zeigenAn('?p=/data/x/&gast=1&zeigen=1')).toBe(true);
    expect(zeigenAn('?zeigen=1')).toBe(true);
    for (const s of ['', '?zeigen', '?zeigen=0', '?zeigen=true', '?zeigen=11', '?x=zeigen=1']) expect(zeigenAn(s), s).toBe(false);
  });
});

// Testdouble ohne DOM (Vitest läuft in node): `matches` kennt nur die Selektoren der Liste.
class Kind {
  inert = false;
  parentNode: unknown = null;
  constructor(readonly sel: string) {}
  matches(liste: string): boolean { return liste.split(',').map((s) => s.trim()).includes(this.sel); }
}
class Beobachter {
  static letzter: Beobachter | null = null;
  ziel: unknown = null;
  constructor(readonly melde: (liste: { addedNodes: Kind[] }[]) => void) { Beobachter.letzter = this; }
  observe(ziel: unknown): void { this.ziel = ziel; }
  disconnect(): void { this.ziel = null; }
}

describe('schlanke Profile: ausgeblendete Kinder sind inert', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('dieselbe Liste wie im :not(…) von zeigen.css', () => {
    const css = readFileSync(new URL('../src/styles/zeigen.css', import.meta.url), 'utf8');
    const liste = /body\.sv-zeigen > :not\(([^)]*)\)/.exec(css)?.[1];
    expect(liste).toBe(ZEIGEN_BLEIBT);
  });

  it('vorhandene und später angehängte Kinder, außer der Bleibe-Liste', () => {
    vi.stubGlobal('HTMLElement', Kind);
    vi.stubGlobal('Element', Kind);
    const body = { children: [new Kind('#view'), new Kind('#toolbar'), new Kind('dialog'), new Kind('.sv-zeigen-leiste')] };
    for (const k of body.children) k.parentNode = body;
    sichereAusgeblendete(body as unknown as HTMLElement, Beobachter as unknown as typeof MutationObserver);
    expect(body.children.map((k) => k.inert)).toEqual([false, true, false, false]);
    expect(Beobachter.letzter?.ziel).toBe(body);
    const spaet = [new Kind('.sv-hinweis'), new Kind('.sv-rueckfrage')];
    for (const k of spaet) k.parentNode = body;
    // Ein Enkel (anderer parentNode) bleibt unberührt: nur direkte Kinder des body blendet zeigen.css aus.
    const enkel = new Kind('.sv-panel');
    enkel.parentNode = spaet[0];
    Beobachter.letzter!.melde([{ addedNodes: [...spaet, enkel] }]);
    expect([...spaet, enkel].map((k) => k.inert)).toEqual([true, false, false]);
  });
});
