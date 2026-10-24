// Ort und Ortszeit für die Live-Sonne (Etappe S2). Reine Logik ohne three.js/Giro3D (Vitest).
//   - Ort: originWorld des Pakets über proj4 nach WGS84. Lokales CRS → kein Ort, keine Sonne (nichts raten).
//   - Ortszeit: Europe/Berlin über Intl (MEZ/MESZ aus der Zeitzonendatenbank des Browsers, keine eigene Regel).
//     Die Zeitzone ist fest: siteBits-Pakete liegen in Deutschland; ein Feld im Manifest folgt, wenn nötig.
import proj4 from 'proj4';
import { classifyCrs } from './crs';
import type { Vec3 } from './manifest';
import { konvergenzGrad, sonnenstand } from './sonneMath';

export const ZEITZONE = 'Europe/Berlin';

export interface SonnenOrt {
  breiteGrad: number;
  laengeGrad: number;
  /** Richtung von geografisch Nord im Gitter (Grad, im Uhrzeigersinn ab Gitter-Nord). */
  konvergenzGrad: number;
}

/** Ort des Paketursprungs, oder null bei lokalem/nicht auswertbarem CRS. */
export function sonnenOrt(originWorld: Vec3, crsWkt: string | null): SonnenOrt | null {
  const c = classifyCrs(crsWkt);
  if (c.kind === 'local') return null;
  // EPSG: hinterlegte proj4-Definition; WKT: proj4 liest WKT1 (und einfache WKT2) selbst.
  const def = c.kind === 'epsg' ? c.def! : c.id;
  try {
    const p = proj4(def, 'WGS84');
    const [laenge, breite] = p.forward([originWorld[0], originWorld[1]]) as [number, number];
    if (!Number.isFinite(laenge) || !Number.isFinite(breite) || Math.abs(breite) > 90) return null;
    const nachGitter = (ll: [number, number]): [number, number] => p.inverse(ll) as [number, number];
    return { breiteGrad: breite, laengeGrad: laenge, konvergenzGrad: konvergenzGrad(breite, laenge, nachGitter) };
  } catch {
    return null;
  }
}

const formatierer = new Intl.DateTimeFormat('en-US', {
  timeZone: ZEITZONE, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric',
});

/** Wanduhr in Berlin zu einem Zeitpunkt. */
function wanduhr(t: Date): { jahr: number; monat: number; tag: number; stunde: number; minute: number; sekunde: number } {
  const teile: Record<string, number> = {};
  for (const p of formatierer.formatToParts(t)) if (p.type !== 'literal') teile[p.type] = Number(p.value);
  return { jahr: teile.year!, monat: teile.month!, tag: teile.day!, stunde: teile.hour!, minute: teile.minute!, sekunde: teile.second! };
}

/** Abstand Ortszeit − UTC in Minuten zum Zeitpunkt t (60 MEZ, 120 MESZ). */
export function versatzMin(t: Date): number {
  const w = wanduhr(t);
  const alsUtc = Date.UTC(w.jahr, w.monat - 1, w.tag, w.stunde, w.minute, w.sekunde);
  return Math.round((alsUtc - Math.floor(t.getTime() / 1000) * 1000) / 60_000);
}

export interface Ortszeit {
  jahr: number;
  /** Tag im Jahr, 1 = 1. Januar. */
  tagImJahr: number;
  /** Minuten seit Mitternacht (Wanduhr). */
  minuten: number;
  kuerzel: 'MEZ' | 'MESZ';
}

const tagImJahrVon = (jahr: number, monat: number, tag: number): number =>
  Math.round((Date.UTC(jahr, monat - 1, tag) - Date.UTC(jahr, 0, 1)) / 86_400_000) + 1;

export function ortszeit(t: Date): Ortszeit {
  const w = wanduhr(t);
  return { jahr: w.jahr, tagImJahr: tagImJahrVon(w.jahr, w.monat, w.tag), minuten: w.stunde * 60 + w.minute + w.sekunde / 60,
    kuerzel: versatzMin(t) > 60 ? 'MESZ' : 'MEZ' };
}

export const tageImJahr = (jahr: number): number => (jahr % 4 === 0 && (jahr % 100 !== 0 || jahr % 400 === 0) ? 366 : 365);

/** Zeitpunkt zur Berliner Wanduhr. Lücke bei der Umstellung auf MESZ (02:00–03:00 gibt es nicht): eine Stunde
 *  später, wie die Uhr. Doppelte Stunde bei der Rückstellung: die erste (noch MESZ). */
export function ausOrtszeit(jahr: number, tagImJahr: number, minuten: number): Date {
  const wand = Date.UTC(jahr, 0, tagImJahr, 0, 0) + minuten * 60_000;
  // Versatz vor und nach der Stelle: Rückstellung → den größeren (erste Stunde), Lücke → den kleineren (+1 h).
  const vorher = versatzMin(new Date(wand - 180 * 60_000));
  const nachher = versatzMin(new Date(wand + 180 * 60_000));
  for (const v of [Math.max(vorher, nachher), Math.min(vorher, nachher)]) {
    const t = new Date(wand - v * 60_000);
    if (versatzMin(t) === v) return t;
  }
  return new Date(wand - Math.min(vorher, nachher) * 60_000);
}

/** Auf- und Untergang (Mittelpunkt bei −0,833° geometrisch: Refraktion + Sonnenradius) am Berliner Kalendertag;
 *  null bei Polartag/-nacht. Bisektion auf dem Höhenverlauf, Genauigkeit ~1 s. */
export function aufUntergang(jahr: number, tagImJahr: number, ort: Pick<SonnenOrt, 'breiteGrad' | 'laengeGrad'>):
  { auf: Date | null; unter: Date | null } {
  const HORIZONT = -0.833;
  const h = (ms: number): number => sonnenstand(new Date(ms), ort.breiteGrad, ort.laengeGrad).hoeheGrad - HORIZONT;
  const start = ausOrtszeit(jahr, tagImJahr, 0).getTime();
  const ende = ausOrtszeit(jahr, tagImJahr + 1, 0).getTime();
  const schritt = 15 * 60_000;
  let auf: Date | null = null; let unter: Date | null = null;
  for (let a = start; a < ende; a += schritt) {
    const b = Math.min(a + schritt, ende);
    const ha = h(a); const hb = h(b);
    if ((ha < 0) === (hb < 0)) continue;
    let lo = a; let hi = b;
    while (hi - lo > 1000) { const m = (lo + hi) / 2; if ((h(m) < 0) === (ha < 0)) lo = m; else hi = m; }
    const t = new Date(Math.round((lo + hi) / 2));
    if (ha < 0) auf ??= t; else unter = t;
  }
  return { auf, unter };
}

/** „13:05“ */
export function uhrzeitText(minuten: number): string {
  const m = Math.round(minuten) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}
