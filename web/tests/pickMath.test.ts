// Reine Picking-Logik: tiefenbewusste Kandidatenwahl, Nachlade-Box, Octree-Auswahl,
// nächster Originalpunkt am Strahl. Ohne Giro3D/three — siehe constraints.md.
import { describe, expect, it } from 'vitest';
import {
  bewerteTreffer,
  mitteTiefen,
  ohneDurchscheinen,
  suchfenster,
  tiefenFensterGueltig,
  boundedCoarse,
  chooseFrontSurface,
  coarseFallback,
  coarseWithinBound,
  collectNodes,
  depthAlong,
  findSurfaceAlongRay,
  frontCluster,
  pixelFootprintM,
  pixelOffset,
  pointsNearRay,
  rayBoxExit,
  rayDistance,
  refineBox,
  schichten,
  surfaceClusters,
  surfaceGapM,
} from '../src/pickMath';
import type { Box, Ray, Vec3 } from '../src/pickMath';

/** Strahl vom Ursprung entlang +X. */
const RAY: Ray = { origin: [0, 0, 0], dir: [1, 0, 0] };
const id = (p: Vec3): Vec3 => p;

describe('depthAlong / rayDistance', () => {
  it('Tiefe entlang des Strahls und senkrechter Abstand', () => {
    expect(depthAlong(RAY, [2, 3, 4])).toBeCloseTo(2);
    expect(rayDistance(RAY, [2, 3, 4])).toBeCloseTo(5);
  });
  it('Punkt hinter dem Ursprung hat negative Tiefe', () => {
    expect(depthAlong(RAY, [-1, 0, 0])).toBeCloseTo(-1);
  });
});

describe('surfaceGapM', () => {
  it('mindestens 5 cm, ab 2,5 m Tiefe 2 % der Tiefe', () => {
    expect(surfaceGapM(1)).toBeCloseTo(0.05);
    expect(surfaceGapM(2.5)).toBeCloseTo(0.05);
    expect(surfaceGapM(10)).toBeCloseTo(0.2);
  });
});

describe('chooseFrontSurface', () => {
  it('leere Kandidatenliste → null', () => {
    expect(chooseFrontSurface([], id, RAY)).toBeNull();
  });

  it('Vordergrundpunkt schlägt Hintergrundpunkt mit kleinerem Strahlabstand', () => {
    const wall: Vec3 = [1.5, 0.004, 0]; // 4 mm neben dem Strahl, auf der Wand
    const behind: Vec3 = [31.2, 0.0001, 0]; // 0,1 mm neben dem Strahl, 30 m dahinter
    expect(chooseFrontSurface([behind, wall], id, RAY)).toEqual(wall);
  });

  it('innerhalb der vordersten Fläche gewinnt der kleinste Strahlabstand', () => {
    const a: Vec3 = [1.5, 0.004, 0];
    const b: Vec3 = [1.51, 0.001, 0]; // 1 cm tiefer (< 5 cm) → selbe Fläche, näher am Strahl
    const bg: Vec3 = [20, 0, 0];
    expect(chooseFrontSurface([a, bg, b], id, RAY)).toEqual(b);
  });

  it('schräge Fläche: viele kleine Tiefenschritte bleiben eine Fläche, auch über 5 cm Gesamtspanne', () => {
    // Boden unter flachem Winkel: Tiefen 3,00 … 3,30 m in 3-cm-Schritten; der strahlnächste liegt hinten.
    const pts: Vec3[] = Array.from({ length: 11 }, (_, i) => [3 + i * 0.03, 0.01 - i * 0.0009, 0] as Vec3);
    expect(chooseFrontSurface(pts, id, RAY)).toEqual(pts[10]);
  });

  it('relativer Schwellwert: auf 10 m trennen 15 cm noch nicht (2 % = 20 cm)', () => {
    const near: Vec3 = [10, 0.01, 0];
    const far: Vec3 = [10.15, 0.001, 0];
    expect(chooseFrontSurface([near, far], id, RAY)).toEqual(far);
  });

  it('Punkte hinter der Kamera (Tiefe ≤ 0) zählen nicht', () => {
    const back: Vec3 = [-1, 0, 0];
    const wall: Vec3 = [2, 0.002, 0];
    expect(chooseFrontSurface([back, wall], id, RAY)).toEqual(wall);
    expect(chooseFrontSurface([back], id, RAY)).toBeNull();
  });

  describe('Regel für einzelne Streupunkte vor der Fläche (minClusterSize)', () => {
    const stray: Vec3 = [1.2, 0.0005, 0]; // 30 cm vor der Wand, genau auf dem Strahl
    const wall: Vec3[] = [
      [1.5, 0.003, 0],
      [1.501, 0.002, 0],
      [1.502, 0.004, 0],
    ];

    it('minClusterSize 2 (feine Stufe): Einzelpunkt vor der Wand gilt als Rauschen, Wand gewinnt', () => {
      expect(chooseFrontSurface([stray, ...wall], id, RAY, { minClusterSize: 2 })).toEqual(wall[1]);
    });

    it('minClusterSize 1 (grobe Stufe, Standard): Einzelpunkt ist die vorderste Fläche', () => {
      // Aus der Nähe ist die grob angezeigte Wand lückig — dort kann ein echter Wandtreffer allein
      // stehen. Die grobe Stufe verwirft deshalb nichts; die feine Stufe entscheidet (s. o.).
      expect(chooseFrontSurface([stray, ...wall], id, RAY)).toEqual(stray);
    });

    it('Einzelpunkt ohne Fläche dahinter bleibt der Treffer (keine Messung ohne Punkt heißt nicht: keinen nehmen)', () => {
      expect(chooseFrontSurface([stray], id, RAY, { minClusterSize: 2 })).toEqual(stray);
    });

    it('nur Einzelpunkte: der vorderste gewinnt', () => {
      const s2: Vec3 = [5, 0, 0];
      expect(chooseFrontSurface([s2, stray], id, RAY, { minClusterSize: 2 })).toEqual(stray);
    });
  });

  it('frontCluster liefert die Elemente der vordersten Fläche, nach Tiefe sortiert', () => {
    const a: Vec3 = [1.52, 0, 0];
    const b: Vec3 = [1.5, 0, 0];
    const c: Vec3 = [9, 0, 0];
    expect(frontCluster([c, a, b], id, RAY)).toEqual([b, a]);
  });

  it('Kandidaten beliebigen Typs über den Positions-Zugriff', () => {
    const items = [
      { name: 'hinten', p: [30, 0, 0] as Vec3 },
      { name: 'vorn', p: [1.5, 0.003, 0] as Vec3 },
    ];
    expect(chooseFrontSurface(items, (i) => i.p, RAY)?.name).toBe('vorn');
  });
});

