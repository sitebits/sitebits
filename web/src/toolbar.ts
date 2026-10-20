// Werkzeugleiste: einziger Besitzer des aktiven Werkzeugs (Navigieren, vier Messarten, Markieren =
// Befund per Klick setzen; daneben „Teilen“ = Befundlink des ausgewählten Befunds) — main.ts leitet jeden Wechsel an das Messwerkzeug und die POIs weiter. Erster Baustein des
// gemeinsamen Designsystems (styles/tokens.css, styles/toolbar.css; keine neue Abhängigkeit).
// Symbole als Inline-SVG im DOM (CSP img-src 'self' — keine data:/blob:-Bilder), Texte nur als Attribute
// bzw. textContent. Tastatur nach dem ARIA-Toolbar-Muster: ein Tab-Stopp (roving tabindex), ←/→ bewegen
// den Fokus, Pos1/Ende springen, Enter/Leertaste lösen den Knopf nativ aus. Die Werkzeug-Kürzel selbst
// laufen über keymap.ts.
import { WERKZEUG_AKTION } from './keymap';
import type { ToolId } from './keymap';
import type { AktionId } from './tastenbelegung';

export interface Toolbar {
  tool(): ToolId;
  setTool(t: ToolId): void;
  onChange(cb: (t: ToolId) => void): void;
  /** Zeichnung offen → „Fertig“/„Abbrechen“ zeigen. */
  setDrawing(on: boolean): void;
  /** Messliste offen → Knopf gedrückt darstellen. */
  setListOpen(on: boolean): void;
  /**
   * Fang (Punktwolke: Kanten; Mesh: Vertices und Merkmalskanten): Standard aus, Taste E schaltet. Kein Knopf in der Leiste: die Abnahme an der
   * echten Kante ist noch nicht bestanden — Zustand liegt trotzdem hier,
   * damit ein späterer Knopf nur noch angehängt wird.
   */
  snapEdges(): boolean;
  setSnapEdges(on: boolean): void;
  onSnapChange(cb: (on: boolean) => void): void;
  /** Beschriftungen aus der Belegung: `f(aktion)` liefert das Kürzel („D“) oder `''` ohne Kürzel. Beschriftet
   *  Werkzeuge und „Messliste ein/aus“ neu; Enter/Esc an Fertig/Abbrechen bleiben fest. */
  setKuerzel(f: (a: AktionId) => string): void;
  /** Knopf „Begehen“: gedrückt, solange es läuft; im Panorama nicht verfügbar (aria-disabled, bleibt
   *  fokussierbar und meldet beim Auslösen den Grund). */
  setBegehen(aktiv: boolean, verfuegbar: boolean): void;
  /** Knopf „Teilen“: ohne ausgewählten Befund aria-disabled (bleibt fokussierbar; das Auslösen sagt den Grund). */
  setTeilen(verfuegbar: boolean): void;
  /** Knopf „Vollbild“ (falls vorhanden): gedrückt, solange Vollbild an ist. */
  setVollbild(an: boolean): void;
  /** Fokus auf „Teilen“ (Rückgabe nach dem Schließen des Link-Felds). */
  fokussiereTeilen(): void;
  /** Fokus in die Leiste (Sprunglink „Zu den Werkzeugen“): auf den Knopf, der den Tab-Stopp hält. */
  fokussiere(): void;
  /** Nach dem Aus- oder Einblenden von Knöpfen (Empfängeransicht): der Tab-Stopp liegt auf einem sichtbaren Knopf. */
  tabStopp(): void;
  readonly element: HTMLElement;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
/** Symbole (24×24, nur Striche; Farbe über currentColor). */
const ICON = {
  navigate: 'M5 3l14 8-6 2-2 6z',
  distance: 'M5 19L19 5M3 17l4 4M17 3l4 4',
  polyline: 'M3 18l6-8 5 5 7-10M3 18h.01M9 10h.01M14 15h.01M21 5h.01',
  area: 'M4 8l7-5 9 6-3 11H6z',
  height: 'M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4',
  poi: 'M12 21s-6-6-6-11a6 6 0 0 1 12 0c0 5-6 11-6 11zM12 8a2 2 0 1 1 0 4 2 2 0 0 1 0-4z',
  teilen: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
  begehen: 'M13 3a1.5 1.5 0 1 0 0 3 1.5 1.5 0 1 0 0-3zM9 21l2.5-7 3 3v4M8 12l3-4 3 2 3 3M11 8l-1.5 5',
  list: 'M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01',
  hilfe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1.9-1.1 1.8M12 17h.01',
  einstellungen: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z',
  vollbild: 'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5',
  mehr: 'M5 12h.01M12 12h.01M19 12h.01',
  finish: 'M5 12l5 5 9-10',
  cancel: 'M6 6l12 12M18 6L6 18',
} as const;

const TOOL_LABEL: Record<ToolId, string> = {
  navigate: 'Navigieren',
  distance: 'Strecke messen',
  polyline: 'Polylinie / Kettenmaß',
  area: 'Fläche messen',
  height: 'Höhe / Lot messen',
  poi: 'Markieren',
};
/** Tooltip des Knopfs „Markieren“: was er tut. */
const MARKIEREN_TITEL = 'Befund setzen';
const TOOL_ORDER: ToolId[] = ['navigate', 'distance', 'polyline', 'area', 'height', 'poi'];

function icon(d: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', d);
  svg.append(path);
  return svg;
}

const mitKuerzel = (label: string, kuerzel: string): string => (kuerzel ? `${label} (${kuerzel})` : label);
function beschrifte(b: HTMLButtonElement, label: string): void {
  b.setAttribute('aria-label', label);
  b.title = label;
}

function button(label: string, d: string): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  beschrifte(b, label);
  b.tabIndex = -1;
  b.append(icon(d));
  // Fokusfalle Enter/Leertaste: ein Mausklick nimmt dem Canvas den Fokus nicht, sonst löste das Enter zum
  // Abschließen den noch fokussierten Knopf erneut aus. Tastaturfokus per Tab bleibt möglich.
  b.addEventListener('mousedown', (e) => e.preventDefault());
  return b;
}

