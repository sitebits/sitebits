import { expect, it, vi } from 'vitest';
import { BufferGeometry, Float32BufferAttribute, Group, Points, DoubleSide, Mesh, MeshBasicMaterial, PerspectiveCamera, PlaneGeometry, Vector2, Vector3 } from 'three';
import { fotoVorfilter, projiziereFoto, pruefeFoto, sucheFotos } from '../src/fotoZuordnung';
import { fotoTiefenquelle } from '../src/fotoTiefe';
import type { Viewer } from '../src/viewer';
import type { OrientedImage } from '../src/images';
import type { MeshLayerHandle } from '../src/meshLayer';

const bild: OrientedImage = { id: 'F0001', label: 'Test', group: 'wand', pose: { translation: [400000, 5800000, 10], rotationQuat: [1, 0, 0, 0] }, intrinsics: { focalPx: 800, width: 1000, height: 800 }, levels: [512], rev: null, tiles: null };
function quelle() {
  const alt = new PerspectiveCamera();
  const pick = vi.fn<(koordinate: { x: number; y: number }, optionen: unknown) => { point: Vector3 }[]>(() => [{ point: new Vector3(400000, 5800000, 5) }]);
  const view = { camera: alt, width: 600, height: 400 };
  const v = { instance: { view, pickObjectsAt: pick, engine: { getWindowSize: () => ({ width: 600, height: 400 }) } }, cloud: {} } as unknown as Viewer;
  return { alt, pick, view, v };
}
it('Tiefen-Pick nutzt Fotopose und passende Pixel, stellt die laufende Kamera wieder her', () => {
  const { alt, pick, view, v } = quelle();
  pick.mockImplementation(() => {
    expect(view.camera).not.toBe(alt); expect(view.camera.position.toArray()).toEqual(bild.pose.translation);
    expect(view.camera.aspect).toBe(1.25);
    return [{ point: new Vector3(400000, 5800000, 5) }];
  });
  const q = fotoTiefenquelle(v, [], () => true);
  expect(q.tiefe(bild, { x: 500, y: 400, abstand: 10, mmProPx: 12.5 })).toBe(5);
  expect(pick.mock.calls[0]![0]).toMatchObject({ x: 300, y: 200 });
  expect(pick.mock.calls[0]![1]).toMatchObject({ radius: 8, limit: 289 });
  expect(view.camera).toBe(alt);
  pick.mockImplementation(() => { throw Error('GPU'); });
  expect(() => q.punkt(bild, 500, 400)).toThrow('GPU'); expect(view.camera).toBe(alt);
});
it('nimmt das vorderste sichtbare Mesh oder die Wolke; ausgeblendet und Lücke bleiben null', () => {
  const { v, pick } = quelle();
  const mesh = new Mesh(new PlaneGeometry(10, 10), new MeshBasicMaterial({ side: DoubleSide }));
  mesh.position.set(400000, 5800000, 6); mesh.updateMatrixWorld(true);
  const layer = { visible: true, occluderMeshes: [mesh] };
  const q = fotoTiefenquelle(v, [layer as unknown as MeshLayerHandle], () => true);
  expect(q.tiefe(bild, { x: 500, y: 400, abstand: 10, mmProPx: 12.5 })).toBe(4);
  layer.visible = false;
  expect(q.tiefe(bild, { x: 500, y: 400, abstand: 10, mmProPx: 12.5 })).toBe(5);
  pick.mockReturnValue([]); expect(q.punkt(bild, 500, 400)).toBeNull();
  expect(q.punkt(bild, -1, 400)).toBeNull();
  mesh.geometry.dispose(); (mesh.material as MeshBasicMaterial).dispose();
});

