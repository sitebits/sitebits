// Marker am Drehpunkt des Orbits: erscheint beim Setzen per Doppelklick und während des Drehens und blendet nach
// kurzer Ruhe aus (bei Bewegungsreduktion ohne Übergang). Beim Setzen sagt die Live-Region „Drehpunkt gesetzt“.
// Die Logik (Drehung erkennen, Ruhezeit) liegt rein in drehpunkt.ts.
//
// CSS2DObject statt Geometrie: feste Bildschirmgröße und immer vor der Punktwolke, ohne eigenen Renderdurchlauf —
// Giro3D rendert CSS2D-Objekte in jedem Bild mit (measure.ts, Kommentar oben). Wie dort muss matrixWorld nach jeder
// Positionsänderung selbst nachgeführt werden, sonst steht der Marker im ersten Bild am Ursprung.
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import { DREHPUNKT_RUHE_MS, drehpunktSichtbar, istDrehung } from './drehpunkt';
import type { Viewer } from './viewer';

type V3 = [number, number, number];

/** Dauer des Ausblendens ohne Bewegungsreduktion. */
const AUSBLENDEN_MS = 300;

export interface DrehpunktMarker {
  /** Marker gerade sichtbar (Ruhezeit seit dem letzten Anlass nicht um). */
  readonly sichtbar: boolean;
  readonly element: HTMLElement;
}

export function attachDrehpunktMarker(
  v: Viewer,
  o: { reduziert: () => boolean; melde: (t: string) => void },
): DrehpunktMarker {
  const { instance, controls } = v;
  const camera = instance.view.camera;
  const el = document.createElement('div');
  el.className = 'sv-drehpunkt';
  el.setAttribute('aria-hidden', 'true');
  el.style.opacity = '0';
  const marker = new CSS2DObject(el);
  marker.name = 'scanview-drehpunkt';
  instance.threeObjects.add(marker);

  let letzterAnlass: number | null = null;
  let ausblenden: number | null = null;

  const anlass = (): void => {
    letzterAnlass = performance.now();
    const t = controls.target;
    marker.position.set(t.x, t.y, t.z);
    marker.updateMatrixWorld(true);
    el.style.transition = 'none';
    el.style.opacity = '1';
    instance.notifyChange(camera);
    if (ausblenden !== null) window.clearTimeout(ausblenden);
    ausblenden = window.setTimeout(() => {
      ausblenden = null;
      if (drehpunktSichtbar(letzterAnlass, performance.now())) return;
      el.style.transition = o.reduziert() ? 'none' : `opacity ${AUSBLENDEN_MS}ms ease-out`;
      el.style.opacity = '0';
    }, DREHPUNKT_RUHE_MS);
  };

  const pose = (): { kamera: V3; ziel: V3 } => ({
    kamera: [camera.position.x, camera.position.y, camera.position.z],
    ziel: [controls.target.x, controls.target.y, controls.target.z],
  });
  let vorher = pose();
  // Jede reine Drehung zeigt den Marker, gleich ob Maus, Tastatur oder Knöpfe der Empfängeransicht sie auslösen.
  // Verschieben und Zoomen bewegen den Drehpunkt bzw. ändern den Abstand und zählen nicht.
  controls.addEventListener('change', () => {
    const jetzt = pose();
    if (controls.enabled && istDrehung(vorher, jetzt)) anlass();
    vorher = jetzt;
  });
  v.onDrehpunktGesetzt(() => {
    vorher = pose();
    anlass();
    o.melde('Drehpunkt gesetzt');
  });

  return {
    get sichtbar() {
      return drehpunktSichtbar(letzterAnlass, performance.now());
    },
    element: el,
  };
}
