import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { VIRIDIS, availableColorModes, fitDistance, hoeheText, localToWorld, nearPlaneFor, orbitSchritt, orbitZoom, relativeLuminanz, worldToLocal } from '../src/viewMath';
import type { Vec3 } from '../src/manifest';

// Attributliste, wie Giro3D sie für halle-vier (LAS-Format 7) liefert.
const GIRO3D_FMT7 = ['Color', 'Z', 'Intensity', 'Classification', 'GpsTime', 'Red', 'Green', 'Blue'];

describe('availableColorModes', () => {
  it('nur RGB im Manifest → keine Intensität, obwohl Giro3D sie meldet', () => {
    expect(availableColorModes(['rgb'], GIRO3D_FMT7)).toEqual(['rgb', 'elevation']);
  });
  it('RGB + Intensität → alle drei in fester Reihenfolge', () => {
    expect(availableColorModes(['intensity', 'rgb'], GIRO3D_FMT7)).toEqual(['rgb', 'intensity', 'elevation']);
  });
  it('Manifest nennt rgb, Quelle hat aber kein Color → kein RGB', () => {
    expect(availableColorModes(['rgb'], ['Z', 'Intensity'])).toEqual(['elevation']);
  });
  it('ohne Z bleibt nichts', () => {
    expect(availableColorModes([], [])).toEqual([]);
  });
});

describe('fitDistance', () => {
  it('Würfel 2×2×2 bei 60° → Radius √3 / sin 30°', () => {
    const b: [Vec3, Vec3] = [[0, 0, 0], [2, 2, 2]];
    expect(fitDistance(b, 60)).toBeCloseTo(Math.sqrt(3) / 0.5, 12);
  });
  it('entartete Box → Mindestradius 1 m', () => {
    const b: [Vec3, Vec3] = [[5, 5, 5], [5, 5, 5]];
    expect(fitDistance(b, 60)).toBeCloseTo(2, 12);
  });
});

describe('worldToLocal / localToWorld', () => {
  const origin: Vec3 = [2529, 8161, 69];
  it('Ursprung ist originWorld', () => {
    expect(worldToLocal(origin, [2529, 8161, 69])).toEqual([0, 0, 0]);
  });
  it('Hin und zurück ist verlustfrei (UTM-Größenordnung, Float64)', () => {
    const utm: Vec3 = [392_000, 5_820_000, 40];
    const p: Vec3 = [392_123.456, 5_820_987.654, 41.234];
    expect(localToWorld(utm, worldToLocal(utm, p))).toEqual(p);
  });
});

describe('nearPlaneFor', () => {
  it('skaliert mit dem Abstand (1/100)', () => {
    expect(nearPlaneFor(15)).toBeCloseTo(0.15, 12);
    expect(nearPlaneFor(30)).toBeCloseTo(0.3, 12);
  });
  it('Zoom auf 0,5 m → Boden 1 cm (nicht 5 mm)', () => {
    expect(nearPlaneFor(0.5)).toBe(0.01);
    expect(nearPlaneFor(0)).toBe(0.01);
  });
  it('Übersicht aus 400 m → 2 m (Giro3D-Vorgabe)', () => {
    expect(nearPlaneFor(400)).toBe(2);
  });
  it('ungültiger Abstand → sichere Obergrenze', () => {
    expect(nearPlaneFor(Number.NaN)).toBe(2);
    expect(nearPlaneFor(Number.POSITIVE_INFINITY)).toBe(2);
  });
});

describe('VIRIDIS / relativeLuminanz', () => {
  it('Höhenskala: die fünf viridis-Stützstellen', () => {
    expect(VIRIDIS).toEqual(['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725']);
  });
  it('Legendenbalken im Stylesheet nutzt genau diese Stufen', () => {
    const css = readFileSync(fileURLToPath(new URL('../src/styles/befund.css', import.meta.url)), 'utf8');
    expect(css).toContain(`linear-gradient(to right, ${VIRIDIS.join(', ')})`);
  });
  it('Helligkeit steigt streng monoton über die Stufen (bleibt auch bei Farbsehschwäche geordnet)', () => {
    const l = VIRIDIS.map(relativeLuminanz);
    for (let i = 1; i < l.length; i++) expect(l[i]!).toBeGreaterThan(l[i - 1]!);
  });
  it('Eckwerte nach WCAG-Formel: Schwarz 0, Weiß 1', () => {
    expect(relativeLuminanz('#000000')).toBe(0);
    expect(relativeLuminanz('#ffffff')).toBe(1);
  });
  it('bekannter Zwischenwert (reines Grün) und Großschreibung', () => {
    expect(relativeLuminanz('#00FF00')).toBeCloseTo(0.7152, 4);
  });
  it('ungültige Farbangabe wirft', () => {
    expect(() => relativeLuminanz('#fff')).toThrow();
    expect(() => relativeLuminanz('blau')).toThrow();
  });
});

