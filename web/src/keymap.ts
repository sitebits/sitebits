// Tastenkürzel: ein Verteiler für alle Kürzel. Reine Zuordnung Taste → Aktion (resolveKey, resolveKeyUp;
// Vitest) und ein einziger Listener-Satz (attachShortcuts). Die Belegung kommt aus tastenbelegung.ts
// (umbelegbar, abschaltbar); viewer.ts und clipBox.ts haben keine eigenen Tasten-Listener mehr —
// vorher hingen f (Alles zeigen) und s/g (Schnittbox-Gizmo) dort und waren weder umbelegbar noch
// abschaltbar (B-BF-V1). Esc gehört nur hierher, damit der Vorrang
// Ziehen → Zeichnung → Begehen → Panorama an einer Stelle steht.
// Capture-Phase auf window: lil-gui ruft auf seinem Wurzelelement stopPropagation() für jeden keydown
// auf (node_modules/lil-gui/dist/lil-gui.esm.js) — ein Bubble-Listener erreichte nie etwas, solange der
// Fokus im GUI-Panel liegt (z. B. direkt auf der gerade angeklickten „Schnittbox“-Checkbox, clipbox.spec).
// Capture läuft vor jedem Bubble-Listener. Texteingaben (POI-Formular, Suche) und offene Dialoge werden
// übersprungen, Checkboxen/Regler nicht (pano.spec „Esc mit Fokus in der Bedienleiste“).
// Ziffern 1–9: schalten die n-te unterstützte Ebene (layerForKey in layers.ts, main.ts löst den Index auf);
// fest (nicht belegbar), nur gesammelt abschaltbar.
// Bewusste Verhaltensänderungen gegenüber den früheren Einzel-Listenern: s/g wurden vorher auch von
// range-/color-Eingaben gesperrt, jetzt sperren nur Texteingaben; f wirkt jetzt auch mit Fokus in lil-gui.
import { akkordVon, aktionFuer, HALTE_AKTIONEN, normalisiere } from './tastenbelegung';
import type { AktionId, Belegung, HalteAktion } from './tastenbelegung';

export type ToolId = 'navigate' | 'distance' | 'polyline' | 'area' | 'height' | 'poi';
export const WERKZEUG_AKTION: Record<ToolId, AktionId> = {
  navigate: 'werkzeug.navigate', distance: 'werkzeug.distance', polyline: 'werkzeug.polyline',
  area: 'werkzeug.area', height: 'werkzeug.height', poi: 'werkzeug.poi',
};
/** Panel-Kürzel: Ansicht, POIs, Minikarte, Ebenen. Messungen läuft über die Aktion `messliste`. */
export type PanelKeyId = 'ansicht' | 'pois' | 'karte' | 'ebenen';
export const PANEL_AKTION: Record<PanelKeyId, AktionId> = {
  ansicht: 'panel.ansicht', pois: 'panel.pois', karte: 'panel.karte', ebenen: 'panel.ebenen',
};
const TEXT_INPUTS = new Set(['text', 'number', 'search', 'email', 'password', 'url', 'tel']);

export type KeyAction =
  | { type: 'tool'; tool: ToolId }
  | { type: 'finish' }
  | { type: 'cancel-drag' }
  | { type: 'cancel-drawing' }
  | { type: 'leave-panorama' }
  | { type: 'undo' }
  | { type: 'delete-selection' }
  | { type: 'toggle-list' }
  | { type: 'toggle-snap' }
  | { type: 'toggle-panel'; panel: PanelKeyId }
  | { type: 'toggle-layer'; index: number }
  | { type: 'alles-zeigen' }
  | { type: 'schnittbox'; modus: 'translate' | 'scale' }
  | { type: 'begehen-start' }
  | { type: 'begehen-ende' }
  | { type: 'tastenhilfe' }
  | { type: 'standpunkt'; richtung: -1 | 1 }
  | { type: 'werkzeug-wechsel'; richtung: -1 | 1 }
  | { type: 'ansage' }
  | { type: 'befund' }
  | { type: 'teilen' }
  | { type: 'koordinaten' }
  | { type: 'koordinate-kopieren' }
  | { type: 'drehen-schritt'; richtung: DrehRichtung }
  | { type: 'halten'; aktion: HalteAktion };

export type DrehRichtung = 'links' | 'rechts' | 'hoch' | 'runter';

