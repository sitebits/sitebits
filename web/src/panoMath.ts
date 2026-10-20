// Reine Panorama-Mathematik (Vitest, ohne three.js):
//  - Blickrichtung ↔ Gier/Neigung (yaw 0 = +X, gegen den Uhrzeigersinn von oben; pitch über
//    dem Horizont; Welt-Z oben).
//  - Bildebene einer Würfelseite aus Bildpose + Intrinsik (Lochkamera blickt entlang −Z,
//    +Y oben, Pixelzeile 0 oben, principalPoint in Pixeln mit Pixelmitte bei ganzen Zahlen). Es wird die
//    ECHTE Pose genutzt, keine achsgerundete Seitenrotation: reale Posen (Referenzpaket Kirche) sind nicht achsgenau
//    (Gier des Scans, Kippung ≈ 1 px bei 4096), und die halbe Bildebene ist 1,000244 statt 1.
//  - Stufenwahl nach Bildschirmauflösung, Sichtbarkeit, Budgetplan, Abstand der Vierecke.
import type { Vec3 } from './manifest';
import type { PinholeImage, Quat } from './stations';

export const PANO_FOV_DEFAULT = 60;
export const PANO_FOV_MIN = 10;
export const PANO_FOV_MAX = 100;
export const PITCH_MAX = 89;
/** Eine Stufe gilt als scharf genug, wenn sie ≥ 80 % der Bildschirmauflösung in Bildmitte liefert. */
export const LEVEL_SLACK = 0.8;
/** Halbe Diagonale einer 90°-Würfelseite: atan(√2). */
const FACE_HALF_DIAG_DEG = 54.735610317245346;
const RAD = Math.PI / 180;

export const normalizeYaw = (y: number): number => ((((y + 180) % 360) + 360) % 360) - 180;
export const clampPitch = (p: number): number => Math.min(PITCH_MAX, Math.max(-PITCH_MAX, p));
export const clampFov = (f: number): number => Math.min(PANO_FOV_MAX, Math.max(PANO_FOV_MIN, f));

export function dirFromYawPitch(yawDeg: number, pitchDeg: number): Vec3 {
  const y = yawDeg * RAD;
  const p = pitchDeg * RAD;
  return [Math.cos(p) * Math.cos(y), Math.cos(p) * Math.sin(y), Math.sin(p)];
}

export function yawPitchFromDir(d: Vec3): { yaw: number; pitch: number } {
  return { yaw: Math.atan2(d[1], d[0]) / RAD, pitch: Math.atan2(d[2], Math.hypot(d[0], d[1])) / RAD };
}

export function yawPitchTo(from: Vec3, to: Vec3): { yaw: number; pitch: number } {
  return yawPitchFromDir([to[0] - from[0], to[1] - from[1], to[2] - from[2]]);
}

const normalize = (v: Vec3): Vec3 => {
  const n = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / n, v[1] / n, v[2] / n];
};

/** Dreht v mit der (auf Länge 1 normierten) Quaternion q = (w, x, y, z). */
export function rotate(q: Quat, v: Vec3): Vec3 {
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  const w = q[0] / n;
  const x = q[1] / n;
  const y = q[2] / n;
  const z = q[3] / n;
  const tx = 2 * (y * v[2] - z * v[1]);
  const ty = 2 * (z * v[0] - x * v[2]);
  const tz = 2 * (x * v[1] - y * v[0]);
  return [v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx)];
}

