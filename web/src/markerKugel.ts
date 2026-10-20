// Kugelmarker für Messpunkte und Befunde: schattierte 3D-Kugel mit Radius in Weltmetern zwischen einer
// Mindest- und Höchstgröße am Bildschirm. Rechnung und Schattierungsbild: markerMath.ts (Vitest).
//
// Erbt von Giro3Ds ConstantSizeSphere (geteilte Kugelgeometrie, Raycast-Schalter); nur die Größe wird in
// onBeforeRender anders bestimmt. Wer Kinder einer Gruppe entsorgt, darf die Geometrie deshalb wie bisher
// nicht freigeben (measure.ts: `instanceof ConstantSizeSphere`).
//
// Verdeckung wie bisher (Kopfkommentar measure.ts): Material `transparent: true` (TRANSPARENT-Eimer nach dem
// EDL-Pass), `depthTest: false` (Marker immer vor der Punktwolke und vor dem Panorama, renderOrder 999 >
// 500 der Panoramaflächen). Ohne Tiefentest zeichnet die Kugel nur ihre Vorderseiten (FrontSide), die sich
// bei einem konvexen Körper nicht überdecken — es entstehen keine Artefakte.
import { DataTexture, MeshMatcapMaterial, RGBAFormat, SRGBColorSpace, Vector2, Vector3 } from 'three';
import type { Camera, Scene, WebGLRenderer } from 'three';
import ConstantSizeSphere from '@giro3d/giro3d/renderer/ConstantSizeSphere.js';
import { kugelRadius, matcapRgba } from './markerMath';
import type { KugelGroesse, KugelKamera } from './markerMath';

const MATCAP_PX = 64;
let matcap: DataTexture | null = null;
const matcapTextur = (): DataTexture => {
  if (!matcap) {
    matcap = new DataTexture(matcapRgba(MATCAP_PX), MATCAP_PX, MATCAP_PX, RGBAFormat);
    matcap.colorSpace = SRGBColorSpace;
    // Zeile 0 ist die untere Bildzeile (markerMath.ts) = Texturkoordinate v = 0, also nicht spiegeln.
    matcap.flipY = false;
    matcap.needsUpdate = true;
  }
  return matcap;
};

/** Material eines Kugelmarkers (je Farbe und Deckkraft einmal anlegen und teilen). */
export const kugelMaterial = (color: number, opacity = 1): MeshMatcapMaterial => {
  const mat = new MeshMatcapMaterial({ color, matcap: matcapTextur(), depthTest: false, depthWrite: false, transparent: true, opacity });
  return mat;
};

const tmpKamera = new Vector3();
const tmpOrt = new Vector3();
const tmpGroesse = new Vector2();
const tmpSkala = new Vector3();

/** Kamera am Ort `ort` für die Größenrechnung; `hoehePx` = Höhe der Zeichenfläche in CSS-Pixeln. */
export function kugelKamera(camera: Camera, ort: Vector3, hoehePx: number): KugelKamera {
  const c = camera as Camera & { isPerspectiveCamera?: boolean; isOrthographicCamera?: boolean; top?: number; bottom?: number; zoom?: number };
  if (c.isOrthographicCamera) return { art: 'orthografisch', sichtHoeheM: Math.abs((c.top ?? 0) - (c.bottom ?? 0)) / (c.zoom ?? 1), hoehePx };
  const fovGrad = (camera as Camera & { getEffectiveFOV(): number }).getEffectiveFOV();
  return { art: 'perspektivisch', abstandM: camera.getWorldPosition(tmpKamera).distanceTo(ort), fovGrad, hoehePx };
}

export class KugelMarker extends ConstantSizeSphere {
  groesse: KugelGroesse;
  /** Bildschirmradius beim letzten Zeichnen [px]. */
  pxZuletzt: number;
  /** Nach jeder Größenbestimmung (z. B. Abstand einer Beschriftung über der Kugel). */
  beimZeichnen: ((px: number) => void) | null = null;

  constructor(o: { material: MeshMatcapMaterial; groesse: KugelGroesse }) {
    super({ radius: o.groesse.minPx, material: o.material });
    this.groesse = o.groesse;
    this.pxZuletzt = o.groesse.minPx;
    this.renderOrder = 999;
  }

  /** @internal */
  override onBeforeRender(renderer: WebGLRenderer, _scene: Scene, camera: Camera): void {
    this.updateWorldMatrix(true, false);
    const ort = this.getWorldPosition(tmpOrt);
    const r = kugelRadius(kugelKamera(camera, ort, renderer.getSize(tmpGroesse).y), this.groesse);
    // Wie ConstantSizeSphere: Skalierung der Eltern herausrechnen.
    const eltern = this.parent?.getWorldScale(tmpSkala) ?? tmpSkala.set(1, 1, 1);
    this.scale.set(r.welt / eltern.x, r.welt / eltern.y, r.welt / eltern.z);
    this.updateMatrixWorld();
    this.pxZuletzt = r.px;
    this.beimZeichnen?.(r.px);
  }
}
