import { describe, expect, it } from 'vitest';
import { DESKTOP, GRENZEN, istTouch, kachelBudget, TOUCH } from '../src/geraet';
import { mipsVerwerfen } from '../src/meshMath';

describe('Gerätegrenzen', () => {
  it('Touch nur bei pointer: coarse; ohne matchMedia oder bei Fehler Desktop', () => {
    expect(istTouch((q) => ({ matches: q === '(pointer: coarse)' }))).toBe(true);
    expect(istTouch(() => ({ matches: false }))).toBe(false);
    expect(istTouch(undefined)).toBe(false);
    expect(istTouch(() => { throw new Error('kaputt'); })).toBe(false);
    expect(GRENZEN).toEqual(DESKTOP); // Vitest läuft ohne Browser
  });
  it('Touch-Budgets sind kleiner als Desktop', () => {
    expect(TOUCH.punkte).toBeLessThan(DESKTOP.punkte);
    expect(TOUCH.meshBytes).toBeLessThan(DESKTOP.meshBytes);
    expect(TOUCH.panoBytes).toBeLessThan(DESKTOP.panoBytes);
    expect(TOUCH.meshDateiBytes).toBeLessThan(DESKTOP.meshDateiBytes);
    expect(TOUCH.meshMobil && !DESKTOP.meshMobil).toBe(true);
  });
  it('Kachel-Budget: Touch gröber (größerer Bildfehler) und mit kleinerem Speicher als Desktop', () => {
    expect(TOUCH.kachelFehlerPx).toBeGreaterThan(DESKTOP.kachelFehlerPx);
    expect(TOUCH.kachelBytes).toBeLessThan(DESKTOP.kachelBytes);
    // Gleiche Obergrenze wie die gemessene Mesh-Grenze des Geräts.
    expect(DESKTOP.kachelBytes).toBe(DESKTOP.meshBytes); expect(TOUCH.kachelBytes).toBe(TOUCH.meshBytes);
  });
  it('kachelBudget gibt errorTarget und LRU-Grenzen (min = 75 % von max) aus den Gerätegrenzen weiter', () => {
    const d = kachelBudget(DESKTOP); const t = kachelBudget(TOUCH);
    expect(d).toEqual({ errorTarget: 8, maxBytes: 512 * 2 ** 20, minBytes: 384 * 2 ** 20 });
    expect(t).toEqual({ errorTarget: 16, maxBytes: 256 * 2 ** 20, minBytes: 192 * 2 ** 20 });
    expect(kachelBudget()).toEqual(d); // Vitest läuft ohne Browser → Desktop
    expect(t.minBytes).toBeLessThan(t.maxBytes);
  });
});

describe('mipsVerwerfen', () => {
  const textur = (kante: number, stufen: number) => ({
    mipmaps: Array.from({ length: stufen }, (_, i) => ({ width: kante >> i, height: kante >> i })),
    image: { width: kante, height: kante }, needsUpdate: false,
  });
  it('verwirft die schärfste Stufe, image folgt', () => {
    const t = textur(4096, 13);
    mipsVerwerfen(t, 1);
    expect(t.mipmaps).toHaveLength(12);
    expect(t.image).toEqual({ width: 2048, height: 2048 });
    expect(t.needsUpdate).toBe(true);
  });
  it('lässt mindestens eine Stufe stehen, ohne Mipmaps keine Änderung', () => {
    const t = textur(8, 2);
    mipsVerwerfen(t, 5);
    expect(t.mipmaps).toEqual([{ width: 4, height: 4 }]);
    const ohne = textur(8, 1);
    mipsVerwerfen(ohne, 1);
    expect(ohne.image.width).toBe(8);
    expect(ohne.needsUpdate).toBe(false);
  });
});
