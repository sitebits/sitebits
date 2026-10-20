import { BoxGeometry, Mesh, MeshBasicMaterial, PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { orbitZoom } from '../src/viewMath';
import { gleicheAchse, stoppAbstand, ZOOM_STOPP_MIN_M, zoomStoppAnbinden } from '../src/zoomStopp';

describe('stoppAbstand', () => {
  it('ohne Treffer oder mit Fläche hinter dem Drehpunkt: keine Grenze', () => {
    expect(stoppAbstand(10, null)).toBe(0);
    expect(stoppAbstand(10, 12)).toBe(0);
    expect(stoppAbstand(10, 10)).toBe(0);
  });
  it('Drehpunkt 2 m hinter der Fläche: Grenze = Tiefe + Abstand vor der Fläche', () => {
    const g = stoppAbstand(10, 8);
    expect(g).toBeGreaterThanOrEqual(2 + ZOOM_STOPP_MIN_M);
    expect(g).toBeLessThan(2.2);
  });
  it('Drehpunkt auf der Fläche (Doppelklick): bis kurz davor', () => {
    expect(stoppAbstand(5, 5 - 1e-6)).toBeCloseTo(ZOOM_STOPP_MIN_M, 3);
  });
  it('der Abstand vor der Fläche wächst mit der Tiefe (Near-Plane schneidet sonst an)', () => {
    const tief = stoppAbstand(500, 100) - 400;
    expect(tief).toBeGreaterThan(400 * 0.03);
  });
  it('nie mehr als der aktuelle Abstand', () => {
    expect(stoppAbstand(0.03, 0.001)).toBeLessThanOrEqual(0.03);
  });
});

describe('gleicheAchse', () => {
  const p = (k: number[], z: number[]) => ({ kamera: new Vector3(...k), ziel: new Vector3(...z) });
  it('gleiche Richtung und Drehpunkt, nur näher: passt (Zoom ändert die Achse nicht)', () => {
    expect(gleicheAchse(p([0, -10, 0], [0, 0, 0]), p([0, -4, 0], [0, 0, 0]))).toBe(true);
  });
  it('gedreht oder Drehpunkt verschoben: passt nicht', () => {
    expect(gleicheAchse(p([0, -10, 0], [0, 0, 0]), p([3, -9.5, 0], [0, 0, 0]))).toBe(false);
    expect(gleicheAchse(p([0, -10, 0], [0, 0, 0]), p([0, -10, 0], [1, 0, 0]))).toBe(false);
  });
});

describe('orbitZoom mit Mindestabstand', () => {
  it('Heranzoomen hält am Zoom-Stopp, nicht erst bei 0,5 m', () => {
    const k = orbitZoom([0, -10, 0], [0, 0, 0], 0.1, 3);
    expect(k[1]).toBeCloseTo(-3, 9);
  });
  it('schon näher als der Stopp: kein Zurückschieben', () => {
    expect(orbitZoom([0, -2, 0], [0, 0, 0], 0.5, 3)[1]).toBeCloseTo(-2, 9);
  });
});

/** Steuerung wie OrbitControls: Ereignisse von Hand auslösen. */
function steuerung() {
  const hoerer: Record<string, (() => void)[]> = {};
  return {
    target: new Vector3(0, 0, 0),
    minDistance: 0,
    addEventListener(typ: string, f: () => void) { (hoerer[typ] ??= []).push(f); },
    feuer(typ: string) { for (const f of hoerer[typ] ?? []) f(); },
  };
}

describe('zoomStoppAnbinden', () => {
  // Würfel 2 m Kantenlänge um den Drehpunkt (Modellmitte), Kamera 10 m davor.
  const wuerfel = new Mesh(new BoxGeometry(2, 2, 2), new MeshBasicMaterial());
  wuerfel.updateMatrixWorld(true);
  const aufbau = (meshes: Mesh[] = [wuerfel], erlaubt?: (p: Vector3) => boolean) => {
    const c = steuerung();
    const kamera = new PerspectiveCamera();
    kamera.position.set(0, -10, 0);
    const stopp = zoomStoppAnbinden({ controls: c, camera: () => kamera, meshes: () => meshes, erlaubt, leerlauf: (f) => f() });
    stopp.messen();
    return { c, kamera, stopp };
  };

  it('Gestenstart setzt minDistance vor die Würfelfläche, Gestenende hebt sie auf', () => {
    const { c, stopp } = aufbau();
    expect(stopp.abstand).toBeGreaterThan(1);
    expect(stopp.abstand).toBeLessThan(1.2);
    c.feuer('start');
    expect(c.minDistance).toBe(stopp.abstand);
    c.feuer('end');
    expect(c.minDistance).toBe(0);
  });

  it('nach Drehen um 90° wird beim Gestenstart sofort neu gemessen (gilt für die neue Achse)', () => {
    const { c, kamera, stopp } = aufbau();
    kamera.position.set(10, 0, 0);
    c.feuer('start');
    expect(c.minDistance).toBeGreaterThan(1);
    expect(c.minDistance).toBe(stopp.abstand);
  });

  it('gedreht, Achse zeigt am Mesh vorbei: neu gemessen, keine Grenze', () => {
    const { c, kamera } = aufbau();
    c.target.set(0, 0, 5);   // Drehpunkt über dem Würfel
    kamera.position.set(0, -10, 5);
    c.feuer('start');
    expect(c.minDistance).toBe(0);
  });

  it('ohne Mesh oder mit weggeschnittener Fläche: keine Grenze', () => {
    expect(aufbau([]).stopp.abstand).toBe(0);
    expect(aufbau([wuerfel], () => false).stopp.abstand).toBe(0);
  });

  it('Kamera schon näher als die Grenze: wird nicht zurückgeschoben', () => {
    const { c, kamera } = aufbau();
    kamera.position.set(0, -1.05, 0);
    c.feuer('start');
    expect(c.minDistance).toBeLessThanOrEqual(1.05);
  });
});
