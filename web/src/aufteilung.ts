// Aufteilung der mobilen Empfängeransicht zwischen 3D-Ansicht und Befundbereich (Trenner in empfang.ts). Rein (Vitest).
// Der Wert ist der Anteil des Befundbereichs an der verfügbaren Länge — hochkant die Höhe unter der Befundliste
// (Befund unten), quer die Breite (Befund rechts). Er ist zugleich aria-valuenow des Trenners (in %), weil der Trenner
// die Befundkarte steuert (aria-controls). Je Lage ein eigener Wert: ein Höhen- und ein Breitenanteil meinen
// Verschiedenes. Grenzen aus zwei Quellen: feste Anteile (hochkant höchstens 60 % wie die bisherige Karte, damit die
// Ansicht nie zum Streifen wird) und Mindestpixel für beide Bereiche (Kopfzeile der Karte mit 44-px-Knöpfen bzw. ein
// noch bedienbares Bild); auf sehr kleinen Flächen gehen die Pixel vor. Im selben Eintrag: ob die Empfängeransicht
// Messbeschriftungen zeigt (empfang.ts, messBeschriftung.ts).
export type Lage = 'hoch' | 'quer';
export interface Grenzen { min: number; max: number }
export interface Anzeige {
  schema: 1;
  hoch: number;
  quer: number;
  /** Messbeschriftungen in der Empfängeransicht zeigen (Schalter „Messwerte zeigen“ in der Befundkarte). */
  messwerte: boolean;
}

export const ANZEIGE_SCHLUESSEL = 'sv-empfang-anzeige';
/** Dicke des Trenners (Greiffläche, ≥ 24 px wie WCAG 2.5.8). */
export const TRENNER_PX = 24;
/** Kleinste Größe beider Bereiche in Pixeln. */
export const MIN_PX = 120;
/** Ein Tastenschritt. */
export const SCHRITT = 0.05;
export const ANTEIL = {
  hoch: { min: 0.25, max: 0.6, vorgabe: 0.45 },
  quer: { min: 0.3, max: 0.65, vorgabe: 0.45 },
} as const;
/** Doppeltipp: zweiter Tipp höchstens so viele ms und px vom ersten entfernt. */
const DOPPEL_MS = 350;
const DOPPEL_PX = 24;

export function grenzen(lage: Lage, laenge: number, anteile: Record<Lage, Grenzen> = ANTEIL): Grenzen {
  const a = anteile[lage];
  const min = Math.max(a.min, MIN_PX / laenge);
  const max = Math.min(a.max, (laenge - TRENNER_PX - MIN_PX) / laenge);
  if (min <= max) return { min, max };
  // Zu klein für beide Mindestgrößen: halbe-halbe, damit wenigstens beide etwas Platz haben.
  const mitte = Math.min(0.9, Math.max(0.1, (laenge - TRENNER_PX) / 2 / laenge));
  return { min: mitte, max: mitte };
}

export const klemme = (anteil: number, g: Grenzen): number =>
  Number.isFinite(anteil) ? Math.min(g.max, Math.max(g.min, anteil)) : g.min;

/**
 * Taste auf dem Trenner → neuer Anteil, null = nicht seine Taste. Die Pfeile folgen der Richtung, in die der Trenner
 * wandert (hochkant ↑ ↓, quer ← →): hochkant ↑ bzw. quer ← vergrößert den Befundbereich. Pos1/Ende wie im Muster
 * „Window Splitter“ (WAI-ARIA APG): kleinster bzw. größter Befundbereich.
 */
export function schritt(lage: Lage, taste: string, anteil: number, g: Grenzen): number | null {
  const mehr = lage === 'hoch' ? 'ArrowUp' : 'ArrowLeft';
  const weniger = lage === 'hoch' ? 'ArrowDown' : 'ArrowRight';
  if (taste === mehr) return klemme(anteil + SCHRITT, g);
  if (taste === weniger) return klemme(anteil - SCHRITT, g);
  if (taste === 'Home') return g.min;
  if (taste === 'End') return g.max;
  return null;
}

/** Doppeltipp/-klick bzw. Eingabe: zwischen Standard (Vorgabe) und „3D groß“ (kleinster Befundbereich) wechseln. */
export function umschalten(anteil: number, g: Grenzen, vorgabe: number): number {
  return anteil - g.min < 0.01 ? klemme(vorgabe, g) : g.min;
}

export interface Tipp { t: number; x: number; y: number }
export const doppeltipp = (vorher: Tipp | null, jetzt: Tipp): boolean =>
  vorher !== null && jetzt.t - vorher.t <= DOPPEL_MS && Math.hypot(jetzt.x - vorher.x, jetzt.y - vorher.y) <= DOPPEL_PX;

/**
 * Zeigerstelle (hochkant y, quer x) → Anteil: die Mitte des Trenners liegt unter dem Finger. Die Fläche reicht von
 * `anfang` (hochkant: Unterkante der Befundliste) über `laenge` bis zum Fensterrand, an dem der Befund liegt.
 */
export const anteilAusZeiger = (_lage: Lage, stelle: number, f: { anfang: number; laenge: number }): number =>
  (f.anfang + f.laenge - stelle - TRENNER_PX / 2) / f.laenge;

export const vorgabeAnzeige = (): Anzeige => ({ schema: 1, hoch: ANTEIL.hoch.vorgabe, quer: ANTEIL.quer.vorgabe, messwerte: true });

const anteilAus = (x: unknown, lage: Lage): number =>
  typeof x === 'number' && Number.isFinite(x) ? klemme(x, ANTEIL[lage]) : ANTEIL[lage].vorgabe;

/** Gespeicherte Aufteilung (fremde Daten): jedes Feld einzeln geprüft und geklemmt, sonst Vorgabe. */
export function ladeAnzeige(raw: string | null): Anzeige {
  const v = vorgabeAnzeige();
  let roh: unknown;
  try { roh = raw === null ? null : JSON.parse(raw); } catch { return v; }
  if (typeof roh !== 'object' || roh === null || (roh as { schema?: unknown }).schema !== 1) return v;
  const r = roh as Record<string, unknown>;
  return {
    schema: 1,
    hoch: anteilAus(r.hoch, 'hoch'),
    quer: anteilAus(r.quer, 'quer'),
    messwerte: typeof r.messwerte === 'boolean' ? r.messwerte : v.messwerte,
  };
}

export const speichereAnzeige = (a: Anzeige): string => JSON.stringify(a);