describe('pixelFootprintM', () => {
  it('90° vertikales Sichtfeld, 1000 px hoch: 2 mm je Pixel auf 1 m', () => {
    expect(pixelFootprintM(1, 90, 1000)).toBeCloseTo(0.002);
    expect(pixelFootprintM(3, 90, 1000)).toBeCloseTo(0.006);
  });
});

describe('refineBox', () => {
  it('umschließt das Strahlstück [Tiefe − vorn, Tiefe + hinten], um den Radius verdickt', () => {
    const box = refineBox(RAY, 2, 0.05, 0.2, 0.01);
    expect(box.min[0]).toBeCloseTo(1.95 - 0.01);
    expect(box.min[1]).toBeCloseTo(-0.01);
    expect(box.min[2]).toBeCloseTo(-0.01);
    expect(box.max[0]).toBeCloseTo(2.2 + 0.01);
    expect(box.max[1]).toBeCloseTo(0.01);
  });
  it('schräger Strahl: Box enthält beide Enden des Strahlstücks', () => {
    const s = Math.SQRT1_2;
    const ray: Ray = { origin: [10, 10, 0], dir: [s, s, 0] };
    const box = refineBox(ray, 1, 0.1, 0.1, 0.005);
    for (const d of [0.9, 1.1]) {
      const p = [10 + s * d, 10 + s * d, 0];
      for (let i = 0; i < 3; i++) {
        expect(p[i]!).toBeGreaterThanOrEqual(box.min[i]!);
        expect(p[i]!).toBeLessThanOrEqual(box.max[i]!);
      }
    }
  });
});

interface N {
  name: string;
  vol: Box;
  kids?: (N | undefined)[];
}
const box = (min: Vec3, max: Vec3): Box => ({ min, max });

