// Ebenen-Register für Punktwolken, Panoramen, glTF-Meshes, Splats und Fotos.
import type { ImagesLayer, Layer, MeshLayer, SplatsLayer, PanoramasLayer, PointcloudLayer, Project, Vec3 } from './manifest';
import type { Station } from './stations';
import type { StationLayer } from './stationLayer';
import type { Viewer } from './viewer';
import { fitDistance } from './viewMath';
import { panelZeilen } from './genauigkeit';

export interface MeshHandle {
  readonly id: string;
  readonly disabled?: boolean;
  readonly shading?: boolean;
  setShading?(on: boolean): void;
  setVisible(on: boolean): void;
  setOpacity(x: number): void;
}

/** Fotos-Ebene (imageLayer.ts): Sichtbarkeit gesamt und je Gruppe. */
export interface ImagesHandle {
  setVisible(on: boolean): void;
  setGroupVisible(group: string, on: boolean): void;
  groups(): { id: string; title: string; count: number; visible: boolean }[];
  positions(): Vec3[];
}

/** Ab so vielen Fotos ist die Ebene zunächst ausgeblendet (Übersicht vor Vollständigkeit). */
export const IMAGES_DEFAULT_MAX = 500;

export interface LayerEntry {
  id: string;
  kind: string;
  title: string;
  supported: boolean;
  visible: boolean;
  opacity: number;
  notes?: string[];
  /** Abschnitt „Genauigkeit“ (F15) bei Punktwolken und Meshes. */
  genauigkeit?: string[];
  shading?: boolean;
  keyNumber?: number;
  groups?: { id: string; title: string; count: number; visible: boolean }[];
}

function isSupported(l: Layer): l is PointcloudLayer | PanoramasLayer | MeshLayer | SplatsLayer | ImagesLayer {
  return !('unsupported' in l) &&
    (l.kind === 'pointcloud' || l.kind === 'panoramas' || l.kind === 'mesh' || l.kind === 'splats' || l.kind === 'images');
}

const defaultOn = (l: Layer): boolean =>
  isSupported(l) && l.startVisible !== false && !(l.kind === 'images' && l.count > IMAGES_DEFAULT_MAX);

/** Voreinstellung (ly= fehlt): alle unterstützten Ebenen sichtbar außer `startVisible: false`, nicht unterstützte nie. Mit ly=
 *  genau die genannten (unbekannte IDs ignoriert, nicht unterstützte auch dann nie sichtbar). */
export function initialVisibility(layers: readonly Layer[], ly: readonly string[] | undefined): Record<string, boolean> {
  // Parameter ly= mit ausschließlich unbekannten IDs (keine passt zu einer vorhandenen Ebene) fällt auf die
  // Voreinstellung zurück, statt alles auszublenden — ein bewusst leeres ly= ("alles aus") bleibt []
  // unverändert (ly.length > 0 schließt das aus).
  const ids = new Set(layers.map((l) => l.id));
  const allUnknown = ly !== undefined && ly.length > 0 && ly.every((id) => !ids.has(id));
  const wanted = ly === undefined || allUnknown ? null : new Set(ly);
  const out: Record<string, boolean> = {};
  for (const l of layers) out[l.id] = wanted === null ? defaultOn(l) : isSupported(l) && wanted.has(l.id);
  return out;
}

/** `undefined`, wenn `visible` genau der Voreinstellung entspricht (dann schreibt hash.ts kein ly=);
 *  sonst die sichtbaren IDs in Ebenen-Reihenfolge (auch `[]`, wenn keine sichtbar ist). */
export function lyParam(layers: readonly Layer[], visible: Record<string, boolean>): string[] | undefined {
  const def = initialVisibility(layers, undefined);
  if (layers.every((l) => (visible[l.id] ?? false) === def[l.id])) return undefined;
  return layers.filter((l) => visible[l.id]).map((l) => l.id);
}

/** 1-basiert, nur unterstützte Ebenen zählen (Tasten 1–9). */
export function layerForKey(layers: readonly Layer[], digit: number): string | null {
  const supported = layers.filter(isSupported);
  return supported[digit - 1]?.id ?? null;
}

