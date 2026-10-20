// Kugelmarker (Messpunkte, Befunde): Größe und Schattierung als reine Rechnung (Vitest); die Kugel selbst
// (three/Giro3D) steht in markerKugel.ts.
//
// Größe: Die Kugel hat einen Radius in Weltmetern — aus der Nähe wirkt sie also perspektivisch wie ein echter
// Körper am Punkt —, wird aber nie kleiner als `minPx` Bildschirmpixel (aus der Ferne verschwände sie sonst)
// und nie größer als `maxPx` (direkt vor der Kamera deckte sie sonst die Umgebung des Punkts zu). Zwischen den
// beiden Grenzen gilt die Weltgröße. Bisher hatten die Marker einen festen Bildschirmradius (5 px Eckpunkt,
// 3 px Mittelpunkt); die Untergrenze liegt jetzt darüber, damit sie aus der Ferne gut zu treffen sind.
//
// Schattierung: Die Szene hat keine Lichter (Punktwolke, Panorama und Overlays brauchen keine). Die Kugel
// bekommt deshalb ein Matcap — ein kleines Bild einer beleuchteten Kugel, das der Shader über die Normale im
// Blickraum abliest. Das Licht steht damit immer fest zur Kamera (oben links vorn), wie bei den Markern in
// Photogrammetrie-Programmen, unabhängig von Szene und Blickrichtung. Das Bild ist grau, die Farbe des Markers
// kommt aus dem Material (Multiplikation).

export interface KugelGroesse {
  /** Radius in Weltmetern (gilt zwischen den Pixelgrenzen). */
  radiusM: number;
  /** Untergrenze des Bildschirmradius [px]. */
  minPx: number;
  /** Obergrenze des Bildschirmradius [px]. */
  maxPx: number;
}

/** Eckpunkt einer Messung, Punkt einer offenen Zeichnung, Geisterpunkt beim Ziehen: 1 cm Radius. */
export const KUGEL_ECKPUNKT: KugelGroesse = { radiusM: 0.018, minPx: 8, maxPx: 34 };
/** Mittelpunkt-Griff (Einfügen): kleiner als ein Eckpunkt, damit er nicht mit ihm verwechselt wird. */
export const KUGEL_MITTELPUNKT: KugelGroesse = { radiusM: 0.011, minPx: 5, maxPx: 20 };
/** Befund/Rückmeldung (POI). */
export const KUGEL_POI: KugelGroesse = { radiusM: 0.025, minPx: 10, maxPx: 40 };
/** Ausgewählter POI: größer (Auswahl nicht nur an der Farbe erkennbar). */
export const KUGEL_POI_GEWAEHLT: KugelGroesse = { radiusM: 0.032, minPx: 13, maxPx: 50 };

/** Kamera, soweit für die Größe nötig: perspektivisch (Abstand, effektives vertikales Sichtfeld in Grad) oder
 *  orthografisch (sichtbare Höhe in Weltmetern). `hoehePx` = Höhe der Zeichenfläche in CSS-Pixeln. */
export type KugelKamera =
  | { art: 'perspektivisch'; abstandM: number; fovGrad: number; hoehePx: number }
  | { art: 'orthografisch'; sichtHoeheM: number; hoehePx: number };

/** Weltmeter je Bildschirmpixel am Ort der Kugel. */
export function meterProPixel(k: KugelKamera): number {
  if (k.hoehePx <= 0) return 0;
  const sichtHoehe = k.art === 'perspektivisch' ? 2 * Math.tan(((k.fovGrad * Math.PI) / 180) / 2) * Math.max(0, k.abstandM) : k.sichtHoeheM;
  return sichtHoehe / k.hoehePx;
}

/** Radius der Kugel in Weltmetern und am Bildschirm (Pixel), mit Weltgröße zwischen minPx und maxPx. */
export function kugelRadius(k: KugelKamera, g: KugelGroesse): { welt: number; px: number } {
  const mpp = meterProPixel(k);
  // Entartete Kamera (Abstand 0, Zeichenfläche 0): Weltgröße, damit die Skalierung nie 0 oder unendlich wird.
  if (!(mpp > 0) || !Number.isFinite(mpp)) return { welt: g.radiusM, px: g.minPx };
  const px = Math.min(g.maxPx, Math.max(g.minPx, g.radiusM / mpp));
  return { welt: px * mpp, px };
}

/**
 * Matcap einer hellgrauen, matt glänzenden Kugel als RGBA-Bild (sRGB-Werte 0–255). Zeile 0 ist die UNTERE
 * Bildzeile (Reihenfolge einer WebGL-Textur ohne flipY, Texturkoordinate v = 0): Pixel (x, y) entspricht der
 * Blickraum-Normale n = (u, v, √(1−u²−v²)) mit u, v ∈ [−1, 1], v nach oben.
 * Licht oben links vorn, diffuser Anteil mit Grundhelligkeit, Glanzpunkt, dunklerer Rand (Kontur gegen helle
 * Punkte). Außerhalb des Kreises: Randfarbe (wird nie abgelesen, verhindert aber Säume beim Filtern).
 */
export function matcapRgba(groesse: number): Uint8Array {
  const out = new Uint8Array(groesse * groesse * 4);
  const l = normiert([-0.45, 0.55, 0.7]);
  const h = normiert([l[0], l[1], l[2] + 1]); // Halbvektor zwischen Licht und Blick (0, 0, 1)
  for (let y = 0; y < groesse; y++) {
    for (let x = 0; x < groesse; x++) {
      const u = ((x + 0.5) / groesse) * 2 - 1;
      const v = ((y + 0.5) / groesse) * 2 - 1;
      const r2 = Math.min(1, u * u + v * v);
      const n: [number, number, number] = [u, v, Math.sqrt(1 - r2)];
      const nl = Math.max(0, n[0] * l[0] + n[1] * l[1] + n[2] * l[2]);
      const nh = Math.max(0, n[0] * h[0] + n[1] * h[1] + n[2] * h[2]);
      const rand = Math.pow(1 - n[2], 3); // 0 in der Mitte, 1 am Umriss
      const hell = Math.min(1, 0.32 + 0.68 * nl - 0.25 * rand + 0.55 * Math.pow(nh, 40));
      const w = Math.round(Math.max(0, hell) * 255);
      const i = (y * groesse + x) * 4;
      out[i] = w;
      out[i + 1] = w;
      out[i + 2] = w;
      out[i + 3] = 255;
    }
  }
  return out;
}

function normiert(a: [number, number, number]): [number, number, number] {
  const len = Math.hypot(a[0], a[1], a[2]);
  return [a[0] / len, a[1] / len, a[2] / len];
}
