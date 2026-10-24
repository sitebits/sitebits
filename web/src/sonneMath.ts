// Sonnenstand für die Vorschau im Viewer (Etappe S2). Reine Logik ohne three.js (Vitest).
//   - Formeln der NOAA-Sonnenrechnung (nach Meeus, „Astronomical Algorithms“): Abweichung gegen NREL SPA
//     (pvlib) im Test ≤ 0,05° für 2000–2030. Genauer als SunCalc (~0,08°), ohne Abhängigkeit.
//   - Gemessen wird nicht hier: Verschattung und Ertrag rechnet der Server mit SPA (Sonnenbericht).
//   - Höhe: geometrisch und scheinbar (Refraktion nach NOAA, Normalatmosphäre); Schatten nutzen die scheinbare.
//   - Azimut geografisch (Nord = 0°, im Uhrzeigersinn). Für die Szene zählt Gitter-Nord des Pakets (UTM):
//     `konvergenzGrad` ist die Richtung von geografisch Nord im Gitter (Potsdam, UTM 33: ≈ +1,5°, westlich des Mittelmeridians liegt geografisch Nord östlich von Gitter-Nord).
import type { Vec3 } from './manifest';

const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;

export interface Sonnenstand {
  /** Geografischer Azimut, Grad, Nord = 0, Ost = 90. */
  azimutGrad: number;
  /** Höhe über dem Horizont ohne Refraktion, Grad. */
  hoeheGrad: number;
  /** Höhe mit Refraktion (so sieht man die Sonne, so fallen die Schatten), Grad. */
  hoeheScheinbarGrad: number;
}

/** Refraktion in Grad für eine geometrische Höhe (NOAA-Näherung, 1013 hPa / 10 °C). */
export function refraktionGrad(h: number): number {
  if (h > 85) return 0;
  const t = Math.tan(h * RAD);
  const sek = h > 5 ? 58.1 / t - 0.07 / t ** 3 + 0.000086 / t ** 5
    : h > -0.575 ? 1735 + h * (-518.2 + h * (103.4 + h * (-12.79 + h * 0.711)))
      : -20.772 / t;
  return sek / 3600;
}

export function sonnenstand(zeit: Date, breiteGrad: number, laengeGrad: number): Sonnenstand {
  const jd = zeit.getTime() / 86_400_000 + 2_440_587.5;
  const T = (jd - 2_451_545) / 36_525; // julianische Jahrhunderte seit J2000
  const L0 = ((280.46646 + T * (36_000.76983 + T * 0.0003032)) % 360 + 360) % 360;
  const M = 357.52911 + T * (35_999.05029 - 0.0001537 * T);
  const e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T);
  const C = Math.sin(M * RAD) * (1.914602 - T * (0.004817 + 0.000014 * T)) +
    Math.sin(2 * M * RAD) * (0.019993 - 0.000101 * T) + Math.sin(3 * M * RAD) * 0.000289;
  const omega = 125.04 - 1934.136 * T;
  const lambda = L0 + C - 0.00569 - 0.00478 * Math.sin(omega * RAD); // scheinbare Länge
  const eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60;
  const eps = eps0 + 0.00256 * Math.cos(omega * RAD);
  const dekl = Math.asin(Math.sin(eps * RAD) * Math.sin(lambda * RAD));
  const y = Math.tan((eps / 2) * RAD) ** 2;
  // Zeitgleichung in Minuten
  const eqt = 4 * DEG * (y * Math.sin(2 * L0 * RAD) - 2 * e * Math.sin(M * RAD) +
    4 * e * y * Math.sin(M * RAD) * Math.cos(2 * L0 * RAD) - 0.5 * y * y * Math.sin(4 * L0 * RAD) -
    1.25 * e * e * Math.sin(2 * M * RAD));
  const minutenUtc = ((jd + 0.5) % 1) * 1440;
  const wahreOrtszeit = (((minutenUtc + eqt + 4 * laengeGrad) % 1440) + 1440) % 1440;
  const stundenwinkel = wahreOrtszeit / 4 - 180;
  const phi = breiteGrad * RAD;
  const cosZ = Math.sin(phi) * Math.sin(dekl) + Math.cos(phi) * Math.cos(dekl) * Math.cos(stundenwinkel * RAD);
  const zenit = Math.acos(Math.min(1, Math.max(-1, cosZ)));
  const hoehe = 90 - zenit * DEG;
  // Azimut über atan2 (stabil auch am Pol und im Zenit)
  const az = Math.atan2(Math.sin(stundenwinkel * RAD),
    Math.cos(stundenwinkel * RAD) * Math.sin(phi) - Math.tan(dekl) * Math.cos(phi)) * DEG + 180;
  return { azimutGrad: ((az % 360) + 360) % 360, hoeheGrad: hoehe, hoeheScheinbarGrad: hoehe + refraktionGrad(hoehe) };
}

/** Einheitsvektor zur Sonne in Weltkoordinaten (X = Gitter-Ost, Y = Gitter-Nord, Z = oben). */
export function sonnenRichtung(s: Pick<Sonnenstand, 'azimutGrad' | 'hoeheScheinbarGrad'>, konvergenzGrad: number): Vec3 {
  const a = (s.azimutGrad + konvergenzGrad) * RAD; const h = s.hoeheScheinbarGrad * RAD;
  return [Math.sin(a) * Math.cos(h), Math.cos(a) * Math.cos(h), Math.sin(h)];
}

/** Richtung von geografisch Nord im Gitter, Grad (im Uhrzeigersinn ab Gitter-Nord). `nachGitter` projiziert
 *  [Länge, Breite] in Gitterkoordinaten [Ost, Nord] — im Viewer proj4 mit der Paketdefinition. */
export function konvergenzGrad(breiteGrad: number, laengeGrad: number, nachGitter: (lonLat: [number, number]) => [number, number]): number {
  const a = nachGitter([laengeGrad, breiteGrad]);
  const b = nachGitter([laengeGrad, breiteGrad + 0.001]);
  return Math.atan2(b[0] - a[0], b[1] - a[1]) * DEG;
}

/** Tagesbahn: Sonnenstände über einen Tag (UTC-Tag des Datums) im Abstand `schrittMin`, nur über dem Horizont. */
export function tagesbahn(tag: Date, breiteGrad: number, laengeGrad: number, schrittMin = 10): { zeit: Date; stand: Sonnenstand }[] {
  const start = Date.UTC(tag.getUTCFullYear(), tag.getUTCMonth(), tag.getUTCDate());
  const aus: { zeit: Date; stand: Sonnenstand }[] = [];
  for (let m = 0; m <= 1440; m += schrittMin) {
    const zeit = new Date(start + m * 60_000);
    const stand = sonnenstand(zeit, breiteGrad, laengeGrad);
    if (stand.hoeheScheinbarGrad > 0) aus.push({ zeit, stand });
  }
  return aus;
}
