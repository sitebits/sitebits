import { describe, expect, it } from 'vitest';
import { snapToEdge, sphereTouchesBox, surfaceVariation } from '../src/edgeSnap';
import type { Vec3 } from '../src/areaMath';

const grid = (f: (a: number, b: number) => Vec3, n = 21, step = 0.002): Vec3[] =>
  Array.from({ length: n * n }, (_, i) => f((Math.floor(i / n) - (n - 1) / 2) * step, ((i % n) - (n - 1) / 2) * step));

describe('edgeSnap Einheiten', () => {
  it('surfaceVariation: Ebene ≈ 0, 90°-Kante deutlich > 0,05', () => {
    expect(surfaceVariation(grid((a, b) => [a, b, 0]))).toBeLessThan(1e-12);
    const edge = [...grid((a, b) => [-Math.abs(a), b, 0]), ...grid((a, b) => [0, b, -Math.abs(a)])];
    expect(surfaceVariation(edge)).toBeGreaterThan(0.05);
  });
  it('Zeitbudget: expired → budget, kein Punkt', () => {
    const pts = grid((a, b) => [a, b, 0]);
    const r = snapToEdge({ ray: { origin: [0, 0, 4], dir: [0, 0, -1] }, hit: [0, 0, 0], k: 0.001, pool: pts, gapM: 0.05, expired: () => true });
    expect(r).toEqual({ point: null, why: 'budget' });
  });
  it('unvollständige Nachbarschaft: kein Kandidat → sparse', () => {
    const pts = grid((a, b) => [a, b, 0]);
    const r = snapToEdge({ ray: { origin: [0, 0, 4], dir: [0, 0, -1] }, hit: [0, 0, 0], k: 0.001, pool: pts, gapM: 0.05, neighbourhoodOk: () => false });
    expect(r).toEqual({ point: null, why: 'sparse' });
  });
  it('Ecke (drei Flächen) → not-linear, kein Fang', () => {
    const n = 41;
    const q = (f: (a: number, b: number) => Vec3): Vec3[] => grid((a, b) => f(-Math.abs(a), -Math.abs(b)), n);
    const corner = [...q((a, b) => [a, b, 0]), ...q((a, b) => [a, 0, b]), ...q((a, b) => [0, a, b])];
    const r = snapToEdge({ ray: { origin: [-2.2, -1, 3.3], dir: [0.55, 0.25, -0.8] }, hit: [-0.004, -0.004, 0], k: 0.00107, pool: corner, gapM: 0.05 });
    expect(r.point).toBeNull();
  });
  it('sphereTouchesBox', () => {
    const box = { min: [0, 0, 0] as Vec3, max: [1, 1, 1] as Vec3 };
    expect(sphereTouchesBox([1.05, 0.5, 0.5], 0.06, box)).toBe(true);
    expect(sphereTouchesBox([1.05, 0.5, 0.5], 0.04, box)).toBe(false);
  });
});

describe('edgeSnap Kantengerade (Auswahl eines Originalpunkts)', () => {
  // 90°-Kante entlang y im Ursprung, Flächen z = 0 (x ≤ 0) und x = 0 (z ≤ 0), Raster 2 mm, ohne Rauschen.
  const edge90 = (): Vec3[] => {
    const pts: Vec3[] = [];
    for (let s = 0; s <= 0.06; s += 0.002)
      for (let y = -0.06; y <= 0.06; y += 0.002) {
        pts.push([-s, y, 0]);
        if (s > 0) pts.push([0, y, -s]);
      }
    return pts;
  };
  const K = 0.00107;
  const rayTo = (cam: Vec3, tgt: Vec3): { origin: Vec3; dir: Vec3 } => {
    const d: Vec3 = [tgt[0] - cam[0], tgt[1] - cam[1], tgt[2] - cam[2]];
    const l = Math.hypot(...d);
    return { origin: cam, dir: [d[0] / l, d[1] / l, d[2] / l] };
  };

  it('rastet auf einen Originalpunkt der Kante ein (Referenz aus dem Vorrat, ≤ 2 mm von der Kante)', () => {
    const pool = edge90();
    const hit = pool.find((p) => Math.abs(p[0] + 0.01) < 1e-9 && Math.abs(p[1]) < 1e-9 && p[2] === 0)!;
    const r = snapToEdge({ ray: rayTo([-2.2, 0.3, 3.3], hit), hit, k: K, pool, gapM: 0.05 });
    expect(r.point).not.toBeNull();
    expect(pool.includes(r.point!)).toBe(true);
    expect(Math.hypot(r.point![0], r.point![2])).toBeLessThanOrEqual(0.002);
    expect(Math.abs(r.point![1])).toBeLessThanOrEqual(0.002);
  });

  it('Feintreffer auf der verdeckten Rückseite: Fußpunkt über den Eintritt in die vordere Fläche', () => {
    const pool = edge90();
    // Strahl trifft die Oberseite bei x = −10 mm; als „Treffer“ ein Punkt der Rückseite x = 0 hinter ihr.
    const tgt: Vec3 = [-0.01, 0, 0];
    const ray = rayTo([-3.3, 2.5, 0.8], tgt);
    const back = pool.filter((p) => p[0] === 0 && p[2] < 0).sort((a, b) => Math.hypot(a[1] + 0.008, a[2] + 0.003) - Math.hypot(b[1] + 0.008, b[2] + 0.003))[0]!;
    const r = snapToEdge({ ray, hit: back, k: K, pool, gapM: 0.05 });
    expect(r.point).not.toBeNull();
    expect(Math.abs(r.point![1] - tgt[1])).toBeLessThanOrEqual(0.003);
  });

  it('eine Fläche nicht eben (Rauschen 8 mm) → kein Fang', () => {
    let s = 3;
    const rnd = (): number => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648 - 0.5) * 0.016;
    const pool = edge90().map((p): Vec3 => (p[0] === 0 && p[2] < 0 ? [p[0] + rnd(), p[1], p[2]] : p));
    const hit = pool.find((p) => Math.abs(p[0] + 0.01) < 1e-9 && Math.abs(p[1]) < 1e-9 && p[2] === 0)!;
    const r = snapToEdge({ ray: rayTo([-2.2, 0.3, 3.3], hit), hit, k: K, pool, gapM: 0.05 });
    expect(r.point).toBeNull();
  });

  it('flacher Knick (10°) → kein Fang', () => {
    const a = (170 * Math.PI) / 180;
    const pool: Vec3[] = [];
    for (let s = 0; s <= 0.06; s += 0.002)
      for (let y = -0.06; y <= 0.06; y += 0.002) {
        pool.push([-s, y, 0]);
        if (s > 0) pool.push([-Math.cos(a) * s, y, -Math.sin(a) * s]);
      }
    const hit = pool.find((p) => Math.abs(p[0] + 0.01) < 1e-9 && Math.abs(p[1]) < 1e-9 && p[2] === 0)!;
    const r = snapToEdge({ ray: rayTo([-2.2, 0.3, 3.3], hit), hit, k: K, pool, gapM: 0.05 });
    expect(r.point).toBeNull();
  });
});