describe('collectNodes', () => {
  // Wurzel 0…8, zwei Kinder 0…4 / 4…8 (x), das linke hat ein Enkelkind 0…2.
  const grand: N = { name: 'g', vol: box([0, 0, 0], [2, 8, 8]) };
  const left: N = { name: 'l', vol: box([0, 0, 0], [4, 8, 8]), kids: [grand, undefined] };
  const right: N = { name: 'r', vol: box([4, 0, 0], [8, 8, 8]) };
  const root: N = { name: 'root', vol: box([0, 0, 0], [8, 8, 8]), kids: [left, right] };
  const names = (q: Box): string[] =>
    collectNodes(root, q, (n) => n.vol, (n) => n.kids).map((n) => n.name).sort();

  it('Vorfahren und schneidende Nachfahren auf allen Stufen (COPC verteilt Punkte ohne Dopplung)', () => {
    expect(names(box([1, 1, 1], [1.5, 1.5, 1.5]))).toEqual(['g', 'l', 'root']);
  });
  it('Teilbäume außerhalb der Box werden nicht betreten', () => {
    expect(names(box([5, 1, 1], [6, 2, 2]))).toEqual(['r', 'root']);
  });
  it('Box auf einer Knotengrenze nimmt beide Seiten', () => {
    expect(names(box([3.9, 1, 1], [4.1, 2, 2]))).toEqual(['l', 'r', 'root']);
  });
  it('Box außerhalb der Wurzel → leer', () => {
    expect(names(box([20, 20, 20], [21, 21, 21]))).toEqual([]);
  });
});

describe('pointsNearRay', () => {
  it('Knotenpuffer relativ zum Knotenursprung, gefiltert nach Radius und Tiefenfenster', () => {
    const chunks = [
      {
        origin: [100, 0, 0] as Vec3,
        // Welt: (101, 0.002, 0) im Radius; (101, 0.05, 0) zu weit seitlich; (90, 0, 0) außerhalb Tiefe
        positions: new Float32Array([1, 0.002, 0, 1, 0.05, 0, -10, 0, 0]),
      },
      { origin: [101.5, 0, 0] as Vec3, positions: new Float32Array([0, -0.001, 0]) },
    ];
    const pts = pointsNearRay(chunks, RAY, 0.01, 100, 102);
    expect(pts).toHaveLength(2);
    expect(pts[0]![0]).toBeCloseTo(101);
    expect(pts[0]![1]).toBeCloseTo(0.002);
    expect(pts[1]![0]).toBeCloseTo(101.5);
  });
  it('accept-Prädikat (Schnittbox) verwirft Punkte', () => {
    const chunks = [{ origin: [0, 0, 0] as Vec3, positions: new Float32Array([1, 0, 0, 2, 0, 0]) }];
    expect(pointsNearRay(chunks, RAY, 0.01, 0, 5, (p) => p[0] > 1.5)).toEqual([[2, 0, 0]]);
  });
  it('Weltbezug in Float64: großer Ursprung + kleiner lokaler Versatz bleibt mm-genau', () => {
    const ray: Ray = { origin: [500000, 5600000, 0], dir: [1, 0, 0] };
    const chunks = [{ origin: [500010, 5600000, 0] as Vec3, positions: new Float32Array([0.0012, 0.001, 0]) }];
    const pts = pointsNearRay(chunks, ray, 0.01, 9, 11);
    expect(pts[0]![0] - 500010).toBeCloseTo(0.0012, 6);
  });
});

describe('surfaceClusters', () => {
  it('alle Flächen nach Tiefe, getrennt an Lücken > max(5 cm, 2 %)', () => {
    const pts: Vec3[] = [[9, 0, 0], [1.5, 0, 0], [1.52, 0, 0], [3, 0, 0]];
    expect(surfaceClusters(pts, id, RAY)).toEqual([[[1.5, 0, 0], [1.52, 0, 0]], [[3, 0, 0]], [[9, 0, 0]]]);
  });
});

describe('pixelOffset', () => {
  it('Strahlabstand in Bildschirmpixeln (k = Pixelkante je Meter Tiefe)', () => {
    expect(pixelOffset(RAY, [2, 0.004, 0], 0.001)).toBeCloseTo(2);
    expect(pixelOffset(RAY, [20, 0.004, 0], 0.001)).toBeCloseTo(0.2);
  });
});

