// Fenster-Effekt (F32): Augposition aus den Gesichtspunkten von MediaPipe FaceLandmarker. Rein (Vitest).
// Landmarken sind auf das Kamerabild normiert (x nach rechts, y nach unten, 0…1), das Bild ist NICHT gespiegelt.
// Abstand über den Iris-Durchmesser (Mittelwert ≈ 11,7 mm, kaum vom Alter abhängig) und die Brennweite aus dem
// angenommenen horizontalen Bildwinkel der Webcam. Ergebnis relativ zur Kamera; die Kamera sitzt meist über dem
// Bildschirm — das gleicht „Mitte setzen“ aus (neutrale Kopfhaltung wird abgezogen).
import type { Auge } from './fensterProjektion';

export const IRIS_DURCHMESSER_M = 0.0117;
/** Iris-Ringe im 478-Punkte-Modell: linkes Auge 468 (Mitte), 469/471 (rechts/links); rechtes 473, 474/476. */
const IRIS = { linksMitte: 468, linksA: 469, linksB: 471, rechtsMitte: 473, rechtsA: 474, rechtsB: 476 } as const;

export interface Landmarke {
  x: number;
  y: number;
}

/** Augmitte vor der Kamera in Metern (x rechts aus Sicht der Person, y oben, z Abstand) oder null ohne Iris. */
export function augeAusLandmarken(
  lm: readonly Landmarke[],
  bild: { breitePx: number; hoehePx: number; bildwinkelDeg: number },
): Auge | null {
  const p = (i: number): Landmarke | undefined => lm[i];
  const lM = p(IRIS.linksMitte), rM = p(IRIS.rechtsMitte);
  const lA = p(IRIS.linksA), lB = p(IRIS.linksB), rA = p(IRIS.rechtsA), rB = p(IRIS.rechtsB);
  if (!lM || !rM || !lA || !lB || !rA || !rB) return null;
  const px = (a: Landmarke, b: Landmarke): number => Math.hypot((a.x - b.x) * bild.breitePx, (a.y - b.y) * bild.hoehePx);
  const irisPx = (px(lA, lB) + px(rA, rB)) / 2;
  if (!(irisPx > 0)) return null;
  const f = bild.breitePx / 2 / Math.tan((bild.bildwinkelDeg * Math.PI) / 360);
  const z = (f * IRIS_DURCHMESSER_M) / irisPx;
  const mx = (lM.x + rM.x) / 2;
  const my = (lM.y + rM.y) / 2;
  // Ungespiegeltes Kamerabild: geht die Person nach rechts, wandert ihr Gesicht im Bild nach links.
  return { x: ((0.5 - mx) * bild.breitePx * z) / f, y: ((0.5 - my) * bild.hoehePx * z) / f, z };
}

/** Kopfhaltung relativ zur gespeicherten Mitte (x/y), Abstand bleibt absolut. */
export function relativZurMitte(a: Auge, mitte: { x: number; y: number } | null): Auge {
  return mitte ? { x: a.x - mitte.x, y: a.y - mitte.y, z: a.z } : a;
}

/**
 * One-Euro-Filter (Casiez et al. 2012) je Achse: ruhig beim Stillhalten, ohne Verzögerung bei schneller Bewegung.
 * `minCutoff` in Hz glättet das Zittern, `beta` lässt schnelle Bewegungen durch.
 */
export class EinEuroFilter {
  private x: number | null = null;
  private dx = 0;
  private t = 0;

  constructor(private readonly minCutoff = 1.0, private readonly beta = 0.02, private readonly dCutoff = 1.0) {}

  private static alpha(cutoff: number, dt: number): number {
    const tau = 1 / (2 * Math.PI * cutoff);
    return 1 / (1 + tau / dt);
  }

  filter(wert: number, zeitS: number): number {
    if (this.x === null || zeitS <= this.t) {
      this.x = wert;
      this.t = zeitS;
      return wert;
    }
    const dt = zeitS - this.t;
    this.t = zeitS;
    const roh = (wert - this.x) / dt;
    this.dx += EinEuroFilter.alpha(this.dCutoff, dt) * (roh - this.dx);
    const cutoff = this.minCutoff + this.beta * Math.abs(this.dx);
    this.x += EinEuroFilter.alpha(cutoff, dt) * (wert - this.x);
    return this.x;
  }

  zuruecksetzen(): void {
    this.x = null;
    this.dx = 0;
  }
}