export interface FaceExtents {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Ränder der Bildebene bei z = −1 (Bild-KS): äußere Pixelkanten, nicht Pixelmitten. */
export function faceExtents(im: PinholeImage): FaceExtents {
  const sx = im.pixelWidth / im.focalLength;
  const sy = im.pixelHeight / im.focalLength;
  const [cx, cy] = im.principalPoint;
  return {
    left: (-0.5 - cx) * sx,
    right: (im.imageWidth - 0.5 - cx) * sx,
    top: (cy + 0.5) * sy,
    bottom: (cy - (im.imageHeight - 0.5)) * sy,
  };
}

export interface FaceQuad {
  /** Oben links, oben rechts, unten rechts, unten links — Weltachsen, relativ zur Bildposition, Abstand 1. */
  corners: [Vec3, Vec3, Vec3, Vec3];
  /** v = 0 am oberen Bildrand: ImageBitmap wird ohne flipY hochgeladen (three ignoriert flipY dafür). */
  uvs: [number, number][];
  /** Blickrichtung der Seite (Welt, normiert). */
  center: Vec3;
}

export function faceQuad(im: PinholeImage): FaceQuad {
  const e = faceExtents(im);
  const q = im.pose.rotationQuat;
  const c = (x: number, y: number): Vec3 => rotate(q, [x, y, -1]);
  return {
    corners: [c(e.left, e.top), c(e.right, e.top), c(e.right, e.bottom), c(e.left, e.bottom)],
    uvs: [[0, 0], [1, 0], [1, 1], [0, 1]],
    center: normalize(rotate(q, [0, 0, -1])),
  };
}

/** Weltrichtung → (Spalte, Zeile) mit Pixelmitte bei ganzen Zahlen; null, wenn nicht in dieser Seite. */
export function facePixel(im: PinholeImage, dir: Vec3): [number, number] | null {
  const q = im.pose.rotationQuat;
  const d = rotate([q[0], -q[1], -q[2], -q[3]], dir);
  if (d[2] >= 0) return null;
  const x = d[0] / -d[2];
  const y = d[1] / -d[2];
  const col = im.principalPoint[0] + (x * im.focalLength) / im.pixelWidth;
  const row = im.principalPoint[1] - (y * im.focalLength) / im.pixelHeight;
  const eps = 1e-9;
  if (col < -0.5 - eps || col > im.imageWidth - 0.5 + eps || row < -0.5 - eps || row > im.imageHeight - 0.5 + eps)
    return null;
  return [col, row];
}

/**
 * Kleinste Stufe, die in Bildmitte ≥ LEVEL_SLACK × Bildschirmauflösung liefert: nötige Seitenbreite
 * = Bildhöhe · tan(45°) / tan(FOV/2). 1080p: 60° → 1871 → 2048; unter ≈ 46° → 4096.
 */
export function requiredLevel(levels: readonly number[], vFovDeg: number, viewportHeightPx: number): number {
  const need = viewportHeightPx / Math.tan((vFovDeg / 2) * RAD);
  return levels.find((l) => l >= need * LEVEL_SLACK) ?? levels[levels.length - 1]!;
}

export function angleDeg(a: Vec3, b: Vec3): number {
  const na = normalize(a);
  const nb = normalize(b);
  const dot = Math.min(1, Math.max(-1, na[0] * nb[0] + na[1] * nb[1] + na[2] * nb[2]));
  return Math.acos(dot) / RAD;
}

export function cameraHalfDiagonalDeg(vFovDeg: number, aspect: number): number {
  return Math.atan(Math.tan((vFovDeg / 2) * RAD) * Math.sqrt(1 + aspect * aspect)) / RAD;
}

/** Konservativ: sichtbar, wenn sich die Diagonalkegel von Kamera und Seite überlappen. */
export function faceVisible(camDir: Vec3, faceCenter: Vec3, vFovDeg: number, aspect: number): boolean {
  return angleDeg(camDir, faceCenter) < cameraHalfDiagonalDeg(vFovDeg, aspect) + FACE_HALF_DIAG_DEG;
}

/**
 * Stufe je sichtbarer Seite unter dem Budget. Die kleinste Stufe aller sechs Seiten ist
 * immer eingerechnet; die zentralste Seite bekommt zuerst die Zielstufe, jede weitere die größte
 * Stufe ≤ Ziel, die noch passt. So verdrängt der Lader nie eine gewünschte Stufe (kein Nachlade-Pingpong).
 */
export function planFaceLevels<F extends string>(
  visible: readonly { face: F; angleDeg: number }[],
  target: number,
  levels: readonly number[],
  bytesOf: (level: number) => number,
  budgetBytes: number,
): Map<F, number> {
  const base = levels[0]!;
  let used = 6 * bytesOf(base);
  const out = new Map<F, number>();
  for (const { face } of [...visible].sort((a, b) => a.angleDeg - b.angleDeg)) {
    const fits = levels.filter((l) => l > base && l <= target && used + bytesOf(l) <= budgetBytes);
    const level = fits.length ? fits[fits.length - 1]! : base;
    if (level > base) used += bytesOf(level);
    out.set(face, level);
  }
  return out;
}

/**
 * Abstand der Panorama-Vierecke von der Kamera. Mit depthTest:false ist jeder Abstand im Sichtvolumen
 * gleich gut; die Mitte eines Vierecks liegt bei r, die Ecken bei ≈ r·√3 — beides muss in [near, far).
 */
export function panoRadius(near: number, far: number): number {
  const lo = near * 1.01;
  const hi = far / (Math.sqrt(3) * 1.01);
  if (hi <= lo) return (near + far) / 2;
  return Math.min(hi, Math.max(lo, Math.sqrt(near * far)));
}

export const smoothstep = (t: number): number => {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
};
