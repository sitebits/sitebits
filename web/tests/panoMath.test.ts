import { describe, expect, it } from 'vitest';
import {
  angleDeg, clampFov, clampPitch, dirFromYawPitch, faceExtents, facePixel, faceQuad, faceVisible,
  normalizeYaw, panoRadius, planFaceLevels, requiredLevel, rotate, smoothstep, yawPitchFromDir, yawPitchTo,
} from '../src/panoMath';
import type { PinholeImage, Quat } from '../src/stations';
import type { Vec3 } from '../src/manifest';

const S = 0.7071067811865476;
const close = (a: readonly number[], b: readonly number[], d = 9): void => a.forEach((x, i) => expect(x).toBeCloseTo(b[i]!, d));
const img = (q: Quat, over: Partial<PinholeImage> = {}): PinholeImage => ({
  face: 'nz', pose: { translation: [0, 0, 0], rotationQuat: q },
  focalLength: 0.01, pixelWidth: 0.01 * 2 / 64, pixelHeight: 0.01 * 2 / 64,
  principalPoint: [31.5, 31.5], imageWidth: 64, imageHeight: 64, ...over,
});
// Werte eines realen Standpunkts (Referenzpaket Kirche, S001, Seite "py"): Intrinsik + nicht achsgenaue Pose.
const kirchePy: PinholeImage = {
  face: 'py',
  pose: { translation: [1469.3127293965924, 7107.296684771446, 1.291483328986767],
    rotationQuat: [-0.40171956740052606, -0.40200734567767143, 0.581827416714536, 0.5817975080283666] },
  focalLength: 0.012284999846809221, pixelWidth: 6e-6, pixelHeight: 6e-6,
  principalPoint: [2047.5, 2047.5], imageWidth: 4096, imageHeight: 4096,
};

describe('Gier/Neigung', () => {
  it('yaw 0 = +X, yaw 90 = +Y, pitch 90 = +Z', () => {
    close(dirFromYawPitch(0, 0), [1, 0, 0]);
    close(dirFromYawPitch(90, 0), [0, 1, 0]);
    close(dirFromYawPitch(0, 90), [0, 0, 1]);
  });
  it('hin und zurück', () => {
    const { yaw, pitch } = yawPitchFromDir(dirFromYawPitch(-123.4, 12.5));
    expect(yaw).toBeCloseTo(-123.4, 9);
    expect(pitch).toBeCloseTo(12.5, 9);
  });
  it('yawPitchTo zielt vom Standpunkt auf einen Weltpunkt', () => {
    const { yaw, pitch } = yawPitchTo([10, 10, 1], [10, 20, 11]);
    expect(yaw).toBeCloseTo(90, 9);
    expect(pitch).toBeCloseTo(45, 9);
  });
  it('Normierung und Grenzen', () => {
    expect(normalizeYaw(400)).toBeCloseTo(40, 12);
    expect(normalizeYaw(180)).toBe(-180);
    expect(normalizeYaw(-190)).toBeCloseTo(170, 12);
    expect(clampPitch(120)).toBe(89);
    expect(clampFov(500)).toBe(100);
    expect(clampFov(1)).toBe(10);
  });
});

describe('rotate', () => {
  it('90° um Z dreht X auf Y', () => close(rotate([Math.cos(Math.PI / 4), 0, 0, Math.sin(Math.PI / 4)], [1, 0, 0]), [0, 1, 0]));
  it('normiert die Quaternion selbst', () => close(rotate([2, 0, 0, 0], [1, 2, 3]), [1, 2, 3]));
});