it('Projektion und Fotostrahl stimmen abseits der Mitte bei gedrehter UTM-Pose überein', () => {
  const { v, pick, view } = quelle();
  const b = { ...bild, pose: { translation: bild.pose.translation, rotationQuat: [Math.SQRT1_2, Math.SQRT1_2, 0, 0] as [number, number, number, number] } };
  const ziel = new Vector3(400001, 5800010, 11);
  pick.mockImplementation((pixel) => {
    const p = ziel.clone().project(view.camera);
    expect(Math.abs(pixel.x - (p.x + 1) / 2 * 600)).toBeLessThan(1.000001);
    expect(Math.abs(pixel.y - (1 - p.y) / 2 * 400)).toBeLessThan(1.000001);
    expect(p.z).toBeGreaterThan(-1); expect(p.z).toBeLessThan(1);
    expect(view.camera.near).toBe(0.01); expect(view.camera.far).toBe(250);
    return [{ point: ziel }];
  });
  const p = projiziereFoto(b, ziel.toArray())!;
  expect(fotoTiefenquelle(v, [], () => true).tiefe(b, p)).toBeCloseTo(p.abstand);
});
it('fehlender GPU-Treffer bestätigt keine Sichtbarkeit; reales Zwischenmesh verdeckt auch ohne GPU-Treffer', () => {
  const { v, pick } = quelle();
  pick.mockReturnValue([]);
  const k = fotoVorfilter([bild], [[400000, 5800000, 0]])[0]!;
  const wand = new Mesh(new PlaneGeometry(10, 10), new MeshBasicMaterial({ side: DoubleSide }));
  wand.position.set(400000, 5800000, 7); wand.updateMatrixWorld(true);
  const layer = { visible: true, occluderMeshes: [wand] };
  const q = fotoTiefenquelle(v, [layer as unknown as MeshLayerHandle], () => true);
  expect(q.tiefe(bild, k.pixel[0]!)).toBe(3);
  expect(pruefeFoto(k, q.tiefe)).toBeNull();
  layer.visible = false;
  expect(q.tiefe(bild, k.pixel[0]!)).toBeNull();
  expect(pruefeFoto(k, q.tiefe)).toBeNull();
  wand.geometry.dispose(); (wand.material as MeshBasicMaterial).dispose();
});

it('dieselbe geladene Zwischenwand verdeckt bei voller und geteilter Leinwand gleich', () => {
  // Derselbe Wandpunkt liegt bei voller Leinwand außerhalb des alten Radius,
  // nach Teilung innerhalb. Der Zielpunkt dahinter bleibt in beiden Fällen
  // pickbar: ein synthetisches Loch im Punktraster, keine geschlossene Fläche.
  const ergebnisse: (number | null)[] = [];
  for (const [breite, hoehe] of [[1920, 1024], [936, 1024], [480, 1024], [1024, 480], [481, 719]] as const) {
    const { v, view, pick } = quelle();
    v.instance.engine.getWindowSize = () => new Vector2(breite, hoehe);
    const wand = new Vector3(400000.025, 5800000, 7);
    const ziel = new Vector3(400000, 5800000, 5);
    pick.mockImplementation((pixel, optionen) => {
      const { radius } = optionen as { radius: number };
      return [wand, ziel].filter((p) => {
        const ndc = p.clone().project(view.camera);
        return Math.hypot((ndc.x + 1) / 2 * breite - pixel.x, (1 - ndc.y) / 2 * hoehe - pixel.y) <= radius;
      }).map((point) => ({ point }));
    });
    ergebnisse.push(fotoTiefenquelle(v, [], () => true).tiefe(bild, { x: 500, y: 400, abstand: 5, mmProPx: 6.25 }));
  }
  for (const tiefe of ergebnisse) expect(tiefe).toBeCloseTo(Math.hypot(3, 0.025));
});


it('Fotostrahl und Umgebung bleiben abseits der Mitte bei Resize und anderer Fotoauflösung gleich', () => {
  const { v, view, pick, alt } = quelle();
  const b = { ...bild, pose: { translation: bild.pose.translation, rotationQuat: [Math.SQRT1_2, Math.SQRT1_2, 0, 0] as [number, number, number, number] } };
  const ziel = new Vector3(400001, 5800010, 11), daneben = ziel.clone().add(new Vector3(0.01, 0, 0.01));
  const abstaende: number[] = [];
  for (const [w, h, faktor] of [[1920, 1024, 1], [936, 1024, 1], [481, 719, 4]] as const) {
    v.instance.engine.getWindowSize = () => new Vector2(w, h);
    const foto = { ...b, intrinsics: { width: 1000 * faktor, height: 800 * faktor, focalPx: 800 * faktor } };
    pick.mockImplementation((pixel) => {
      const mitte = ziel.clone().project(view.camera), rand = daneben.clone().project(view.camera);
      expect((mitte.x + 1) / 2 * w).toBeCloseTo(pixel.x, 5);
      expect((1 - mitte.y) / 2 * h).toBeCloseTo(pixel.y, 5);
      abstaende.push(Math.hypot((rand.x - mitte.x) / 2 * w, (rand.y - mitte.y) / 2 * h));
      return [{ point: ziel }];
    });
    const wand = new Mesh(new PlaneGeometry(10, 10), new MeshBasicMaterial({ side: DoubleSide }));
    wand.rotation.x = Math.PI / 2; wand.position.set(400000, 5800005, 10); wand.updateMatrixWorld(true);
    const layer = { visible: false, occluderMeshes: [wand] };
    let wolke = true;
    const q = fotoTiefenquelle(v, [layer as unknown as MeshLayerHandle], () => wolke), p = projiziereFoto(foto, ziel.toArray())!;
    expect(q.tiefe(foto, p)).toBeCloseTo(p.abstand);
    // Dieselbe Tiefenquelle nach dem GPU-Pick: der vorige ViewOffset darf
    // weder den nächsten mittigen noch den außermittigen Mesh-Strahl verändern.
    wolke = false; layer.visible = true;
    expect(q.punkt(foto, foto.intrinsics.width / 2, foto.intrinsics.height / 2)?.[1]).toBeCloseTo(5800005);
    expect(q.punkt(foto, p.x, p.y)?.[0]).toBeCloseTo(400000.5);
    expect(view.camera).toBe(alt);
    wand.geometry.dispose(); (wand.material as MeshBasicMaterial).dispose();
  }
  for (const abstand of abstaende) expect(abstand).toBeCloseTo(abstaende[0]!, 5);
});


