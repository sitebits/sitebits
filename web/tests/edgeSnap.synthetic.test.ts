// Abnahme Kantenfang auf synthetischen Daten. Szenen, Seed und Toleranzen stehen vorab
// fest; Abstimmung nur an Seed 7/1234, hier ausschließlich Seed 4711.
import { describe, expect, it } from 'vitest';
import { snapToEdge } from '../src/edgeSnap';
import type { Vec3 } from '../src/areaMath';

const SEED = 4711;
const N = 100;
const K = (2 * Math.tan(Math.PI / 6)) / 1080; // FOV 60°, 1080 px
type Kind = 'flat' | 'edge' | 'corner';
interface Scene { name: string; kind: Kind; angleDeg: number; noiseM: number; spacingM: number; depthM: number; camDir: Vec3; minRate?: number }

function lcg(seed: number): () => number {
  let s = seed;
  return () => (s = (s * 1103515245 + 12345) % 2147483648) / 2147483648;
}
function gaussFrom(rnd: () => number): () => number {
  return () => {
    let u = 0;
    while (u === 0) u = rnd();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd());
  };
}
/** Kante entlang y im Ursprung; Fläche 1 in z = 0 (x ≤ 0), Fläche 2 unter dem Öffnungswinkel; Ecke: y ≤ 0 plus Fläche y = 0. */
function cloud(sc: Scene, gauss: () => number, ext = 0.3): Vec3[] {
  const pts: Vec3[] = [];
  const n = (): number => sc.noiseM * gauss();
  const a = (sc.angleDeg * Math.PI) / 180;
  if (sc.kind === 'flat') {
    for (let x = -ext; x <= ext; x += sc.spacingM) for (let y = -ext; y <= ext; y += sc.spacingM) pts.push([x + n(), y + n(), n()]);
    return pts;
  }
  for (let s = 0; s <= ext; s += sc.spacingM)
    for (let y = -ext; y <= ext; y += sc.spacingM) {
      if (sc.kind === 'corner' && y > 0) continue;
      pts.push([-s + n(), y + n(), n()]);
      pts.push([-Math.cos(a) * s + n(), y + n(), -Math.sin(a) * s + n()]);
    }
  if (sc.kind === 'corner') for (let s = 0; s <= ext; s += sc.spacingM) for (let t = 0; t <= ext; t += sc.spacingM) pts.push([-s + n(), n(), -t + n()]);
  return pts;
}

function run(sc: Scene): { snapped: number; errs: number[]; fromPool: boolean } {
  const rnd = lcg(SEED);
  const pts = cloud(sc, gaussFrom(rnd));
  const len = Math.hypot(...sc.camDir);
  const cam: Vec3 = [(sc.camDir[0] / len) * sc.depthM, (sc.camDir[1] / len) * sc.depthM, (sc.camDir[2] / len) * sc.depthM];
  const errs: number[] = [];
  let snapped = 0;
  let fromPool = true;
  for (let i = 0; i < N; i++) {
    const off = rnd() * 8 * K * sc.depthM;
    const tgt: Vec3 = sc.kind === 'flat' ? [(rnd() - 0.5) * 0.2, (rnd() - 0.5) * 0.2, 0] : sc.kind === 'corner' ? [-off * rnd(), -off * rnd(), 0] : [-off, (rnd() - 0.5) * 0.2, 0];
    const d: Vec3 = [tgt[0] - cam[0], tgt[1] - cam[1], tgt[2] - cam[2]];
    const L = Math.hypot(...d);
    const ray = { origin: cam, dir: [d[0] / L, d[1] / L, d[2] / L] as Vec3 };
    const R = 13 * K * L + 0.04;
    const pool: Vec3[] = [];
    let hit: Vec3 | null = null;
    let best = Infinity;
    for (const p of pts) {
      const v = [p[0] - cam[0], p[1] - cam[1], p[2] - cam[2]];
      const t = v[0]! * ray.dir[0] + v[1]! * ray.dir[1] + v[2]! * ray.dir[2];
      const r2 = v[0]! ** 2 + v[1]! ** 2 + v[2]! ** 2 - t * t;
      if (Math.abs(t - L) < 0.3 && r2 <= R * R) pool.push(p);
      if (Math.abs(t - L) < 0.05 && r2 < best) {
        best = r2;
        hit = p;
      }
    }
    const r = snapToEdge({ ray, hit: hit!, k: K, pool, gapM: Math.max(0.05, 0.02 * L) });
    if (!r.point) continue;
    snapped++;
    if (!pool.includes(r.point)) fromPool = false;
    errs.push(sc.kind === 'corner' ? Math.hypot(...r.point) : Math.hypot(r.point[0], r.point[1] - tgt[1], r.point[2]));
  }
  errs.sort((x, y) => x - y);
  return { snapped, errs, fromPool };
}

