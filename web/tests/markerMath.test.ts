import { describe, expect, it } from 'vitest';
import { KUGEL_ECKPUNKT, KUGEL_MITTELPUNKT, KUGEL_POI, KUGEL_POI_GEWAEHLT, kugelRadius, matcapRgba, meterProPixel } from '../src/markerMath';
import type { KugelKamera } from '../src/markerMath';

// 60° Sichtfeld, 1080 px hoch: 2·tan(30°)/1080 ≈ 1,069 mm je Pixel und Meter Abstand.
const persp = (abstandM: number): KugelKamera => ({ art: 'perspektivisch', abstandM, fovGrad: 60, hoehePx: 1080 });
const G = { radiusM: 0.01, minPx: 5, maxPx: 20 };

describe('meterProPixel', () => {
  it('perspektivisch: wächst linear mit dem Abstand', () => {
    expect(meterProPixel(persp(1))).toBeCloseTo((2 * Math.tan(Math.PI / 6)) / 1080, 12);
    expect(meterProPixel(persp(10))).toBeCloseTo(10 * meterProPixel(persp(1)), 12);
  });
  it('orthografisch: sichtbare Höhe durch Pixelhöhe, unabhängig vom Abstand', () => {
    expect(meterProPixel({ art: 'orthografisch', sichtHoeheM: 54, hoehePx: 1080 })).toBeCloseTo(0.05, 12);
  });
});

describe('kugelRadius', () => {
  it('mittlerer Abstand: Weltgröße gilt (perspektivisch)', () => {
    const r = kugelRadius(persp(1), G);
    expect(r.welt).toBeCloseTo(0.01, 12);
    expect(r.px).toBeCloseTo(0.01 / meterProPixel(persp(1)), 9); // ≈ 9,4 px
    expect(r.px).toBeGreaterThan(G.minPx);
    expect(r.px).toBeLessThan(G.maxPx);
  });
  it('aus der Ferne: nie kleiner als minPx (Weltradius wächst mit)', () => {
    const r = kugelRadius(persp(50), G);
    expect(r.px).toBe(5);
    expect(r.welt).toBeCloseTo(5 * meterProPixel(persp(50)), 12);
    expect(r.welt).toBeGreaterThan(G.radiusM);
  });
  it('aus der Nähe: nie größer als maxPx (Weltradius schrumpft)', () => {
    const r = kugelRadius(persp(0.1), G);
    expect(r.px).toBe(20);
    expect(r.welt).toBeLessThan(G.radiusM);
  });
  it('Umschaltpunkte liegen genau dort, wo Weltgröße und Pixelgrenze gleich sind', () => {
    const mpp1 = meterProPixel(persp(1));
    const dMin = G.radiusM / (G.minPx * mpp1); // ab hier greift minPx
    const dMax = G.radiusM / (G.maxPx * mpp1); // bis hier greift maxPx
    expect(kugelRadius(persp(dMin), G).px).toBeCloseTo(5, 9);
    expect(kugelRadius(persp(dMax), G).px).toBeCloseTo(20, 9);
    expect(kugelRadius(persp(dMin * 1.01), G).px).toBe(5);
    expect(kugelRadius(persp(dMax * 0.99), G).px).toBe(20);
  });
  it('orthografisch (Draufsicht): Grenzen gelten ebenso', () => {
    expect(kugelRadius({ art: 'orthografisch', sichtHoeheM: 540, hoehePx: 1080 }, G).px).toBe(5);
    expect(kugelRadius({ art: 'orthografisch', sichtHoeheM: 1.08, hoehePx: 1080 }, G).px).toBeCloseTo(10, 9);
  });
  it('entartete Kamera (Abstand 0, Zeichenfläche 0): endliche Weltgröße statt 0 oder unendlich', () => {
    expect(kugelRadius(persp(0), G)).toEqual({ welt: 0.01, px: 5 });
    expect(kugelRadius({ art: 'perspektivisch', abstandM: 3, fovGrad: 60, hoehePx: 0 }, G)).toEqual({ welt: 0.01, px: 5 });
  });
  it('Voreinstellungen: Mittelpunkt kleiner als Eckpunkt, ausgewählter POI größer als POI', () => {
    for (const d of [0.2, 1, 5, 50]) {
      expect(kugelRadius(persp(d), KUGEL_MITTELPUNKT).px).toBeLessThan(kugelRadius(persp(d), KUGEL_ECKPUNKT).px);
      expect(kugelRadius(persp(d), KUGEL_POI_GEWAEHLT).px).toBeGreaterThan(kugelRadius(persp(d), KUGEL_POI).px);
    }
    // Untergrenze mindestens so groß wie die früheren festen Marker (5 px Eckpunkt, 3 px Mittelpunkt).
    expect(KUGEL_ECKPUNKT.minPx).toBeGreaterThanOrEqual(5);
    expect(KUGEL_MITTELPUNKT.minPx).toBeGreaterThanOrEqual(3);
  });
});

describe('matcapRgba', () => {
  const N = 64;
  const bild = matcapRgba(N);
  // Zeile 0 = unten (WebGL-Reihenfolge ohne flipY).
  const grau = (u: number, v: number): number => {
    const x = Math.round(((u + 1) / 2) * N - 0.5);
    const y = Math.round(((v + 1) / 2) * N - 0.5);
    return bild[(y * N + x) * 4]!;
  };
  it('Größe, Graustufen, deckend', () => {
    expect(bild.length).toBe(N * N * 4);
    for (let i = 0; i < bild.length; i += 4) {
      expect(bild[i]).toBe(bild[i + 1]);
      expect(bild[i]).toBe(bild[i + 2]);
      expect(bild[i + 3]).toBe(255);
    }
  });
  it('schattiert: Licht oben links heller als unten rechts, Rand dunkler als Mitte', () => {
    expect(grau(-0.4, 0.4)).toBeGreaterThan(grau(0.4, -0.4) + 60);
    expect(grau(0, 0)).toBeGreaterThan(grau(0.95, -0.1));
    expect(grau(0.6, -0.6)).toBeGreaterThan(0); // Schattenseite nicht schwarz: Markerfarbe bleibt erkennbar
  });
  it('Glanzpunkt oben links ist die hellste Stelle', () => {
    let max = 0;
    let at = 0;
    for (let i = 0; i < N * N; i++) if (bild[i * 4]! > max) [max, at] = [bild[i * 4]!, i];
    const x = ((at % N) + 0.5) / N * 2 - 1;
    const y = (Math.floor(at / N) + 0.5) / N * 2 - 1;
    expect(max).toBe(255);
    expect(x).toBeLessThan(0);
    expect(y).toBeGreaterThan(0);
  });
});
