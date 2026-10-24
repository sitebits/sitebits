// Begehen: reine Bewegungs- und Blickmathematik (Z oben, Yaw um +Z ab +X, Pitch ±89°).
// Keine Kollision, keine Schwerkraft; ohne DOM und ohne three.
import type { HalteAktion } from './tastenbelegung';

export type V3 = readonly [number, number, number];
/** Je −1…1; `seit` +1 = rechts. */
export interface WalkEingabe { vor: number; seit: number; hoch: number; schnell: boolean }
export interface WalkParameter { tempo: number; schnellFaktor: number; zeitkonstante: number; hoeheHalten: boolean; reduziert: boolean }
export interface WalkZustand { pos: V3; yaw: number; pitch: number; vel: V3 }

export const PITCH_MAX = (89 * Math.PI) / 180;
export const AUSTRITT_ABSTAND_M = 10;
const DT_MAX = 0.1;
const MAUS_FAKTOR = 0.002;
const TEMPO_MIN = 0.2;
const TEMPO_MAX = 50;
const RASTE_FAKTOR = 1.2;
const VIGNETTE_MAX = 0.6;
const GRAD = Math.PI / 180;

const klemme = (x: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, x));
const klemmePitch = (p: number): number => klemme(p, -PITCH_MAX, PITCH_MAX);
/** Bildzeit: nicht endliche Werte (Zeitgeber-Sprung, Rechenfehler) zählen als 0, sonst auf 0…DT_MAX geklemmt. */
const bildzeit = (dt: number): number => (Number.isFinite(dt) ? klemme(dt, 0, DT_MAX) : 0);