describe('findSurfaceAlongRay (feinste Stufe, abschnittsweise entlang des Strahls)', () => {
  /** Fake-Lader: liefert die Punkte mit Tiefe in [a, b) und zählt die Aufrufe. */
  const loader = (pts: Vec3[]) => {
    const calls: [number, number][] = [];
    const load = (a: number, b: number): Promise<Vec3[]> => {
      calls.push([a, b]);
      return Promise.resolve(pts.filter((p) => depthAlong(RAY, p) >= a && depthAlong(RAY, p) < b));
    };
    return { calls, load };
  };
  const seg = (): number => 0.2;
  const wall = (d: number): Vec3[] => [
    [d, 0.003, 0],
    [d + 0.001, 0.001, 0],
    [d + 0.002, 0.004, 0],
  ];

  it('Fläche im ersten Abschnitt: ein Abschnitt reicht, strahlnächster Wandpunkt', async () => {
    const { calls, load } = loader([...wall(1.5), [19, 0, 0]]);
    const r = await findSurfaceAlongRay(RAY, 1.45, 19.5, load, { segmentM: seg, minClusterSize: 2 });
    expect(r.point).toEqual([1.501, 0.001, 0]);
    expect(calls).toHaveLength(1);
    expect(r.segments).toBe(1);
  });

  it('Wand hinter Lücke in der Anzeige: Suche beginnt vorn und stoppt an der Wand, nicht am Hintergrund', async () => {
    const { calls, load } = loader([...wall(2.0), [19.2, 0, 0], [19.21, 0, 0]]);
    const r = await findSurfaceAlongRay(RAY, 1.45, 19.5, load, { segmentM: seg, minClusterSize: 2 });
    expect(r.point).toEqual([2.001, 0.001, 0]);
    expect(calls.length).toBeLessThanOrEqual(4);
  });

  it('Fläche über einer Abschnittsgrenze: nächster Abschnitt wird noch gelesen', async () => {
    // Abschnitt [1.5, 1.7) sieht nur 1.69 + 1.695; 1.72 (strahlnächst) liegt im nächsten.
    const pts: Vec3[] = [[1.69, 0.004, 0], [1.695, 0.003, 0], [1.72, 0.0005, 0]];
    const { calls, load } = loader(pts);
    const r = await findSurfaceAlongRay(RAY, 1.5, 5, load, { segmentM: seg, minClusterSize: 2 });
    expect(r.point).toEqual([1.72, 0.0005, 0]);
    expect(calls).toHaveLength(2);
  });

  it('Streupunkt vor der Wand wird übersprungen, Suche läuft weiter bis zur Wand', async () => {
    const { load } = loader([[1.5, 0, 0], ...wall(1.9)]);
    const r = await findSurfaceAlongRay(RAY, 1.45, 5, load, { segmentM: seg, minClusterSize: 2 });
    expect(r.point).toEqual(wall(1.9)[1]);
  });

  it('echtes Loch in der Wand: Suche erreicht den Hintergrund', async () => {
    const { load } = loader([[19.2, 0.01, 0], [19.25, 0.002, 0]]);
    const r = await findSurfaceAlongRay(RAY, 1.45, 19.5, load, { minClusterSize: 2 });
    expect(r.point).toEqual([19.25, 0.002, 0]);
  });

  it('nur ein Einzelpunkt im ganzen Fenster: er bleibt der Treffer', async () => {
    const { load } = loader([[3, 0.001, 0]]);
    const r = await findSurfaceAlongRay(RAY, 1.45, 5, load, { segmentM: seg, minClusterSize: 2 });
    expect(r.point).toEqual([3, 0.001, 0]);
  });

  it('nichts im Fenster → null', async () => {
    const { load } = loader([]);
    const r = await findSurfaceAlongRay(RAY, 1, 2, load, { segmentM: seg });
    expect(r.point).toBeNull();
    expect(r.stopped).toBe(false);
  });

  it('Frist abgelaufen: keine weiteren Abschnitte, stopped = true', async () => {
    const { calls, load } = loader([[19.2, 0, 0], [19.21, 0, 0]]);
    let n = 0;
    const r = await findSurfaceAlongRay(RAY, 1.45, 19.5, load, { segmentM: seg, shouldStop: () => ++n > 3 });
    expect(r.stopped).toBe(true);
    expect(r.point).toBeNull();
    expect(calls).toHaveLength(3);
  });
});

