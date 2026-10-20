import { describe, expect, it } from 'vitest';
import { DESKTOP, GRENZEN, istTouch, TOUCH } from '../src/geraet';
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
