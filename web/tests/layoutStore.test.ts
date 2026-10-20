import { describe, expect, it } from 'vitest';
import { defaultLayout, setOpen } from '../src/layoutModel';
import type { PanelDef } from '../src/layoutModel';
import { clearLayout, layoutKey, loadLayout, saveLayout } from '../src/layoutStore';
import type { KV } from '../src/layoutStore';

const DEFS: PanelDef[] = [{ id: 'karte', title: 'Minikarte', defaults: { dock: 'right', group: 1, open: true, collapsed: false, float: { x: 0, y: 0, w: 300, h: 200 } } }];
const mem = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) };
};
const throwing: KV = { getItem: () => { throw new Error('SecurityError'); }, setItem: () => { throw new Error('QuotaExceeded'); }, removeItem: () => { throw new Error('x'); } };

describe('layoutStore', () => {
  it('Schlüssel je Projekt', () => expect(layoutKey('abc')).toBe('scanview.layout.abc'));
  it('Rundreise speichern → laden', () => {
    const s = mem();
    const l = setOpen(defaultLayout(DEFS), 'karte', false);
    expect(saveLayout(s, 'p1', l)).toBe(true);
    expect(loadLayout(s, 'p1', DEFS)).toEqual(l);
    expect(loadLayout(s, 'p2', DEFS)).toEqual(defaultLayout(DEFS));
  });
  it('kaputtes JSON → Standard', () => {
    const s = mem();
    s.data.set(layoutKey('p1'), '{kaputt');
    expect(loadLayout(s, 'p1', DEFS)).toEqual(defaultLayout(DEFS));
  });
  it('werfender Speicher und null → Standard, save meldet false, clear wirft nicht', () => {
    expect(loadLayout(throwing, 'p1', DEFS)).toEqual(defaultLayout(DEFS));
    expect(loadLayout(null, 'p1', DEFS)).toEqual(defaultLayout(DEFS));
    expect(saveLayout(throwing, 'p1', defaultLayout(DEFS))).toBe(false);
    expect(() => clearLayout(throwing, 'p1')).not.toThrow();
  });
});
