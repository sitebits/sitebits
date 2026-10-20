import { describe, expect, it } from 'vitest';
import { EinEuroFilter, IRIS_DURCHMESSER_M, augeAusLandmarken, relativZurMitte } from '../src/fensterKopf';
import type { Landmarke } from '../src/fensterKopf';

const BILD = { breitePx: 640, hoehePx: 480, bildwinkelDeg: 60 };
const F = 320 / Math.tan(Math.PI / 6);

/** Synthetisches Gesicht: Person bei (x, y, z) Metern vor der Kamera (x rechts aus ihrer Sicht, y oben). */
function gesicht(x: number, y: number, z: number): Landmarke[] {
  const lm: Landmarke[] = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
  const irisPx = (F * IRIS_DURCHMESSER_M) / z;
  const augenAbstandPx = (F * 0.063) / z;
  const mx = 0.5 - (x * F) / z / 640;
  const my = 0.5 - (y * F) / z / 480;
  for (const [mitte, a, b, seite] of [[468, 469, 471, -1], [473, 474, 476, 1]] as const) {
    const cx = mx + (seite * augenAbstandPx) / 2 / 640;
    lm[mitte] = { x: cx, y: my };
    lm[a] = { x: cx + irisPx / 2 / 640, y: my };
    lm[b] = { x: cx - irisPx / 2 / 640, y: my };
  }
  return lm;
}

describe('Fenster-Effekt: Kopfposition (F32)', () => {
  it('Abstand und seitliche Lage aus Iris und Augmitte (±10 %)', () => {
    for (const [x, y, z] of [[0, 0, 0.6], [0.1, -0.05, 0.6], [-0.15, 0.08, 0.45]] as const) {
      const a = augeAusLandmarken(gesicht(x, y, z), BILD)!;
      expect(a.z).toBeCloseTo(z, 6);
      expect(Math.abs(a.x - x)).toBeLessThanOrEqual(0.1 * Math.max(Math.abs(x), 0.01));
      expect(Math.abs(a.y - y)).toBeLessThanOrEqual(0.1 * Math.max(Math.abs(y), 0.01));
    }
  });

  it('ohne Iris-Landmarken keine Position; Mitte wird abgezogen', () => {
    expect(augeAusLandmarken(gesicht(0, 0, 0.6).slice(0, 468), BILD)).toBeNull();
    const r = relativZurMitte({ x: 0.12, y: 0.2, z: 0.6 }, { x: 0.02, y: 0.2 });
    expect(r.x).toBeCloseTo(0.1, 12);
    expect([r.y, r.z]).toEqual([0, 0.6]);
  });

  it('One-Euro-Filter: Zittern gedämpft, Sprung wird schnell erreicht', () => {
    const f = new EinEuroFilter();
    let rest = 0;
    for (let i = 0; i < 60; i++) {
      const y = f.filter((i % 2 ? 1 : -1) * 0.002, i / 30);
      if (i >= 10) rest = Math.max(rest, Math.abs(y)); // erster Wert geht ungefiltert durch
    }
    expect(rest).toBeLessThan(0.001);
    const g = new EinEuroFilter(1, 2);
    let v = 0;
    for (let i = 0; i < 15; i++) v = g.filter(i === 0 ? 0 : 0.2, i / 30);
    expect(v).toBeGreaterThan(0.15);
  });
});