describe('coarseFallback (grober Ersatz bei Zeitbudget/Fehler)', () => {
  // k = 1 mm je Pixel und Meter Tiefe; Cursor-Radius 4 px.
  const K = 0.001;
  it('Türrahmen neben einer Öffnung: vorderste Fläche der 16-px-Umgebung liegt 12 px daneben → Punkt unter dem Cursor', () => {
    const frame: Vec3 = [1.5, 0.018, 0]; // 12 px neben dem Strahl, 1,5 m (Rahmen)
    const behind: Vec3 = [6, 0.006, 0]; // 1 px neben dem Strahl, 6 m (durch die Öffnung)
    const wide = [frame, behind];
    const near = wide.filter((p) => pixelOffset(RAY, p, K) <= 4);
    expect(near).toEqual([behind]);
    expect(coarseFallback(wide, near, id, RAY, K, 4)).toEqual(behind);
  });
  it('vorderste Fläche liegt selbst unter dem Cursor → deren strahlnächster Punkt', () => {
    const wall: Vec3 = [1.5, 0.003, 0]; // 2 px
    const bg: Vec3 = [19, 0.001, 0];
    const wide = [wall, bg, [1.51, 0.02, 0] as Vec3];
    expect(coarseFallback(wide, [wall, bg], id, RAY, K, 4)).toEqual(wall);
  });
  it('nichts unter dem Cursor → null', () => {
    expect(coarseFallback([[1.5, 0.02, 0] as Vec3], [], id, RAY, K, 4)).toBeNull();
  });
});

describe('coarseWithinBound (grober Ersatz im Panorama nur bis 10 mm Fehlerschranke)', () => {
  // k = 1 mm je Pixel und Meter Tiefe; Cursor-Radius 4 px → Schranke 4 mm je Meter Tiefe.
  const K = 0.001;
  it('2 m Tiefe: 8 mm ≤ 10 mm → Ersatz gilt', () => {
    expect(coarseWithinBound(RAY, [2, 0.002, 0], K, 4, 0.01)).toBe(true);
  });
  it('genau auf der Schranke (2,5 m → 10 mm) → Ersatz gilt', () => {
    expect(coarseWithinBound(RAY, [2.5, 0, 0], K, 4, 0.01)).toBe(true);
  });
  it('10 m Tiefe: 40 mm > 10 mm → Ersatz verworfen', () => {
    expect(coarseWithinBound(RAY, [10, 0.001, 0], K, 4, 0.01)).toBe(false);
  });
  it('Schranke hängt an der Tiefe entlang des Strahls, nicht am Abstand zum Ursprung', () => {
    // 2 m Tiefe, aber 3 m seitlich: Tiefe zählt (8 mm), nicht die Luftlinie.
    expect(coarseWithinBound(RAY, [2, 3, 0], K, 4, 0.01)).toBe(true);
  });
});

describe('boundedCoarse (Schranke im Panorama für jeden groben Ersatz)', () => {
  const K = 0.001; // 4 px · 1 mm/px/m → 4 mm je Meter Tiefe
  const far: Vec3 = [10, 0.001, 0]; // 40 mm Schranke
  const close: Vec3 = [2, 0.001, 0]; // 8 mm Schranke
  it.each(['timeout', 'error', 'empty'] as const)('%s: über der Schranke verworfen, Ursache gemeldet', (why) => {
    expect(boundedCoarse(far, why, RAY, K, 4, 0.01)).toEqual({ point: null, rejected: why });
  });
  it.each(['timeout', 'error', 'empty'] as const)('%s: unter der Schranke bleibt der Ersatz', (why) => {
    expect(boundedCoarse(close, why, RAY, K, 4, 0.01)).toEqual({ point: close, rejected: null });
  });
  it('ohne Schranke (Orbit) bleibt auch ein ferner Ersatz — Inventar Punkt 4 unverändert', () => {
    expect(boundedCoarse(far, 'empty', RAY, K, 4)).toEqual({ point: far, rejected: null });
  });
  it('kein Ersatz → nichts zu verwerfen', () => {
    expect(boundedCoarse(null, 'timeout', RAY, K, 4, 0.01)).toEqual({ point: null, rejected: null });
  });
});

describe('pointsNearRay mit Scanfilter', () => {
  const chunk = { origin: [0, 0, 0] as Vec3, positions: new Float32Array([1, 0, 0, 2, 0, 0, 3, 0, 0]), ids: new Uint16Array([1, 2, 1]) };
  it('nur Punkte mit der PointSourceId der Station', () => {
    expect(pointsNearRay([chunk], RAY, 0.01, 0, 5, undefined, 1)).toEqual([
      [1, 0, 0],
      [3, 0, 0],
    ]);
  });
  it('Knoten ohne IDs zählt bei aktivem Filter nicht (nie ein ungeprüfter Punkt)', () => {
    expect(pointsNearRay([{ origin: chunk.origin, positions: chunk.positions }], RAY, 0.01, 0, 5, undefined, 1)).toEqual([]);
  });
  it('ohne Filter bleiben alle Punkte (Orbit, Altpaket)', () => {
    expect(pointsNearRay([chunk], RAY, 0.01, 0, 5)).toHaveLength(3);
  });
});