export function createToolbar(
  root: HTMLElement, onFinish: () => void, onCancel: () => void, onList: () => void, onHilfe: () => void, onEinstellungen: () => void, onBegehen: () => void,
  onTeilen: () => void = () => undefined,
  /** Knopf „Vollbild“: schaltet Vollbild (bedienhilfe.ts::vollbildVerfuegbar) oder erklärt den Weg (iPhone); fehlt = kein Knopf. */
  onVollbild?: () => void,
): Toolbar {
  root.setAttribute('role', 'toolbar');
  root.setAttribute('aria-label', 'Werkzeuge');
  root.setAttribute('aria-orientation', 'horizontal');
  let current: ToolId = 'navigate';
  const listeners: ((t: ToolId) => void)[] = [];

  const toolButtons = new Map<ToolId, HTMLButtonElement>();
  for (const t of TOOL_ORDER) {
    const b = button(TOOL_LABEL[t], ICON[t]);
    b.dataset.tool = t;
    b.setAttribute('aria-pressed', String(t === current));
    b.addEventListener('click', () => setTool(t === current ? 'navigate' : t));
    toolButtons.set(t, b);
  }
  // „Markieren“ trägt sichtbaren Text (der zugängliche Name beginnt damit), der Tooltip nennt die Wirkung.
  // Nur in der Werkzeugansicht: Messen, Markieren, Begehen, Messliste, Zeichnung abschließen/verwerfen.
  for (const [t, b] of toolButtons) if (t !== 'navigate') b.classList.add('sv-nur-werkzeug');
  const markieren = toolButtons.get('poi')!;
  const markierenText = document.createElement('span');
  markierenText.className = 'sv-knopf-text';
  markierenText.textContent = TOOL_LABEL.poi;
  markieren.append(markierenText);
  markieren.title = MARKIEREN_TITEL;
  const sep = document.createElement('div');
  sep.className = 'sep';
  sep.setAttribute('role', 'separator');
  let snapOn = false;
  const snapListeners: ((on: boolean) => void)[] = [];
  const setSnapEdges = (on: boolean): void => {
    if (on === snapOn) return;
    snapOn = on;
    snapListeners.forEach((cb) => cb(on));
  };
  // Kein data-tool: Begehen ist ein Kamerazustand, kein Werkzeug, und läuft neben dem gewählten Werkzeug.
  const begehen = button('Begehen', ICON.begehen);
  begehen.dataset.begehen = '';
  begehen.setAttribute('aria-pressed', 'false');
  begehen.addEventListener('click', onBegehen);
  let begehenKuerzel = '';
  let begehenVerfuegbar = true;
  const beschrifteBegehen = (): void =>
    beschrifte(begehen, begehenVerfuegbar ? mitKuerzel('Begehen', begehenKuerzel) : 'Begehen – im Panorama nicht verfügbar');
  // „Teilen“ mit sichtbarem Text wie „Markieren“; ohne Auswahl aria-disabled statt disabled (fokussierbar).
  const teilen = button('Teilen', ICON.teilen);
  teilen.dataset.teilen = '';
  const teilenText = document.createElement('span');
  teilenText.className = 'sv-knopf-text';
  teilenText.textContent = 'Teilen';
  teilen.append(teilenText);
  teilen.addEventListener('click', onTeilen);
  let teilenKuerzel = '';
  let teilenVerfuegbar = false;
  const beschrifteTeilen = (): void => {
    beschrifte(teilen, mitKuerzel('Teilen', teilenKuerzel));
    teilen.title = teilenVerfuegbar ? mitKuerzel('Befundlink teilen', teilenKuerzel) : 'Befundlink teilen – erst einen POI auswählen';
  };
  const list = button('Messliste ein/aus', ICON.list);
  list.setAttribute('aria-pressed', 'false');
  list.addEventListener('click', onList);
  const hilfe = button('Bedienung (?)', ICON.hilfe);
  hilfe.addEventListener('click', onHilfe);
  const einstellungen = button('Einstellungen Bedienung', ICON.einstellungen);
  einstellungen.addEventListener('click', onEinstellungen);
  const finish = button('Zeichnung abschließen (Enter)', ICON.finish);
  finish.dataset.drawing = '';
  finish.classList.add('sv-abschliessen');
  finish.addEventListener('click', onFinish);
  const cancel = button('Zeichnung verwerfen (Esc)', ICON.cancel);
  cancel.dataset.drawing = '';
  cancel.addEventListener('click', onCancel);
  for (const b of [begehen, list, finish, cancel]) b.classList.add('sv-nur-werkzeug');
  teilen.setAttribute('aria-disabled', 'true');
  beschrifteTeilen();
  // Vollbild: gedrückt, solange die Seite den Bildschirm füllt (fullscreenchange, main.ts); in beiden Ansichten.
  const vollbild = onVollbild ? button('Vollbild', ICON.vollbild) : null;
  if (vollbild && onVollbild) {
    vollbild.setAttribute('aria-pressed', 'false');
    vollbild.addEventListener('click', onVollbild);
  }
  // Handy: Teilen, Messliste, Hilfe, Einstellungen, Vollbild hinter „Mehr“ (die Leiste passte nicht aufs iPhone,
  // 17.10.); auf breiten Fenstern ist „Mehr“ ausgeblendet und alles sichtbar (toolbar.css).
  const ueberlauf = [teilen, list, hilfe, einstellungen, ...(vollbild ? [vollbild] : [])];
  for (const b of ueberlauf) b.classList.add('sv-ueberlauf');
  const mehr = button('Mehr', ICON.mehr);
  mehr.classList.add('sv-mehr');
  mehr.setAttribute('aria-expanded', 'false');
  mehr.addEventListener('click', () => {
    const offen = root.classList.toggle('sv-mehr-offen');
    mehr.setAttribute('aria-expanded', String(offen));
  });
  for (const b of ueberlauf) b.addEventListener('click', () => { root.classList.remove('sv-mehr-offen'); mehr.setAttribute('aria-expanded', 'false'); });
  root.replaceChildren(...toolButtons.values(), sep, begehen, mehr, ...ueberlauf, finish, cancel);
  toolButtons.get(current)!.tabIndex = 0;

  // Roving tabindex: genau ein Knopf ist per Tab erreichbar, Pfeiltasten wandern zwischen den sichtbaren.
  const visible = (): HTMLButtonElement[] => [...root.querySelectorAll('button')].filter((b) => b.offsetParent !== null);
  const focusButton = (b: HTMLButtonElement): void => {
    for (const x of root.querySelectorAll('button')) x.tabIndex = x === b ? 0 : -1;
    b.focus();
  };
  root.addEventListener('focusin', (ev) => {
    if (ev.target instanceof HTMLButtonElement) for (const x of root.querySelectorAll('button')) x.tabIndex = x === ev.target ? 0 : -1;
  });
  root.addEventListener('keydown', (ev) => {
    const bs = visible();
    const i = bs.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    const next =
      ev.key === 'ArrowRight' ? bs[(i + 1) % bs.length] : ev.key === 'ArrowLeft' ? bs[(i - 1 + bs.length) % bs.length] : ev.key === 'Home' ? bs[0] : ev.key === 'End' ? bs.at(-1) : undefined;
    if (!next) return;
    ev.preventDefault();
    focusButton(next);
  });

  const setTool = (t: ToolId): void => {
    if (t === current) return;
    current = t;
    for (const [id, b] of toolButtons) b.setAttribute('aria-pressed', String(id === t));
    listeners.forEach((cb) => cb(t));
  };

  return {
    tool: () => current,
    setTool,
    onChange: (cb) => {
      listeners.push(cb);
    },
    setDrawing: (on) => {
      root.classList.toggle('drawing', on);
    },
    setListOpen: (on) => {
      list.setAttribute('aria-pressed', String(on));
    },
    snapEdges: () => snapOn,
    setSnapEdges,
    onSnapChange: (cb) => {
      snapListeners.push(cb);
    },
    setKuerzel: (f) => {
      for (const [id, b] of toolButtons) beschrifte(b, mitKuerzel(TOOL_LABEL[id], f(WERKZEUG_AKTION[id])));
      markieren.title = mitKuerzel(MARKIEREN_TITEL, f(WERKZEUG_AKTION.poi));
      beschrifte(list, mitKuerzel('Messliste ein/aus', f('messliste')));
      beschrifte(hilfe, mitKuerzel('Bedienung', f('tastenhilfe')));
      begehenKuerzel = f('begehen');
      beschrifteBegehen();
      teilenKuerzel = f('teilen');
      beschrifteTeilen();
    },
    setBegehen: (aktiv, verfuegbar) => {
      begehen.setAttribute('aria-pressed', String(aktiv));
      if (verfuegbar) begehen.removeAttribute('aria-disabled');
      else begehen.setAttribute('aria-disabled', 'true');
      begehenVerfuegbar = verfuegbar;
      beschrifteBegehen();
    },
    setTeilen: (verfuegbar) => {
      if (verfuegbar) teilen.removeAttribute('aria-disabled');
      else teilen.setAttribute('aria-disabled', 'true');
      teilenVerfuegbar = verfuegbar;
      beschrifteTeilen();
    },
    // Umschaltknopf: feste Beschriftung, der Zustand steht nur in aria-pressed (sonst hieße es „Vollbild beenden, gedrückt“).
    setVollbild: (an) => vollbild?.setAttribute('aria-pressed', String(an)),
    fokussiereTeilen: () => focusButton(teilen),
    fokussiere: () => {
      const bs = visible();
      const b = bs.find((x) => x.tabIndex === 0) ?? bs[0];
      if (b) focusButton(b);
    },
    tabStopp: () => {
      const bs = visible();
      if (bs.length > 0 && !bs.some((x) => x.tabIndex === 0)) for (const x of root.querySelectorAll('button')) x.tabIndex = x === bs[0] ? 0 : -1;
    },
    element: root,
  };
}
