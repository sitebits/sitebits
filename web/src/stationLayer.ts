// Standpunkt-Marker in 3D: flache Scheiben am Boden (groundZ, sonst Scannerhöhe),
// türkis = mit Panorama, grau = ohne, gelb = aktiv. Orbit-Modus: alle; Panorama-Modus: nur Nachbarn
// als Bodenmarken (stationMath.neighbours). Treffer per Bildschirmabstand (pickScreenMarker) statt
// Raycast — eine 35-cm-Scheibe in 20 m Entfernung ist nur wenige Pixel groß.
// Im Panorama-Modus stehen die Nachbarn als Bildschirm-Marken fester Pixelgröße (Sprite ohne
// sizeAttenuation) an derselben Stelle: Eine flache Scheibe auf Scannerhöhe (Standpunkt ohne groundZ)
// sähe man vom Nachbarstandpunkt aus genau von der Kante — praktisch unsichtbar.
// depthTest:false + transparent (TRANSPARENT-Eimer nach dem EDL-Pass, siehe measure.ts), renderOrder
// 900: über dem Panorama (500), unter den Messmarken (998/999). Giro3D pflegt matrixWorld selbst →
// nach jeder Änderung updateMatrixWorld(true) vor notifyChange.
import {
  CircleGeometry, DataTexture, Group, Mesh, MeshBasicMaterial, RGBAFormat, Sprite, SpriteMaterial, Vector3,
} from 'three';
import type { Station } from './stations';
import { markerPosition, neighbours, pickScreenMarker } from './stationMath';
import type { ScreenMarker } from './stationMath';
import type { Viewer } from './viewer';

const RADIUS_M = 0.35;
const LIFT_M = 0.02;
/** Größe der Bildschirm-Marke (Anteil der Bildhöhe, sizeAttenuation aus). */
const MARKE_GROESSE = 0.045;

/** Runde Marke mit hellem Rand, ohne DOM/Canvas (läuft auch in Vitest). */
function markenTextur(): DataTexture {
  const n = 64;
  const d = new Uint8Array(n * n * 4);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const r = Math.hypot(x + 0.5 - n / 2, y + 0.5 - n / 2) / (n / 2);
      const i = (y * n + x) * 4;
      const rand = r > 0.72 && r <= 0.95;
      d[i] = d[i + 1] = d[i + 2] = 255;
      d[i + 3] = r <= 0.72 ? 200 : rand ? 255 : 0;
      if (rand) d[i] = d[i + 1] = d[i + 2] = 255;
    }
  }
  const t = new DataTexture(d, n, n, RGBAFormat);
  t.needsUpdate = true;
  return t;
}

export interface StationLayer {
  showAll(): void;
  showNeighboursOf(id: string): void;
  setActive(id: string | null): void;
  setVisible(on: boolean): void;
  visibleIds(): string[];
  hitTest(clientX: number, clientY: number): string | null;
}

export function attachStationLayer(v: Viewer, stations: readonly Station[]): StationLayer {
  const { instance } = v;
  const group = new Group();
  group.name = 'scanview-stations';
  instance.threeObjects.add(group);
  const geom = new CircleGeometry(RADIUS_M, 32); // liegt in der XY-Ebene → flach (Z ist oben)
  const mat = (color: number): MeshBasicMaterial =>
    new MeshBasicMaterial({ color, transparent: true, opacity: 0.85, depthTest: false, depthWrite: false });
  const matPano = mat(0x33d1ff);
  const matNone = mat(0x888888);
  const matActive = mat(0xffcc00);
  const textur = markenTextur();
  const marke = (color: number): SpriteMaterial =>
    new SpriteMaterial({ map: textur, color, sizeAttenuation: false, depthTest: false, depthWrite: false, transparent: true });
  const markePano = marke(0x33d1ff);
  const markeAktiv = marke(0xffcc00);
  const meshes = new Map<string, { mesh: Mesh; sprite: Sprite; station: Station }>();
  for (const s of stations) {
    const mesh = new Mesh(geom, s.panorama ? matPano : matNone);
    const [x, y, z] = markerPosition(s);
    mesh.position.set(x, y, z + LIFT_M);
    mesh.renderOrder = 900;
    mesh.name = `station-${s.id}`;
    group.add(mesh);
    const sprite = new Sprite(markePano);
    sprite.position.set(x, y, z + LIFT_M);
    sprite.scale.set(MARKE_GROESSE, MARKE_GROESSE, 1);
    sprite.renderOrder = 900;
    sprite.name = `station-marke-${s.id}`;
    sprite.visible = false;
    group.add(sprite);
    meshes.set(s.id, { mesh, sprite, station: s });
  }
  let active: string | null = null;

  const refresh = (): void => {
    for (const [id, { mesh, sprite, station }] of meshes) {
      mesh.material = id === active ? matActive : station.panorama ? matPano : matNone;
      sprite.material = id === active ? markeAktiv : markePano;
    }
    group.updateMatrixWorld(true);
    instance.notifyChange(group);
  };
  /** `ids === null`: Orbit, alle als Scheiben; sonst Panorama, nur diese als Bildschirm-Marken. */
  const show = (ids: Set<string> | null): void => {
    for (const [id, { mesh, sprite }] of meshes) {
      mesh.visible = ids === null;
      sprite.visible = ids !== null && ids.has(id);
    }
    refresh();
  };

  // Ohne diesen ersten Durchlauf stünde matrixWorld bis zur ersten Auswahl auf Identität — alle
  // Scheiben lägen unsichtbar im Ursprung.
  refresh();

  const p = new Vector3();
  return {
    showAll: () => show(null),
    showNeighboursOf: (id) => show(new Set(neighbours(stations, id).map((s) => s.id))),
    setActive: (id) => {
      active = id;
      refresh();
    },
    setVisible: (on) => {
      group.visible = on;
      instance.notifyChange(group);
    },
    visibleIds: () => (group.visible ? [...meshes].filter(([, { mesh, sprite }]) => mesh.visible || sprite.visible).map(([id]) => id) : []),
    hitTest: (clientX, clientY) => {
      if (!group.visible) return null;
      const cam = instance.view.camera;
      cam.updateMatrixWorld();
      const r = instance.domElement.getBoundingClientRect();
      const pts: ScreenMarker[] = [];
      for (const [id, { mesh, sprite }] of meshes) {
        if (!mesh.visible && !sprite.visible) continue;
        p.copy(mesh.position).project(cam);
        pts.push({
          id,
          x: r.left + ((p.x + 1) / 2) * r.width,
          y: r.top + ((1 - p.y) / 2) * r.height,
          behind: p.z < -1 || p.z > 1,
        });
      }
      return pickScreenMarker(pts, { x: clientX, y: clientY });
    },
  };
}