function wuerfelwand() {
  const { v, pick, view, alt } = quelle();
  const root = new Group();
  root.position.set(400000, 5800000, 30);
  const knoten = (y: number): Points => {
    const position: number[] = [];
    for (let x = 8; x <= 12.001; x += 0.02) for (let z = 0; z <= 1.501; z += 0.02) position.push(x, y, z);
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(position, 3));
    geometry.computeBoundingBox();
    const points = new Points(geometry); root.add(points); return points;
  };
  knoten(9); const hindernis = knoten(11); hindernis.visible = false;
  v.cloud = { object3d: root } as unknown as Viewer['cloud'];
  const bilder: OrientedImage[] = Array.from({ length: 8 }, (_, i) => ({ ...bild, id: `F${String(i + 1).padStart(4, '0')}`,
    pose: { translation: [400010, 5800000 + (i < 5 ? 3 - i : i < 7 ? 14 : 3), 30.75],
      rotationQuat: [Math.SQRT1_2, i < 5 ? Math.SQRT1_2 : -Math.SQRT1_2, 0, 0] },
  }));
  const stichproben = Array.from({ length: 25 }, (_, i): [number, number, number] => [400010 + (i % 5 - 2) * 0.02, 5800009, 30.75 + (Math.floor(i / 5) - 2) * 0.02]);
  return { v, pick, view, alt, root, hindernis, bilder, stichproben };
}
it('CPU-Würfelwand: Polygon zuerst und nach Punkt, unsichtbares Hindernis → genau fünf, ohne GPU', async () => {
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1; });
  try {
    const { v, pick, view, alt, root, hindernis, bilder, stichproben } = wuerfelwand();
    pick.mockImplementation(() => { throw Error('CPU darf keinen GPU-Pick ausführen'); });
    const q = fotoTiefenquelle(v, [], () => true);
    const suche = (punkte: [number, number, number][]) => sucheFotos({ kandidaten: fotoVorfilter(bilder, punkte), tiefe: q.tiefeAsync,
      portion: 16, signal: new AbortController().signal, pause: () => Promise.resolve(), fortschritt: () => {} });
    // Dieselbe Szene unabhängig vom Hauptkamera-Frustum, Leinwand und LOD-Sichtbarkeit.
    for (const vorherPunkt of [false, true]) {
      view.camera.position.set(0, 0, 100); view.camera.updateMatrixWorld();
      v.instance.engine.getWindowSize = () => new Vector2(vorherPunkt ? 936 : 1920, 1024);
      if (vorherPunkt) expect(await suche([stichproben[12]!])).toHaveLength(5);
      const ergebnis = await suche(stichproben);
      expect(ergebnis.map((t) => t.bild.id)).toEqual(['F0001', 'F0002', 'F0003', 'F0004', 'F0005']);
      expect(ergebnis.every((t) => t.sichtbar.length === 25)).toBe(true);
      expect(view.camera).toBe(alt);
    }
    expect(pick).not.toHaveBeenCalled();
    // Gegenprobe: Knoten wirklich entladen statt nur unsichtbar → beide Kameras kommen zurück.
    root.remove(hindernis);
    expect(await suche(stichproben)).toHaveLength(7);
  } finally { vi.unstubAllGlobals(); }
});
it('geladene CPU-Geometrie ohne Strahltreffer nutzt keinen GPU-Ersatz; ausgeblendete Wolke zählt nicht', () => {
  const { v, pick } = quelle();
  const root = new Group(), points = new Points(new BufferGeometry());
  points.geometry.setAttribute('position', new Float32BufferAttribute([1, 0, 7], 3));
  root.position.set(400000, 5800000, 0); root.add(points);
  v.cloud = { object3d: root } as unknown as Viewer['cloud'];
  const p = { x: 500, y: 400, abstand: 10, mmProPx: 12.5 };
  expect(fotoTiefenquelle(v, [], () => true).tiefe(bild, p)).toBeNull();
  points.position.x = -1;
  expect(fotoTiefenquelle(v, [], () => false).tiefe(bild, p)).toBeNull();
  expect(fotoTiefenquelle(v, [], () => true).tiefe(bild, p)).toBe(3);
  expect(pick).not.toHaveBeenCalled();
  points.geometry.setAttribute('position', new Float32BufferAttribute([], 3));
  expect(fotoTiefenquelle(v, [], () => true).tiefe(bild, p)).toBe(5);
  expect(pick).toHaveBeenCalledTimes(1);
});
it('CPU-Tiefe verwendet dieselbe 0,3-m-/2-%-Verdeckungsgrenze wie pruefeFoto', () => {
  const { v } = quelle(); const root = new Group();
  const points = new Points(new BufferGeometry()); root.add(points);
  root.position.set(400000, 5800000, 0); v.cloud = { object3d: root } as unknown as Viewer['cloud'];
  const q = fotoTiefenquelle(v, [], () => true);
  for (const [z, ziel, sichtbar] of [[0.29, 0, true], [0.31, 0, false], [-88.01, -90, true], [-87.99, -90, false]] as const) {
    points.geometry.setAttribute('position', new Float32BufferAttribute([0, 0, z], 3)); points.geometry.boundingBox = null;
    const k = fotoVorfilter([bild], [[400000, 5800000, ziel]])[0]!;
    expect(pruefeFoto(k, q.tiefe) !== null).toBe(sichtbar);
  }
});
it('CPU-Strahl gibt nach Zeitbudget einen Frame ab und beachtet Abbruch innerhalb eines Knotens', async () => {
  const { v, view, alt, pick } = quelle();
  const root = new Group(), geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(Array.from({ length: 6000 }, () => [0, 0, 7]).flat(), 3));
  root.position.set(400000, 5800000, 0); root.add(new Points(geometry)); v.cloud = { object3d: root } as unknown as Viewer['cloud'];
  const controller = new AbortController();
  let zeit = 0;
  const jetzt = vi.spyOn(performance, 'now').mockImplementation(() => zeit += 5);
  const pause = vi.fn<(cb: FrameRequestCallback) => number>((cb) => {
    expect(view.camera).toBe(alt); controller.abort(); cb(0); return 1;
  });
  vi.stubGlobal('requestAnimationFrame', pause);
  try {
    await expect(fotoTiefenquelle(v, [], () => true).tiefeAsync(bild, { x: 500, y: 400, abstand: 10, mmProPx: 12.5 }, 0, controller.signal)).rejects.toThrow();
    expect(pause).toHaveBeenCalledTimes(1); expect(pick).not.toHaveBeenCalled();
  } finally { jetzt.mockRestore(); vi.unstubAllGlobals(); }
});

