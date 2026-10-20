// Schnittbox: Gizmo (TransformControls, three.js, MIT) auf einem Box-Drahtgitter, das
// zugleich als Platzhalter für die Transformation dient (S = Skalieren, G = Verschieben); steuert
// `v.cloud.clippingPlanes`. Wie `viewer.ts`/`measure.ts` ohne Vitest-Test — abgesichert über
// typecheck/build und die Sichtprüfung per Playwright (screenshot-clip.mjs).
//
// Mechanismus-Wahl (geprüft in node_modules/@giro3d/giro3d 2.0.4, kein "exports"-Feld → tiefe Pfade):
//   - PointCloud.intersectingVolumes (entities/PointCloud.js, renderer/PointCloudMaterial.js,
//     shader/chunk/giro3d_intersecting_volume_pars.glsl) FÄRBT Punkte innerhalb einer Box nur um
//     (`if (volumeNc.xyz < 1.0) { color = volumes[i].color; return; }`, kein `discard`) — das ist
//     Giro3Ds Hervorhebungs-Mechanismus, kein Wegschneide-Mechanismus. Ungeeignet hier.
//   - `Entity3D.clippingPlanes` (entities/Entity3D.js) ist die dafür vorgesehene Giro3D-Schnittstelle:
//     `PointCloud` überschreibt `setupMaterial` nicht, erbt sie also unverändert von `Entity3D`.
//     `Entity3D.setupMaterial()` setzt `material.clippingPlanes = this._clippingPlanes` bei jedem
//     `onObjectCreated()`, und `PointCloud` ruft `onObjectCreated(mesh)` für jeden neu erzeugten
//     Knoten-Mesh selbst auf (`createMesh()` → `onObjectCreated(mesh)`, PointCloud.js:1239) — neu
//     nachgeladene Knoten bekommen die Ebenen also automatisch, ohne eigenen 'object-created'-
//     Listener. `PointCloudMaterial` deklariert `clipping: true` und bindet die drei.js-Standard-
//     Clipping-Shader-Chunks ein (PointsVS/FS: `clipping_planes_pars_vertex/fragment`,
//     `clipping_planes_vertex/fragment`) — Clipping ist also für Punktwolken bereits vollständig
//     verdrahtet. `renderer.localClippingEnabled` setzt Giro3D selbst beim Anlegen des WebGLRenderer
//     bereits unbedingt (renderer/c3DEngine.js:64) — der in der Aufgabe beschriebene Fallback (selbst
//     `renderer.localClippingEnabled = true` setzen und `material.clippingPlanes` per eigenem
//     'object-created'-Listener auf jedes neue Knotenmaterial anwenden) ist hier folglich nicht nötig,
//     beides ist bereits vorhanden. Bewusst `cloud.clippingPlanes` (Entity3D-Eigenschaft, nur diese
//     Punktwolke) statt `renderer.clippingPlanes`: Letzteres ist global und würde auch Gizmo,
//     Box-Drahtgitter und die Streckenmessung (measure.ts) mit wegschneiden.
//   - Derselbe Vertex-/Fragment-Shader bedient auch Giro3Ds Picking-Durchlauf (nur mit gesetztem
//     `pickingId`-Uniform, die `clipping_planes_*`-Includes laufen unverändert mit) — ausgeblendete
//     Punkte tauchen dadurch GPU-seitig bereits nicht in `instance.pickObjectsAt()` auf. Der in
//     `measure.ts` ergänzte `insideBox`-Filter ist damit größtenteils Verteidigung in der
//     Tiefe, kein Ersatz für die Clipping-Planes.
import { BoxGeometry, EdgesGeometry, LineBasicMaterial, LineSegments, Plane, Vector3 } from 'three';
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js';
import type { Vec3 } from './manifest';
import { boxFromCenterScale, boxToPlanes, centerScaleFromBox, centralBox, clampScale } from './clipBoxMath';
import type { ClipBoxState } from './clipBoxMath';
import type { Viewer } from './viewer';

