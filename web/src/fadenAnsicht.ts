// Bereich „Verlauf“ eines Befunds (DOM, Viewer-Befundkarte und Portal-Seite /befunde): Einträge chronologisch mit
// Nummer nach Position, Art, Person und Zeit; interne Einträge mit dem Wort „intern“ abgesetzt (nicht nur Farbe).
// Darunter die Formulare Kommentar/Link, Status, Zuständig und Frist — jedes nur, wenn `rechte` des Servers es
// erlaubt; der Schalter „intern“ nur für Berechtigte. Eigene Kommentare und Links lassen sich zurückziehen.
// Fehlertexte des Servers erscheinen unverändert; bei 409 lädt der Bereich den aktuellen Stand neu und hängt das an.
// Nur createElement/textContent (strenge CSP im Portal), keine Inline-Stile. Reine Teile: fadenModell.ts.
import type { BeteiligterInfo, GewerkInfo, NeuerEintrag } from './fadenApi';
import { eintragSchreiben, fadenLesen, namenLaden, zurueckziehen } from './fadenApi';
import {
  artText, eintragInhalt, linkAdresse, namenAus, nummeriert, OHNE_NAMEN, standZeile as standText, statusText, zeitText,
} from './fadenModell';
import type { Faden, FadenEintrag, Namen, Stand } from './fadenModell';
import { fadenAnnehmen, fadenVerwerfen } from './vorschlagApi';
import { PlattformFehler } from './plattformApi';

/** Hinweis nach 409, hinter dem unveränderten Text des Servers. */
export const NEU_GELADEN = 'Der aktuelle Stand ist jetzt geladen – bitte prüfen und erneut senden.';
export const ZURUECKZIEHEN_FRAGE = 'Eintrag zurückziehen? Der Text wird für alle durch „zurückgezogen“ ersetzt.';
const TEXT_MAX = 4000;

export interface FadenOptionen {
  pruefen?: boolean;
  pid: string;
  /** Präfix eindeutiger IDs (Beschriftungen, Eintrags-Anker). */
  id: string;
  /** Eigene Konto-ID (Zurückziehen nur eigener Einträge); null = unbekannt. */
  nutzerId: number | null;
  /** Ebene der Überschrift „Verlauf“. */
  ebene: 2 | 3;
  /** Beschriftungen des Kommentarformulars; `formId` z. B. für einen äußeren Öffner (aria-controls). */
  kommentar: { label: string; senden: string; formId?: string; textId?: string };
  /** Kommentarformular erst auf Wunsch zeigen (Viewer); sonst sichtbar, sobald kommentiert werden darf. */
  eingeklappt?: boolean;
  /** Nach einer eigenen Änderung des Stands (Status, Zuständig, Frist). */
  onStand?: (poi: string, st: Stand) => void;
  /** Nach jedem Laden (null = kein Befund oder Fehler). */
  onGeladen?: (f: Faden | null) => void;
  /** Ansage (Live-Region der Umgebung), zusätzlich zur Statuszeile. */
  melde?: (t: string) => void;
}

