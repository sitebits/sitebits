// Fenster-Effekt (F32): Perspektive folgt dem Kopf. Reine Mathematik, ohne Kamera-/Webcam-Zugriff.
// Modell: Der Bildschirm ist ein Fenster, das in der Szene in der Ebene des Drehpunkts (Abstand `ebene` vor der
// Kamera) liegt. Bewegt sich das Auge vor dem echten Bildschirm, wandert die Kamera parallel zur Bildebene mit, und
// das Frustum wird so verschoben (setViewOffset, asymmetrisch), dass genau diese Ebene am Bildschirm stehen bleibt.
// Nähere Dinge wandern dann gegen die Kopfrichtung, fernere mit ihr — Bewegungsparallaxe ohne Brille.
// setViewOffset bleibt bei jedem updateProjectionMatrix() erhalten; Panorama/Begehen stören sich daran nicht.
import { Vector3 } from 'three';
import type { PerspectiveCamera } from 'three';

/** Augposition vor dem echten Bildschirm in Metern, relativ zur Bildschirmmitte (x rechts, y oben, z > 0 davor). */
export interface Auge {
  x: number;
  y: number;
  z: number;
}

export interface FensterVersatz {
  /** Kameraverschiebung in Szeneneinheiten entlang Kamera-rechts und Kamera-oben. */
  rechts: number;
  oben: number;
  /** Frustum-Verschiebung als Anteil der Bildbreite/-höhe (positiv = Ausschnitt nach rechts/oben). */
  anteilX: number;
  anteilY: number;
}

export const OHNE_VERSATZ: FensterVersatz = { rechts: 0, oben: 0, anteilX: 0, anteilY: 0 };

/**
 * `ebene`: Abstand Kamera → Drehpunkt (Szeneneinheiten); `fovDeg`: vertikaler Bildwinkel der Kamera; `aspect`:
 * Breite/Höhe; `bildschirmBreiteM`: sichtbare Breite des echten Bildschirms; `staerke`: 1 = physikalisch richtig.
 */
export function fensterVersatz(
  auge: Auge,
  o: { ebene: number; fovDeg: number; aspect: number; bildschirmBreiteM: number; staerke: number },
): FensterVersatz {
  if (!(o.ebene > 0) || !(o.bildschirmBreiteM > 0) || !(auge.z > 0) || o.staerke === 0) return OHNE_VERSATZ;
  const halbHoehe = o.ebene * Math.tan((o.fovDeg * Math.PI) / 360);
  const halbBreite = halbHoehe * o.aspect;
  // Szeneneinheiten je echtem Meter in der Fensterebene.
  const massstab = halbBreite / (o.bildschirmBreiteM / 2);
  const rechts = auge.x * massstab * o.staerke;
  const oben = auge.y * massstab * o.staerke;
  // Die Kamera ist um (rechts, oben) gewandert; der Ausschnitt muss in der Ebene um dasselbe zurück.
  return { rechts, oben, anteilX: -rechts / (2 * halbBreite), anteilY: -oben / (2 * halbHoehe) };
}

/**
 * Wendet den Versatz auf eine Kamera an, deren Grundstellung (Position) `basis` ist. Gibt die neue Position zurück;
 * die Blickrichtung bleibt (keine Drehung — das ist der Unterschied zur normalen Kamerabewegung).
 */
export function anwenden(kamera: PerspectiveCamera, basis: Vector3, v: FensterVersatz, breitePx: number, hoehePx: number): void {
  const rechts = new Vector3().setFromMatrixColumn(kamera.matrixWorld, 0).normalize();
  const oben = new Vector3().setFromMatrixColumn(kamera.matrixWorld, 1).normalize();
  kamera.position.copy(basis).addScaledVector(rechts, v.rechts).addScaledVector(oben, v.oben);
  if (v.anteilX === 0 && v.anteilY === 0) kamera.clearViewOffset();
  // setViewOffset: y wächst nach unten (Bildschirmkoordinaten), daher das Vorzeichen.
  else kamera.setViewOffset(breitePx, hoehePx, v.anteilX * breitePx, -v.anteilY * hoehePx, breitePx, hoehePx);
  kamera.updateMatrixWorld();
}
