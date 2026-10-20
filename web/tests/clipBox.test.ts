// Reine Geometrie der Schnittbox. Ein Import aus `../src/clipBox` läge nahe, aber die
// bindende Vorgabe (constraints.md) verlangt, dass die reinen Funktionen ohne
// Three.js-/Giro3D-Import auskommen, damit Vitest sie ohne Bundler laden kann. `clipBox.ts` selbst
// braucht drei.js (Plane, TransformControls) und ist deshalb wie `measure.ts`/`viewer.ts` ohne
// Vitest-Test; die reine Geometrie liegt in `clipBoxMath.ts`, hier importiert.
import { describe, expect, it } from 'vitest';
import {
  boxFromCenterScale,
  boxToPlanes,
  centerScaleFromBox,
  centralBox,
  clampScale,
  insideBox,
  MIN_BOX_SIZE_M,
  pointAllowed,
} from '../src/clipBoxMath';

describe('boxToPlanes', () => {
  it('sechs Ebenen, Punkt innen erfüllt alle', () => {
    const planes = boxToPlanes([0, 0, 0], [2, 2, 2]);
    expect(planes).toHaveLength(6);
    const p = [1, 1, 1];
    for (const pl of planes) expect(pl.normal[0] * p[0]! + pl.normal[1] * p[1]! + pl.normal[2] * p[2]! + pl.constant).toBeGreaterThanOrEqual(0);
  });

  it('Punkt knapp außerhalb einer Seite verfehlt genau diese eine Ebene', () => {
    const planes = boxToPlanes([0, 0, 0], [2, 2, 2]);
    // Knapp jenseits der Seite bei x=2 (Ebenenindex 1, Normale (-1,0,0)): nur diese Ebene wird negativ.
    const p = [2.1, 1, 1];
    const values = planes.map((pl) => pl.normal[0] * p[0]! + pl.normal[1] * p[1]! + pl.normal[2] * p[2]! + pl.constant);
    expect(values[1]).toBeLessThan(0);
    expect(values.filter((v) => v < 0)).toHaveLength(1);
  });
});

describe('insideBox', () => {
  it('innen/außen', () => {
    expect(insideBox([1, 1, 1], [0, 0, 0], [2, 2, 2])).toBe(true);
    expect(insideBox([3, 1, 1], [0, 0, 0], [2, 2, 2])).toBe(false);
  });
  it('Grenze zählt als innen (>=/<=)', () => {
    expect(insideBox([0, 0, 0], [0, 0, 0], [2, 2, 2])).toBe(true);
    expect(insideBox([2, 2, 2], [0, 0, 0], [2, 2, 2])).toBe(true);
  });
});

describe('centralBox', () => {
  it('schrumpft auf die mittleren 50 % je Achse', () => {
    const [min, max] = centralBox([
      [0, 0, 0],
      [4, 8, 2],
    ]);
    expect(min).toEqual([1, 2, 0.5]);
    expect(max).toEqual([3, 6, 1.5]);
  });
});

describe('boxFromCenterScale / centerScaleFromBox', () => {
  it('Rundreise Box → Mittelpunkt/Größe → Box', () => {
    const min: [number, number, number] = [-1, 2, 0];
    const max: [number, number, number] = [3, 6, 4];
    const { center, scale } = centerScaleFromBox(min, max);
    expect(center).toEqual([1, 4, 2]);
    expect(scale).toEqual([4, 4, 4]);
    expect(boxFromCenterScale(center, scale)).toEqual([min, max]);
  });

  it('negative Skalierung (Gizmo durch Null gezogen) ergibt trotzdem eine gültige Box', () => {
    // Skalieren-Modus von TransformControls erlaubt negative Werte je Achse.
    expect(boxFromCenterScale([0, 0, 0], [-4, 2, 2])).toEqual([
      [-2, -1, -1],
      [2, 1, 1],
    ]);
  });
});

describe('pointAllowed', () => {
  it('ohne Zustand oder ausgeschaltet: immer erlaubt', () => {
    expect(pointAllowed(undefined, [99, 99, 99])).toBe(true);
    expect(pointAllowed({ enabled: false, box: [[0, 0, 0], [1, 1, 1]] }, [99, 99, 99])).toBe(true);
  });
  it('eingeschaltet: nur innerhalb der Box erlaubt', () => {
    const state = { enabled: true, box: [[0, 0, 0], [1, 1, 1]] as [[number, number, number], [number, number, number]] };
    expect(pointAllowed(state, [0.5, 0.5, 0.5])).toBe(true);
    expect(pointAllowed(state, [2, 0.5, 0.5])).toBe(false);
  });
});

describe('clampScale (D35: Box lässt sich immer wieder aufziehen)', () => {
  it('Mindestgröße 1 cm', () => {
    expect(MIN_BOX_SIZE_M).toBe(0.01);
  });
  it('0 wird zu +Mindestgröße', () => {
    expect(clampScale([0, 0, 0])).toEqual([0.01, 0.01, 0.01]);
  });
  it('winzige Werte werden hochgesetzt, Vorzeichen bleibt', () => {
    expect(clampScale([1e-9, -1e-9, 0.005])).toEqual([0.01, -0.01, 0.01]);
  });
  it('normale und große negative Werte bleiben unverändert', () => {
    expect(clampScale([2.5, -3, 0.01])).toEqual([2.5, -3, 0.01]);
  });
  it('geklemmte Box ist nicht entartet und wächst multiplikativ wieder', () => {
    const s = clampScale([0, 0, 0]);
    const grown = s.map((v) => v * 100) as [number, number, number];
    const [min, max] = boxFromCenterScale([0, 0, 0], grown);
    expect(max[0] - min[0]).toBeCloseTo(1, 12);
  });
});
