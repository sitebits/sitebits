// Rückfrage beim Setzen (DOM): nicht-modaler Dialog am Fadenkreuz bzw. am Klickort mit zwei Knöpfen —
// „Welche Fläche?“ bei zwei Flächen, „trotzdem setzen?“ bei einem Treffer nur aus der groben Stufe (Texte in
// zielpunkt.ts). Tastatur: ↑/↓ wechseln, Enter/Leertaste wählen, Esc bricht ab; Mausrad, linke Maustaste
// und Gamepad kommen über wechsle()/bestaetige()/abbreche() (das Begehen leitet sie weiter). Kein
// Zeitablauf. Esc bricht auch ab, wenn der Fokus woanders liegt. Texte nur per textContent, Stile in styles/fadenkreuz.css.
import { rueckfrageTexte } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';

export interface RueckfrageDialog {
  /** `ort` = Fensterkoordinaten, an denen der Dialog erscheint; ohne Angabe die Bildmitte. */
  frage(b: Rueckfrage, ort?: { x: number; y: number }): Promise<Wahl | null>;
  offen(): boolean;
  /** Auswahl wechseln (Mausrad, Steuerkreuz). */
  wechsle(): void;
  /** Gewählten Knopf bestätigen (linke Maustaste unter Pointer-Lock, Gamepad A). */
  bestaetige(): void;
  /** Ohne Wahl schließen (Gamepad B, Ende des Begehens, Panorama betreten). */
  abbreche(): void;
  /** Liegt `n` in der Rückfrage (Fokuswechsel dorthin gilt nicht als Verlassen der Leinwand)? */
  enthaelt(n: Node | null): boolean;
}

let zaehler = 0;

export function createRueckfrage(root: HTMLElement, melde: (t: string) => void): RueckfrageDialog {
  /** Gewählter Knopf (folgt dem Fokus, aber auch ohne ihn — z. B. wenn die Maus anderswo hinklickt). */
  let aktuell: Wahl = 'vorn';
  const box = document.createElement('div');
  box.className = 'sv-rueckfrage';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'false');
  const titel = document.createElement('p');
  titel.className = 'sv-rueckfrage-titel';
  titel.id = `sv-rueckfrage-titel-${++zaehler}`;
  box.setAttribute('aria-labelledby', titel.id);
  const knopf = (text: string, wahl: Wahl): HTMLButtonElement => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    b.dataset.wahl = wahl;
    b.addEventListener('click', () => schliesse(wahl));
    b.addEventListener('focus', () => {
      aktuell = wahl;
    });
    return b;
  };
  const vorn = knopf('Vordere Fläche', 'vorn');
  const hinten = knopf('Hintere Fläche', 'hinten');
  box.append(titel, vorn, hinten);
  box.hidden = true;
  root.append(box);

  let antwort: ((w: Wahl | null) => void) | null = null;
  let vorher: Element | null = null;
  /** Bei „trotzdem setzen?“ heißt der zweite Knopf „Nicht setzen“: Antwort null statt einer Fläche. */
  let hintenBrichtAb = false;
  const schliesse = (wahl: Wahl | null): void => {
    const a = antwort;
    if (!a) return;
    const w = wahl === 'hinten' && hintenBrichtAb ? null : wahl;
    antwort = null;
    box.hidden = true;
    // Fokus zurück (im Begehen die Leinwand, sonst wo er vorher war), damit die Tasten wieder wirken.
    if (vorher instanceof HTMLElement && vorher.isConnected) vorher.focus({ preventScroll: true });
    vorher = null;
    a(w);
  };
  const waehle = (w: Wahl): void => {
    aktuell = w;
    (w === 'vorn' ? vorn : hinten).focus({ preventScroll: true });
    box.dataset.wahl = w;
  };
  const wechsle = (): void => {
    if (antwort) waehle(aktuell === 'vorn' ? 'hinten' : 'vorn');
  };

  box.addEventListener('keydown', (ev) => {
    if (!antwort) return;
    if (ev.key === 'ArrowUp' || ev.key === 'ArrowDown') wechsle();
    // Selbst wählen statt der Knopfaktivierung zu überlassen: die Leertaste löste sie erst beim Loslassen aus.
    else if (ev.key === 'Enter' || ev.key === ' ') schliesse(aktuell);
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  });

  // Esc bricht ab, auch wenn der Fokus die Rückfrage verlassen hat (Erfassungsphase, vor den Tastenkürzeln).
  // Ausnahme: der Fokus liegt in einem anderen offenen Menü oder Dialog — dort schließt Esc zuerst diesen
  // (Fenster-/Positionsmenü, Einstellungsdialog bei der Tastenaufnahme). Ein laufendes Ziehen eines Panels
  // ist hier nicht erkennbar (PanelHost bietet keine Abfrage); Esc schließt dann die Rückfrage.
  window.addEventListener(
    'keydown',
    (ev) => {
      if (!antwort || ev.key !== 'Escape') return;
      const t = ev.target;
      if (t instanceof Element && !box.contains(t) && t.closest('[role="menu"], .sv-move-menu, dialog[open], [role="dialog"]')) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      schliesse(null);
    },
    true,
  );
  // Fokus verlässt die offene Rückfrage (Tab, Klick daneben): das Begehen steht weiter — sagen, warum.
  box.addEventListener('focusout', (ev) => {
    if (antwort && !(ev.relatedTarget instanceof Node && box.contains(ev.relatedTarget))) melde('Rückfrage offen – Esc bricht ab');
  });

  /** Am Klickort, aber ganz im sichtbaren Bereich (rechter und unterer Rand). */
  const platziere = (ort: { x: number; y: number } | undefined): void => {
    const x = ort ? ort.x : window.innerWidth / 2;
    const y = ort ? ort.y : window.innerHeight / 2;
    box.style.left = `${x}px`;
    box.style.top = `${y}px`;
    const r = box.getBoundingClientRect();
    const rand = 8;
    if (r.right > window.innerWidth - rand) box.style.left = `${Math.max(0, x - (r.right - (window.innerWidth - rand)))}px`;
    if (r.bottom > window.innerHeight - rand) box.style.top = `${Math.max(0, y - (r.bottom - (window.innerHeight - rand)))}px`;
  };

  return {
    frage(b, ort) {
      if (antwort) {
        melde('Erst die Rückfrage beantworten');
        return Promise.resolve(null);
      }
      vorher = document.activeElement;
      const t = rueckfrageTexte(b.grund);
      titel.textContent = t.titel;
      vorn.textContent = t.vorn;
      hinten.textContent = t.hinten;
      hintenBrichtAb = t.hintenBrichtAb;
      box.hidden = false;
      box.dataset.grund = b.grund;
      platziere(ort);
      waehle(b.trefferSchicht);
      melde(t.ansage);
      return new Promise<Wahl | null>((resolve) => {
        antwort = resolve;
      });
    },
    offen: () => antwort !== null,
    wechsle,
    bestaetige: () => schliesse(aktuell),
    abbreche: () => schliesse(null),
    enthaelt: (n) => n !== null && box.contains(n),
  };
}