export interface KeyInput {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  /** AltGr gedrückt (`getModifierState('AltGraph')`): unter Windows melden Strg+Alt zugleich, das Zeichen
   *  (`[`, `]` …) zählt dann als Taste ohne Strg/Alt. */
  altGraph: boolean;
  inTextField: boolean;
  /** Fokus liegt im Positions- oder Fenster-Menü (panels.ts/panelMenu.ts, role="menu"/.sv-move-menu):
   *  Esc/Enter durchlassen, statt sie hier zu verbrauchen — sonst verlässt Esc das Panorama/verwirft die
   *  Zeichnung statt das Menü zu schließen, und Enter schließt die Zeichnung statt den Eintrag per
   *  nativer Knopf-Aktivierung zu wählen (M1). */
  inMenu: boolean;
  /** Ein offener <dialog>, ein Element mit role="dialog" oder ein Bereich mit [data-tasten-eigen] (POI-Formular) hat den Fokus: der Dialog behandelt seine Tasten selbst, auch Esc, und die
   *  Taste, die gerade umbelegt wird, darf nicht zusätzlich ausgeführt werden. */
  inDialog: boolean;
}
export interface KeyContext {
  drawing: boolean;
  dragging: boolean;
  panorama: boolean;
  selection: boolean;
  /** Kamerazustand Begehen aktiv. */
  begehen: boolean;
  /** Fokus oder Pointer-Lock liegt auf der Leinwand (Voraussetzung für die Ebene `begehen`). */
  leinwandFokus: boolean;
  /** Fadenkreuz im Panorama: Panorama aktiv, Fokus auf der Leinwand, ein Werkzeug außer Navigieren gewählt.
   *  Dann gilt aus der Ebene `begehen` nur die Teilmenge `PANORAMA_ZIEL_AKTIONEN`. */
  panoramaZiel: boolean;
  /** Pointer-Lock besteht im Begehen oder wurde eben erst (< 150 ms) gelöst: Esc gehört dann dem Lösen des
   *  Locks bzw. dem Begehen, nicht einer offenen Zeichnung — der Browser gibt den Lock beim Esc selbst frei. */
  zeigerGesperrt: boolean;
  /** Schnittbox eingeschaltet (nur dann sind die Gizmo-Tasten belegt, sonst bleiben s/g frei). */
  schnittbox: boolean;
  /** Empfängeransicht aktiv: nur Lesen und Umsehen, siehe EMPFANG_AKTIONEN. */
  empfang: boolean;
  /** Empfängeransicht mit „Hier markieren“ (ein Werkzeug außer Navigieren): Esc bricht das Markieren ab, im
   *  Panorama-Fadenkreuz setzen Leertaste und Befund-Taste die Stelle. */
  markieren: boolean;
}

const WERKZEUG_VON_AKTION = new Map(Object.entries(WERKZEUG_AKTION).map(([t, a]) => [a, t as ToolId] as const));
const PANEL_VON_AKTION = new Map(Object.entries(PANEL_AKTION).map(([t, a]) => [a, t as PanelKeyId] as const));
const HALTE = new Set<AktionId>(HALTE_AKTIONEN);

const istHalteAktion = (a: AktionId): a is HalteAktion => HALTE.has(a);

/** Tasten der Ebene `begehen`, die auch im Panorama-Fadenkreuz gelten: drehen und neigen in Schritten, setzen,
 *  Befund setzen, Werkzeug wechseln. Bewegungstasten und Zielen gibt es dort nicht; diese Tasten fallen in die
 *  globale Ebene durch (z. B. `a` wählt weiter „Fläche messen“). */
export const PANORAMA_ZIEL_AKTIONEN: ReadonlySet<AktionId> = new Set<AktionId>([
  'begehen.drehen-links', 'begehen.drehen-rechts', 'begehen.neigen-hoch', 'begehen.neigen-runter',
  'begehen.setzen', 'begehen.befund', 'begehen.werkzeug-voriges', 'begehen.werkzeug-naechstes',
]);

/** Globale Aktionen, die in der Empfängeransicht gelten. Alles andere (Werkzeuge, Ebenen, Begehen, Löschen,
 *  Rückgängig, Schnittbox) bleibt dort wirkungslos: der Empfang zeigt, er bearbeitet nicht. */
export const EMPFANG_AKTIONEN: ReadonlySet<AktionId> = new Set<AktionId>([
  'tastenhilfe', 'standpunkt.voriger', 'standpunkt.naechster', 'panel.karte', 'teilen',
]);
/** Drehtasten der Ebene `begehen` als Schritt im Empfang (dort gibt es kein Halten). */
const DREH_SCHRITT: ReadonlyMap<AktionId, DrehRichtung> = new Map<AktionId, DrehRichtung>([
  ['begehen.drehen-links', 'links'], ['begehen.drehen-rechts', 'rechts'],
  ['begehen.neigen-hoch', 'hoch'], ['begehen.neigen-runter', 'runter'],
]);

