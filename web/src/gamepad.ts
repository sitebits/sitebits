// Gamepad über die Standard-Gamepad-API: Achsen mit radialer Totzone, Knöpfe als benannte Aktionen,
// Kantenerkennung. Rein (Vitest): kein Zugriff auf navigator oder window; die Abfrage je Bild liegt in
// padQuelle.ts.
export type PadAktion =
  | 'zielen' | 'setzen' | 'werkzeug-voriges' | 'werkzeug-naechstes' | 'befund' | 'standpunkt-voriger'
  | 'standpunkt-naechster' | 'hoch' | 'runter' | 'bestaetigen' | 'zurueck' | 'karte';

export const PAD_AKTIONEN: readonly PadAktion[] = [
  'zielen', 'setzen', 'werkzeug-voriges', 'werkzeug-naechstes', 'befund', 'standpunkt-voriger',
  'standpunkt-naechster', 'hoch', 'runter', 'bestaetigen', 'zurueck', 'karte',
];

export const PAD_TITEL: Record<PadAktion, string> = {
  zielen: 'Zielen',
  setzen: 'Punkt setzen',
  'werkzeug-voriges': 'Voriges Werkzeug',
  'werkzeug-naechstes': 'Nächstes Werkzeug',
  befund: 'Befund',
  'standpunkt-voriger': 'Voriger Standpunkt',
  'standpunkt-naechster': 'Nächster Standpunkt',
  hoch: 'Aufwärts',
  runter: 'Abwärts',
  bestaetigen: 'Bestätigen',
  zurueck: 'Zurück (Begehen beenden)',
  karte: 'Karte',
};

/** Standard-Belegung: Auslöser links/rechts = Zielen/Setzen, Schultertasten = Werkzeug, Y = Befund,
 *  Steuerkreuz = Standpunkt (links/rechts) und Höhe (oben/unten), A = Bestätigen, B = Zurück, Start = Karte.
 *  X (2) bleibt frei. */
export const PAD_VORGABE: Record<PadAktion, number> = {
  zielen: 6, setzen: 7, 'werkzeug-voriges': 4, 'werkzeug-naechstes': 5, befund: 3,
  'standpunkt-voriger': 14, 'standpunkt-naechster': 15, hoch: 12, runter: 13, bestaetigen: 0, zurueck: 1, karte: 9,
};

/** Namen der Knöpfe 0–16 im Standard-Mapping. */
export const PAD_KNOEPFE: readonly string[] = [
  'A (unten)', 'B (rechts)', 'X (links)', 'Y (oben)', 'Linke Schultertaste', 'Rechte Schultertaste',
  'Linker Auslöser', 'Rechter Auslöser', 'Zurück (Auswahl)', 'Start (Menü)', 'Linker Stick drücken',
  'Rechter Stick drücken', 'Steuerkreuz oben', 'Steuerkreuz unten', 'Steuerkreuz links', 'Steuerkreuz rechts', 'Mitte (Home)',
];
export const PAD_KNOPF_MAX = PAD_KNOEPFE.length - 1;
/** Wert für „Aktion nicht belegt“. */
export const PAD_NICHT_BELEGT = -1;

export const TOTZONE = 0.15;
/** Auslöser (Knöpfe mit Analogwert) zählen ab diesem Wert als gedrückt. */
const TRIGGER_SCHWELLE = 0.5;
const SCHRITT_AUSLOESEN = 0.5;
const SCHRITT_SCHARF = 0.3;

export interface PadRoh { axes: readonly number[]; buttons: readonly { pressed: boolean; value: number }[]; id: string }
export interface PadLage { vor: number; seit: number; dreh: number; neig: number; gedrueckt: ReadonlySet<PadAktion> }

/** Radiale Totzone, danach so neu skaliert, dass der Rand der Totzone 0 und voller Ausschlag 1 ergibt. */
export function totzone(x: number, y: number, tz: number = TOTZONE): [number, number] {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return [0, 0];
  const m = Math.hypot(x, y);
  if (m <= tz || m === 0) return [0, 0];
  const f = (Math.min(m, 1) - tz) / (1 - tz) / m;
  return [x * f, y * f];
}

const achse = (p: PadRoh, i: number): number => {
  const w = p.axes[i];
  return typeof w === 'number' ? w : 0;
};

export function lies(p: PadRoh, belegung: Record<PadAktion, number>, o: { invertY: boolean; tz?: number }): PadLage {
  const [lx, ly] = totzone(achse(p, 0), achse(p, 1), o.tz);
  const [rx, ry] = totzone(achse(p, 2), achse(p, 3), o.tz);
  const gedrueckt = new Set<PadAktion>();
  for (const a of PAD_AKTIONEN) {
    const b = p.buttons[belegung[a]];
    if (b && (b.pressed || b.value >= TRIGGER_SCHWELLE)) gedrueckt.add(a);
  }
  // Stick oben ist negativ: vor = −LY; rechts drehen = Blickwinkel kleiner = −RX; oben schauen = −RY.
  const neig = o.invertY ? ry : -ry;
  return { vor: -ly + 0, seit: lx, dreh: -rx + 0, neig: neig + 0, gedrueckt };
}

export function kanten(vorher: ReadonlySet<PadAktion>, jetzt: ReadonlySet<PadAktion>): { runter: PadAktion[]; hoch: PadAktion[] } {
  return {
    runter: PAD_AKTIONEN.filter((a) => jetzt.has(a) && !vorher.has(a)),
    hoch: PAD_AKTIONEN.filter((a) => vorher.has(a) && !jetzt.has(a)),
  };
}

export function controllerTyp(id: string): 'xbox' | 'playstation' | 'generisch' {
  if (/xbox|vendor:\s*045e/i.test(id)) return 'xbox';
  if (/dualsense|dualshock|playstation|vendor:\s*054c|\bps[345]\b/i.test(id)) return 'playstation';
  return 'generisch';
}

/** Drehen in Schritten mit dem Stick: löst bei |wert| ≥ 0,5 einmal aus und wird erst unter 0,3 wieder scharf. */
export function stickSchritt(wert: number, bereit: boolean): { schritt: -1 | 0 | 1; bereit: boolean } {
  const a = Math.abs(wert);
  if (a < SCHRITT_SCHARF) return { schritt: 0, bereit: true };
  if (bereit && a >= SCHRITT_AUSLOESEN) return { schritt: wert > 0 ? 1 : -1, bereit: false };
  return { schritt: 0, bereit };
}
