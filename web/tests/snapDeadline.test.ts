import { describe, expect, it } from 'vitest';
import { snapToEdge } from '../src/edgeSnap';
import { snapWithinDeadline } from '../src/snapDeadline';
import type { Vec3 } from '../src/areaMath';

// 90°-Kante entlang y (wie edgeSnap.test), ein Klick 10 mm neben der Kante — rastet ohne Frist ein.
const pool: Vec3[] = [];
for (let s = 0; s <= 0.06; s += 0.002)
  for (let y = -0.06; y <= 0.06; y += 0.002) {
    pool.push([-s, y, 0]);
    if (s > 0) pool.push([0, y, -s]);
  }
const hit = pool.find((p) => Math.abs(p[0] + 0.01) < 1e-9 && Math.abs(p[1]) < 1e-9 && p[2] === 0)!;
const cam: Vec3 = [-2.2, 0.3, 3.3];
const d: Vec3 = [hit[0] - cam[0], hit[1] - cam[1], hit[2] - cam[2]];
const l = Math.hypot(...d);
const input = { ray: { origin: cam, dir: [d[0] / l, d[1] / l, d[2] / l] as Vec3 }, hit, k: 0.00107, pool, gapM: 0.05 };

/** Fake-Uhr: jeder Aufruf rückt 1 ms vor. */
const fakeClock = (): { now: () => number; calls: () => number } => {
  let t = 0;
  return { now: () => ++t, calls: () => t };
};

describe('snapWithinDeadline (Frist nach snapToEdge)', () => {
  it('ohne Fristende: Fang wie snapToEdge', () => {
    const r = snapWithinDeadline(input, fakeClock().now, Infinity);
    expect(r.point).not.toBeNull();
  });
  it('Frist läuft erst nach der letzten Prüfung in snapToEdge ab → verspäteter Fang wird budget', () => {
    // Probelauf direkt an snapToEdge: wie oft fragt der Fang selbst nach der Frist (m Abfragen)?
    let m = 0;
    expect(snapToEdge({ ...input, expired: () => (m++, false) }).point).not.toBeNull();
    // Frist nach der letzten Prüfung in snapToEdge (Uhr m) und vor der Nachprüfung (Uhr m + 1).
    const r = snapWithinDeadline(input, fakeClock().now, m + 0.5);
    expect(r).toEqual({ point: null, why: 'budget' });
  });
  it('Frist schon vorher abgelaufen → budget aus snapToEdge selbst', () => {
    expect(snapWithinDeadline(input, fakeClock().now, 0)).toEqual({ point: null, why: 'budget' });
  });
});