/** Tasten der Empfängeransicht. Esc bricht zuerst ein „Hier markieren“ ab und verlässt sonst nur ein Panorama —
 *  den Empfang selbst beendet man mit dem Knopf „Im Werkzeug öffnen“, damit ein Esc zum Schließen eines Bereichs
 *  nicht die ganze Ansicht wechselt. */
function resolveEmpfang(k: KeyInput, c: KeyContext, b: Belegung): KeyAction | null {
  if (k.key === 'Escape') return c.markieren ? { type: 'tool', tool: 'navigate' } : c.panorama ? { type: 'leave-panorama' } : null;
  const akkord = normalisiere(k);
  if (c.leinwandFokus) {
    const d = aktionFuer(akkord, 'begehen', b);
    const richtung = d ? DREH_SCHRITT.get(d) : undefined;
    if (richtung) return { type: 'drehen-schritt', richtung };
    // „Hier markieren“ im Panorama-Fadenkreuz: Leertaste setzt beim Loslassen, die Befund-Taste sofort.
    if (c.markieren && c.panoramaZiel && d === 'begehen.setzen') return { type: 'halten', aktion: d };
    if (c.markieren && c.panoramaZiel && d === 'begehen.befund') return { type: 'befund' };
  }
  const a = aktionFuer(akkord, 'global', b);
  if (!a || !EMPFANG_AKTIONEN.has(a)) return null;
  switch (a) {
    case 'tastenhilfe': return { type: 'tastenhilfe' };
    case 'standpunkt.voriger': return { type: 'standpunkt', richtung: -1 };
    case 'standpunkt.naechster': return { type: 'standpunkt', richtung: 1 };
    case 'panel.karte': return { type: 'toggle-panel', panel: 'karte' };
    case 'teilen': return { type: 'teilen' };
    default: return null;
  }
}

export function resolveKey(k: KeyInput, c: KeyContext, b: Belegung): KeyAction | null {
  if (k.inTextField || k.inDialog) return null;
  if (k.inMenu && (k.key === 'Escape' || k.key === 'Enter')) return null;
  if (c.empfang) return resolveEmpfang(k, c, b);
  if (k.key === 'Escape') {
    if (c.dragging) return { type: 'cancel-drag' };
    if (c.begehen && c.zeigerGesperrt) return { type: 'begehen-ende' };
    if (c.drawing) return { type: 'cancel-drawing' };
    if (c.begehen) return { type: 'begehen-ende' };
    return c.panorama ? { type: 'leave-panorama' } : null;
  }
  const mod = k.ctrlKey || k.metaKey;
  if (mod && !k.altKey && !k.shiftKey && k.key.toLowerCase() === 'z') return { type: 'undo' };
  if (k.key === 'Enter') return mod || k.altKey ? null : c.drawing ? { type: 'finish' } : null;
  if (k.key === 'Delete' || k.key === 'Backspace') return !mod && !k.altKey && c.selection && !c.drawing ? { type: 'delete-selection' } : null;
  if (!mod && !k.altKey && /^[1-9]$/.test(k.key)) return b.einzeltastenAus ? null : { type: 'toggle-layer', index: Number(k.key) };

  const akkord = normalisiere(k);
  if ((c.begehen || c.panoramaZiel) && c.leinwandFokus) {
    const gefunden = aktionFuer(akkord, 'begehen', b);
    const a = gefunden && (c.begehen || PANORAMA_ZIEL_AKTIONEN.has(gefunden)) ? gefunden : null;
    if (a) {
      if (istHalteAktion(a)) return { type: 'halten', aktion: a };
      if (a === 'begehen.werkzeug-voriges') return { type: 'werkzeug-wechsel', richtung: -1 };
      if (a === 'begehen.werkzeug-naechstes') return { type: 'werkzeug-wechsel', richtung: 1 };
      if (a === 'begehen.ansage') return { type: 'ansage' };
      if (a === 'begehen.befund') return { type: 'befund' };
    }
  }
  const a = aktionFuer(akkord, 'global', b);
  if (a) {
    const werkzeug = WERKZEUG_VON_AKTION.get(a);
    if (werkzeug) return { type: 'tool', tool: werkzeug };
    const panel = PANEL_VON_AKTION.get(a);
    if (panel) return { type: 'toggle-panel', panel };
    switch (a) {
      case 'messliste': return { type: 'toggle-list' };
      case 'kantenfang': return { type: 'toggle-snap' };
      case 'alles-zeigen': return c.begehen ? null : { type: 'alles-zeigen' };
      case 'schnittbox.verschieben': return c.schnittbox ? { type: 'schnittbox', modus: 'translate' } : null;
      case 'schnittbox.skalieren': return c.schnittbox ? { type: 'schnittbox', modus: 'scale' } : null;
      // Nicht während eines Ziehens: das Begehen sperrte die Steuerung mitten in der Ziehgeste.
      case 'begehen': return c.begehen || c.dragging ? null : { type: 'begehen-start' };
      case 'tastenhilfe': return { type: 'tastenhilfe' };
      case 'standpunkt.voriger': return { type: 'standpunkt', richtung: -1 };
      case 'standpunkt.naechster': return { type: 'standpunkt', richtung: 1 };
      case 'teilen': return { type: 'teilen' };
      case 'koordinaten': return { type: 'koordinaten' };
      case 'koordinate.kopieren': return { type: 'koordinate-kopieren' };
    }
  }
  // Mit Strg/Alt ohne Treffer: Browser-Kürzel bleiben unberührt.
  return null;
}