describe('hoeheText', () => {
  it('deutsch mit einer Nachkommastelle', () => {
    expect(hoeheText(312.44)).toBe('312,4');
    expect(hoeheText(-0.06)).toBe('-0,1');
    expect(hoeheText(1234.5)).toBe('1.234,5');
  });
  it('kein „-0,0“ bei Werten knapp unter null', () => {
    expect(hoeheText(-0.04)).toBe('0,0');
  });
});

const abstand = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
/** Neigung der Blickrichtung Kamera → Ziel über der Waagerechten, in Grad (positiv = Kamera über dem Ziel). */
const neigung = (cam: Vec3, tgt: Vec3): number =>
  (Math.atan2(cam[2] - tgt[2], Math.hypot(cam[0] - tgt[0], cam[1] - tgt[1])) * 180) / Math.PI;
const nahe = (a: Vec3, b: Vec3, eps = 1e-9): void => a.forEach((x, i) => expect(Math.abs(x - b[i]!)).toBeLessThanOrEqual(eps));

describe('orbitSchritt', () => {
  it('90° nach links um das Ziel (Z oben)', () => {
    nahe(orbitSchritt([10, 0, 0], [0, 0, 0], 90, 0), [0, 10, 0]);
    nahe(orbitSchritt([10, 0, 0], [0, 0, 0], -90, 0), [0, -10, 0]);
  });
  it('Ziel außerhalb des Ursprungs, Abstand bleibt bei beliebigen Schritten', () => {
    const tgt: Vec3 = [412_000, 5_650_000, 300];
    let cam: Vec3 = [412_010, 5_650_003, 304];
    const r0 = abstand(cam, tgt);
    for (const [dy, dp] of [[15, 0], [0, 15], [-37, 22], [200, -60], [0, -50]] as const) {
      cam = orbitSchritt(cam, tgt, dy, dp);
      expect(abstand(cam, tgt)).toBeCloseTo(r0, 6);
    }
  });
  it('Neigung begrenzt auf ±85°', () => {
    const oben = orbitSchritt([10, 0, 0], [0, 0, 0], 0, 200);
    expect(neigung(oben, [0, 0, 0])).toBeCloseTo(85, 9);
    expect(abstand(oben, [0, 0, 0])).toBeCloseTo(10, 9);
    expect(neigung(orbitSchritt([10, 0, 0], [0, 0, 0], 0, -200), [0, 0, 0])).toBeCloseTo(-85, 9);
    expect(neigung(orbitSchritt([10, 0, 0], [0, 0, 0], 0, 30), [0, 0, 0])).toBeCloseTo(30, 9);
  });
  it('steilere Ausgangslage springt nicht auf die Grenze, wird aber auch nicht steiler', () => {
    const steil: Vec3 = [Math.cos(89 * Math.PI / 180) * 10, 0, Math.sin(89 * Math.PI / 180) * 10];
    expect(neigung(orbitSchritt(steil, [0, 0, 0], 0, 0), [0, 0, 0])).toBeCloseTo(89, 9);
    expect(neigung(orbitSchritt(steil, [0, 0, 0], 15, 0), [0, 0, 0])).toBeCloseTo(89, 9);
    expect(neigung(orbitSchritt(steil, [0, 0, 0], 0, 15), [0, 0, 0])).toBeCloseTo(89, 9);
    expect(neigung(orbitSchritt(steil, [0, 0, 0], 0, -15), [0, 0, 0])).toBeCloseTo(74, 9);
  });
  it('Kamera im Ziel oder ungültige Schritte: unverändert', () => {
    expect(orbitSchritt([1, 2, 3], [1, 2, 3], 15, 0)).toEqual([1, 2, 3]);
    expect(orbitSchritt([10, 0, 0], [0, 0, 0], Number.NaN, 0)).toEqual([10, 0, 0]);
  });
});

describe('orbitZoom', () => {
  it('Abstand × Faktor auf der Geraden zum Ziel', () => {
    nahe(orbitZoom([10, 0, 0], [0, 0, 0], 0.8), [8, 0, 0]);
    nahe(orbitZoom([8, 0, 0], [0, 0, 0], 1 / 0.8), [10, 0, 0]);
    nahe(orbitZoom([3, 4, 12], [0, 0, 0], 2), [6, 8, 24]);
  });
  it('näher als die Untergrenze: Heranzoomen schiebt die Kamera nicht hinaus', () => {
    nahe(orbitZoom([0.3, 0, 0], [0, 0, 0], 0.8), [0.3, 0, 0]);
    nahe(orbitZoom([0.3, 0, 0], [0, 0, 0], 1 / 0.8), [0.375, 0, 0]);
  });
  it('mindestens 0,5 m vom Ziel', () => {
    nahe(orbitZoom([1, 0, 0], [0, 0, 0], 0.1), [0.5, 0, 0]);
  });
  it('Kamera im Ziel oder ungültiger Faktor: unverändert', () => {
    expect(orbitZoom([1, 2, 3], [1, 2, 3], 0.8)).toEqual([1, 2, 3]);
    expect(orbitZoom([10, 0, 0], [0, 0, 0], Number.NaN)).toEqual([10, 0, 0]);
    expect(orbitZoom([10, 0, 0], [0, 0, 0], 0)).toEqual([10, 0, 0]);
  });
});
