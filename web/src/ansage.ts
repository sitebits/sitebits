// Wert-Ansage am Fadenkreuz: reine Logik ohne DOM. Ein Wert wird erst angesagt, wenn er sich eine halbe
// Sekunde lang nicht um mehr als einen Zentimeter bewegt hat; Treffer/kein Treffer wird einmal beim
// Wechsel angesagt. So spricht die Hilfstechnik nicht bei jedem Bild.

export interface AnsageZustand {
  /** Wert, an dem die Ruhezeit gemessen wird. */
  anker: number | null;
  /** Zeitpunkt, seit dem der Wert (innerhalb der Schwelle) um den Anker liegt [ms]. */
  ankerZeit: number;
  /** Zuletzt angesagter Wert. */
  angesagt: number | null;
  /** Letzter Zustand Treffer / kein Treffer (null vor dem ersten Bild). */
  treffer: boolean | null;
}

/** Ruhezeit, nach der ein geänderter Wert angesagt wird [ms]. */
export const RUHE_MS = 500;
/** Änderungen bis zu dieser Größe gelten als Ruhe [m]. */
export const SCHWELLE_M = 0.01;

export const startZustand: AnsageZustand = { anker: null, ankerZeit: 0, angesagt: null, treffer: null };

export function ansageSchritt(
  z: AnsageZustand,
  e: { t: number; treffer: boolean; wertM: number | null; text: (m: number) => string },
): { zustand: AnsageZustand; ansage: string | null } {
  if (z.treffer !== e.treffer) {
    // Wechsel: einmal ansagen, Anker neu beginnen (bei Treffer gleich mit dem aktuellen Wert).
    const anker = e.treffer ? e.wertM : null;
    return {
      zustand: { anker, ankerZeit: e.t, angesagt: null, treffer: e.treffer },
      ansage: e.treffer ? 'Treffer' : 'kein Treffer',
    };
  }
  if (!e.treffer || e.wertM === null) return { zustand: z, ansage: null };
  let { anker, ankerZeit } = z;
  if (anker === null || Math.abs(e.wertM - anker) > SCHWELLE_M) {
    anker = e.wertM;
    ankerZeit = e.t;
  }
  if (e.t - ankerZeit >= RUHE_MS && (z.angesagt === null || Math.abs(anker - z.angesagt) > SCHWELLE_M)) {
    return { zustand: { anker, ankerZeit, angesagt: anker, treffer: true }, ansage: e.text(anker) };
  }
  return { zustand: { ...z, anker, ankerZeit }, ansage: null };
}

const zahl = (n: number): string => n.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** „4,32 Meter“; unter einem Meter „43 Zentimeter“. */
export function formatiereMeter(m: number): string {
  const cm = Math.round(m * 100);
  if (Math.abs(cm) < 100) return `${cm} Zentimeter`;
  return `${zahl(m)} Meter`;
}

/** Kurzform für die Platte am Fadenkreuz: „≈ 4,32 m“. */
export function kurzText(m: number): string {
  return `≈ ${zahl(m)} m`;
}

const ART: Record<'ziel' | 'strecke' | 'hoehe', string> = {
  ziel: 'bis Zielpunkt',
  strecke: 'zum letzten Punkt',
  hoehe: 'Höhenunterschied',
};

/** Gesprochener Text: „ungefähr 4,32 Meter bis Zielpunkt[, Wandfläche]“. */
export function liveText(o: { wertM: number; art: 'ziel' | 'strecke' | 'hoehe'; klasse?: string }): string {
  return `ungefähr ${formatiereMeter(o.wertM)} ${ART[o.art]}${o.klasse ? `, ${o.klasse}` : ''}`;
}

/** Vorschau-Takt im Begehen: nur bei geänderter Kamera und höchstens jedes dritte Bild neu picken. */
export const VORSCHAU_ABSTAND_BILDER = 3;
/** Bei ruhender Kamera spätestens nach so vielen Bildern neu picken (nachladende Kacheln). */
export const VORSCHAU_RUHE_BILDER = 30;
export function vorschauNoetig(o: { hat: boolean; poseGleich: boolean; bilderSeitPick: number; erzwingen?: boolean }): boolean {
  if (o.erzwingen || !o.hat) return true;
  if (o.poseGleich) return o.bilderSeitPick >= VORSCHAU_RUHE_BILDER;
  return o.bilderSeitPick >= VORSCHAU_ABSTAND_BILDER;
}