export interface LayerRegistry {
  entries(): LayerEntry[];
  setVisible(id: string, on: boolean): void;
  /** Deckkraft räumlicher Ebenen; für andere Ebenenarten ein No-op. */
  setOpacity(id: string, x: number): void;
  setShading(id: string, on: boolean): void;
  /** Gruppe der Fotos-Ebene ein-/ausblenden. */
  setGroupVisible(id: string, group: string, on: boolean): void;
  /** Kamera auf die Ebene rahmen (Punktwolke: ihre boundsWorld; Standpunkte: Vereinigung der
   *  Stations-Positionen). Nicht unterstützte oder unbekannte IDs: No-op. */
  frame(id: string): void;
  isVisible(id: string): boolean;
  onChange(cb: () => void): void;
}

const centerOf = (b: readonly [Vec3, Vec3]): Vec3 => [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2, (b[0][2] + b[1][2]) / 2];

/** Achsenparallele Vereinigung mehrerer Punkte; `null` ohne Punkte (nichts zu rahmen). */
function boundsOfPoints(pts: readonly Vec3[]): [Vec3, Vec3] | null {
  const first = pts[0];
  if (!first) return null;
  const lo: Vec3 = [...first];
  const hi: Vec3 = [...first];
  for (const p of pts) {
    for (let i = 0; i < 3; i++) {
      if (p[i]! < lo[i]!) lo[i] = p[i]!;
      if (p[i]! > hi[i]!) hi[i] = p[i]!;
    }
  }
  return [lo, hi];
}

/** Vertikales Sichtfeld wie viewer.ts (frameAll): 'fov' in camera, sonst Giro3Ds Vorgabe 30°. */
function verticalFov(v: Viewer): number {
  const camera = v.instance.view.camera;
  return 'fov' in camera ? (camera as unknown as { fov: number }).fov : 30;
}

