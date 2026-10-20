// Befundliste der Empfängeransicht (DOM): die Textalternative zur 3D-Ansicht. Je Befund ein Knopf mit dem Titel
// (aria-current beim gezeigten) und eine Beschreibungsliste mit Kategorie, Standpunkt, Koordinate, Datum, Urheber,
// Notiz und „Frage an Sie“ — leere Felder entfallen. Die Einträge baut empfangModell.ts. Mit Befund-Fäden zusätzlich
// der Stand als Text (Status, Frist, „überfällig“) und über der Liste dieselben Filter wie im Portal.
import { datumText, koordinateText } from './empfangModell';
import type { BefundEintrag } from './empfangModell';
import { FILTER_LEER, FRIST_TEXT, FRIST_WERTE, STATUS_WERTE, statusText, ZUSTAENDIG_TEXT, ZUSTAENDIG_WERTE } from './fadenModell';
import type { Filter } from './fadenModell';

export interface BefundListe {
  /** section#befundliste (Ziel des Sprunglinks, per Skript fokussierbar). */
  readonly element: HTMLElement;
  /** Liste neu aufbauen; ein Fokus auf einem Eintrag bleibt auf demselben Befund. */
  zeichne(eintraege: readonly BefundEintrag[], aktuell: string | null): void;
  /** Gezeigten Befund markieren (ohne Neuaufbau). */
  setzeAktuell(id: string | null): void;
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};

function auswahl(id: string, text: string, optionen: readonly (readonly [string, string])[]): { label: HTMLDivElement; select: HTMLSelectElement } {
  const select = el('select');
  select.id = id;
  for (const [wert, t] of optionen) {
    const opt = el('option', t);
    opt.value = wert;
    select.append(opt);
  }
  // Beschriftung neben (nicht um) die Liste: sonst trüge ihr Name auch die gewählte Option.
  const label = el('label', text);
  label.htmlFor = id;
  const feld = el('div');
  feld.append(label, select);
  return { label: feld, select };
}

/** Filter der Befund-Fäden (Status, Zuständig, Frist, Kategorie); jede Änderung meldet den neuen Filter. */
function filterFormular(kategorien: readonly string[], onFilter: (f: Filter) => void): HTMLFormElement {
  const form = el('form');
  form.className = 'sv-befund-filter';
  form.setAttribute('role', 'search');
  form.setAttribute('aria-label', 'Befunde filtern');
  // Auswahllisten bedienen Pfeiltasten selbst (nicht die Ansicht drehen).
  form.dataset.tastenEigen = '';
  const status = auswahl('sv-bl-status', 'Status', [['', 'alle'], ...STATUS_WERTE.map((x) => [x, statusText(x)] as const)]);
  const zust = auswahl('sv-bl-zustaendig', 'Zuständig', ZUSTAENDIG_WERTE.map((x) => [x, ZUSTAENDIG_TEXT[x]] as const));
  const frist = auswahl('sv-bl-frist', 'Frist', [['', 'alle'], ...FRIST_WERTE.map((x) => [x, FRIST_TEXT[x]] as const)]);
  const kat = auswahl('sv-bl-kategorie', 'Kategorie', [['', 'alle'], ...kategorien.map((k) => [k, k] as const)]);
  form.append(status.label, zust.label, frist.label, kat.label);
  const melde = (): void => onFilter({
    ...FILTER_LEER,
    status: status.select.value as Filter['status'],
    zustaendig: zust.select.value as Filter['zustaendig'],
    frist: frist.select.value as Filter['frist'],
    kategorie: kat.select.value,
  });
  for (const s of [status.select, zust.select, frist.select, kat.select]) s.addEventListener('change', melde);
  form.addEventListener('submit', (ev) => ev.preventDefault());
  return form;
}

export function createBefundListe(o: {
  onWaehle: (id: string) => void;
  /** Befund-Fäden: Filter über der Liste (Kategorien zur Auswahl, Rückmeldung bei jeder Änderung). */
  filter?: { kategorien: readonly string[]; onFilter: (f: Filter) => void };
}): BefundListe {
  const section = el('section');
  section.id = 'befundliste';
  section.className = 'sv-befundliste';
  section.tabIndex = -1;
  const h = el('h2', 'Befunde');
  h.id = 'befundliste-titel';
  section.setAttribute('aria-labelledby', h.id);
  const leer = el('p', o.filter ? 'Keine Befunde für diese Auswahl.' : 'Keine Befunde in diesem Projekt.');
  const ul = el('ul');
  section.append(h);
  if (o.filter) section.append(filterFormular(o.filter.kategorien, o.filter.onFilter));
  section.append(leer, ul);

  const knoepfe = (): HTMLButtonElement[] => Array.from(ul.querySelectorAll<HTMLButtonElement>('button[data-id]'));
  const setzeAktuell = (id: string | null): void => {
    for (const b of knoepfe()) {
      if (b.dataset.id === id) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    }
  };

  const zeichne = (eintraege: readonly BefundEintrag[], aktuell: string | null): void => {
    const fokus = document.activeElement instanceof HTMLButtonElement && ul.contains(document.activeElement) ? document.activeElement.dataset.id : undefined;
    ul.replaceChildren(...eintraege.map((e) => {
      const li = el('li');
      const b = el('button', e.titel);
      b.type = 'button';
      b.dataset.id = e.id;
      b.addEventListener('click', () => o.onWaehle(e.id));
      const dl = el('dl');
      const feld = (name: string, wert: string | null): void => {
        if (wert === null || wert === '') return;
        const paar = el('div');
        paar.append(el('dt', name), el('dd', wert));
        dl.append(paar);
      };
      feld('Stand', e.kennzeichen);
      feld('Kategorie', e.kategorie);
      feld('Standpunkt', e.standpunkt);
      feld('Koordinate', koordinateText(e.koordinate));
      feld('Datum', datumText(e.datum));
      feld('Urheber', e.urheber);
      feld('Notiz', e.notiz);
      feld('Frage an Sie', e.frage);
      li.append(b, dl);
      return li;
    }));
    leer.hidden = eintraege.length > 0;
    setzeAktuell(aktuell);
    if (fokus !== undefined) (knoepfe().find((b) => b.dataset.id === fokus) ?? section).focus();
  };

  return { element: section, zeichne, setzeAktuell };
}
