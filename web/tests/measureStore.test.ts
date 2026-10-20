import { describe, expect, it } from 'vitest';
import { MeasureError, MeasureStore, UNDO_LIMIT, distance } from '../src/measureStore';
import type { AreaMeasurement, HeightMeasurement, PolylineMeasurement } from '../src/measureStore';

describe('distance', () => {
  it('rechnet in Float64 auch bei UTM-Koordinaten millimetergenau', () => {
    expect(distance([500000.0, 5800000.0, 30], [500000.9, 5800000.0, 30])).toBeCloseTo(0.9, 9);
  });
});

describe('MeasureStore — Strecke wie Scheibe 1', () => {
  it('fügt hinzu, listet, entfernt; a/b/distanceM bleiben', () => {
    const s = new MeasureStore();
    const m = s.add([0, 0, 0], [3, 4, 0]);
    expect(m).toMatchObject({ kind: 'distance', a: [0, 0, 0], b: [3, 4, 0], distanceM: 5, points: [[0, 0, 0], [3, 4, 0]] });
    expect(s.list()).toHaveLength(1);
    s.remove(m.id);
    expect(s.list()).toHaveLength(0);
  });
  it('JSON: schemaVersion 2, Projekt-Revision, Weltkoordinaten, Strecke mit a/b', () => {
    const s = new MeasureStore();
    s.add([1, 2, 3], [1, 2, 4]);
    s.addGeometry('height', [[0, 0, 0], [0, 0, 1]]);
    const j = s.toJSON({ projectId: 'p', revision: 3 }) as { schemaVersion: number; revision: number; measurements: { kind: string; a?: number[]; points: number[][] }[] };
    expect(j.schemaVersion).toBe(2);
    expect(j.revision).toBe(3);
    expect(j.measurements[0]).toMatchObject({ kind: 'distance', a: [1, 2, 3], points: [[1, 2, 3], [1, 2, 4]] });
    expect(j.measurements[1]!.kind).toBe('height');
  });
});

describe('MeasureStore — Messarten', () => {
  it('Polylinie: Teilstrecken und Summe', () => {
    const p = new MeasureStore().addGeometry('polyline', [[0, 0, 0], [3, 4, 0], [3, 4, 2]]) as PolylineMeasurement;
    expect(p.segmentsM).toEqual([5, 2]);
    expect(p.lengthM).toBe(7);
  });
  it('Höhe/Lot: Δz = zweiter minus erster Punkt, horizontal, schräg', () => {
    const h = new MeasureStore().addGeometry('height', [[1, 1, 1], [1.3, 1.4, -0.5]]) as HeightMeasurement;
    expect(h.dzM).toBeCloseTo(-1.5, 12);
    expect(h.horizontalM).toBeCloseTo(0.5, 12);
    expect(h.slantM).toBeCloseTo(Math.hypot(0.5, 1.5), 12);
  });
  it('Fläche: Werte aus areaMath, Selbstschnitt → MeasureError, Speicher unverändert', () => {
    const s = new MeasureStore();
    const a = s.addGeometry('area', [[0, 0, 0], [4, 0, 0], [4, 2.5, 0], [0, 2.5, 0]]) as AreaMeasurement;
    expect(a.areaM2).toBeCloseTo(10, 9);
    expect(a.perimeterM).toBeCloseTo(13, 9);
    expect(a.nonPlanar).toBe(false);
    expect(() => s.addGeometry('area', [[0, 0, 0], [2, 2, 0], [2, 0, 0], [0, 2, 0]])).toThrow(/schneidet sich selbst/);
    expect(s.list()).toHaveLength(1);
  });
  it('Polylinie: Null-Teilstrecke (Doppelklick auf denselben Punkt) wird abgelehnt', () => {
    const s = new MeasureStore();
    expect(() => s.addGeometry('polyline', [[0, 0, 0], [1, 0, 0], [1, 0, 0.00001]])).toThrow(/fallen zusammen/);
    const p = s.addGeometry('polyline', [[0, 0, 0], [1, 0, 0]]);
    expect(() => s.insertVertex(p.id, 1, [0, 0, 0])).toThrow(MeasureError);
    expect(s.get(p.id)!.points).toHaveLength(2);
  });
  it('Eckpunktzahl je Art wird geprüft', () => {
    const s = new MeasureStore();
    expect(() => s.addGeometry('distance', [[0, 0, 0]])).toThrow(MeasureError);
    expect(() => s.addGeometry('height', [[0, 0, 0], [1, 0, 0], [2, 0, 0]])).toThrow(/2 Eckpunkte/);
    expect(() => s.addGeometry('area', [[0, 0, 0], [1, 0, 0]])).toThrow(/mindestens 3/);
  });
});

describe('MeasureStore — Eckpunkte bearbeiten und Rückgängig', () => {
  const square = (s: MeasureStore): AreaMeasurement =>
    s.addGeometry('area', [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]]) as AreaMeasurement;
  it('verschieben, einfügen, löschen werten neu aus; ID bleibt', () => {
    const s = new MeasureStore();
    const a = square(s);
    expect((s.moveVertex(a.id, 2, [4, 2, 0]) as AreaMeasurement).areaM2).toBeCloseTo(6, 9);
    expect(s.insertVertex(a.id, 2, [3, 1, 0]).points).toHaveLength(5);
    expect(s.removeVertex(a.id, 2).points).toHaveLength(4);
    expect(s.get(a.id)?.id).toBe(a.id);
  });
  it('ungültige Änderung (Selbstschnitt, zu wenige Punkte) ändert nichts und landet nicht im Verlauf', () => {
    const s = new MeasureStore();
    const a = square(s);
    const before = s.list();
    expect(() => s.moveVertex(a.id, 1, [0, 3, 0])).toThrow(/schneidet sich selbst/);
    s.removeVertex(a.id, 0);
    expect(() => s.removeVertex(a.id, 0)).toThrow(/mindestens 3/);
    expect(s.get(a.id)!.points).toHaveLength(3);
    s.undo();
    expect(s.list()).toEqual(before);
  });
  it('Rückgängig über add, move, remove, clear; resetHistory nach dem Laden', () => {
    const s = new MeasureStore();
    s.add([0, 0, 0], [1, 0, 0]);
    s.resetHistory();
    expect(s.canUndo()).toBe(false);
    const a = square(s);
    s.moveVertex(a.id, 0, [-1, 0, 0]);
    s.clear();
    expect(s.list()).toHaveLength(0);
    expect(s.undo()).toBe(true); // clear
    expect(s.list()).toHaveLength(2);
    expect(s.undo()).toBe(true); // move
    expect(s.get(a.id)!.points[0]).toEqual([0, 0, 0]);
    expect(s.undo()).toBe(true); // add
    expect(s.list().map((m) => m.kind)).toEqual(['distance']);
    expect(s.undo()).toBe(false);
  });
  it(`Verlauf ist auf ${UNDO_LIMIT} Schritte begrenzt`, () => {
    const s = new MeasureStore();
    for (let i = 0; i < UNDO_LIMIT + 20; i++) s.add([0, 0, 0], [i + 1, 0, 0]);
    let n = 0;
    while (s.undo()) n++;
    expect(n).toBe(UNDO_LIMIT);
    expect(s.list()).toHaveLength(20);
  });
});
