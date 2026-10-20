// Kantenfang. Aus den schon dekodierten Originalpunkten um den Klickstrahl den Punkt
// auf einer Kante wählen. Nie geraten: das Ergebnis ist immer ein Element von ``pool`` (Referenz), nie ein
// berechneter Schnittpunkt; Geometrie wählt nur aus. Kein Fang ist immer erlaubt. Reine Logik (Vitest).
// Ablauf:
//   1. Kantenmaß: Flächenvariation σ = λ3/(λ1+λ2+λ3) der Nachbarschaft je Kandidat (areaMath.pca).
//      Kein Kandidat mit σ ≥ SNAP_EDGE_SIGMA → 'flat'. Kantenpunkte nicht linienförmig (Ecke, Kreuzung,
//      Rauschen) → 'not-linear' (Ecken werden bewusst nie gefangen).
//   2. Kantengerade nur zum Auswählen: die ebenen Kandidaten (σ < SNAP_FACE_SIGMA) nach ihrer Normalen in
//      zwei Flächen teilen, je eine Ebene anpassen; Gerade = Schnitt der Ebenen (edgeLine).
//   3. Fußpunkt: der Strahl tritt in die vordere der beiden Flächen ein; dieser Eintritt, auf die Gerade
//      projiziert (footOnEdge). Nicht der Feintreffer (kann bei nicht sichtabhängiger Wolke auf der
//      verdeckten Rückseite liegen, dann wandert seine Projektion bei streifendem Blick die Kante entlang)
//      und nicht der strahlnächste Kantenpunkt (der Strahl kann fast parallel zur Kante liegen).
//   4. Ergebnis: der Originalpunkt des Vorrats, der dem Fußpunkt am nächsten liegt, höchstens
//      SNAP_MAX_OFF_LINE_M entfernt — sonst 'sparse'.
// Verweigert ('ambiguous'), wenn eine Fläche zu wenige ebene Kandidaten hat, die Flächen weniger als
// SNAP_MIN_BEND_DEG geknickt sind, eine Fläche nicht eben ist (RMS > SNAP_PLANE_RMS_M), der Strahl keine
// der beiden Flächen trifft oder der Fußpunkt außerhalb von Fangkreis/Tiefenband liegt.
// Entwicklung: der Prototyp (σ-Maximum in einer Scheibe um die Projektion des Feintreffers
// auf die Hauptachse der Kantenpunkte) lag im Entwicklungssatz bei p95 15,1 mm (60°), 21,8 mm (schräg),
// 10,7 mm (10 m). Ursachen per Zerlegung quer/längs der Kante: quer das σ-Maximum neben der Kante (60°,
// 10 m), längs der Feintreffer auf der verdeckten Rückseite (schräg: Fehler ≈ 0,76 · Klickversatz). Schritte
// 2–4 ersetzen deshalb die Scheibe.
import { centroidOf, fitPlane, pca } from './areaMath';
import type { Vec3 } from './areaMath';
import { depthAlong, rayDistance } from './pickMath';
import type { Box, Ray } from './pickMath';

export const SNAP_RADIUS_PX = 10;
export const SNAP_NEIGHBOUR_PX = 3;
export const SNAP_NEIGHBOUR_MIN_M = 0.015;
export const SNAP_MIN_NEIGHBOURS = 20;
export const SNAP_EDGE_SIGMA = 0.05;
export const SNAP_LINEAR_MIN = 0.9;
export const SNAP_MAX_CANDIDATES = 1500;
export const SNAP_MAX_POOL = 30_000;
/** Ebene Kandidaten (Flächen beiderseits der Kante) für die Kantengerade: σ darunter. */
export const SNAP_FACE_SIGMA = 0.025;
/** Mindestens so viele ebene Kandidaten je Fläche. */
export const SNAP_MIN_FACE_POINTS = 20;
/** Die beiden Flächen müssen mindestens so stark gegeneinander geknickt sein [°]. */
export const SNAP_MIN_BEND_DEG = 20;
/** Jede Fläche muss so eben sein (RMS der Abstände zur angepassten Ebene) [m]. */
export const SNAP_PLANE_RMS_M = 0.005;
/** Der gewählte Originalpunkt liegt höchstens so weit vom Fußpunkt auf der Kantengeraden [m]. */
export const SNAP_MAX_OFF_LINE_M = 0.006;