export type { ClipBoxState } from './clipBoxMath';

const BOX_COLOR = 0xffa500;

export interface ClipBox {
  setEnabled(on: boolean): void;
  setBox(minWorld: Vec3, maxWorld: Vec3): void;
  /**
   * Aktueller Zustand (Ein/Aus + aktuelle Box) — Erweiterung über die
   * {setEnabled, setBox}-Schnittstelle hinaus, analog zu `MeasureTool.sync`: measure.ts
   * braucht eine Möglichkeit, den *aktuellen* (ggf. gerade per Gizmo gezogenen) Boxzustand
   * abzufragen, um Treffer außerhalb zu verwerfen, ohne dass clipBox.ts von measure.ts
   * wüsste.
   */
  getState(): ClipBoxState;
  /** Griffe: verschieben oder Größe je Achse ändern (Tasten G/S; im Panel sichtbar, auch ohne Tastatur). */
  setMode(m: ClipBoxMode): void;
  getMode(): ClipBoxMode;
  /** Schnittbox eingeschaltet (für den Tastenverteiler: s/g gelten nur dann). */
  readonly enabled: boolean;
}

export type ClipBoxMode = 'translate' | 'scale';

export function attachClipBox(v: Viewer, boundsWorld: readonly [Vec3, Vec3], onPlanes?: (planes: Plane[]) => void): ClipBox {
  const { instance, cloud, controls: mapControls } = v;

  // Das Drahtgitter dient zugleich als Gizmo-Ziel: Einheitswürfel (Kanten −0,5…0,5), Weltbezug über
  // position (Mittelpunkt) und scale (Größe je Achse) — vermeidet einen zusätzlichen Platzhalter.
  const wire = new LineSegments(
    new EdgesGeometry(new BoxGeometry(1, 1, 1)),
    // transparent:true trotz Opacity 1 — wie in measure.ts: der EDL-Durchlauf schwächt sonst
    // gewöhnliche (opake) Geometrie ab, die an echten Punktpositionen liegt (RenderPipeline.ts,
    // TRANSPARENT-Eimer rendert nach dem Punktwolken-Pass). depthTest:false hält die Box immer
    // sichtbar, auch wenn Punkte davor liegen (Gizmo-Konvention).
    new LineBasicMaterial({ color: BOX_COLOR, depthTest: false, transparent: true }),
  );
  wire.name = 'scanview-clipbox';
  wire.renderOrder = 996;
  wire.visible = false;
  instance.threeObjects.add(wire);

  const gizmo = new TransformControls(instance.view.camera, instance.domElement);
  gizmo.setMode('translate');
  gizmo.enabled = false;
  const gizmoHelper = gizmo.getHelper();
  gizmoHelper.name = 'scanview-clipbox-gizmo';
  gizmoHelper.visible = false;
  instance.threeObjects.add(gizmoHelper);
  // TransformControlsRoot berechnet Größe/Ausrichtung der Griffe (kameraabstandsabhängig, siehe
  // TransformControls.js) in einem überschriebenen updateMatrixWorld() — das läuft nur, wenn drei.js
  // die Szene traversiert. Giro3D managt matrixWorld selbst (siehe measure.ts-Kommentarblock zum
  // selben Problem bei frisch hinzugefügten Objekten) und ruft es für den Gizmo-Helfer nie von
  // selbst auf; ohne diesen Aufruf bleiben die Achsen-Griffe unsichtbar/falsch positioniert, auch
  // wenn `visible` stimmt. 'before-camera-update' feuert vor jedem gerenderten Frame (siehe
  // viewer.ts, dort für die Near-Plane genutzt) — günstig genug, um es bedingungslos zu abonnieren.
  instance.addEventListener('before-camera-update', () => gizmoHelper.updateMatrixWorld(true));

  let state: ClipBoxState = { enabled: false, box: null };
  let mode: ClipBoxMode = 'translate';

  const applyPlanes = (): void => {
    const planes =
      state.enabled && state.box
        ? boxToPlanes(state.box[0], state.box[1]).map((p) => new Plane(new Vector3(...p.normal), p.constant))
        : null;
    if (cloud) cloud.clippingPlanes = planes;
    onPlanes?.(planes ?? []);
    instance.notifyChange(cloud ?? undefined);
  };

  const placeWire = ([min, max]: [Vec3, Vec3]): void => {
    const { center, scale } = centerScaleFromBox(min, max);
    wire.position.set(...center);
    // Gleiche Untergrenze wie beim Ziehen (objectChange): das Drahtgitter ist zugleich Gizmo-Ziel.
    wire.scale.set(...clampScale(scale));
    // Wie in measure.ts: frisch (um)platzierte Objekte brauchen ihre matrixWorld sofort, bevor der
    // nächste (EDL-)Redraw die Szene traversiert, sonst bleibt die Box/das Gizmo einen Frame lang an
    // der alten Stelle bzw. unsichtbar hängen.
    wire.updateMatrixWorld(true);
  };

  const setBox = (minWorld: Vec3, maxWorld: Vec3): void => {
    const box: [Vec3, Vec3] = [minWorld, maxWorld];
    state = { ...state, box };
    placeWire(box);
    applyPlanes();
    instance.notifyChange(wire);
  };

  // Gizmo-Drag → Box aus Mittelpunkt/Skalierung des Drahtgitters neu berechnen (Betrag: siehe
  // clipBoxMath.boxFromCenterScale zur negativen Skalierung im Skalieren-Modus).
  gizmo.addEventListener('objectChange', () => {
    const p = wire.position;
    // Untergrenze auch hier (D35) und ins Objekt zurückschreiben: TransformControls nimmt die
    // Startskalierung des nächsten Ziehens vom Objekt, nicht aus unserem Zustand.
    const s = clampScale([wire.scale.x, wire.scale.y, wire.scale.z]);
    wire.scale.set(...s);
    state = { ...state, box: boxFromCenterScale([p.x, p.y, p.z], s) };
    applyPlanes();
  });
  // Hover-/Auswahlfarben der Gizmo-Achsen ändern sich auch ohne objectChange (reines Neuzeichnen,
  // Giro3D rendert nur bei notifyChange neu).
  gizmo.addEventListener('change', () => instance.notifyChange());
  // MapControls (Kamera-Pan/Zoom/-Rotation) blockieren, solange am Gizmo gezogen wird — sonst
  // verschiebt ein Drag gleichzeitig Box und Kamera.
  gizmo.addEventListener('dragging-changed', (ev) => {
    mapControls.enabled = !ev.value;
  });

  // Tasten s/g (Gizmo-Modus): keymap.ts, ein Verteiler für alle Kürzel (B-BF-V1; dort auch zur Capture-Phase).

  function setMode(m: ClipBoxMode): void {
    mode = m;
    gizmo.setMode(m);
    instance.notifyChange();
  }

  const setEnabled = (on: boolean): void => {
    state = { ...state, enabled: on };
    // Erstes Einschalten ohne vorherige setBox()-Vorgabe: Weltgrenzen des Manifests auf die
    // mittleren 50 % je Achse geschrumpft, damit die Wirkung sofort sichtbar ist.
    if (on && !state.box) setBox(...centralBox(boundsWorld));
    wire.visible = on;
    gizmoHelper.visible = on;
    gizmo.enabled = on;
    if (on) gizmo.attach(wire);
    else gizmo.detach();
    wire.updateMatrixWorld(true);
    applyPlanes();
    instance.notifyChange();
  };

  return {
    setEnabled,
    setBox,
    getState: () => state,
    setMode,
    getMode: () => mode,
    get enabled() {
      return state.enabled;
    },
  };
}