/** Winkel auf (−π, π] bringen. */
function normiereYaw(y: number): number {
  const r = ((((y + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
  return r === -Math.PI ? Math.PI : r;
}

export function blick(yaw: number, pitch: number): V3 {
  const c = Math.cos(pitch);
  return [c * Math.cos(yaw), c * Math.sin(yaw), Math.sin(pitch)];
}

export function yawPitch(dir: V3): { yaw: number; pitch: number } {
  const len = Math.hypot(dir[0], dir[1], dir[2]);
  if (len === 0) return { yaw: 0, pitch: 0 };
  return {
    yaw: Math.atan2(dir[1], dir[0]),
    pitch: Math.asin(klemme(dir[2] / len, -1, 1)),
  };
}

export function schritt(z: WalkZustand, e: WalkEingabe, p: WalkParameter, dt: number): WalkZustand {
  const d = bildzeit(dt);
  const yaw = normiereYaw(z.yaw);
  const pitch = klemmePitch(z.pitch);
  const vorn: V3 = p.hoeheHalten ? [Math.cos(yaw), Math.sin(yaw), 0] : blick(yaw, pitch);
  const rechts: V3 = [Math.sin(yaw), -Math.cos(yaw), 0];
  const hoch = p.hoeheHalten ? 0 : e.hoch;
  const r: [number, number, number] = [
    vorn[0] * e.vor + rechts[0] * e.seit,
    vorn[1] * e.vor + rechts[1] * e.seit,
    vorn[2] * e.vor + rechts[2] * e.seit + hoch,
  ];
  // Normieren nur bei Überlänge: Schrägfahrt ist nicht schneller, kleine Stickausschläge bleiben langsamer.
  const len = Math.hypot(r[0], r[1], r[2]);
  const skala = len > 1 ? 1 / len : 1;
  const tempo = p.tempo * (e.schnell ? p.schnellFaktor : 1);
  const ziel: V3 = [r[0] * skala * tempo, r[1] * skala * tempo, r[2] * skala * tempo];

  let vel: V3;
  if (p.reduziert || p.zeitkonstante <= 0) {
    vel = ziel;
  } else {
    const a = 1 - Math.exp(-d / p.zeitkonstante);
    vel = [
      z.vel[0] + (ziel[0] - z.vel[0]) * a,
      z.vel[1] + (ziel[1] - z.vel[1]) * a,
      z.vel[2] + (ziel[2] - z.vel[2]) * a,
    ];
  }
  const pos: V3 = [z.pos[0] + vel[0] * d, z.pos[1] + vel[1] * d, z.pos[2] + vel[2] * d];
  return { pos, yaw, pitch, vel };
}

export function mausBlick(
  yaw: number, pitch: number, dx: number, dy: number, empfindlichkeit: number, invertY: boolean,
): { yaw: number; pitch: number } {
  return {
    yaw: normiereYaw(yaw - dx * MAUS_FAKTOR * empfindlichkeit),
    pitch: klemmePitch(pitch - (invertY ? -1 : 1) * dy * MAUS_FAKTOR * empfindlichkeit),
  };
}

/** Stetiges Drehen (Tasten/Stick): `dreh` +1 = nach links, `neig` +1 = nach oben. */
export function drehe(
  yaw: number, pitch: number, dreh: number, neig: number, rateRadS: number, dt: number,
): { yaw: number; pitch: number } {
  const d = bildzeit(dt);
  return {
    yaw: normiereYaw(yaw + dreh * rateRadS * d),
    pitch: klemmePitch(pitch + neig * rateRadS * d),
  };
}

/** Blick um feste Winkel [rad] drehen (Controller: Rate und Bildzeit sind schon eingerechnet). */
export function dreheUm(yaw: number, pitch: number, dyaw: number, dpitch: number): { yaw: number; pitch: number } {
  if (!Number.isFinite(dyaw) || !Number.isFinite(dpitch)) return { yaw, pitch };
  return { yaw: normiereYaw(yaw + dyaw), pitch: klemmePitch(pitch + dpitch) };
}

export function drehSchritt(yaw: number, richtung: -1 | 1, schrittGrad: number): number {
  return normiereYaw(yaw + richtung * schrittGrad * GRAD);
}

export function neigSchritt(pitch: number, richtung: -1 | 1, schrittGrad: number): number {
  return klemmePitch(pitch + richtung * schrittGrad * GRAD);
}

/** Mausrad nach oben (deltaY < 0) = schneller; ×1,2 je Raste, Betrag von deltaY zählt nicht. */
export function tempoAusRad(tempo: number, deltaY: number): number {
  if (!Number.isFinite(deltaY)) return tempo;
  const raste = -Math.sign(deltaY);
  return klemme(tempo * Math.pow(RASTE_FAKTOR, raste), TEMPO_MIN, TEMPO_MAX);
}

/** Ansage nach dem Drehen am Rad: „Tempo 2,4 m/s“ (eine Nachkommastelle, ganze Werte ohne). */
export function tempoText(tempo: number): string {
  const r = Math.round(tempo * 10) / 10;
  return `Tempo ${String(r).replace('.', ',')} m/s`;
}

export function zielBeimAustritt(pos: V3, dir: V3, treffer: V3 | null): V3 {
  if (treffer) return treffer;
  const len = Math.hypot(dir[0], dir[1], dir[2]);
  if (len === 0) return pos;
  const k = AUSTRITT_ABSTAND_M / len;
  return [pos[0] + dir[0] * k, pos[1] + dir[1] * k, pos[2] + dir[2] * k];
}

/** Randabdunklung bei hoher Geschwindigkeit: 0 bis 1,5× Tempo, linear bis 0,6 bei 4× Tempo. */
export function vignette(geschwindigkeit: number, tempo: number, an: boolean): number {
  if (!an || tempo <= 0) return 0;
  const von = 1.5 * tempo;
  const bis = 4 * tempo;
  if (geschwindigkeit <= von) return 0;
  return Math.min(VIGNETTE_MAX, (VIGNETTE_MAX * (geschwindigkeit - von)) / (bis - von));
}

const BEWEGUNG: readonly HalteAktion[] = [
  'begehen.vor', 'begehen.zurueck', 'begehen.links', 'begehen.rechts',
  'begehen.runter', 'begehen.hoch', 'begehen.schnell',
];

/** Gehaltene Bewegungstasten; `leere()` bei Fokusverlust stoppt sofort alles. */
export class Haltezustand {
  private readonly halten = new Set<HalteAktion>();
  private schnellUm = false;

  druecke(a: HalteAktion): void {
    if (!BEWEGUNG.includes(a)) return;
    // Tastenwiederholung (bereits gehalten) schaltet nicht erneut um. Wechselt die Einstellung zwischen
    // „halten“ und „umschalten“, ruft der Aufrufer leere() — sonst gälte ein alter Umschaltstand weiter.
    if (a === 'begehen.schnell' && !this.halten.has(a)) this.schnellUm = !this.schnellUm;
    this.halten.add(a);
  }

  lasse(a: HalteAktion): void {
    this.halten.delete(a);
  }

  leere(): void {
    this.halten.clear();
    this.schnellUm = false;
  }

  gehalten(a: HalteAktion): boolean {
    return this.halten.has(a);
  }

  eingabe(schnellUmschalten: boolean): WalkEingabe {
    const g = (a: HalteAktion): number => (this.halten.has(a) ? 1 : 0);
    return {
      vor: g('begehen.vor') - g('begehen.zurueck'),
      seit: g('begehen.rechts') - g('begehen.links'),
      hoch: g('begehen.hoch') - g('begehen.runter'),
      schnell: schnellUmschalten ? this.schnellUm : this.halten.has('begehen.schnell'),
    };
  }
}

/**
 * Laufende Setz-Vorgänge im Begehen (solange einer läuft, steht die Kamera). Jeder Start liefert sein
 * eigenes „fertig“ (wirkt höchstens einmal). Zurücksetzen beim Ende des Begehens beginnt eine neue
 * Generation: ein spätes „fertig“ aus der alten ändert den Zähler der neuen nicht.
 */
export class SetzZaehler {
  private offen = 0;
  private generation = 0;
  get laeuft(): boolean {
    return this.offen > 0;
  }
  start(): () => void {
    const gen = this.generation;
    let erledigt = false;
    this.offen++;
    return () => {
      if (erledigt || gen !== this.generation) return;
      erledigt = true;
      this.offen--;
    };
  }
  zuruecksetzen(): void {
    this.generation++;
    this.offen = 0;
  }
}
