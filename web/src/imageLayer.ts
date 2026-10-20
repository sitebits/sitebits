// Fotos-Ebene in 3D: je Gruppe eine LineSegments-Geometrie aller Blickpyramiden (eine Draw-Call je
// Gruppe, auch bei 3 000 Kameras), die aktive Kamera als eigene, gelbe Pyramide. Puffer relativ zur
// ersten Kamera (Float32), die Gruppe selbst sitzt Float64 an dieser Stelle (wie mesh.position).
// Treffer per Bildschirmabstand zur Spitze (imageMath.pickNearest) statt Raycast — Linien sind dünn.
// renderOrder 900 wie Standpunkt-Marker; depthTest an, damit Pyramiden hinter Wänden verschwinden.
import { BufferAttribute, BufferGeometry, Group, LineBasicMaterial, LineSegments, Vector3 } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { Vec3 } from './manifest';
import type { ImageSet, OrientedImage } from './images';
import { pickNearest, pyramidSegments } from './imageMath';
import type { ScreenPoint } from './imageMath';

const FARBE = 0xff8a3d;
const FARBE_AKTIV = 0xffcc00;

export interface ImageLayer {
  setVisible(on: boolean): void;
  setGroupVisible(group: string, on: boolean): void;
  groups(): { id: string; title: string; count: number; visible: boolean }[];
  positions(): Vec3[];
  setTiefe(meter: number): void;
  setTreffer(ids: ReadonlySet<string> | null): void;
  setActive(id: string | null): void;
  /** `sichtbar`: optionale Verdeckungsprüfung je Kandidat (imageMath.pickNearest). */
  hitTest(clientX: number, clientY: number, sichtbar?: (p: ScreenPoint) => boolean): string | null;
  image(id: string): OrientedImage | null;
}

export function attachImageLayer(instance: Instance, set: ImageSet, o: { tiefeM?: number }): ImageLayer {
  let tiefe = o.tiefeM ?? 1.5;
  const root = new Group();
  root.name = 'scanview-fotos';
  instance.threeObjects.add(root);
  const origin: Vec3 = set.images[0]?.pose.translation ?? [0, 0, 0];
  const byId = new Map(set.images.map((i) => [i.id, i] as const));
  const linien = (images: readonly OrientedImage[], color: number, name: string): LineSegments => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(pyramidSegments(images, tiefe, origin), 3));
    const l = new LineSegments(g, new LineBasicMaterial({ color, transparent: true, opacity: 0.9 }));
    l.name = name;
    l.position.set(origin[0], origin[1], origin[2]);
    l.renderOrder = 900;
    root.add(l);
    return l;
  };
  const gruppen = new Map<string, LineSegments>();
  for (const g of set.groups) {
    const images = set.images.filter((i) => i.group === g.id);
    if (images.length) gruppen.set(g.id, linien(images, FARBE, `fotos-${g.id}`));
  }
  // Genau ein Objekt für die aktive Kamera; beim Wechsel nur die Geometrie tauschen.
  const aktiv = linien([], FARBE_AKTIV, 'fotos-aktiv');
  aktiv.visible = false;
  aktiv.renderOrder = 901;
  let aktivId: string | null = null;
  let treffer: LineSegments | null = null;
  let trefferIds: ReadonlySet<string> | null = null;
  const geometrie = (l: LineSegments, bilder: readonly OrientedImage[]): void => {
    l.geometry.dispose();
    l.geometry = new BufferGeometry();
    l.geometry.setAttribute('position', new BufferAttribute(pyramidSegments(bilder, tiefe, origin), 3));
  };
  const trefferauswahl = (): void => {
    if (treffer) geometrie(treffer, set.images.filter((i) => trefferIds?.has(i.id) && gruppen.get(i.group)?.visible));
    for (const l of gruppen.values()) (l.material as LineBasicMaterial).opacity = trefferIds ? 0.12 : 0.9;
  };

  const refresh = (): void => {
    root.updateMatrixWorld(true);
    instance.notifyChange(root);
  };
  refresh();

  const p = new Vector3();
  const q = new Vector3();
  return {
    setVisible: (on) => {
      root.visible = on;
      refresh();
    },
    setGroupVisible: (group, on) => {
      const l = gruppen.get(group);
      if (l) l.visible = on;
      trefferauswahl();
      refresh();
    },
    groups: () => set.groups.map((g) => ({ ...g, visible: gruppen.get(g.id)?.visible ?? false })),
    positions: () => set.images.map((i) => [...i.pose.translation] as Vec3),
    setTiefe: (meter) => {
      if (!Number.isFinite(meter)) return;
      tiefe = Math.max(0.1, Math.min(20, meter));
      for (const [id, l] of gruppen) geometrie(l, set.images.filter((i) => i.group === id));
      geometrie(aktiv, aktivId && byId.has(aktivId) ? [byId.get(aktivId)!] : []);
      trefferauswahl();
      refresh();
    },
    setTreffer: (ids) => { if (ids && !treffer) treffer = linien([], FARBE_AKTIV, 'fotos-treffer'); trefferIds = ids; trefferauswahl(); refresh(); },
    setActive: (id) => {
      if (id === aktivId) return;
      aktivId = id;
      const img = id ? byId.get(id) : undefined;
      aktiv.geometry.dispose();
      aktiv.geometry = new BufferGeometry();
      aktiv.geometry.setAttribute('position', new BufferAttribute(pyramidSegments(img ? [img] : [], tiefe, origin), 3));
      aktiv.visible = !!img;
      refresh();
    },
    hitTest: (clientX, clientY, sichtbar) => {
      if (!root.visible) return null;
      const cam = instance.view.camera;
      cam.updateMatrixWorld();
      const r = instance.domElement.getBoundingClientRect();
      const pts: ScreenPoint[] = [];
      for (const img of set.images) {
        if (!gruppen.get(img.group)?.visible) continue;
        const t = img.pose.translation;
        // Vector3.project rechnet in JS-double (nicht in Float32-GPU-Matrizen) — UTM-Beträge sind hier unkritisch.
        p.set(t[0], t[1], t[2]).project(cam);
        pts.push({
          id: img.id,
          x: r.left + ((p.x + 1) / 2) * r.width,
          y: r.top + ((1 - p.y) / 2) * r.height,
          behind: p.z < -1 || p.z > 1,
          tiefeM: cam.position.distanceTo(q.set(t[0], t[1], t[2])),
        });
      }
      return pickNearest(pts, { x: clientX, y: clientY }, undefined, sichtbar);
    },
    image: (id) => byId.get(id) ?? null,
  };
}