export interface SnapInput {
  ray: Ray;
  /** Feintreffer (Originalpunkt). */
  hit: Vec3;
  /** Pixel-Fußabdruck je Meter Tiefe. */
  k: number;
  /** Dekodierte, Schnittbox-gefilterte Originalpunkte um den Strahl. */
  pool: readonly Vec3[];
  /** Tiefenband um den Treffer für Kandidaten [m] (surfaceGapM). */
  gapM: number;
  /** false → Nachbarschaftskugel nicht vollständig dekodiert, Kandidat zählt nicht. */
  neighbourhoodOk?: (p: Vec3, radiusM: number) => boolean;
  /** true → Zeitbudget erschöpft. */
  expired?: () => boolean;
}
export type SnapResult =
  | { point: Vec3; kind: 'edge'; sigma: number }
  | { point: null; why: 'flat' | 'sparse' | 'not-linear' | 'ambiguous' | 'budget' };

const thin = <T>(a: readonly T[], max: number): readonly T[] =>
  a.length <= max ? a : Array.from({ length: max }, (_, i) => a[Math.floor((i * a.length) / max)]!);

export function surfaceVariation(neigh: readonly Vec3[]): number {
  const { values } = pca(neigh, centroidOf(neigh));
  const s = values[0] + values[1] + values[2];
  return s > 0 ? values[0] / s : 0;
}

export function sphereTouchesBox(c: Vec3, r: number, b: Box): boolean {
  let d2 = 0;
  for (let i = 0; i < 3; i++) {
    const v = Math.max(b.min[i]! - c[i]!, 0, c[i]! - b.max[i]!);
    d2 += v * v;
  }
  return d2 <= r * r;
}

export function snapToEdge(s: SnapInput): SnapResult {
  const d = depthAlong(s.ray, s.hit);
  const snapR = SNAP_RADIUS_PX * s.k * d;
  const nbR = Math.max(SNAP_NEIGHBOUR_MIN_M, SNAP_NEIGHBOUR_PX * s.k * d);
  const pool = thin(s.pool, SNAP_MAX_POOL);
  const cands = thin(
    pool.filter((p) => rayDistance(s.ray, p) <= snapR && Math.abs(depthAlong(s.ray, p) - d) <= s.gapM),
    SNAP_MAX_CANDIDATES,
  );
  // Nachbarsuche über ein Gitter mit Zellgröße nbR (3×3×3 Zellen je Anfrage).
  const cell = (v: number): number => Math.floor(v / nbR);
  const grid = new Map<string, Vec3[]>();
  for (const p of pool) {
    const key = `${cell(p[0])},${cell(p[1])},${cell(p[2])}`;
    const list = grid.get(key);
    if (list) list.push(p);
    else grid.set(key, [p]);
  }
  const nb2 = nbR * nbR;
  const scored: Scored[] = [];
  for (const c of cands) {
    if (s.expired?.()) return { point: null, why: 'budget' };
    if (s.neighbourhoodOk && !s.neighbourhoodOk(c, nbR)) continue;
    const cx = cell(c[0]);
    const cy = cell(c[1]);
    const cz = cell(c[2]);
    const nb: Vec3[] = [];
    for (let i = -1; i <= 1; i++)
      for (let j = -1; j <= 1; j++)
        for (let l = -1; l <= 1; l++)
          for (const p of grid.get(`${cx + i},${cy + j},${cz + l}`) ?? []) {
            const dx = p[0] - c[0];
            const dy = p[1] - c[1];
            const dz = p[2] - c[2];
            if (dx * dx + dy * dy + dz * dz <= nb2) nb.push(p);
          }
    if (nb.length >= SNAP_MIN_NEIGHBOURS) scored.push({ p: c, ...variation(nb) });
  }
  if (scored.length === 0) return { point: null, why: 'sparse' };
  const band = scored.filter((x) => x.sigma >= SNAP_EDGE_SIGMA);
  if (band.length === 0) return { point: null, why: 'flat' };
  if (band.length < 3) return { point: null, why: 'not-linear' };
  const pts = band.map((x) => x.p);
  const bc = centroidOf(pts);
  const { values } = pca(pts, bc);
  if (values[2] < SNAP_LINEAR_MIN * (values[0] + values[1] + values[2])) return { point: null, why: 'not-linear' };
  if (s.expired?.()) return { point: null, why: 'budget' };
  const line = edgeLine(scored, s.hit);
  if (!line) return { point: null, why: 'ambiguous' };
  const foot = footOnEdge(line, s.ray, s.hit);
  // Fußpunkt im Fangkreis und im Tiefenband, sonst ist es nicht die Kante unter dem Klick.
  if (!foot || rayDistance(s.ray, foot) > snapR || Math.abs(depthAlong(s.ray, foot) - d) > s.gapM) return { point: null, why: 'ambiguous' };
  // Ergebnis: der Originalpunkt des Vorrats, der dem Fußpunkt am nächsten liegt — die Gerade wählt aus,
  // sie wird nie Ergebnis.
  let best: Vec3 | null = null;
  let bestD2 = SNAP_MAX_OFF_LINE_M * SNAP_MAX_OFF_LINE_M;
  for (const p of pool) {
    const dx = p[0] - foot[0];
    const dy = p[1] - foot[1];
    const dz = p[2] - foot[2];
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 <= bestD2) {
      bestD2 = d2;
      best = p;
    }
  }
  if (!best) return { point: null, why: 'sparse' };
  let sigma = 0;
  for (const x of band) sigma = Math.max(sigma, x.sigma);
  return { point: best, kind: 'edge', sigma };
}