describe('rayBoxExit', () => {
  const box: Box = { min: [-1, -1, -1], max: [10, 1, 1] };
  it('Tiefe, in der der Strahl die Box verlässt', () => {
    expect(rayBoxExit(RAY, box)).toBeCloseTo(10);
    expect(rayBoxExit({ origin: [0, 0, 0], dir: [0, 1, 0] }, box)).toBeCloseTo(1);
  });
  it('Strahl verfehlt die Box → 0', () => {
    expect(rayBoxExit({ origin: [0, 5, 0], dir: [1, 0, 0] }, box)).toBe(0);
  });
});

describe('schichten', () => {
  it('trennt an Tiefenlücken, sortiert vorn nach hinten', () => {
    expect(schichten([8.01, 5, 5.02, 8])).toEqual([
      { min: 5, max: 5.02 },
      { min: 8, max: 8.01 },
    ]);
  });
  it('leer, eine Fläche, schräge Fläche in kleinen Schritten', () => {
    expect(schichten([])).toEqual([]);
    expect(schichten([3])).toEqual([{ min: 3, max: 3 }]);
    expect(schichten([1, 1.04, 1.08, 1.12])).toHaveLength(1);
  });
});

describe('bewerteTreffer', () => {
  const wand = Array.from({ length: 40 }, (_, i) => 5 + i * 0.001);
  const hinten = Array.from({ length: 20 }, (_, i) => 8 + i * 0.001);
  it('Himmel/Rand: kein Treffer', () => {
    expect(bewerteTreffer({ trefferTiefe: null, level: 'none', mitteTiefen: [], fensterTiefen: [] }).art).toBe('kein-treffer');
    expect(bewerteTreffer({ trefferTiefe: 5, level: 'fine', mitteTiefen: [], fensterTiefen: wand }).art).toBe('kein-treffer');
    expect(bewerteTreffer({ trefferTiefe: 5, level: 'none', mitteTiefen: [5], fensterTiefen: wand }).art).toBe('kein-treffer');
  });
  it('Wand mitten im Bild: eindeutig', () => {
    expect(bewerteTreffer({ trefferTiefe: 5.01, level: 'fine', mitteTiefen: [5, 5.02], fensterTiefen: wand }).art).toBe('eindeutig');
  });
  it('verdeckte Fläche hinter der Wand: nie', () => {
    expect(bewerteTreffer({ trefferTiefe: 9, level: 'fine', mitteTiefen: [5], fensterTiefen: wand }).art).toBe('verdeckt');
  });
  it('durch die Öffnung sichtbar: nur mit Rückfrage', () => {
    const b = bewerteTreffer({ trefferTiefe: 8.01, level: 'fine', mitteTiefen: [8, 8.02], fensterTiefen: [...wand, ...hinten] });
    expect(b).toMatchObject({ art: 'rueckfrage', grund: 'zwei-schichten', trefferSchicht: 'hinten' });
  });
  it('Geländer vor Wand (vorn getroffen, hinten im Fenster): Rückfrage', () => {
    const b = bewerteTreffer({ trefferTiefe: 5.01, level: 'fine', mitteTiefen: [5], fensterTiefen: [...wand, ...hinten] });
    expect(b).toMatchObject({ art: 'rueckfrage', trefferSchicht: 'vorn' });
  });
  it('grobe Stufe: Rückfrage', () => {
    expect(bewerteTreffer({ trefferTiefe: 5, level: 'coarse', mitteTiefen: [5], fensterTiefen: wand })).toMatchObject({ art: 'rueckfrage', grund: 'grobe-stufe' });
  });
  it('Toleranz = surfaceGapM: 4 cm hinter der Mitte bei 2 m ist noch dieselbe Fläche', () => {
    expect(bewerteTreffer({ trefferTiefe: 2.04, level: 'fine', mitteTiefen: [2], fensterTiefen: [2, 2.04] }).art).toBe('eindeutig');
    expect(bewerteTreffer({ trefferTiefe: 2.06, level: 'fine', mitteTiefen: [2], fensterTiefen: [2] }).art).toBe('verdeckt');
  });
  it('Treffertiefe nicht endlich oder nicht positiv: kein Treffer', () => {
    for (const t of [Number.NaN, Number.POSITIVE_INFINITY, 0, -1]) {
      expect(bewerteTreffer({ trefferTiefe: t, level: 'fine', mitteTiefen: [5], fensterTiefen: wand }).art).toBe('kein-treffer');
    }
  });
  it('Treffer in der Lücke zwischen zwei Schichten: Rückfrage, Treffer gilt als hinten', () => {
    const b = bewerteTreffer({ trefferTiefe: 6.5, level: 'fine', mitteTiefen: [6.5], fensterTiefen: [...wand, ...hinten] });
    expect(b).toMatchObject({ art: 'rueckfrage', grund: 'zwei-schichten', trefferSchicht: 'hinten' });
    if (b.art !== 'rueckfrage') throw new Error('Rückfrage erwartet');
    expect(b.vorn.min).toBeCloseTo(5);
    expect(b.hinten.min).toBeCloseTo(8);
  });
});

