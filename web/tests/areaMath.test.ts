import { describe, expect, it } from 'vitest';
import { PLANARITY_WARN_M, fitPlane, polygonArea, selfIntersects } from '../src/areaMath';
import type { Vec3 } from '../src/areaMath';

// „Flächenfehler Testpolygone ≤ 0,01 %“.
const MAX_REL = 1e-4;
const shift = (pts: Vec3[], o: Vec3): Vec3[] => pts.map((p) => [p[0] + o[0], p[1] + o[1], p[2] + o[2]]);
const rotX = (p: Vec3, a: number): Vec3 => [p[0], p[1] * Math.cos(a) - p[2] * Math.sin(a), p[1] * Math.sin(a) + p[2] * Math.cos(a)];
const UTM: Vec3 = [500000, 5700000, 35];
const KIRCHE: Vec3 = [1485, 7112, 22];

function area(pts: Vec3[]): { areaM2: number; perimeterM: number; nonPlanar: boolean; dev: number } {
  const r = polygonArea(pts);
  if (!r.ok) throw new Error(`abgelehnt: ${r.reason}`);
  return { areaM2: r.areaM2, perimeterM: r.perimeterM, nonPlanar: r.nonPlanar, dev: r.plane.maxDeviationM };
}
const rel = (x: number, soll: number): number => Math.abs(x - soll) / soll;

describe('polygonArea — Testpolygone (≤ 0,01 %)', () => {
  it('Rechteck 4 × 2,5 m waagerecht bei UTM-Koordinaten', () => {
    const r = area(shift([[0, 0, 0], [4, 0, 0], [4, 2.5, 0], [0, 2.5, 0]], UTM));
    expect(rel(r.areaM2, 10)).toBeLessThanOrEqual(MAX_REL);
    expect(r.perimeterM).toBeCloseTo(13, 6);
    expect(r.nonPlanar).toBe(false);
  });
  it('Rechteck 3 × 2 m, Ebene um 30° gekippt, bei UTM-Koordinaten', () => {
    const r = area(shift([[0, 0, 0], [3, 0, 0], [3, 2, 0], [0, 2, 0]].map((p) => rotX(p as unknown as Vec3, Math.PI / 6)), UTM));
    expect(rel(r.areaM2, 6)).toBeLessThanOrEqual(MAX_REL);
    expect(r.perimeterM).toBeCloseTo(10, 6);
  });
  it('senkrechte Wand, L-förmig (konkav)', () => {
    const r = area(shift([[0, 0, 0], [4, 0, 0], [4, 0, 1], [1, 0, 1], [1, 0, 3], [0, 0, 3]], KIRCHE));
    expect(rel(r.areaM2, 6)).toBeLessThanOrEqual(MAX_REL);
    expect(r.perimeterM).toBeCloseTo(14, 9);
  });
  it('regelmäßiges 12-Eck r = 5 m in schräger Ebene (Normale (1,2,3)/√14)', () => {
    const n: Vec3 = [1 / Math.sqrt(14), 2 / Math.sqrt(14), 3 / Math.sqrt(14)];
    const a: Vec3 = [2 / Math.sqrt(5), -1 / Math.sqrt(5), 0];
    const b: Vec3 = [n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0]];
    const gon: Vec3[] = Array.from({ length: 12 }, (_, i) => {
      const t = (2 * Math.PI * i) / 12;
      return [0, 1, 2].map((k) => 5 * Math.cos(t) * a[k]! + 5 * Math.sin(t) * b[k]!) as unknown as Vec3;
    });
    const r = area(shift(gon, UTM));
    expect(rel(r.areaM2, 75)).toBeLessThanOrEqual(MAX_REL); // (n/2)·r²·sin(2π/n) = 6·25·0,5
    expect(r.perimeterM).toBeCloseTo(120 * Math.sin(Math.PI / 12), 6); // 31,0583 m
  });
  it('Dreieck 3-4-5 am großen Ursprung (Kirche); Umlaufsinn egal', () => {
    expect(rel(area(shift([[0, 0, 0], [3, 0, 0], [0, 4, 0]], KIRCHE)).areaM2, 6)).toBeLessThanOrEqual(MAX_REL);
    const cw = area(shift([[0, 0, 0], [0, 2.5, 0], [4, 2.5, 0], [4, 0, 0]], UTM));
    expect(rel(cw.areaM2, 10)).toBeLessThanOrEqual(MAX_REL);
  });
});

describe('polygonArea — Ebenheit (PLANARITY_WARN_M = 2 cm)', () => {
  it('eine Ecke 10 cm angehoben: Abweichung 2,5 cm → Warnung', () => {
    const r = area([[0, 0, 0], [2, 0, 0], [2, 2, 0.1], [0, 2, 0]]);
    expect(r.dev).toBeCloseTo(0.025, 3);
    expect(r.dev).toBeGreaterThan(PLANARITY_WARN_M);
    expect(r.nonPlanar).toBe(true);
  });
  it('eine Ecke 4 cm angehoben: Abweichung 1 cm → keine Warnung', () => {
    const r = area([[0, 0, 0], [2, 0, 0], [2, 2, 0.04], [0, 2, 0]]);
    expect(r.dev).toBeCloseTo(0.01, 3);
    expect(r.nonPlanar).toBe(false);
  });
});

describe('polygonArea — Ablehnungen', () => {
  it('Selbstschnitt (Schleife) und Berührung einer nicht benachbarten Kante', () => {
    expect(polygonArea([[0, 0, 0], [2, 2, 0], [2, 0, 0], [0, 2, 0]])).toEqual({ ok: false, reason: 'self-intersecting' });
    expect(polygonArea([[0, 0, 0], [4, 0, 0], [4, 4, 0], [2, 0, 0], [0, 4, 0]])).toEqual({ ok: false, reason: 'self-intersecting' });
  });
  it('zu wenige, doppelte, kollineare Eckpunkte', () => {
    expect(polygonArea([[0, 0, 0], [1, 1, 1]])).toEqual({ ok: false, reason: 'too-few' });
    expect(polygonArea([[0, 0, 0], [0, 0, 0.00001], [2, 0, 0], [2, 2, 0]])).toEqual({ ok: false, reason: 'duplicate' });
    expect(polygonArea([[0, 0, 0], [1, 1, 1], [2, 2, 2]])).toEqual({ ok: false, reason: 'degenerate' });
  });
  it('selfIntersects in 2D: Quadrat nein, Schleife ja', () => {
    expect(selfIntersects([[0, 0], [1, 0], [1, 1], [0, 1]])).toBe(false);
    expect(selfIntersects([[0, 0], [1, 1], [1, 0], [0, 1]])).toBe(true);
  });
  it('fitPlane: Normale einer gekippten Ebene', () => {
    const f = fitPlane([[0, 0, 0], [1, 0, 0], [1, 1, 1], [0, 1, 1]]);
    expect(Math.abs(f.normal[0])).toBeCloseTo(0, 9);
    expect(Math.abs(f.normal[1])).toBeCloseTo(Math.SQRT1_2, 9);
    expect(Math.abs(f.normal[2])).toBeCloseTo(Math.SQRT1_2, 9);
  });
});