it('CPU-Strahl abseits der Mitte mit gedrehter Fotopose wählt auch ein näheres Mesh', () => {
  const { v, pick } = quelle();
  const foto = { ...bild, pose: { translation: bild.pose.translation, rotationQuat: [Math.SQRT1_2, Math.SQRT1_2, 0, 0] as [number, number, number, number] } };
  const ziel: [number, number, number] = [400001, 5800010, 11];
  const root = new Group(), geometry = new BufferGeometry();
  root.position.set(...bild.pose.translation);
  geometry.setAttribute('position', new Float32BufferAttribute([0.5, 5, 0.5], 3)); root.add(new Points(geometry));
  v.cloud = { object3d: root } as unknown as Viewer['cloud'];
  const wand = new Mesh(new PlaneGeometry(10, 10), new MeshBasicMaterial({ side: DoubleSide }));
  wand.rotation.x = Math.PI / 2; wand.position.set(400000, 5800003, 10); wand.updateMatrixWorld(true);
  const layer = { visible: false, occluderMeshes: [wand] };
  const q = fotoTiefenquelle(v, [layer as unknown as MeshLayerHandle], () => true), p = projiziereFoto(foto, ziel)!;
  expect(q.tiefe(foto, p)).toBeCloseTo(p.abstand / 2);
  layer.visible = true;
  expect(q.tiefe(foto, p)).toBeCloseTo(p.abstand * 0.3);
  expect(pick).not.toHaveBeenCalled();
});
