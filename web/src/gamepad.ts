// Gamepad über die Standard-Gamepad-API, erste Stufe der Eingabeschicht: Rohwerte → radiale Totzone →
// Ansprechkurve → benannte Aktionen mit Flanken. Die Aktionen sind ansichtsunabhängig benannt (bestätigen, zurück,
// Werkzeug …); was sie in Übersicht, Begehen, Panorama und Menü tun, steht in padModus.ts. Rein (Vitest): kein
// Zugriff auf navigator oder window; die Abfrage je Bild liegt in padSteuerung.ts.
export type PadAktion =
  | 'bestaetigen' | 'zurueck' | 'zielen' | 'kontext'
  | 'werkzeug-voriges' | 'werkzeug-naechstes' | 'runter' | 'hoch'
  | 'belegung' | 'menue' | 'schnell' | 'ausrichten'
  | 'tempo-hoch' | 'tempo-runter' | 'standpunkt-voriger' | 'standpunkt-naechster';

export const PAD_AKTIONEN: readonly PadAktion[] = [
  'bestaetigen', 'zurueck', 'zielen', 'kontext', 'werkzeug-voriges', 'werkzeug-naechstes', 'runter', 'hoch',
  'belegung', 'menue', 'schnell', 'ausrichten', 'tempo-hoch', 'tempo-runter', 'standpunkt-voriger', 'standpunkt-naechster',
];

/** Titel für die Umbelegung im Einstellungsdialog: was die Aktion in allen Ansichten bedeutet. */
export const PAD_TITEL: Record<PadAktion, string> = {
  bestaetigen: 'Setzen / Bestätigen',
  zurueck: 'Zurück / Abbrechen',
  zielen: 'Zielen / Fang',
  kontext: 'Kontextaktion (Begehen starten, Befund setzen)',
  'werkzeug-voriges': 'Voriges Werkzeug',
  'werkzeug-naechstes': 'Nächstes Werkzeug',
  runter: 'Abwärts / Herauszoomen',
  hoch: 'Aufwärts / Hineinzoomen',
  belegung: 'Belegung zeigen',
  menue: 'Menü',
  schnell: 'Schneller (Sprinten)',
  ausrichten: 'Ansicht ausrichten',
  'tempo-hoch': 'Tempo schneller',
  'tempo-runter': 'Tempo langsamer',
  'standpunkt-voriger': 'Voriger Standpunkt',
  'standpunkt-naechster': 'Nächster Standpunkt',
};

/** Vorgabe nach Spielkonvention (Standard-Mapping): unten bestätigt, rechts geht zurück, links zielt, oben ist die
 *  Kontextaktion; Schultertasten wechseln das Werkzeug, Auslöser sind die Höhen- bzw. Zoomachse. */
export const PAD_VORGABE: Record<PadAktion, number> = {
  bestaetigen: 0, zurueck: 1, zielen: 2, kontext: 3, 'werkzeug-voriges': 4, 'werkzeug-naechstes': 5, runter: 6, hoch: 7,
  belegung: 8, menue: 9, schnell: 10, ausrichten: 11, 'tempo-hoch': 12, 'tempo-runter': 13,
  'standpunkt-voriger': 14, 'standpunkt-naechster': 15,
};

/** Namen der Knöpfe 0–17 im Standard-Mapping (17 = Touchpad-Klick, nur bei manchen Controllern). */
export const PAD_KNOEPFE: readonly string[] = [
  'Knopf unten', 'Knopf rechts', 'Knopf links', 'Knopf oben', 'Linke Schultertaste', 'Rechte Schultertaste',
  'Linker Auslöser', 'Rechter Auslöser', 'Auswahl (Create/Ansicht)', 'Start (Options/Menü)', 'Linker Stick drücken',
  'Rechter Stick drücken', 'Steuerkreuz oben', 'Steuerkreuz unten', 'Steuerkreuz links', 'Steuerkreuz rechts', 'Mitte (Home)',
  'Touchpad-Klick',
];
export const PAD_KNOPF_MAX = PAD_KNOEPFE.length - 1;
/** Touchpad-Klick: zeigt zusätzlich die Belegung (wie Create/Ansicht), solange keine Aktion auf ihm liegt. */
export const KNOPF_TOUCHPAD = 17;
/** Wert für „Aktion nicht belegt“. */
export const PAD_NICHT_BELEGT = -1;

/** Radiale Totzone der Sticks und Sättigung außen. */
export const TOTZONE = 0.15;
export const AUSSEN = 0.95;
/** Totzone der analogen Auslöser. */
export const TRIGGER_TOTZONE = 0.08;
/** Linearer Anteil der Ansprechkurve: f(m) = k·m + (1−k)·m². */
export const KURVE_LINEAR = 0.2;
/** Knöpfe mit Analogwert (Auslöser) zählen ab diesem Wert als gedrückt. */
const TRIGGER_SCHWELLE = 0.5;
const SCHRITT_AUSLOESEN = 0.5;
const SCHRITT_SCHARF = 0.3;

export interface PadRoh { axes: readonly number[]; buttons: readonly { pressed: boolean; value: number }[]; id: string }
/** Ein Bild des Controllers nach Totzone und Kurve. Sticks: x rechts positiv, y oben positiv, Betrag ≤ 1.
 *  `hochAchse` = rechter minus linker Auslöser (analog, −1…1). */
