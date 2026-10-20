// Dekodierte Knoten der Strahlsuche über Klicks hinweg (rayPick.ts): gleichzeitige und kurz aufeinander
// folgende Suchen teilen sich Laden und Dekodieren, statt jede für sich das Zeitbudget zu verbrauchen.
import { describe, expect, it, vi } from 'vitest';
import { knotenCache } from '../src/knotenCache';

const offen = <T>(): { p: Promise<T>; fertig: (v: T) => void; fehler: (e: unknown) => void } => {
  let fertig!: (v: T) => void;
  let fehler!: (e: unknown) => void;
  const p = new Promise<T>((a, b) => ((fertig = a), (fehler = b)));
  return { p, fertig, fehler };
};
const ruhe = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

describe('knotenCache', () => {
  it('gleichzeitige Anfragen desselben Knotens laden genau einmal', async () => {
    const c = knotenCache<number>(100, () => 1);
    const laden = vi.fn(() => Promise.resolve(7));
    const [a, b] = await Promise.all([c.get('n', laden), c.get('n', laden)]);
    expect([a, b]).toEqual([7, 7]);
    expect(laden).toHaveBeenCalledTimes(1);
  });

  it('später: aus dem Cache, ohne neu zu laden', async () => {
    const c = knotenCache<number>(100, () => 1);
    const laden = vi.fn(() => Promise.resolve(7));
    await c.get('n', laden);
    await ruhe();
    expect(await c.get('n', laden)).toBe(7);
    expect(laden).toHaveBeenCalledTimes(1);
  });

  it('Obergrenze in Bytes: der am längsten unbenutzte fertige Knoten fällt zuerst', async () => {
    const c = knotenCache<number>(30, (v) => v);
    await c.get('a', () => Promise.resolve(10));
    await c.get('b', () => Promise.resolve(10));
    await c.get('c', () => Promise.resolve(10));
    await c.get('a', () => Promise.resolve(99)); // a wieder benutzt → b ist jetzt der älteste
    await c.get('d', () => Promise.resolve(10));
    await ruhe();
    expect(c.bytes()).toBeLessThanOrEqual(30);
    const neu = vi.fn(() => Promise.resolve(10));
    await c.get('a', neu);
    await c.get('c', neu);
    await c.get('d', neu);
    expect(neu).not.toHaveBeenCalled();
    await c.get('b', neu);
    expect(neu).toHaveBeenCalledTimes(1);
  });

  it('ein einzelner Knoten über der Obergrenze wird geliefert, aber nicht behalten', async () => {
    const c = knotenCache<number>(5, (v) => v);
    await c.get('klein', () => Promise.resolve(4));
    expect(await c.get('gross', () => Promise.resolve(50))).toBe(50);
    await ruhe();
    expect(c.bytes()).toBe(4); // „klein“ bleibt, der große verdrängt nichts
  });

  it('noch ladende Knoten werden nicht verdrängt', async () => {
    const c = knotenCache<number>(10, (v) => v);
    const langsam = offen<number>();
    const p = c.get('langsam', () => langsam.p);
    await c.get('x', () => Promise.resolve(8));
    await c.get('y', () => Promise.resolve(8)); // verdrängt x, nicht den ladenden Knoten
    const nochmal = vi.fn(() => Promise.resolve(1));
    const q = c.get('langsam', nochmal);
    langsam.fertig(1);
    expect(await p).toBe(1);
    expect(await q).toBe(1);
    expect(nochmal).not.toHaveBeenCalled();
  });

  it('Fehler werden nicht gemerkt: der nächste Aufruf lädt neu', async () => {
    const c = knotenCache<number>(100, () => 1);
    const kaputt = offen<number>();
    const p = c.get('n', () => kaputt.p);
    kaputt.fehler(new Error('Netz weg'));
    await expect(p).rejects.toThrow('Netz weg');
    await ruhe();
    expect(await c.get('n', () => Promise.resolve(3))).toBe(3);
    expect(c.bytes()).toBe(1);
  });
});