const FRONT: Vec3 = [-2.2, 0.3, 3.3];
const SCENES: Scene[] = [
  { name: 'Ebene 4 m', kind: 'flat', angleDeg: 0, noiseM: 0.001, spacingM: 0.002, depthM: 4, camDir: [-1, 0.2, 3] },
  { name: 'Ebene 10 m', kind: 'flat', angleDeg: 0, noiseM: 0.002, spacingM: 0.005, depthM: 10, camDir: [-1, 0.2, 3] },
  { name: '90° 4 m', kind: 'edge', angleDeg: 90, noiseM: 0.001, spacingM: 0.002, depthM: 4, camDir: FRONT, minRate: 0.8 },
  { name: '120° 4 m', kind: 'edge', angleDeg: 120, noiseM: 0.001, spacingM: 0.002, depthM: 4, camDir: FRONT, minRate: 0.8 },
  { name: '90° 6 m', kind: 'edge', angleDeg: 90, noiseM: 0.0015, spacingM: 0.003, depthM: 6, camDir: FRONT, minRate: 0.8 },
  { name: '120° 6 m', kind: 'edge', angleDeg: 120, noiseM: 0.0015, spacingM: 0.003, depthM: 6, camDir: FRONT, minRate: 0.8 },
  { name: '60° 6 m', kind: 'edge', angleDeg: 60, noiseM: 0.0015, spacingM: 0.003, depthM: 6, camDir: FRONT },
  { name: '90° schräg 6 m', kind: 'edge', angleDeg: 90, noiseM: 0.0015, spacingM: 0.003, depthM: 6, camDir: [-3.3, 2.5, 0.8] },
  { name: '90° 10 m', kind: 'edge', angleDeg: 90, noiseM: 0.002, spacingM: 0.005, depthM: 10, camDir: FRONT },
  { name: 'Ecke 4 m', kind: 'corner', angleDeg: 90, noiseM: 0.001, spacingM: 0.002, depthM: 4, camDir: [-2.2, 1, 3.3] },
  { name: '90° 4 m dicht (Ausdünnen)', kind: 'edge', angleDeg: 90, noiseM: 0.001, spacingM: 0.0007, depthM: 4, camDir: FRONT, minRate: 0.8 },
];

// Läuft nur mit SCANVIEW_SNAP_ACCEPT=1 — genau einmal am Ende von Step 2. Sonst würde jedes `npm test` den
// Abnahme-Seed sehen (dann wäre er verbraucht), und ein ehrliches ✗ hielte das Gate für alle späteren Commits rot.
describe.skipIf(process.env.SCANVIEW_SNAP_ACCEPT !== '1')('Kantenfang — synthetische Abnahme (Seed 4711)', () => {
  for (const sc of SCENES) {
    it(sc.name, { timeout: 120_000 }, () => {
      const { snapped, errs, fromPool } = run(sc);
      const p95 = errs[Math.floor(errs.length * 0.95)] ?? 0;
      const max = errs.at(-1) ?? 0;
      console.log(JSON.stringify({ scene: sc.name, snapped, p95Mm: +(p95 * 1000).toFixed(1), maxMm: +(max * 1000).toFixed(1) }));
      expect(fromPool, 'E3: Originalpunkt aus dem Vorrat').toBe(true);
      if (sc.kind === 'flat') expect(snapped, 'E1: Ebene rastet nie ein').toBe(0);
      expect(p95, 'E1: p95 ≤ 10 mm').toBeLessThanOrEqual(0.01);
      expect(max, 'E1: max ≤ 15 mm').toBeLessThanOrEqual(0.015);
      if (sc.minRate) expect(snapped / N, 'E2: Fangquote').toBeGreaterThanOrEqual(sc.minRate);
    });
  }
});