describe('tiefenFensterGueltig', () => {
  it('endlich, min ≥ 0, min < max', () => {
    expect(tiefenFensterGueltig({ min: 0, max: 1 })).toBe(true);
    expect(tiefenFensterGueltig({ min: 4.9, max: 5.1 })).toBe(true);
  });
  it('ungültige Fenster', () => {
    expect(tiefenFensterGueltig({ min: -0.1, max: 1 })).toBe(false);
    expect(tiefenFensterGueltig({ min: 2, max: 2 })).toBe(false);
    expect(tiefenFensterGueltig({ min: 3, max: 2 })).toBe(false);
    expect(tiefenFensterGueltig({ min: Number.NaN, max: 2 })).toBe(false);
    expect(tiefenFensterGueltig({ min: 1, max: Number.POSITIVE_INFINITY })).toBe(false);
  });
});

describe('mitteTiefen', () => {
  it('Treffer bis zum Mittenradius zählen', () => {
    expect(mitteTiefen([{ tiefe: 5, px: 1 }, { tiefe: 5.1, px: 2 }, { tiefe: 9, px: 6 }], 2)).toEqual([5, 5.1]);
  });
  it('große Punkte: keiner im Mittenradius → die Gruppe mit dem kleinsten Pixelabstand (+1 px)', () => {
    expect(mitteTiefen([{ tiefe: 9, px: 7 }, { tiefe: 5, px: 3.2 }, { tiefe: 5.2, px: 4.1 }, { tiefe: 6, px: 4.3 }], 2)).toEqual([5, 5.2]);
  });
  it('ohne Treffer leer', () => {
    expect(mitteTiefen([], 2)).toEqual([]);
  });
});