describe('Bildebene aus Pose + Intrinsik (E57: Blick −Z, +Y oben, Zeile 0 oben)', () => {
  it('Kirche: halbe Bildebene 2048·6e-6/0,012285 ≈ 1,000244, nicht 1', () => {
    const e = faceExtents(kirchePy);
    const h = (2048 * 6e-6) / 0.012284999846809221;
    expect(e.left).toBeCloseTo(-h, 12);
    expect(e.right).toBeCloseTo(h, 12);
    expect(e.top).toBeCloseTo(h, 12);
    expect(e.bottom).toBeCloseTo(-h, 12);
    expect(h).toBeGreaterThan(1.0002);
  });
  it('Identität: oben links (−1, 1, −1), Mitte (0, 0, −1), v = 0 oben', () => {
    const q = faceQuad(img([1, 0, 0, 0]));
    close(q.corners[0], [-1, 1, -1]);
    close(q.corners[2], [1, -1, -1]);
    close(q.center, [0, 0, -1]);
    expect(q.uvs).toEqual([[0, 0], [1, 0], [1, 1], [0, 1]]);
  });
  it('Seite px (−90° um Y) blickt nach +X', () => close(faceQuad(img([S, 0, -S, 0])).center, [1, 0, 0]));
  it('keine Spiegelung: rechts im Bild = größere Spalte, oben im Bild = kleinere Zeile', () => {
    const im = img([1, 0, 0, 0]);
    const [c0, r0] = facePixel(im, [0, 0, -1])!;
    const [c1] = facePixel(im, [0.1, 0, -1])!;
    const [, r1] = facePixel(im, [0, 0.1, -1])!;
    expect(c0).toBeCloseTo(31.5, 9);
    expect(r0).toBeCloseTo(31.5, 9);
    expect(c1).toBeGreaterThan(c0);
    expect(r1).toBeLessThan(r0);
    close(facePixel(im, [-1, 1, -1])!, [-0.5, -0.5]);
    expect(facePixel(im, [0, 0, 1])).toBeNull();
  });
  it('Kirche „py“: Bild-oben ist Welt-oben, Bild-rechts = Blick × oben (rechtshändig)', () => {
    const q = kirchePy.pose.rotationQuat;
    const up = rotate(q, [0, 1, 0]);
    const right = rotate(q, [1, 0, 0]);
    const view = rotate(q, [0, 0, -1]);
    expect(up[2]).toBeGreaterThan(0.9999);
    const cross: Vec3 = [view[1] * up[2] - view[2] * up[1], view[2] * up[0] - view[0] * up[2], view[0] * up[1] - view[1] * up[0]];
    close(right, cross, 6);
    // Ein Punkt etwas über der Blickrichtung liegt in einer oberen Bildzeile.
    const [, row] = facePixel(kirchePy, [view[0], view[1], view[2] + 0.2])!;
    expect(row).toBeLessThan(2047.5);
  });
});

describe('Stufenwahl und Sichtbarkeit', () => {
  const L = [512, 1024, 2048, 4096];
  it('1080p: 60° → 2048, 45° → 4096, 100° → 1024', () => {
    expect(requiredLevel(L, 60, 1080)).toBe(2048);
    expect(requiredLevel(L, 45, 1080)).toBe(4096);
    expect(requiredLevel(L, 100, 1080)).toBe(1024);
  });
  it('nie über die größte Stufe', () => expect(requiredLevel(L, 10, 2160)).toBe(4096));
  it('Blick nach +X: +X sichtbar, −X nicht, +Y am Rand sichtbar (60°, 16:9)', () => {
    expect(faceVisible([1, 0, 0], [1, 0, 0], 60, 16 / 9)).toBe(true);
    expect(faceVisible([1, 0, 0], [-1, 0, 0], 60, 16 / 9)).toBe(false);
    expect(faceVisible([1, 0, 0], [0, 1, 0], 60, 16 / 9)).toBe(true);
  });
  it('Winkel', () => expect(angleDeg([1, 0, 0], [0, 2, 0])).toBeCloseTo(90, 12));
  it('Budgetplan: zentralste Seiten zuerst, was nicht passt, bekommt die nächstkleinere Stufe', () => {
    const bytes = (l: number): number => l * l * 4;
    const plan = planFaceLevels(
      [{ face: 'd', angleDeg: 80 }, { face: 'a', angleDeg: 10 }, { face: 'b', angleDeg: 40 }, { face: 'c', angleDeg: 60 }],
      4096, L, bytes, 256 * 2 ** 20,
    );
    expect(plan.get('a')).toBe(4096);
    expect(plan.get('b')).toBe(4096);
    expect(plan.get('c')).toBe(4096);
    expect(plan.get('d')).toBe(2048);
  });
  it('Abstand der Vierecke liegt zwischen near und far (Ecken bei r·√3)', () => {
    const r = panoRadius(0.01, 1000);
    expect(r).toBeGreaterThan(0.01);
    expect(r * Math.sqrt(3)).toBeLessThan(1000);
    const r2 = panoRadius(1, 1.5);
    expect(r2).toBeGreaterThan(1);
    expect(r2).toBeLessThan(1.5);
  });
  it('smoothstep', () => {
    expect(smoothstep(-1)).toBe(0);
    expect(smoothstep(0.5)).toBeCloseTo(0.5, 12);
    expect(smoothstep(2)).toBe(1);
  });
});
