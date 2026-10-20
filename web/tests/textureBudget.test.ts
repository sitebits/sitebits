import { describe, expect, it } from 'vitest';
import { TextureBudget } from '../src/textureBudget';

function mk(limit: number): { b: TextureBudget<string>; released: string[] } {
  const released: string[] = [];
  return { b: new TextureBudget<string>(limit, (v) => released.push(v)), released };
}

describe('TextureBudget', () => {
  it('zählt Bytes', () => {
    const { b } = mk(100);
    expect(b.add('a', 'A', 40)).toBe(true);
    expect(b.usedBytes).toBe(40);
    expect(b.get('a')).toBe('A');
  });
  it('verdrängt den am längsten nicht genutzten, nicht angehefteten Eintrag', () => {
    const { b, released } = mk(100);
    b.add('a', 'A', 40);
    b.add('b', 'B', 40);
    b.get('a');
    b.add('c', 'C', 40);
    expect(released).toEqual(['B']);
    expect(b.keys().sort()).toEqual(['a', 'c']);
    expect(b.usedBytes).toBe(80);
  });
  it('Angeheftetes bleibt; passt nichts mehr, wird der neue Eintrag abgewiesen und freigegeben', () => {
    const { b, released } = mk(100);
    b.add('a', 'A', 60, true);
    expect(b.add('b', 'B', 60)).toBe(false);
    expect(released).toEqual(['B']);
    expect(b.usedBytes).toBe(60);
    b.pin('a', false);
    expect(b.add('b', 'B2', 60)).toBe(true);
    expect(released).toEqual(['B', 'A']);
  });
  it('gleicher Schlüssel ersetzt und gibt den alten Wert frei', () => {
    const { b, released } = mk(100);
    b.add('a', 'A', 10);
    b.add('a', 'A2', 20);
    expect(released).toEqual(['A']);
    expect(b.usedBytes).toBe(20);
  });
  it('retain/clear geben frei', () => {
    const { b, released } = mk(100);
    b.add('S1/px/512', '1', 1);
    b.add('S2/px/512', '2', 1);
    b.retain((k) => k.startsWith('S2/'));
    expect(released).toEqual(['1']);
    b.clear();
    expect(released).toEqual(['1', '2']);
    expect(b.usedBytes).toBe(0);
  });
  it('Machbarkeit zuerst: passt der neue Eintrag neben dem Angehefteten nicht, wird nichts verdrängt', () => {
    const { b, released } = mk(100);
    b.add('p', 'P', 60, true);
    b.add('u', 'U', 30);
    expect(b.add('n', 'N', 50)).toBe(false);
    expect(released).toEqual(['N']);
    expect(b.keys().sort()).toEqual(['p', 'u']);
    expect(b.usedBytes).toBe(90);
  });
  it('Machbarkeit: derselbe Schlüssel zählt nicht doppelt; bei Abweisung bleibt der alte Wert', () => {
    const { b, released } = mk(100);
    b.add('a', 'A', 60, true);
    expect(b.add('a', 'A2', 90, true)).toBe(true); // ersetzt, 90 ≤ 100
    expect(released).toEqual(['A']);
    b.add('q', 'Q', 10, true);
    expect(b.add('a', 'A3', 95, true)).toBe(false); // 10 + 95 > 100
    expect(b.get('a')).toBe('A2');
    expect(b.usedBytes).toBe(100);
  });
  it('fits(): angeheftete Bytes + neu ≤ Limit', () => {
    const { b } = mk(100);
    b.add('p', 'P', 60, true);
    b.add('u', 'U', 30);
    expect(b.fits(40)).toBe(true);
    expect(b.fits(41)).toBe(false);
  });
});
