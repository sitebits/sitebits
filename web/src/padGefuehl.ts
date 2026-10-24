// Gefühl des Controllers, zweite Stufe der Eingabeschicht: aus dem Stickwert (nach Totzone und Kurve, gamepad.ts)
// wird eine Drehrate mit Beschleunigung beim Halten und sanftem Nachlauf. Alles rechnet mit der Bildzeit dt, so dass
// 60 und 144 Bilder je Sekunde gleich wirken. Bei reduzierter Bewegung: keine Beschleunigung, kein Nachlauf. Rein (Vitest).

/** Ab diesem Ausschlag (Betrag) zählt der Stick als „voll gehalten“. */
export const BESCHLEUNIGUNG_AB = 0.9;
/** So lange muss er voll gehalten sein, bevor die Drehung beschleunigt [s]. */
export const BESCHLEUNIGUNG_VERZUG_S = 0.2;
/** In dieser Zeit wächst die Rate auf das BESCHLEUNIGUNG_FAKTOR-Fache [s]. */
export const BESCHLEUNIGUNG_DAUER_S = 0.35;
export const BESCHLEUNIGUNG_FAKTOR = 1.75;
/** Zeitkonstante des Nachlaufs (Anlaufen und Abbremsen) [s]. */
export const NACHLAUF_S = 0.05;
const DT_MAX = 0.1;
const RUHE = 1e-4;

/** Drehraten in rad/s (bzw. °/s im Panorama) bei vollem Ausschlag ohne Beschleunigung. */
export interface DrehRaten { waagerecht: number; senkrecht: number }
export interface DrehZustand { rateX: number; rateY: number; gehalten: number }
export const drehRuhe = (): DrehZustand => ({ rateX: 0, rateY: 0, gehalten: 0 });

const bildzeit = (dt: number): number => (Number.isFinite(dt) ? Math.min(DT_MAX, Math.max(0, dt)) : 0);

/** Beschleunigungsfaktor nach `gehalten` Sekunden voll gehaltenem Stick. */
export function beschleunigung(gehalten: number): number {
  const t = (gehalten - BESCHLEUNIGUNG_VERZUG_S) / BESCHLEUNIGUNG_DAUER_S;
  return 1 + (BESCHLEUNIGUNG_FAKTOR - 1) * Math.min(1, Math.max(0, t));
}

/**
 * Ein Bild: `x`/`y` = Stick (−1…1, nach Kurve), Ergebnis = Winkeländerung in diesem Bild (dx waagerecht, dy senkrecht)
 * und der neue Zustand. `faktor` = Empfindlichkeit × Zielen × Sichtfeld-Anteil.
 */
export function drehSchritt(
  z: DrehZustand, x: number, y: number, dt: number, o: { raten: DrehRaten; faktor: number; reduziert: boolean },
): { z: DrehZustand; dx: number; dy: number } {
  const d = bildzeit(dt);
  const m = Math.hypot(x, y);
  const gehalten = !o.reduziert && m >= BESCHLEUNIGUNG_AB ? z.gehalten + d : 0;
  // Beschleunigt wird nur waagerecht (wie in Spielen): beim Neigen überschösse der Blick.
  const zielX = x * o.raten.waagerecht * o.faktor * beschleunigung(gehalten);
  const zielY = y * o.raten.senkrecht * o.faktor;
  if (o.reduziert) return { z: { rateX: zielX, rateY: zielY, gehalten }, dx: zielX * d, dy: zielY * d };
  // Exponentielles Annähern an die Zielrate, der Winkel als exaktes Integral darüber: so ist das Ergebnis von der
  // Bildrate unabhängig (zwei halbe Bilder = ein ganzes, solange das Ziel gleich bleibt).
  const a = 1 - Math.exp(-d / NACHLAUF_S);
  let rateX = z.rateX + (zielX - z.rateX) * a;
  let rateY = z.rateY + (zielY - z.rateY) * a;
  const dx = zielX * d + (z.rateX - zielX) * NACHLAUF_S * a;
  const dy = zielY * d + (z.rateY - zielY) * NACHLAUF_S * a;
  if (x === 0 && Math.abs(rateX) < RUHE) rateX = 0;
  if (y === 0 && Math.abs(rateY) < RUHE) rateY = 0;
  return { z: { rateX, rateY, gehalten }, dx, dy };
}

/** Zoomfaktor eines Bilds aus der Auslöserachse (−1…1): exponentiell, damit er in jedem Maßstab gleich wirkt.
 *  Positiv = hinein (Abstand bzw. Sichtfeld wird kleiner). */
export const ZOOM_RATE = 1.6;
export function zoomFaktor(achse: number, dt: number): number {
  return Math.exp(-ZOOM_RATE * achse * bildzeit(dt));
}
