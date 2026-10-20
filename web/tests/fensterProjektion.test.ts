import { PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { OHNE_VERSATZ, anwenden, fensterVersatz } from '../src/fensterProjektion';

// Kamera bei z = 10, Blick auf den Ursprung (Drehpunkt), Fensterebene z = 0.
const B = 1600;
const H = 1000;
function kamera(): { k: PerspectiveCamera; basis: Vector3 } {
  const k = new PerspectiveCamera(50, B / H, 0.1, 1000);
  k.position.set(0, 0, 10);
  k.lookAt(0, 0, 0);
  k.updateMatrixWorld();
  k.updateProjectionMatrix();
  return { k, basis: k.position.clone() };
}
const ndc = (k: PerspectiveCamera, p: Vector3): Vector3 => p.clone().project(k);
const OPT = { ebene: 10, fovDeg: 50, aspect: B / H, bildschirmBreiteM: 0.34, staerke: 1 };

describe('Fenster-Effekt: Projektion (F32)', () => {
  it('Auge mittig oder Stärke 0 → keine Änderung', () => {
    const mitte = fensterVersatz({ x: 0, y: 0, z: 0.6 }, OPT);
    expect([mitte.rechts, mitte.oben, mitte.anteilX, mitte.anteilY].every((x) => x === 0)).toBe(true);
    expect(fensterVersatz({ x: 0.1, y: 0, z: 0.6 }, { ...OPT, staerke: 0 })).toBe(OHNE_VERSATZ);
    expect(fensterVersatz({ x: 0.1, y: 0, z: 0 }, OPT)).toBe(OHNE_VERSATZ);
  });

  it('Ebene des Drehpunkts bleibt am Bildschirm stehen, nahe Punkte wandern gegen die Kopfrichtung', () => {
    const { k, basis } = kamera();
    const inEbene = [new Vector3(2, 1, 0), new Vector3(-3, -1.5, 0)];
    const nah = new Vector3(0.5, 0, 6);
    const fern = new Vector3(0.5, 0, -20);
    const vorher = [...inEbene, nah, fern].map((p) => ndc(k, p));

    // Kopf 10 cm nach rechts und 5 cm nach oben.
    const v = fensterVersatz({ x: 0.1, y: 0.05, z: 0.6 }, OPT);
    expect(v.rechts).toBeGreaterThan(0);
    anwenden(k, basis, v, B, H);
    k.updateProjectionMatrix();
    const nachher = [...inEbene, nah, fern].map((p) => ndc(k, p));

    for (const i of [0, 1]) {
      expect(nachher[i]!.x).toBeCloseTo(vorher[i]!.x, 9);
      expect(nachher[i]!.y).toBeCloseTo(vorher[i]!.y, 9);
    }
    // Nah (vor der Ebene) wandert nach links (gegen den Kopf), fern nach rechts (mit dem Kopf).
    expect(nachher[2]!.x).toBeLessThan(vorher[2]!.x);
    expect(nachher[3]!.x).toBeGreaterThan(vorher[3]!.x);
    // Blickrichtung unverändert: keine Drehung.
    const blick = k.getWorldDirection(new Vector3());
    expect(blick.distanceTo(new Vector3(0, 0, -1))).toBeLessThan(1e-9);
  });

  it('zurück in die Mitte stellt Position und Projektion wieder her', () => {
    const { k, basis } = kamera();
    const p0 = k.projectionMatrix.clone();
    anwenden(k, basis, fensterVersatz({ x: -0.2, y: 0.1, z: 0.5 }, OPT), B, H);
    k.updateProjectionMatrix();
    expect(k.projectionMatrix.equals(p0)).toBe(false);
    anwenden(k, basis, OHNE_VERSATZ, B, H);
    k.updateProjectionMatrix();
    expect(k.projectionMatrix.equals(p0)).toBe(true);
    expect(k.position.equals(basis)).toBe(true);
  });

  it('Stärke skaliert linear; physikalisch: Kopf um halbe Bildschirmbreite = halbe Fensterbreite in der Szene', () => {
    const halb = fensterVersatz({ x: OPT.bildschirmBreiteM / 2, y: 0, z: 0.6 }, OPT);
    const halbeFensterbreite = 10 * Math.tan((50 * Math.PI) / 360) * (B / H);
    expect(halb.rechts).toBeCloseTo(halbeFensterbreite, 9);
    expect(halb.anteilX).toBeCloseTo(-0.5, 9);
    expect(fensterVersatz({ x: 0.1, y: 0, z: 0.6 }, { ...OPT, staerke: 2 }).rechts)
      .toBeCloseTo(2 * fensterVersatz({ x: 0.1, y: 0, z: 0.6 }, OPT).rechts, 12);
  });
});
