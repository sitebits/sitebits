// Zoom-Stopp vor Mesh-Oberflächen: Heranzoomen (Mausrad, zwei Finger) läuft auf den Drehpunkt zu;
// liegt der in der Modellmitte, führte der Weg durch die Oberfläche („durch die Textur“). Gemessen
// wird im Leerlauf nach jeder Kamerabewegung (Strahl Kamera → Drehpunkt gegen die gezeichneten
// Meshes); beim Start einer Geste wird der Abstand als minDistance gesetzt — passt der letzte
// Messwert nicht mehr zu Drehpunkt und Blickrichtung, wird dann sofort gemessen (einmal je Achse). Außerhalb von Gesten (Startansicht, Sprung zu Befunden) gilt
// keine Klemme — OrbitControls zöge die Kamera sonst auf einen veralteten Abstand zurück.
import { Raycaster, Vector3 } from 'three';
import type { Camera, Mesh } from 'three';
import { strahlSchnitte } from './meshStrahl';
import { nearPlaneFor } from './viewMath';

/** Kleinster Abstand vor der Oberfläche. */
export const ZOOM_STOPP_MIN_M = 0.05;
/** Abstand vor der Oberfläche in Vielfachen des Near-Plane-Bodens (sonst schneidet er die Fläche an). */
const NEAR_VIELFACHES = 3;
/** Toleranzen, ab denen ein gemessener Abstand nicht mehr zur Pose passt. */
const ZIEL_TOLERANZ_ANTEIL = 0.01;
const RICHTUNG_TOLERANZ_GRAD = 2;
const RUHE_MS = 250;

/**
 * minDistance für einen Strahl Kamera → Drehpunkt der Länge `radius`, der die Oberfläche nach
 * `treffer` Metern trifft (null = kein Treffer). Ohne Treffer oder Fläche hinter dem Drehpunkt: 0.
 * Sonst die Tiefe des Drehpunkts hinter der Fläche plus ein Abstand, der mit dem Near-Plane-Boden
 * an der Stopp-Stelle mitwächst. Nie mehr als `radius` — die Kamera springt nicht zurück.
 */
export function stoppAbstand(radius: number, treffer: number | null): number {
  if (treffer === null || !Number.isFinite(treffer) || !(treffer < radius)) return 0;
  const tiefe = radius - treffer;
  // vor = max(min, k · near(tiefe + vor)) mit near(d) ≤ d/100 → geschlossen gelöst für den linearen Teil.
  const anteil = NEAR_VIELFACHES / 100;
  const vor = Math.max(ZOOM_STOPP_MIN_M, (anteil * tiefe) / (1 - anteil), NEAR_VIELFACHES * nearPlaneFor(tiefe));
  return Math.min(radius, tiefe + vor);
}

export interface Pose { kamera: Vector3; ziel: Vector3 }

/** Passt ein für `alt` gemessener Abstand noch zu `neu`? Drehpunkt kaum verschoben, Blickrichtung kaum gedreht. */
export function gleicheAchse(alt: Pose, neu: Pose): boolean {
  const r = alt.kamera.distanceTo(alt.ziel);
  if (!(r > 0) || alt.ziel.distanceTo(neu.ziel) > r * ZIEL_TOLERANZ_ANTEIL) return false;
  const a = alt.ziel.clone().sub(alt.kamera).normalize();
  const b = neu.ziel.clone().sub(neu.kamera).normalize();
  return a.angleTo(b) <= (RICHTUNG_TOLERANZ_GRAD * Math.PI) / 180;
}

interface Steuerung {
  target: Vector3;
  minDistance: number;
  addEventListener(typ: 'start' | 'end' | 'change', f: () => void): void;
}

export interface ZoomStopp {
  /** Neu messen (z. B. nachdem eine Mesh-Ebene geladen oder umgeschaltet wurde). */
  messen(): void;
  /** Gültiger Mindestabstand zum Drehpunkt für die aktuelle Pose (0 = keiner bekannt) — für
   *  schrittweises Zoomen ohne Geste (Knopf „Näher“). */
  grenze(): number;
  /** Zuletzt gemessener Abstand (Test/Diagnose). */
  readonly abstand: number;
}

export function zoomStoppAnbinden(o: {
  controls: Steuerung;
  camera: () => Camera;
  meshes: () => readonly Mesh[];
  /** Treffer, die zählen (z. B. nicht von der Schnittbox weggeschnitten). */
  erlaubt?: (p: Vector3) => boolean;
  leerlauf?: (f: () => void) => void;
}): ZoomStopp {
  const leerlauf = o.leerlauf ?? ((f) => {
    if ('requestIdleCallback' in window) window.requestIdleCallback(f, { timeout: 1000 });
    else setTimeout(f, 0);
  });
  const raycaster = new Raycaster();
  let gemessen: { pose: Pose; abstand: number } | null = null;
  let inGeste = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const pose = (): Pose => ({ kamera: o.camera().position.clone(), ziel: o.controls.target.clone() });

  const messen = (): void => {
    const p = pose();
    const radius = p.kamera.distanceTo(p.ziel);
    const meshes = o.meshes();
    if (!(radius > 0) || !meshes.length) { gemessen = { pose: p, abstand: 0 }; return; }
    raycaster.set(p.kamera, p.ziel.clone().sub(p.kamera).normalize());
    raycaster.near = 0;
    raycaster.far = radius;
    const erlaubt = o.erlaubt ?? ((): boolean => true);
    const treffer = strahlSchnitte(raycaster, meshes)
      .find((t) => erlaubt(t.point))?.distance ?? null;
    gemessen = { pose: p, abstand: stoppAbstand(radius, treffer) };
  };
  const spaeter = (): void => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (!inGeste) leerlauf(messen); }, RUHE_MS);
  };

  const grenze = (): number => {
    const jetzt = pose();
    // Kein passender Wert (gleich nach einem Sprung oder Drehen, bevor im Leerlauf gemessen wurde):
    // jetzt messen — sonst ginge schnelles Scrollen, das den Leerlauf-Timer immer neu startet, durch.
    if (gemessen === null || !gleicheAchse(gemessen.pose, jetzt)) messen();
    if (gemessen === null) return 0;
    // Nie mehr als der aktuelle Abstand: wer schon näher ist, wird nicht zurückgeschoben.
    return Math.min(gemessen.abstand, jetzt.kamera.distanceTo(jetzt.ziel));
  };
  o.controls.addEventListener('start', () => {
    inGeste = true;
    o.controls.minDistance = grenze();
  });
  o.controls.addEventListener('end', () => {
    inGeste = false;
    o.controls.minDistance = 0;
    spaeter();
  });
  o.controls.addEventListener('change', () => { if (!inGeste) spaeter(); });
  spaeter();
  return {
    messen: () => leerlauf(messen),
    grenze,
    get abstand() { return gemessen?.abstand ?? 0; },
  };
}
