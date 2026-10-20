// Reine Geometrie der Schnittbox: Ebenen (innen positiv), Punkt-in-Box-Test, Anfangsbox
// beim Einschalten, Umrechnung Mittelpunkt/Größe ↔ Min/Max sowie der Messungs-Filter.
// Kein Import von Three.js/Giro3D — dadurch in Vitest ohne Bundler testbar (tests/clipBox.test.ts).
// `clipBox.ts` baut die Gizmo-/Clipping-Hülle (TransformControls, cloud.clippingPlanes) darauf auf.
import type { Vec3 } from './manifest';

export interface ClipPlane {
  normal: Vec3;
  constant: number;
}

/**
 * Sechs Halbraum-Ebenen einer achsenparallelen Box, Innenseite positiv: normal·p + constant ≥ 0
 * für jeden Punkt innerhalb [min, max] — three.js-Konvention für Material.clippingPlanes (ein
 * Punkt wird verworfen, sobald er auch nur eine der gesetzten Ebenen verfehlt).
 */
export function boxToPlanes(min: Vec3, max: Vec3): ClipPlane[] {
  return [
    { normal: [1, 0, 0], constant: -min[0] },
    { normal: [-1, 0, 0], constant: max[0] },
    { normal: [0, 1, 0], constant: -min[1] },
    { normal: [0, -1, 0], constant: max[1] },
    { normal: [0, 0, 1], constant: -min[2] },
    { normal: [0, 0, -1], constant: max[2] },
  ];
}

/** Punkt innerhalb [min, max] (Grenze zählt als innen). */
export function insideBox(p: Vec3, min: Vec3, max: Vec3): boolean {
  return p[0] >= min[0] && p[0] <= max[0] && p[1] >= min[1] && p[1] <= max[1] && p[2] >= min[2] && p[2] <= max[2];
}

/** Anfangsbox beim Einschalten: Weltgrenzen auf die mittleren 50 % je Achse geschrumpft. */
export function centralBox([lo, hi]: readonly [Vec3, Vec3]): [Vec3, Vec3] {
  const q = (a: number, b: number): number => a + (b - a) * 0.25;
  return [
    [q(lo[0], hi[0]), q(lo[1], hi[1]), q(lo[2], hi[2])],
    [q(hi[0], lo[0]), q(hi[1], lo[1]), q(hi[2], lo[2])],
  ];
}

/**
 * Box → Mittelpunkt/Größe für den Gizmo-Platzhalter (position = center, scale = size, bei einer
 * 1×1×1-Einheitsbox-Geometrie).
 */
export function centerScaleFromBox(min: Vec3, max: Vec3): { center: Vec3; scale: Vec3 } {
  return {
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2],
    scale: [max[0] - min[0], max[1] - min[1], max[2] - min[2]],
  };
}

/**
 * Umkehrung für den Gizmo-Platzhalter: Mittelpunkt/Größe → Welt-Min/Max. Betrag der Größe, weil
 * TransformControls im Skalieren-Modus negative Werte je Achse zulässt, sobald man den Griff durch
 * den Mittelpunkt hindurchzieht — die Box bliebe sonst mit vertauschtem min/max ungültig.
 */
export function boxFromCenterScale(center: Vec3, scale: Vec3): [Vec3, Vec3] {
  const hx = Math.abs(scale[0]) / 2;
  const hy = Math.abs(scale[1]) / 2;
  const hz = Math.abs(scale[2]) / 2;
  return [
    [center[0] - hx, center[1] - hy, center[2] - hz],
    [center[0] + hx, center[1] + hy, center[2] + hz],
  ];
}

/**
 * Mindestkantenlänge der Box je Achse. TransformControls skaliert multiplikativ: eine auf ≈ 0
 * gezogene Box ließe sich nie wieder aufziehen (0 · Faktor = 0), und alle Punkte blieben
 * weggeschnitten (D35). 1 cm lässt sich per Gizmo jederzeit wieder vergrößern.
 */
export const MIN_BOX_SIZE_M = 0.01;

/**
 * Klemmt die Gizmo-Skalierung je Achse auf |s| ≥ min. Das Vorzeichen bleibt (TransformControls
 * erlaubt beim Durchziehen negative Werte; ein Vorzeichenwechsel würde die Box mitten im Ziehen
 * springen lassen). 0 wird zu +min.
 */
export function clampScale(scale: Vec3, min: number = MIN_BOX_SIZE_M): Vec3 {
  const c = (s: number): number => (Math.abs(s) >= min ? s : s < 0 ? -min : min);
  return [c(scale[0]), c(scale[1]), c(scale[2])];
}

export interface ClipBoxState {
  enabled: boolean;
  box: [Vec3, Vec3] | null;
}

/** measure.ts: darf ein Punkt als Messtreffer zählen? Box aus/keine Box → immer ja. */
export function pointAllowed(state: ClipBoxState | undefined, p: Vec3): boolean {
  if (!state?.enabled || !state.box) return true;
  return insideBox(p, state.box[0], state.box[1]);
}