interface Scored {
  p: Vec3;
  sigma: number;
  /** Normale der Nachbarschaft (Achse zum kleinsten Eigenwert). */
  normal: Vec3;
}

function variation(neigh: readonly Vec3[]): { sigma: number; normal: Vec3 } {
  const { values, axes } = pca(neigh, centroidOf(neigh));
  const s = values[0] + values[1] + values[2];
  return { sigma: s > 0 ? values[0] / s : 0, normal: axes[0] };
}

const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** Hauptrichtung einer Normalenmenge ohne Vorzeichen (größter Eigenvektor von Σ n·nᵀ). */
const meanAxis = (ns: readonly Vec3[]): Vec3 => pca(ns, [0, 0, 0]).axes[2];

/**
 * Kantengerade aus den zwei Flächen beiderseits der Kante — nur zum Auswählen. Ebene Kandidaten
 * (σ < SNAP_FACE_SIGMA) werden nach ihrer Normalen in zwei Flächen geteilt (Startwerte: Normale des
 * ebenen Kandidaten am Feintreffer und die dazu am stärksten abweichende; zwei Umläufe), je Fläche eine
 * Ebene angepasst (areaMath.fitPlane). null (kein Fang), wenn eine Fläche zu wenige Punkte hat, die Flächen
 * weniger als SNAP_MIN_BEND_DEG gegeneinander geknickt sind oder eine Ebene nicht eben ist
 * (Abweichung RMS > SNAP_PLANE_RMS_M). Gerechnet relativ zum Feintreffer (UTM-Stellen).
 */