export interface PadLage {
  links: { x: number; y: number };
  rechts: { x: number; y: number };
  hochAchse: number;
  gedrueckt: ReadonlySet<PadAktion>;
}

/** Radiale Totzone, danach so neu skaliert, dass der Rand der Totzone 0 und `aussen` 1 ergibt (darüber gesättigt). */
export function totzone(x: number, y: number, tz: number = TOTZONE, aussen: number = AUSSEN): [number, number] {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return [0, 0];
  const m = Math.hypot(x, y);
  if (m <= tz || m === 0) return [0, 0];
  const f = Math.min(1, (m - tz) / (aussen - tz)) / m;
  return [x * f, y * f];
}

/** Ansprechkurve auf einen Betrag 0…1: kleine Ausschläge fein, voller Ausschlag bleibt 1, monoton. */
export function kurve(m: number, linear: number = KURVE_LINEAR): number {
  const a = Math.min(1, Math.max(0, Number.isFinite(m) ? m : 0));
  return linear * a + (1 - linear) * a * a;
}

/** Stick: Totzone, dann die Kurve auf den Betrag (die Richtung bleibt). */
export function stick(x: number, y: number, tz: number = TOTZONE): { x: number; y: number } {
  const [dx, dy] = totzone(x, y, tz);
  const m = Math.hypot(dx, dy);
  if (m === 0) return { x: 0, y: 0 };
  const k = kurve(m) / m;
  return { x: dx * k + 0, y: dy * k + 0 };
}

/** Analoger Auslöser 0…1 mit Totzone und Kurve. */
export function ausloeser(wert: number): number {
  if (!Number.isFinite(wert) || wert <= TRIGGER_TOTZONE) return 0;
  return kurve((Math.min(1, wert) - TRIGGER_TOTZONE) / (1 - TRIGGER_TOTZONE));
}

const achse = (p: PadRoh, i: number): number => {
  const w = p.axes[i];
  return typeof w === 'number' ? w : 0;
};
const knopfWert = (p: PadRoh, i: number): number => {
  const b = i >= 0 ? p.buttons[i] : undefined;
  if (!b) return 0;
  return typeof b.value === 'number' && b.value > 0 ? b.value : b.pressed ? 1 : 0;
};

export function lies(p: PadRoh, belegung: Record<PadAktion, number>, o: { invertY: boolean; tz?: number }): PadLage {
  // Stick oben ist in der API negativ: y = −Achse. invertY kehrt nur den Blick (rechter Stick) um.
  const l = stick(achse(p, 0), -achse(p, 1), o.tz);
  const r = stick(achse(p, 2), -achse(p, 3), o.tz);
  const gedrueckt = new Set<PadAktion>();
  for (const a of PAD_AKTIONEN) {
    const b = p.buttons[belegung[a]];
    if (b && (b.pressed || b.value >= TRIGGER_SCHWELLE)) gedrueckt.add(a);
  }
  // Touchpad-Klick zeigt die Belegung, solange keine andere Aktion auf ihm liegt.
  const tp = p.buttons[KNOPF_TOUCHPAD];
  if (tp?.pressed && !PAD_AKTIONEN.some((a) => belegung[a] === KNOPF_TOUCHPAD)) gedrueckt.add('belegung');
  const hochAchse = ausloeser(knopfWert(p, belegung.hoch)) - ausloeser(knopfWert(p, belegung.runter));
  return {
    links: l,
    rechts: { x: r.x, y: o.invertY ? -r.y + 0 : r.y },
    hochAchse: hochAchse + 0,
    gedrueckt,
  };
}

export function kanten(vorher: ReadonlySet<PadAktion>, jetzt: ReadonlySet<PadAktion>): { runter: PadAktion[]; hoch: PadAktion[] } {
  return {
    runter: PAD_AKTIONEN.filter((a) => jetzt.has(a) && !vorher.has(a)),
    hoch: PAD_AKTIONEN.filter((a) => vorher.has(a) && !jetzt.has(a)),
  };
}

export type PadTyp = 'playstation' | 'xbox' | 'neutral';

/** Controllertyp aus `gamepad.id`. Chrome: „DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product:
 *  0ce6)“, Firefox: „054c-0ce6-…“. 054c = Sony (DualSense 0ce6, DualSense Edge 0df2, DualShock 4 05c4/09cc),
 *  045e = Microsoft. Alles andere (auch Nintendo mit vertauschtem A/B) bekommt neutrale Namen. */
export function controllerTyp(id: string): PadTyp {
  if (/vendor:\s*054c|^054c-|dualsense|dualshock|playstation|\bps[345]\b/i.test(id)) return 'playstation';
  if (/vendor:\s*045e|^045e-|xbox|xinput/i.test(id)) return 'xbox';
  return 'neutral';
}

/** Drehen in Schritten mit dem Stick: löst bei |wert| ≥ 0,5 einmal aus und wird erst unter 0,3 wieder scharf. */
export function stickSchritt(wert: number, bereit: boolean): { schritt: -1 | 0 | 1; bereit: boolean } {
  const a = Math.abs(wert);
  if (a < SCHRITT_SCHARF) return { schritt: 0, bereit: true };
  if (bereit && a >= SCHRITT_AUSLOESEN) return { schritt: wert > 0 ? 1 : -1, bereit: false };
  return { schritt: 0, bereit };
}