describe('ohneDurchscheinen', () => {
  // Raster mit 2 px Abstand um die Mitte; `tiefe` je Feld.
  const raster = (tiefe: (x: number, y: number) => number | null) => {
    const out: { tiefe: number; x: number; y: number }[] = [];
    for (let x = -8; x <= 8; x += 2) for (let y = -8; y <= 8; y += 2) {
      const t = tiefe(x, y);
      if (t !== null) out.push({ tiefe: t, x, y });
    }
    return out;
  };
  it('Wand mit Lücken der Darstellung: die Fläche dahinter scheint nur durch und fällt weg', () => {
    // Jedes dritte Feld zeigt die verdeckte Fläche (9 m), alle übrigen die Wand (5 m).
    let i = 0;
    const e = raster(() => (i++ % 3 === 0 ? 9 : 5));
    const r = ohneDurchscheinen(e);
    expect(r.every((h) => h.tiefe === 5)).toBe(true);
    expect(r.length).toBe(e.filter((h) => h.tiefe === 5).length);
  });
  it('Öffnung: die Fläche dahinter bleibt, nur ihr Saum direkt an der Wand fällt weg', () => {
    // Wand links (x ≤ −4), dahinter durch die Öffnung die Fläche bei 8 m.
    const e = raster((x) => (x <= -4 ? 5 : 8));
    const r = ohneDurchscheinen(e);
    expect(r.filter((h) => h.tiefe === 5).length).toBe(e.filter((h) => h.tiefe === 5).length);
    const hinten = r.filter((h) => h.tiefe === 8);
    expect(hinten.length).toBeGreaterThan(0);
    expect(Math.min(...hinten.map((h) => h.x))).toBeGreaterThan(-2);
  });
  it('vereinzelte Treffer weit dahinter (durch ein Fenster, Streupunkte) zählen nicht als Fläche', () => {
    const e = [...raster((x) => (x <= -4 ? 5 : null)), { tiefe: 23, x: 8, y: 8 }, { tiefe: 23.01, x: 6, y: 8 }];
    expect(ohneDurchscheinen(e).every((h) => h.tiefe === 5)).toBe(true);
  });
  it('eine Fläche allein bleibt unverändert, ebenso ein leeres Fenster', () => {
    const e = raster(() => 5);
    expect(ohneDurchscheinen(e)).toEqual(e);
    expect(ohneDurchscheinen([])).toEqual([]);
  });
  it('Geländer vor der Wand: die Wand bleibt neben dem Geländer sichtbar', () => {
    const e = raster((x) => (x === 0 ? 2 : 5));
    const r = ohneDurchscheinen(e);
    expect(r.some((h) => h.tiefe === 5)).toBe(true);
    expect(r.filter((h) => h.tiefe === 2).length).toBe(9);
  });
  // Ganze Kette wie in der Vorschau: Filter, Mitte, Bewertung eines Feintreffers.
  const bewerte = (e: { tiefe: number; x: number; y: number }[], treffer: number) => {
    const f = ohneDurchscheinen(e);
    return bewerteTreffer({
      trefferTiefe: treffer,
      level: 'fine',
      mitteTiefen: mitteTiefen(f.map((h) => ({ tiefe: h.tiefe, px: Math.hypot(h.x, h.y) })), 2),
      fensterTiefen: f.map((h) => h.tiefe),
    });
  };
  it('zwei Streupunkte vor der Wand unterdrücken die Wand nicht', () => {
    const wand = raster(() => 5);
    expect(wand).toHaveLength(81);
    const e = [...wand, { tiefe: 2, x: -5, y: 0 }, { tiefe: 2.01, x: 5, y: 0 }];
    const f = ohneDurchscheinen(e);
    expect(f.filter((h) => h.tiefe === 5)).toHaveLength(81);
    expect(bewerte(e, 5.001).art).toBe('rueckfrage');
  });
  it('schmale echte Öffnung (≈ 6 px): die Punkte dahinter bleiben, Treffer hindurch → Rückfrage', () => {
    const e = raster((x) => (Math.abs(x) <= 2 ? 8 : 5));
    const f = ohneDurchscheinen(e);
    expect(f.filter((h) => h.tiefe === 8).length).toBeGreaterThanOrEqual(3);
    expect(bewerte(e, 8.001)).toMatchObject({ art: 'rueckfrage', trefferSchicht: 'hinten' });
  });
});

describe('suchfenster', () => {
  const b = (vorn: [number, number], hinten: [number, number]) =>
    ({ art: 'rueckfrage', grund: 'zwei-schichten', vorn: { min: vorn[0], max: vorn[1] }, hinten: { min: hinten[0], max: hinten[1] }, trefferSchicht: 'hinten' }) as const;
  it('Schicht ± Tiefenlücke, geklemmt auf die Mitte zwischen den Schichten', () => {
    const v = suchfenster(b([5, 5.04], [8, 8.02]), 'vorn');
    expect(v.min).toBeCloseTo(4.9);
    expect(v.max).toBeCloseTo(5.04 + 0.1008);
    const h = suchfenster(b([5, 5.04], [5.2, 5.3]), 'hinten');
    expect(h.min).toBeCloseTo(5.12);
    expect(h.max).toBeCloseTo(5.3 + 0.106);
    expect(suchfenster(b([5, 5.04], [5.1, 5.2]), 'vorn').max).toBeCloseTo(5.07);
  });
  it('Schicht aus einer einzigen Probe: gültiges, verbreitertes Fenster', () => {
    const f = suchfenster(b([5, 5], [8, 8]), 'vorn');
    expect(tiefenFensterGueltig(f)).toBe(true);
    expect(f.min).toBeLessThan(5);
    expect(f.max).toBeGreaterThan(5);
  });
  it('nie unter 0', () => {
    expect(suchfenster(b([0.02, 0.03], [3, 3]), 'vorn').min).toBe(0);
  });
  it('grobe Stufe (eine Fläche): kein Klemmen', () => {
    const g = { art: 'rueckfrage', grund: 'grobe-stufe', vorn: { min: 4.9, max: 5.1 }, hinten: { min: 4.9, max: 5.1 }, trefferSchicht: 'vorn' } as const;
    const f = suchfenster(g, 'hinten');
    expect(f.min).toBeLessThan(4.9);
    expect(f.max).toBeGreaterThan(5.1);
  });
});