/** Loslassen einer Halte-Taste der Ebene `begehen`. Bewusst ohne Leinwandfokus-Bedingung und nur über
 *  `taste` verglichen: ein Loslassen muss immer ankommen, auch nach Fokuswechsel oder wenn zwischendurch
 *  Strg gedrückt wurde — sonst läuft die Kamera weiter. Deshalb alle Halte-Aktionen mit dieser Taste
 *  (z. B. `w` und `Alt+W`), nicht nur die erste. Im Panorama-Fadenkreuz nur die dort geltende Teilmenge —
 *  dort setzt erst das Loslassen der Leertaste. */
export function resolveKeyUp(k: KeyInput, c: KeyContext, b: Belegung): HalteAktion[] {
  if (!c.begehen && !c.panoramaZiel) return [];
  const taste = normalisiere(k).taste;
  return HALTE_AKTIONEN.filter((a) => akkordVon(b, a)?.taste === taste && (c.begehen || PANORAMA_ZIEL_AKTIONEN.has(a)));
}

type VerteilerOptionen = {
  context: () => KeyContext;
  run: (a: KeyAction) => void;
  loslassen: (a: HalteAktion) => void;
  belegung: () => Belegung;
};

export function attachShortcuts(o: VerteilerOptionen): void {
  const eingabe = (ev: KeyboardEvent): KeyInput => {
    const t = ev.target;
    const inTextField =
      t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && TEXT_INPUTS.has(t.type));
    // Positions-/Fenster-Menü (panels.ts/panelMenu.ts) hängen ihre eigenen Esc/Enter-Behandlungen auf
    // `document` an — diese Erfassungsphase auf `window` läuft zuerst und würde sie sonst nie erreichen.
    const inMenu = t instanceof Element && t.closest('[role="menu"], .sv-move-menu') !== null;
    // Auch nicht-modale Dialoge (Rückfrage „Welche Fläche?“, role="dialog") und als data-tasten-eigen markierte
    // Bereiche (POI-Formular: Esc schließt es) behandeln ihre Tasten selbst.
    const inDialog = t instanceof Element && t.closest('dialog[open], [role="dialog"], [data-tasten-eigen]') !== null;
    return {
      key: ev.key, ctrlKey: ev.ctrlKey, metaKey: ev.metaKey, altKey: ev.altKey, shiftKey: ev.shiftKey,
      altGraph: ev.getModifierState('AltGraph'), inTextField, inMenu, inDialog,
    };
  };
  window.addEventListener(
    'keydown',
    (ev) => {
      const k = eingabe(ev);
      const c = o.context();
      const action = resolveKey(k, c, o.belegung());
      if (!action) {
        // Leertaste im Begehen und im Panorama-Fadenkreuz verbrauchen (sonst scrollt sie oder aktiviert einen Knopf).
        if (ev.key === ' ' && (c.begehen || c.panoramaZiel) && c.leinwandFokus && !k.inTextField && !k.inDialog) {
          ev.preventDefault();
          ev.stopPropagation();
        }
        return;
      }
      // Verbraucht: kein natives Verhalten (Enter auf einem fokussierten Leistenknopf, Rücktaste, Strg+Z).
      ev.preventDefault();
      ev.stopPropagation();
      // Gehaltene Taste: nur der erste Anschlag zählt (sonst schaltet z. B. „D“ im Takt hin und her,
      // Strg+Z leert den ganzen Verlauf). Verbraucht wird die Wiederholung trotzdem.
      if (ev.repeat) return;
      o.run(action);
    },
    true,
  );
  window.addEventListener(
    'keyup',
    (ev) => {
      const aktionen = resolveKeyUp(eingabe(ev), o.context(), o.belegung());
      if (aktionen.length === 0) return;
      ev.preventDefault();
      for (const a of aktionen) o.loslassen(a);
    },
    true,
  );
}