// --- Reihenfolge der Ansagen ----------------------------------------------------------------------------------
// Eine Live-Region spricht nur den letzten Text; zwei Meldungen kurz hintereinander verschlucken die erste. Eine
// Ansage mit Vorrang (z. B. „Befund 2 von 3: …“ beim Öffnen eines Befundlinks) steht deshalb eine Weile; was in
// dieser Zeit gemeldet wird, wartet und folgt danach im Abstand. Eine eben erst gezeigte Meldung, die der Vorrang
// verdrängt, wird danach wiederholt. Ohne Vorrang bleibt alles wie bisher: jede Meldung sofort.

/** So lange steht eine Ansage mit Vorrang, bevor Wartendes folgt [ms]. */
export const VORRANG_HALTEN_MS = 2500;
/** Eine Meldung, die jünger ist, wenn der Vorrang sie verdrängt, wird danach wiederholt [ms]. */
export const NACHHOLEN_MS = 1000;
/** Abstand zwischen wartenden Meldungen [ms]. */
export const ABSTAND_MS = 1500;
/** Bewusste Grenze: höchstens so viele Meldungen warten; bei mehr fällt die älteste weg (Ansagen sind flüchtig). */
export const WARTEN_MAX = 3;

export interface AnsageSchlange {
  /** Bis zu diesem Zeitpunkt wartet jede Meldung ohne Vorrang [ms]. */
  bis: number;
  /** Zuletzt gezeigte Meldung. */
  zuletzt: { text: string; t: number; vorrang: boolean } | null;
  warten: readonly string[];
}

export const leereSchlange: AnsageSchlange = { bis: 0, zuletzt: null, warten: [] };

const einreihen = (warten: readonly string[], text: string): string[] => [...warten.filter((w) => w !== text), text].slice(-WARTEN_MAX);

/** Eine neue Meldung: `jetzt` ist der sofort zu zeigende Text, sonst null (sie wartet). */
export function ansageMelden(s: AnsageSchlange, e: { t: number; text: string; vorrang?: boolean }): { schlange: AnsageSchlange; jetzt: string | null } {
  if (e.vorrang) {
    const z = s.zuletzt;
    const nachholen = z !== null && !z.vorrang && z.text !== e.text && e.t - z.t < NACHHOLEN_MS;
    // Die verdrängte Meldung kommt zuerst; bei voller Schlange fällt die älteste wartende weg (wie beim Einreihen).
    const warten = nachholen ? [z.text, ...s.warten.filter((w) => w !== z.text).slice(-(WARTEN_MAX - 1))] : [...s.warten];
    return { schlange: { bis: e.t + VORRANG_HALTEN_MS, zuletzt: { text: e.text, t: e.t, vorrang: true }, warten }, jetzt: e.text };
  }
  // Während des Haltens und solange noch etwas wartet, reiht sich die Meldung ein (kein Überholen).
  if (e.t < s.bis || s.warten.length > 0) return { schlange: { ...s, warten: einreihen(s.warten, e.text) }, jetzt: null };
  return { schlange: { ...s, zuletzt: { text: e.text, t: e.t, vorrang: false } }, jetzt: e.text };
}

/** Zeitpunkt erreicht: die nächste wartende Meldung, wenn das Halten vorbei ist. */
export function ansageFaellig(s: AnsageSchlange, t: number): { schlange: AnsageSchlange; jetzt: string | null } {
  const [erste, ...rest] = s.warten;
  if (erste === undefined || t < s.bis) return { schlange: s, jetzt: null };
  return { schlange: { bis: t + ABSTAND_MS, zuletzt: { text: erste, t, vorrang: false }, warten: rest }, jetzt: erste };
}
