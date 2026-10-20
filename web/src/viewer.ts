// Giro3D-Viewer: lädt die COPC-Punktwolke eines Pakets, richtet Kamera/Navigation ein und bietet
// die Darstellungs-Schalter für die Bedienleiste. Nur über den Bundler ladbar (Giro3D), daher ohne
// Vitest-Test — abgesichert über typecheck/build und die Sichtprüfung per Playwright.
//
// API-Fakten (geprüft in node_modules/@giro3d/giro3d 2.0.4, kein "exports"-Feld → tiefe Pfade):
//   - COPCSource liefert die Attribute als Pseudo-Dimension 'Color' (nur wenn Red/Green/Blue
//     vorhanden) sowie die LAS-Dimensionen unter ihrem Namen ('Intensity', 'Z', …); X/Y fehlen.
//     PointCloud.setActiveAttribute() wirft UnsupportedAttributeError bei unbekanntem Namen —
//     deshalb werden die Farbmodi aus cloud.getSupportedAttributes() abgeleitet.
//   - Knotenpuffer sind Float32 relativ zum Knotenmittelpunkt (readers.readPosition: x − origin.x),
//     der Weltbezug steckt in mesh.position (Float64 in JS). Die Szene läuft damit in Welt-
//     koordinaten ohne Präzisionsverlust im Vertexpuffer.
//   - View.setControls(controls) registriert three.js-Controls ('change' → Neuzeichnen).
//   - Unbekannte Skalar-Attribute bekommen die Standard-Farbskala 0…1000 schwarz→weiß; für 'Z'
//     und 'Intensity' setzen wir den Wertebereich selbst.
import { Color, Vector3 } from 'three';
import { MapControls } from 'three/examples/jsm/controls/MapControls.js';
import Instance from '@giro3d/giro3d/core/Instance.js';
import ColorMap from '@giro3d/giro3d/core/ColorMap.js';
import PointCloud from '@giro3d/giro3d/entities/PointCloud.js';
import COPCSource from '@giro3d/giro3d/sources/COPCSource.js';
import Fetcher from '@giro3d/giro3d/utils/Fetcher.js';
import { createGate, createPrefetchingGetter } from './copcGetter';
import type { PointcloudLayer, Project, Vec3 } from './manifest';
import { resolveUrl } from './manifest';
import { resolveCrs } from './crsGiro3d';
import { ATTRIBUTE_OF, VIRIDIS, availableColorModes, fitDistance, localToWorld, nearPlaneFor, worldToLocal } from './viewMath';
import type { ColorMode } from './viewMath';
import { OHNE_FILTER, dekodierfilter } from './klassen';
import type { Punktfilter } from './klassen';

export type { ColorMode } from './viewMath';

export interface Viewer {
  instance: Instance;
  cloud: PointCloud | null;
  controls: MapControls;
  local: boolean;
  /** Gesetzt, wenn eine vorhandene CRS-Angabe nicht auswertbar war und lokal angezeigt wird (I4). */
  crsHint?: string;
  /** Farbmodi, die diese Punktwolke tatsächlich unterstützt (Reihenfolge: rgb, intensity, elevation). */
  colorModes: ColorMode[];
  frameAll(boundsWorld?: [Vec3, Vec3] | null): void;
  reset(): void;
  /** Kamera auf ``target`` rahmen (Schrägblick aus Südwest, Abstand in m) — z. B. Sprung zu Standpunkt/POI. */
  frameAt(target: Vec3, distance: number): void;
  setColorMode(mode: ColorMode): void;
  /** Punktfilter (Klassen, Echos, F30): wirkt beim Dekodieren, für Darstellung und Strahlsuche gleich. */
  setPunktfilter(f: Punktfilter): void;
  punktfilter(): Punktfilter;
  /** Farbe einer Klasse im Farbmodus „Klassen“ als #rrggbb (Giro3D-ASPRS-Palette). */
  klassenFarbe(klasse: number): string;
  /** Wertebereich [min, max] der Höhenfarben in Metern (Weltkoordinaten), null ohne Höhenattribut. */
  hoehenSpanne(): [number, number] | null;
  setPointSize(px: number): void;
  setPointBudget(n: number): void;
  setEdl(on: boolean): void;
  /** Drehpunkt per Doppelklick neu gesetzt (Marker, Ansage: drehpunktMarker.ts). */
  onDrehpunktGesetzt(cb: () => void): void;
  /** Punktwolken-Nachladen anhalten (keine neuen COPC-Anfragen ans Netz) bzw. fortsetzen. */
  setStreamingPaused(paused: boolean): void;
  readonly streamingPaused: boolean;
  worldToLocal(p: Vec3): Vec3;
  localToWorld(p: Vec3): Vec3;
}

// Höhenskala viridis (dunkelviolett → gelb, Helligkeit steigt mit der Höhe; viewMath.ts).
const ELEVATION_RAMP = VIRIDIS.map((c) => new Color(c));