export function createLayerRegistry(o: {
  project: Project;
  viewer: Viewer;
  stationLayer: StationLayer | null;
  initial: Record<string, boolean>;
  /** Für „Einrahmen" der Standpunkte-Ebene (Vereinigung der Stations-Positionen); fehlt/leer → kein Rahmen. */
  stations?: readonly Station[];
  meshes?: readonly MeshHandle[];
  /** Fehlt (images.json 404: für diese Rolle nicht freigegeben) → Fotos-Ebene gesperrt. */
  images?: ImagesHandle | null;
}): LayerRegistry {
  const { project, viewer, stationLayer, initial, stations, meshes = [], images = null } = o;
  const supported = (l: Layer): boolean => isSupported(l) &&
    !((l.kind === 'mesh' || l.kind === 'splats') && meshes.find((m) => m.id === l.id)?.disabled) &&
    !(l.kind === 'images' && (images === null || project.images?.id !== l.id));
  const byId = new Map(project.layers.map((l) => [l.id, l] as const));
  const visible: Record<string, boolean> = {};
  const opacity: Record<string, number> = {};
  const listeners: (() => void)[] = [];

  /** Wirkt den Sichtbarkeitszustand auf die tatsächliche Giro3D-Entität; ist `l` nicht die Ebene, die
   *  der Viewer tatsächlich lädt (project.cloud/project.stations), passiert nichts — die scanview
   *  rendert je Art höchstens eine Ebene (L7). */
  const applyVisible = (l: Layer, on: boolean): void => {
    if (l.kind === 'pointcloud' && project.cloud?.id === l.id) {
      if (!viewer.cloud) return;
      viewer.cloud.visible = on;
      // Ausgeblendet lädt die Punktwolke nichts nach (sonst streamt sie unsichtbar weiter, A.2-Befund).
      viewer.cloud.frozen = !on;
      viewer.instance.notifyChange(viewer.cloud);
    } else if (l.kind === 'panoramas' && project.stations?.id === l.id) {
      stationLayer?.setVisible(on);
    } else if (isSupported(l) && (l.kind === 'mesh' || l.kind === 'splats')) {
      meshes.find((m) => m.id === l.id)?.setVisible(on);
    } else if (isSupported(l) && l.kind === 'images' && project.images?.id === l.id) {
      images?.setVisible(on);
    }
  };

  // Deep-Link/Voreinstellung sofort anwenden — vor dieser Zuweisung sind Wolke und Standpunkte immer
  // sichtbar (Giro3D-/Stationslayer-Vorgabe), unabhängig vom Hash.
  for (const l of project.layers) {
    const on = supported(l) && (initial[l.id] ?? false);
    visible[l.id] = on;
    opacity[l.id] = 1;
    applyVisible(l, on);
  }

  const fireChange = (): void => {
    for (const cb of listeners) cb();
  };

  return {
    entries: () =>
      project.layers.map((l) => ({
        id: l.id,
        kind: l.kind,
        title: l.title,
        supported: supported(l),
        keyNumber: isSupported(l) ? project.layers.filter(isSupported).findIndex((x) => x.id === l.id) + 1 : undefined,
        visible: visible[l.id] ?? false,
        opacity: opacity[l.id] ?? 1,
        ...(!('unsupported' in l) && (l.kind === 'pointcloud' || l.kind === 'mesh')
          ? { genauigkeit: panelZeilen(l.provenance.accuracy) } : {}),
        ...(!('unsupported' in l) && l.kind === 'mesh' ? {
          notes: [!l.provenance.scaleVerified ? 'Modellmaß – Maßstab nicht belegt' : '',
            l.provenance.interpolated ? 'Oberfläche teils interpoliert' : ''].filter(Boolean),
          shading: meshes.find((m) => m.id === l.id)?.shading ?? false,
        } : {}),
        ...(!('unsupported' in l) && l.kind === 'splats' ? { notes: ['Sichtschicht – Messen und Fangen auf Punktwolke oder Mesh'] } : {}),
        ...(!('unsupported' in l) && l.kind === 'images' && images && project.images?.id === l.id
          ? { groups: images.groups() } : {}),
      })),
    setVisible: (id, on) => {
      const l = byId.get(id);
      if (!l || !supported(l) || visible[id] === on) return;
      visible[id] = on;
      applyVisible(l, on);
      fireChange();
    },
    setOpacity: (id, x) => {
      const l = byId.get(id);
      if (!l || !isSupported(l) || (l.kind !== 'pointcloud' && l.kind !== 'mesh' && l.kind !== 'splats')) return;
      opacity[id] = x;
      if (project.cloud?.id === id && viewer.cloud) {
        viewer.cloud.opacity = x;
        viewer.instance.notifyChange(viewer.cloud);
      }
      if (l.kind === 'mesh' || l.kind === 'splats') meshes.find((m) => m.id === id)?.setOpacity(x);
      fireChange();
    },
    setShading: (id, on) => {
      meshes.find((m) => m.id === id)?.setShading?.(on);
      fireChange();
    },
    setGroupVisible: (id, group, on) => {
      if (!images || project.images?.id !== id) return;
      images.setGroupVisible(group, on);
      fireChange();
    },
    frame: (id) => {
      // isSupported schließt UnsupportedLayer aus (dessen `kind` ist nur als `string` typisiert — ohne
      // diesen Schritt bliebe l.kind === 'pointcloud' unten mehrdeutig, TS könnte boundsWorld nicht sehen).
      const l = byId.get(id);
      if (!l || !isSupported(l)) return;
      const fov = verticalFov(viewer);
      if (l.kind === 'pointcloud' || l.kind === 'mesh' || l.kind === 'splats') {
        viewer.frameAt(centerOf(l.boundsWorld), fitDistance(l.boundsWorld, fov));
        return;
      }
      const b = boundsOfPoints(l.kind === 'images' ? images?.positions() ?? [] : (stations ?? []).map((s) => s.pose.translation));
      if (b) viewer.frameAt(centerOf(b), fitDistance(b, fov));
    },
    isVisible: (id) => visible[id] ?? false,
    onChange: (cb) => {
      listeners.push(cb);
    },
  };
}

/** Rahmung aller sichtbaren räumlichen Ebenen; die Minikarte bleibt unverändert. */
export function visibleBounds(project: Project, isVisible: (id: string) => boolean): [Vec3, Vec3] | null {
  return boundsOfPoints(project.layers.flatMap((l) =>
    isSupported(l) && (l.kind === 'pointcloud' || l.kind === 'mesh' || l.kind === 'splats') && isVisible(l.id) ? l.boundsWorld : []));
}