export interface FadenAnsicht {
  readonly element: HTMLElement;
  readonly kommentarForm: HTMLFormElement;
  /** Faden eines Befunds (Server-ID) laden; `eintrag` fokussiert diesen Eintrag. null = leeren. */
  zeige(poi: string | null, eintrag?: string): Promise<void>;
  /** Kommentarformular zeigen bzw. verbergen (nur, wenn kommentiert werden darf). */
  kommentarOffen(auf: boolean, fokus?: boolean): void;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

/** Beschriftung neben (nicht um) das Feld: sonst trüge der Name einer Auswahlliste auch ihre gewählte Option. */
function beschriftet<T extends HTMLElement>(text: string, id: string, eingabe: T): { label: HTMLDivElement; eingabe: T } {
  eingabe.id = id;
  const label = el('label', undefined, text);
  label.htmlFor = id;
  const feld = el('div', 'sv-feld');
  feld.append(label, eingabe);
  return { label: feld, eingabe };
}

function option(wert: string, text: string): HTMLOptionElement {
  const o = el('option', undefined, text);
  o.value = wert;
  return o;
}

function knopf(text: string, typ: 'submit' | 'button' = 'submit'): HTMLButtonElement {
  const b = el('button', undefined, text);
  b.type = typ;
  return b;
}

function gruppe(legende: string, ...inhalt: HTMLElement[]): HTMLFieldSetElement {
  const f = el('fieldset', 'sv-faden-gruppe');
  f.append(el('legend', undefined, legende), ...inhalt);
  return f;
}

export function createFadenAnsicht(o: FadenOptionen): FadenAnsicht {
  const section = el('section', 'sv-faden');
  const h = el(o.ebene === 2 ? 'h2' : 'h3', undefined, 'Verlauf');
  h.id = `${o.id}-titel`;
  section.setAttribute('aria-labelledby', h.id);
  const standZeile = el('p', 'sv-faden-stand');
  const fehler = el('p', 'sv-fehler');
  fehler.setAttribute('role', 'alert');
  fehler.hidden = true;
  const status = el('p', 'sv-status');
  status.setAttribute('role', 'status');
  const liste = el('ol', 'sv-faden-liste');
  const leer = el('p', 'sv-faden-leer', 'Noch keine Einträge.');
  leer.hidden = true;

  // --- Kommentar oder Link ---
  const kommentarForm = el('form', 'sv-faden-form');
  kommentarForm.id = o.kommentar.formId ?? `${o.id}-kommentar`;
  kommentarForm.noValidate = true;
  const text = beschriftet(o.kommentar.label, o.kommentar.textId ?? `${o.id}-text`, el('textarea'));
  text.eingabe.rows = 3;
  text.eingabe.maxLength = TEXT_MAX;
  const url = beschriftet('Link-Adresse (optional: Webadresse oder kopierter Befundlink)', `${o.id}-url`, el('input'));
  url.eingabe.type = 'url';
  url.eingabe.autocomplete = 'off';
  const titel = beschriftet('Link-Titel', `${o.id}-linktitel`, el('input'));
  titel.eingabe.maxLength = 200;
  const internCb = el('input');
  internCb.type = 'checkbox';
  internCb.id = `${o.id}-intern`;
  const internLabel = el('label', 'sv-schalter');
  internLabel.htmlFor = internCb.id;
  internLabel.append(internCb, el('span', undefined, 'intern – nur für die Bauherrenseite sichtbar'));
  kommentarForm.append(gruppe('Kommentar oder Link', text.label, url.label, titel.label, internLabel, knopf(o.kommentar.senden)));

  // --- Status ---
  const statusForm = el('form', 'sv-faden-form');
  statusForm.noValidate = true;
  const statusWahl = beschriftet('Neuer Status', `${o.id}-status`, el('select'));
  const begruendung = beschriftet('Begründung', `${o.id}-begruendung`, el('textarea'));
  begruendung.eingabe.rows = 2;
  begruendung.eingabe.maxLength = TEXT_MAX;
  statusForm.append(gruppe('Status ändern', statusWahl.label, begruendung.label, knopf('Status setzen')));

  // --- Zuständig ---
  const zustForm = el('form', 'sv-faden-form');
  zustForm.noValidate = true;
  const gewerkWahl = beschriftet('Gewerk', `${o.id}-gewerk`, el('select'));
  const personWahl = beschriftet('Person (optional)', `${o.id}-person`, el('select'));
  zustForm.append(gruppe('Zuständigkeit', gewerkWahl.label, personWahl.label, knopf('Zuständigkeit setzen')));

  // --- Frist ---
  const fristForm = el('form', 'sv-faden-form');
  fristForm.noValidate = true;
  const fristFeld = beschriftet('Frist (Kalendertag)', `${o.id}-frist`, el('input'));
  fristFeld.eingabe.type = 'date';
  const fristWeg = knopf('Frist entfernen', 'button');
  fristForm.append(gruppe('Frist', fristFeld.label, knopf('Frist setzen'), fristWeg));

  const formulare = el('div', 'sv-faden-formulare');
  formulare.append(kommentarForm, statusForm, zustForm, fristForm);
  section.append(h, standZeile, fehler, status, liste, leer, formulare);

  let poi: string | null = null;
  let aktuell: Faden | null = null;
  let lauf = 0;
  let namen: Namen = OHNE_NAMEN;
  let gewerke: GewerkInfo[] = [];
  let beteiligte: BeteiligterInfo[] = [];
  let namenP: Promise<void> | null = null;
  let kommentarAuf = !o.eingeklappt;
  let sendet = false;

  const zeigeFehler = (t: string): void => {
    fehler.textContent = t;
    fehler.hidden = t === '';
  };
  const sage = (t: string): void => {
    status.textContent = t;
    if (t) o.melde?.(t);
  };

  const personen = (gewerk: number | null, gewaehlt: number | null): void => {
    personWahl.eingabe.replaceChildren(option('', 'ohne Person'));
    for (const b of beteiligte) {
      if (gewerk !== null && b.gewerk?.id === gewerk) personWahl.eingabe.append(option(String(b.nutzerId), b.nutzer));
    }
    personWahl.eingabe.value = gewaehlt !== null && beteiligte.some((b) => b.nutzerId === gewaehlt && b.gewerk?.id === gewerk) ? String(gewaehlt) : '';
    personWahl.eingabe.disabled = gewerk === null;
  };
  gewerkWahl.eingabe.addEventListener('change', () => {
    personen(gewerkWahl.eingabe.value === '' ? null : Number(gewerkWahl.eingabe.value), null);
  });

  const eintragElement = (nr: number, e: FadenEintrag): HTMLLIElement => {
    const li = el('li', e.intern ? 'sv-faden-eintrag sv-faden-intern' : 'sv-faden-eintrag');
    li.id = `${o.id}-e-${e.schluessel}`;
    li.tabIndex = -1;
    li.dataset.schluessel = e.schluessel;
    const kopf = el('p', 'sv-faden-kopf');
    kopf.append(el('span', 'sv-faden-nr', `${nr}.`), ` ${artText(e.art)}`);
    if (e.intern) {
      kopf.append(' · ');
      kopf.append(el('strong', 'sv-intern-marke', 'intern'));
    }
    kopf.append(` · ${e.erstellt_von.name ?? 'unbekannt'} · ${zeitText(e.erstellt_am)}`);
    const inhalt = el('p', e.zurueckgezogen_am !== null ? 'sv-faden-text sv-faden-zurueck' : 'sv-faden-text', eintragInhalt(e, namen));
    if (e.herkunft === 'ki') kopf.append(' · KI');
    li.append(kopf, inhalt);
    if (e.bezug) { const a = el('a',undefined,'Bezug zum Vorschlag'); a.href = `#${o.id}-e-${e.bezug}`; li.append(a); }
    if (e.art === 'vorschlag') {
      li.classList.add('sv-faden-vorschlag');
      inhalt.textContent = `KI ${eintragInhalt(e,namen)}`;
      if (e.offen !== true) {
        li.classList.add('sv-faden-entschieden'); li.append(el('p',undefined,'Entschieden'));
        const antwort = aktuell?.eintraege.find(x => x.bezug === e.schluessel);
        if (antwort) { const a = el('a',undefined,'Zur Entscheidung'); a.href = `#${o.id}-e-${antwort.schluessel}`; li.append(a); }
      } else if (o.pruefen) {
        const grund = beschriftet('Begründung (optional)',`${o.id}-grund-${e.schluessel}`,el('input')); grund.eingabe.maxLength = TEXT_MAX;
        li.append(grund.label);
        for (const a of ['annehmen','verwerfen'] as const) {
          const b = knopf(a === 'annehmen' ? 'Annehmen' : 'Verwerfen','button');
          b.addEventListener('click',() => { void pruefeVorschlag(e,a,grund.eingabe.value); }); li.append(b);
        }
      }
    }
    const adresse = linkAdresse(e);
    if (adresse !== null) {
      const a = el('a', 'sv-faden-link', adresse);
      a.href = adresse;
      a.rel = 'noopener noreferrer';
      li.append(a);
    }
    const eigen = o.nutzerId !== null && e.erstellt_von.id === o.nutzerId;
    if (eigen && (e.art === 'kommentar' || e.art === 'link') && e.zurueckgezogen_am === null && poi !== null) {
      const zurueck = knopf('Zurückziehen', 'button');
      zurueck.setAttribute('aria-label', `Eintrag ${nr} zurückziehen`);
      const fuer = poi;
      zurueck.addEventListener('click', () => {
        if (sendet || !window.confirm(ZURUECKZIEHEN_FRAGE)) return;
        sendet = true;
        zeigeFehler('');
        zurueckziehen(o.pid, fuer, e.schluessel)
          .then(async () => {
            await zeige(fuer);
            sage(`Eintrag ${nr} zurückgezogen.`);
            document.getElementById(li.id)?.focus();
          }, (f: unknown) => zeigeFehler(f instanceof Error ? f.message : String(f)))
          .finally(() => {
            sendet = false;
          });
      });
      li.append(zurueck);
    }
    return li;
  };

  const pruefeVorschlag = async (e: FadenEintrag, a: 'annehmen' | 'verwerfen', text: string): Promise<void> => {
    const fuer = poi; if (sendet || fuer === null || aktuell === null) return;
    sendet = true; zeigeFehler('');
    try {
      if (a === 'annehmen') { const r = await fadenAnnehmen(o.pid,fuer,e.schluessel,aktuell.stand.version); o.onStand?.(fuer,r.stand); }
      else await fadenVerwerfen(o.pid,fuer,e.schluessel,text);
      await zeige(fuer); sage(a === 'annehmen' ? 'Vorschlag angenommen.' : 'Vorschlag verworfen.');
    } catch (f) {
      if (f instanceof PlattformFehler && f.status === 409 && f.aktuell != null) { await zeige(fuer); zeigeFehler(`${f.message} ${NEU_GELADEN}`); }
      else zeigeFehler(f instanceof Error ? f.message : String(f));
    } finally { sendet = false; }
  };

  const zeichne = (f: Faden): void => {
    const st = f.stand;
    standZeile.textContent = standText(st, namen);
    liste.replaceChildren(...nummeriert(f.eintraege).map(({ nr, eintrag }) => eintragElement(nr, eintrag)));
    leer.hidden = f.eintraege.length > 0;
    const r = f.rechte;
    kommentarForm.hidden = !r.kommentieren || !kommentarAuf;
    internLabel.hidden = !r.intern;
    if (!r.intern) internCb.checked = false;
    statusForm.hidden = r.status.length === 0;
    const vorher = statusWahl.eingabe.value;
    statusWahl.eingabe.replaceChildren(...r.status.map((s) => option(s, statusText(s))));
    if (r.status.includes(vorher)) statusWahl.eingabe.value = vorher;
    zustForm.hidden = !r.zustaendig;
    const gid = st.zustaendig?.gewerk_id ?? null;
    gewerkWahl.eingabe.replaceChildren(option('', 'niemand'), ...gewerke.map((g) => option(String(g.id), `${g.name} (${g.kurz})`)));
    gewerkWahl.eingabe.value = gid !== null && gewerke.some((g) => g.id === gid) ? String(gid) : '';
    personen(gid, st.zustaendig?.nutzer_id ?? null);
    fristForm.hidden = !r.frist;
    fristFeld.eingabe.value = st.frist ?? '';
    fristWeg.hidden = st.frist === null;
  };

  async function zeige(neu: string | null, eintrag?: string): Promise<void> {
    const n = ++lauf;
    if (neu !== poi) {
      zeigeFehler('');
      sage('');
      text.eingabe.value = '';
      url.eingabe.value = '';
      titel.eingabe.value = '';
      begruendung.eingabe.value = '';
    }
    poi = neu;
    if (neu === null) {
      aktuell = null;
      section.hidden = true;
      o.onGeladen?.(null);
      return;
    }
    namenP ??= namenLaden(o.pid).then((x) => {
      gewerke = x.gewerke;
      beteiligte = x.beteiligte;
      namen = namenAus(x.gewerke, x.beteiligte);
    });
    let f: Faden;
    try {
      [f] = await Promise.all([fadenLesen(o.pid, neu), namenP]);
    } catch (e) {
      if (n !== lauf) return;
      aktuell = null;
      section.hidden = false;
      liste.replaceChildren();
      leer.hidden = true;
      standZeile.textContent = '';
      formulare.hidden = true;
      zeigeFehler(e instanceof PlattformFehler && e.status === 404
        ? 'Verlauf nicht verfügbar: Befund gelöscht oder Befund-Fäden ausgeschaltet.'
        : e instanceof Error ? e.message : 'Der Verlauf konnte nicht geladen werden.');
      o.onGeladen?.(null);
      return;
    }
    if (n !== lauf) return;
    aktuell = f;
    section.hidden = false;
    formulare.hidden = false;
    zeichne(f);
    o.onGeladen?.(f);
    if (eintrag !== undefined) {
      const ziel = liste.querySelector<HTMLElement>(`li[data-schluessel="${CSS.escape(eintrag)}"]`);
      if (ziel) {
        ziel.scrollIntoView({ block: 'nearest' });
        ziel.focus({ preventScroll: true });
      }
    }
  }

  /** Einen Eintrag senden; Erfolg: Formular leeren, Faden neu laden, ansagen. 409: neu laden und Hinweis. */
  const sende = async (e: NeuerEintrag, erfolg: string, leeren: () => void): Promise<void> => {
    const fuer = poi;
    if (sendet || fuer === null || aktuell === null) return;
    sendet = true;
    zeigeFehler('');
    sage('');
    try {
      const antwort = await eintragSchreiben(o.pid, fuer, e);
      leeren();
      await zeige(fuer);
      sage(erfolg);
      if (e.art === 'status' || e.art === 'zustaendig' || e.art === 'frist') o.onStand?.(fuer, antwort.stand);
    } catch (f) {
      if (f instanceof PlattformFehler && f.status === 409) {
        await zeige(fuer);
        zeigeFehler(`${f.message} ${NEU_GELADEN}`);
      } else zeigeFehler(f instanceof Error ? f.message : String(f));
    } finally {
      sendet = false;
    }
  };

  const version = (): number => aktuell?.stand.version ?? 0;
  const oderNull = (s: string): string | null => (s.trim() === '' ? null : s);

  kommentarForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const adresse = url.eingabe.value.trim();
    const intern = !internLabel.hidden && internCb.checked;
    const leeren = (): void => {
      text.eingabe.value = '';
      url.eingabe.value = '';
      titel.eingabe.value = '';
      internCb.checked = false;
    };
    if (adresse !== '') {
      void sende({ art: 'link', text: oderNull(text.eingabe.value), wert: { url: adresse, titel: titel.eingabe.value }, intern }, 'Link gespeichert.', leeren);
    } else void sende({ art: 'kommentar', text: text.eingabe.value, intern }, 'Kommentar gespeichert.', leeren);
  });
  statusForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const ziel = statusWahl.eingabe.value;
    void sende({ art: 'status', wert: ziel, text: oderNull(begruendung.eingabe.value), version: version() },
      `Status auf „${statusText(ziel)}“ gesetzt.`, () => {
        begruendung.eingabe.value = '';
      });
  });
  zustForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const g = gewerkWahl.eingabe.value;
    const p = personWahl.eingabe.value;
    const wert = g === '' ? null : { gewerk_id: Number(g), nutzer_id: p === '' ? null : Number(p) };
    void sende({ art: 'zustaendig', wert, version: version() }, 'Zuständigkeit gesetzt.', () => undefined);
  });
  fristForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const w = fristFeld.eingabe.value;
    void sende({ art: 'frist', wert: w === '' ? null : w, version: version() }, w === '' ? 'Frist entfernt.' : 'Frist gesetzt.', () => undefined);
  });
  fristWeg.addEventListener('click', () => {
    void sende({ art: 'frist', wert: null, version: version() }, 'Frist entfernt.', () => undefined);
  });

  section.hidden = true;
  return {
    element: section,
    kommentarForm,
    zeige,
    kommentarOffen: (auf, fokus = true) => {
      kommentarAuf = auf;
      kommentarForm.hidden = !auf || !aktuell?.rechte.kommentieren;
      if (!kommentarForm.hidden && fokus) text.eingabe.focus();
    },
  };
}