function edgeLine(scored: readonly Scored[], hit: Vec3): EdgeLine | null {
  const flat = scored.filter((x) => x.sigma < SNAP_FACE_SIGMA);
  if (flat.length < 2 * SNAP_MIN_FACE_POINTS) return null;
  let seed = flat[0]!;
  let seedD = Infinity;
  for (const x of flat) {
    const dd = (x.p[0] - hit[0]) ** 2 + (x.p[1] - hit[1]) ** 2 + (x.p[2] - hit[2]) ** 2;
    if (dd < seedD) {
      seedD = dd;
      seed = x;
    }
  }
  let na = seed.normal;
  let nb = flat[0]!.normal;
  let minDot = Infinity;
  for (const x of flat) {
    const c = Math.abs(dot(x.normal, na));
    if (c < minDot) {
      minDot = c;
      nb = x.normal;
    }
  }
  const cosBend = Math.cos((SNAP_MIN_BEND_DEG * Math.PI) / 180);
  if (minDot > cosBend) return null;
  let A: Scored[] = [];
  let B: Scored[] = [];
  for (let iter = 0; iter < 2; iter++) {
    A = [];
    B = [];
    for (const x of flat) (Math.abs(dot(x.normal, na)) >= Math.abs(dot(x.normal, nb)) ? A : B).push(x);
    if (A.length < SNAP_MIN_FACE_POINTS || B.length < SNAP_MIN_FACE_POINTS) return null;
    na = meanAxis(A.map((x) => x.normal));
    nb = meanAxis(B.map((x) => x.normal));
  }
  const rel = (p: Vec3): Vec3 => [p[0] - hit[0], p[1] - hit[1], p[2] - hit[2]];
  const planes = [A, B].map((F) => {
    const local = F.map((x) => rel(x.p));
    const f = fitPlane(local);
    let ss = 0;
    for (const q of local) ss += dot([q[0] - f.centroid[0], q[1] - f.centroid[1], q[2] - f.centroid[2]], f.normal) ** 2;
    return { n: f.normal, d: dot(f.normal, f.centroid), c: f.centroid, rms: Math.sqrt(ss / local.length) };
  });
  const [p1, p2] = planes as [(typeof planes)[0], (typeof planes)[0]];
  if (p1.rms > SNAP_PLANE_RMS_M || p2.rms > SNAP_PLANE_RMS_M) return null;
  if (Math.abs(dot(p1.n, p2.n)) > cosBend) return null;
  const e0 = cross(p1.n, p2.n);
  const el = Math.hypot(e0[0], e0[1], e0[2]);
  const e: Vec3 = [e0[0] / el, e0[1] / el, e0[2] / el];
  // Schnitt der Ebenen n1·x = d1, n2·x = d2 mit der Ebene e·x = 0 durch den Feintreffer (lokal 0):
  // x = (d1 (n2 × e) + d2 (e × n1)) / (n1 · (n2 × e)).
  const n2e = cross(p2.n, e);
  const en1 = cross(e, p1.n);
  const det = dot(p1.n, n2e);
  const o: Vec3 = [(p1.d * n2e[0] + p2.d * en1[0]) / det, (p1.d * n2e[1] + p2.d * en1[1]) / det, (p1.d * n2e[2] + p2.d * en1[2]) / det];
  // Je Fläche die Richtung in ihrer Ebene von der Kante weg zu ihren Punkten (Halbebene der Fläche).
  const faces = [p1, p2].map((pl) => {
    const v: Vec3 = [pl.c[0] - o[0], pl.c[1] - o[1], pl.c[2] - o[2]];
    const w = cross(e, pl.n);
    const u: Vec3 = dot(w, v) >= 0 ? w : [-w[0], -w[1], -w[2]];
    return { n: pl.n, d: pl.d, u };
  });
  return { o, e, faces };
}

interface EdgeLine {
  /** Punkt der Kantengeraden, relativ zum Feintreffer. */
  o: Vec3;
  e: Vec3;
  /** Ebenen (n·x = d, relativ zum Feintreffer) und je die Richtung in der Ebene zur Fläche. */
  faces: { n: Vec3; d: number; u: Vec3 }[];
}

/**
 * Fußpunkt auf der Kantengeraden (Welt) — nur zum Auswählen. Nicht über den Feintreffer: ist die Wolke
 * nicht sichtabhängig (Orbit, alle Scans), kann er auf der verdeckten Rückseite liegen, und seine Projektion
 * wanderte bei streifendem Blick die Kante entlang. Stattdessen der Eintritt des Strahls in die zwei
 * Flächen: je Ebene der Schnitt mit dem Strahl, gültig nur auf der Halbebene, auf der die Fläche liegt;
 * davon der vorderste. Dessen Projektion auf die Kante ist der Fußpunkt. null, wenn der Strahl keine der
 * beiden Flächen trifft.
 */
function footOnEdge(line: EdgeLine, ray: Ray, hit: Vec3): Vec3 | null {
  const ro: Vec3 = [ray.origin[0] - hit[0], ray.origin[1] - hit[1], ray.origin[2] - hit[2]];
  let bestT = Infinity;
  let entry: Vec3 | null = null;
  for (const f of line.faces) {
    const den = dot(f.n, ray.dir);
    if (Math.abs(den) < 1e-9) continue;
    const t = (f.d - dot(f.n, ro)) / den;
    if (t <= 0 || t >= bestT) continue;
    const x: Vec3 = [ro[0] + ray.dir[0] * t, ro[1] + ray.dir[1] * t, ro[2] + ray.dir[2] * t];
    if (dot([x[0] - line.o[0], x[1] - line.o[1], x[2] - line.o[2]], f.u) < 0) continue;
    bestT = t;
    entry = x;
  }
  if (!entry) return null;
  const a = dot([entry[0] - line.o[0], entry[1] - line.o[1], entry[2] - line.o[2]], line.e);
  return [line.o[0] + line.e[0] * a + hit[0], line.o[1] + line.e[1] * a + hit[1], line.o[2] + line.e[2] * a + hit[2]];
}