/**
 * @param cloudSize Dateigröße der COPC-Datei in Byte (aus rangeProbe) — begrenzt das Vorabladen der
 *   Hierarchie (copcGetter.ts) auf [evlrOffset, cloudSize).
 */
export async function createViewer(
  el: HTMLDivElement,
  m: Project,
  cloudLayer: PointcloudLayer | null,
  pkgBase: string,
  cloudSize: number,
): Promise<Viewer> {
  const { crs, local, hint: crsHint } = resolveCrs(m);
  const instance = new Instance({ target: el, crs, backgroundColor: '#111' });
  // Eigener Getter statt URL: gleiche Range-Anfrage wie Giro3Ds createGetter (Fetcher → Warteschlange
  // je Host, Fehlerereignisse), aber die Hierarchie kommt in einer Anfrage statt ~145 nacheinander.
  // Vorgeschaltete Schranke: hält neue Bereichsanfragen an, solange ein Panorama lädt.
  const gate = createGate();
  let source: COPCSource | null = null;
  let cloud: PointCloud | null = null;
  if (cloudLayer) {
    const cloudUrl = resolveUrl(pkgBase, cloudLayer.url);
    const fetchRange = async (begin: number, end: number): Promise<Uint8Array> => {
      await gate.wait();
      const blob = await Fetcher.blob(cloudUrl, { headers: { Range: `bytes=${begin}-${end - 1}` } });
      return new Uint8Array(await blob.arrayBuffer());
    };
    source = new COPCSource({ url: createPrefetchingGetter(fetchRange, cloudSize) });
    cloud = new PointCloud({ source });
    await instance.add(cloud);
  }
  let filter: Punktfilter = OHNE_FILTER;

  const supported = cloud?.getSupportedAttributes() ?? [];
  const byName = new Map(supported.map((a) => [a.name, a]));
  // Farbmodi nur, wo Daten da sind (Giro3D meldet Color/Intensity auch ohne Quelldaten).
  const colorModes = availableColorModes(
    cloudLayer?.attributes ?? [],
    supported.map((a) => a.name),
  );

  // Wertebereiche der Skalar-Attribute: Z aus den Weltgrenzen des Manifests, Intensität aus dem
  // Quellbereich (LAS: 0…65535) — sonst greift die Giro3D-Vorgabe 0…1000.
  const spatial = m.layers.filter((l) => !('unsupported' in l) && 'boundsWorld' in l);
  const corners = spatial.flatMap((l) => 'boundsWorld' in l ? l.boundsWorld : []);
  if (!corners.length) throw new Error('Paket enthält keine räumliche Ebene mit boundsWorld.');
  const bounds: [Vec3, Vec3] = [
    [0, 1, 2].map((i) => Math.min(...corners.map((p) => p[i]!))) as Vec3,
    [0, 1, 2].map((i) => Math.max(...corners.map((p) => p[i]!))) as Vec3,
  ];
  const [lo, hi] = bounds;
  if (cloud && byName.has('Z')) {
    cloud.setAttributeColorMap('Z', new ColorMap({ colors: ELEVATION_RAMP, min: cloudLayer?.boundsWorld[0][2] ?? lo[2], max: cloudLayer?.boundsWorld[1][2] ?? hi[2] }));
  }
  const intensity = byName.get('Intensity');
  if (cloud && intensity) {
    cloud.setAttributeColorMap(
      'Intensity',
      new ColorMap({
        colors: [new Color('black'), new Color('white')],
        min: intensity.min ?? 0,
        max: intensity.max ?? 65535,
      }),
    );
  }

  // setColoringMode('attribute') nur einmal aufrufen: Jeder Aufruf stößt in Giro3D 2.0.4 ein
  // Nachladen der Attributpuffer aller angezeigten Knoten an (updateColoringFromAttribute(true)).
  // Moduswechsel laufen deshalb nur über setActiveAttribute — und nur bei tatsächlicher Änderung.
  let current: ColorMode | undefined;
  const setColorMode = (mode: ColorMode): void => {
    // Nicht unterstützte Modi fallen auf den ersten verfügbaren zurück, statt zu werfen.
    const effective = colorModes.includes(mode) ? mode : colorModes[0];
    if (!cloud || effective === undefined || effective === current) return;
    cloud.setActiveAttribute(ATTRIBUTE_OF[effective]);
    current = effective;
    instance.notifyChange(cloud ?? undefined);
  };
  setColorMode(colorModes[0] ?? 'elevation');
  cloud?.setColoringMode('attribute');
  instance.renderingOptions.enableEDL = cloud !== null;

  // Giro3D klemmt die Near-Plane standardmäßig auf ≥ 2 m (DEFAULT_MIN_NEAR_PLANE) und fällt auf
  // genau diesen Wert zurück, sobald die Kamera innerhalb der Knotenvolumen steht (MainLoop:
  // resetPlanes(), berechnete Distanz < 0 wird verworfen). Für Innenräume und Zoom bis wenige
  // Zentimeter würde alles in den ersten 2 m weggeschnitten. Ein fester 1-cm-Boden wiederum
  // ruiniert die Tiefenauflösung (EDL-Tiefentextur ist 16 bit) bei langen Innen-Sichtachsen.
  // Deshalb skaliert der Boden in jedem Frame mit dem Abstand Kamera–Drehpunkt (nearPlaneFor).
  const camera = instance.view.camera;
  const controls = new MapControls(camera, instance.domElement);
  controls.screenSpacePanning = true;
  controls.enableDamping = false;
  instance.view.setControls(controls);
  const updateNearFloor = (): void => {
    instance.view.minNearPlane = nearPlaneFor(camera.position.distanceTo(controls.target));
  };
  // before-camera-update läuft vor resetPlanes()/Near-Berechnung jedes Frames (MainLoop.step).
  instance.addEventListener('before-camera-update', updateNearFloor);
  updateNearFloor();

  const center = new Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
  const target = m.initialViewDeclared ? new Vector3(...m.initialView.target) : center.clone();

  // Schrägblick von Südwesten oben auf das Ziel; dist = Abstand Kamera–Ziel in Metern.
  const frame = (t: Vector3, dist: number): void => {
    camera.up.set(0, 0, 1);
    camera.position.set(t.x - dist * 0.5, t.y - dist * 0.7, t.z + dist * 0.6);
    controls.target.copy(t);
    controls.update();
    instance.notifyChange(camera);
  };
  const reset = (): void => {
    if (m.initialView.position) {
      camera.up.set(0, 0, 1);
      camera.position.set(...m.initialView.position);
      controls.target.copy(target);
      controls.update();
      instance.notifyChange(camera);
    } else frame(target, Math.max(m.initialViewDeclared ? m.initialView.distance : fitDistance(bounds, 'fov' in camera ? camera.fov : 30), 1));
  };
  // Abstand so, dass die Hüllkugel der Bounding-Box ins (engere) vertikale Sichtfeld passt.
  // Giro3D legt ohne camera-Option eine PerspectiveCamera an; 30° ist deren Vorgabe-FOV.
  const frameAll = (boundsWorld?: [Vec3, Vec3] | null): void => {
    const b = boundsWorld ?? bounds;
    const c = boundsWorld ? new Vector3(...b[0].map((x, i) => (x + b[1][i]!) / 2)) : center;
    frame(c, fitDistance(b, 'fov' in camera ? camera.fov : 30));
  };
  reset();

  // Doppelklick auf einen Punkt setzt den Drehpunkt dorthin.
  const drehpunktCbs: (() => void)[] = [];
  instance.domElement.addEventListener('dblclick', (ev) => {
    if (!controls.enabled || !cloud) return;
    const hit = instance.pickObjectsAt(ev, { where: [cloud], radius: 3, limit: 1, sortByDistance: true })[0];
    if (hit) {
      controls.target.copy(hit.point);
      controls.update();
      instance.notifyChange(camera);
      for (const cb of drehpunktCbs) cb();
    }
  });
  // Taste f (Alles zeigen): keymap.ts, ein Verteiler für alle Kürzel (B-BF-V1).

  // Lokales Paket-KS: Ursprung ist originWorld (nicht cloud.offset — das ist nur der LAS-Offset).
  const o = m.originWorld;
  return {
    instance,
    cloud,
    controls,
    local,
    crsHint,
    colorModes,
    frameAll,
    reset,
    frameAt: (t, dist) => frame(new Vector3(...t), Math.max(dist, 1)),
    setColorMode,
    setPunktfilter: (f) => {
      filter = f;
      // Setzen löst 'updated' an der Quelle aus: PointCloud verwirft die angezeigten Knoten und lädt neu.
      if (source) source.filters = dekodierfilter(f);
      instance.notifyChange(cloud ?? undefined);
    },
    punktfilter: () => filter,
    klassenFarbe: (k) => `#${cloud?.getAttributeClassifications('Classification')[k]?.color.getHexString() ?? 'ff8100'}`,
    hoehenSpanne: () => (cloudLayer && byName.has('Z') ? [cloudLayer.boundsWorld[0][2], cloudLayer.boundsWorld[1][2]] : null),
    setPointSize: (px) => {
      if (cloud) cloud.pointSize = px;
      instance.notifyChange(cloud ?? undefined);
    },
    setPointBudget: (n) => {
      if (cloud) cloud.pointBudget = n;
      instance.notifyChange(cloud ?? undefined);
    },
    setEdl: (on) => {
      instance.renderingOptions.enableEDL = on;
      instance.notifyChange();
    },
    onDrehpunktGesetzt: (cb) => {
      drehpunktCbs.push(cb);
    },
    setStreamingPaused: (paused) => (paused ? gate.close() : gate.open()),
    get streamingPaused() {
      return gate.closed;
    },
    worldToLocal: (p) => worldToLocal(o, p),
    localToWorld: (p) => localToWorld(o, p),
  };
}
