// Zeichenbausteine des Messwerkzeugs (aus measure.ts herausgelöst, reine Verschiebung):
// Materialien, Marker, Labels, Linien mit lokalem Vertexpuffer, Flächenfüllung. Begründungen (EDL-Befund
// „transparent: true“, ConstantSizeSphere, CSS2D-Labels, lokale Puffer bei UTM): Kopfkommentar measure.ts.
// Marker sind schattierte Kugeln in Weltmetern mit Mindest-/Höchstgröße am Bildschirm (markerKugel.ts).
import {
  BufferGeometry,
  DoubleSide,
  Float32BufferAttribute,
  Line,
  LineBasicMaterial,
  LineDashedMaterial,
  LineLoop,
  Mesh,
  MeshBasicMaterial,
  MeshMatcapMaterial,
  ShapeUtils,
  Vector2,
  Vector3,
} from 'three';
import type { Material } from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import { fitPlane, projectToPlane } from './areaMath';
import type { Vec3 } from './manifest';
import { KugelMarker, kugelMaterial } from './markerKugel';
import { KUGEL_ECKPUNKT, KUGEL_MITTELPUNKT } from './markerMath';
import type { KugelGroesse } from './markerMath';

const PENDING_COLOR = 0xffcc00;
const DONE_COLOR = 0x33d1ff;
/** Füllung der Flächen: leicht, damit Punktwolke/Panorama darunter lesbar bleiben. */
const AREA_FILL_OPACITY = 0.2;

// Materialien einmal anlegen (alle transparent: true — siehe Kopfkommentar measure.ts, EDL-Befund (2)).
export const MAT = {
  vertex: kugelMaterial(DONE_COLOR),
  vertexSel: kugelMaterial(PENDING_COLOR),
  midpoint: kugelMaterial(DONE_COLOR, 0.5),
  pending: kugelMaterial(PENDING_COLOR),
  ghost: kugelMaterial(PENDING_COLOR, 0.6),
  line: new LineBasicMaterial({ color: DONE_COLOR, depthTest: false, transparent: true }),
  lineSel: new LineBasicMaterial({ color: PENDING_COLOR, depthTest: false, transparent: true }),
  dashed: new LineDashedMaterial({ color: DONE_COLOR, depthTest: false, transparent: true, dashSize: 0.05, gapSize: 0.04 }),
  preview: new LineDashedMaterial({ color: PENDING_COLOR, depthTest: false, transparent: true, dashSize: 0.05, gapSize: 0.04 }),
  fill: new MeshBasicMaterial({
    color: DONE_COLOR,
    transparent: true,
    opacity: AREA_FILL_OPACITY,
    depthTest: false,
    depthWrite: false,
    side: DoubleSide,
  }),
  fillSel: new MeshBasicMaterial({
    color: PENDING_COLOR,
    transparent: true,
    opacity: AREA_FILL_OPACITY,
    depthTest: false,
    depthWrite: false,
    side: DoubleSide,
  }),
};

export { KUGEL_ECKPUNKT, KUGEL_MITTELPUNKT };

export function makeMarker(mat: MeshMatcapMaterial, groesse: KugelGroesse = KUGEL_ECKPUNKT): KugelMarker {
  return new KugelMarker({ material: mat, groesse });
}

/** Beschriftung: Messwert in der ersten Zeile, Zusätze (Warnungen, Herkunft) kleiner darunter und umbrechend —
 *  eine einzige lange Zeile lief auf dem Handy über den Rand (17.10.). */
export function makeLabel(text: string, zusatz = ''): CSS2DObject {
  const el = document.createElement('div');
  el.className = 'scanview-measure-label';
  el.style.cssText =
    'padding:2px 6px;background:#0009;color:#fff;font:12px system-ui;border-radius:3px;text-align:center;' +
    'max-width:min(70vw,360px);pointer-events:none;transform:translate(-50%,-110%)';
  const wert = document.createElement('div');
  wert.textContent = text;
  wert.style.whiteSpace = 'nowrap';
  el.append(wert);
  if (zusatz) {
    const z = document.createElement('div');
    z.className = 'scanview-measure-zusatz';
    z.textContent = zusatz;
    z.style.cssText = 'font-size:10px;opacity:.85;white-space:normal;line-height:1.25';
    el.append(z);
  }
  return new CSS2DObject(el);
}

export const vec = (p: Vec3): Vector3 => new Vector3(p[0], p[1], p[2]);
export const mid = (a: Vec3, b: Vec3): Vec3 => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];

/** Linienzug mit lokalem Vertexpuffer relativ zum ersten Punkt (Float32-Präzision bei UTM). */
export function localLine(points: readonly Vec3[], mat: Material, closed = false): Line {
  const o = points[0]!;
  const geom = new BufferGeometry().setFromPoints(points.map((p) => new Vector3(p[0] - o[0], p[1] - o[1], p[2] - o[2])));
  const line = closed ? new LineLoop(geom, mat) : new Line(geom, mat);
  if (mat instanceof LineDashedMaterial) line.computeLineDistances();
  line.position.set(o[0], o[1], o[2]);
  line.renderOrder = 998;
  return line;
}

/** Füllung einer Fläche: Dreiecke aus der Projektion auf die bestangepasste Ebene (earcut in three),
 *  Eckpunkte selbst bleiben die echten 3D-Punkte (lokal relativ zum ersten). Selbstschnitte hat der
 *  Speicher schon abgelehnt, die Triangulierung ist also eindeutig. */
export function areaFill(points: readonly Vec3[], mat: Material): Mesh | null {
  const o = points[0]!;
  const local = points.map((p): Vec3 => [p[0] - o[0], p[1] - o[1], p[2] - o[2]]);
  const flat = projectToPlane(local, fitPlane(local)).map((q) => new Vector2(q[0], q[1]));
  const faces = ShapeUtils.triangulateShape(flat, []);
  if (!faces.length) return null;
  const geom = new BufferGeometry();
  geom.setAttribute('position', new Float32BufferAttribute(local.flat(), 3));
  geom.setIndex(faces.flat());
  const mesh = new Mesh(geom, mat);
  mesh.position.set(o[0], o[1], o[2]);
  mesh.renderOrder = 997;
  return mesh;
}
