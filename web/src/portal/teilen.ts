// Nur-Lese-Links (F28): die öffentliche Seite /teilen#t=<token> (tauscht das Token gegen ein Gast-Cookie,
// fragt bei Bedarf das Link-Passwort) und der Abschnitt „Nur-Lese-Links“ in den Projekt-Einstellungen.
// Nur createElement/textContent (strenge CSP). Logik in teilenModell.ts.
import { anfrage } from '../plattformApi';
import { ApiFehler, post } from './api';
import { kontoFehlerText } from './apiZugaenge';
import { abschnitt, el, feld, fehlerText, knopf, tabelle, tabelleRegion, zeile } from './verwaltung';
import { datumText } from './verwaltungModell';
import {
  ANSICHT_MAX, ansichtLesen, EHRLICH_TEXT, gastZiel, linkAdresse, linkTeile, PASSWORT_MIN, PROFIL_HILFE, PROFIL_TEXT, TAGE_MAX, TAGE_STANDARD, teilenText,
  zustand, ZUSTAND_TEXT,
} from './teilenModell';
import type { ProfilWahl, TeilenAngelegt, TeilenLink, TeilenListe } from './teilenModell';

type Melde = (text: string) => void;

// --- Öffentliche Seite /teilen ---------------------------------------------------------------------

export function zeigeTeilen(root: HTMLElement, hash: string): void {
  // Token und Startansicht (cam/tgt/ly, ADR 0016) aus dem Fragment, bevor es aus der Adresszeile verschwindet. Die
  // Ansicht bleibt im Browser: der POST unten trägt nur Token und Passwort.
  const teile = linkTeile(hash);
  const token = teile?.token ?? null;
  const ansicht = teile?.ansicht ?? null;
  // Vor dem Aufräumen der Adresszeile lesen: der Show-Modus steht in der Query (`/teilen?zeigen=1#t=…`).
  // Bewusst ohne Import aus dem Viewer (zeigen.ts::zeigenAn, gleiche Regel): sonst teilen sich Portal und Viewer einen Chunk mehr.
  const zeigen = new URLSearchParams(location.search).get('zeigen') === '1';
  // Fragment sofort aus Adresszeile und Verlauf nehmen (Token nicht beim Teilen des Bildschirms zeigen).
  history.replaceState(null, '', location.pathname);
  root.replaceChildren();
  const box = el('main', 'sv-einladung');
  const titel = el('h1', undefined, '3D-Ansicht öffnen');
  const meldung = el('p', 'sv-fehler');
  meldung.setAttribute('role', 'alert');
  meldung.hidden = true;
  const status = el('p', 'sv-status', token ? 'Link wird geprüft …' : '');
  status.setAttribute('role', 'status');
  box.append(titel, meldung, status);
  root.append(box);
  const melde = (t: string): void => {
    meldung.textContent = t;
    meldung.hidden = t === '';
  };
  if (!token) {
    status.textContent = '';
    melde('Der Link ist unvollständig. Bitte den ganzen Link aus der Nachricht öffnen.');
    return;
  }

  let formular: HTMLFormElement | null = null;
  const versuchen = (passwort?: string): void => {
    post<{ projekt: string }>('/auth/teilen', passwort === undefined ? { token } : { token, passwort }).then(
      (a) => {
        status.textContent = 'Ansicht wird geöffnet …';
        // Ziel hier gebaut, nur aus einer geprüften Projekt-ID — keine Adresse aus der Antwort übernehmen.
        location.replace(gastZiel(a.projekt, zeigen, ansicht));
      },
      (e: unknown) => {
        status.textContent = '';
        if (e instanceof ApiFehler && e.status === 401) {
          if (!formular) {
            formular = passwortFormular(versuchen);
            box.append(formular);
          }
          if (passwort !== undefined) melde(e.message);
          formular.querySelector('input')?.focus();
          return;
        }
        melde(e instanceof ApiFehler ? e.message : fehlerText(e));
      },
    );
  };
  versuchen();
}

function passwortFormular(versuchen: (pw: string) => void): HTMLFormElement {
  const form = el('form', 'sv-einladen');
  const pw = el('input');
  pw.type = 'password';
  pw.required = true;
  pw.autocomplete = 'off';
  pw.maxLength = 128;
  const ok = el('button', undefined, 'Öffnen');
  ok.type = 'submit';
  form.append(el('p', undefined, 'Für diesen Link braucht es ein Passwort. Du hast es getrennt vom Link bekommen.'),
    feld('Passwort', 'sv-teilen-passwort', pw), ok);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    versuchen(pw.value);
  });
  return form;
}

// --- Abschnitt in den Projekt-Einstellungen --------------------------------------------------------

function zeigeNeuenLink(platz: HTMLElement, adresse: string, text: string, hinweis?: string): void {
  platz.replaceChildren();
  const box = el('div', 'sv-einladungslink');
  box.append(el('p', undefined, 'Nur-Lese-Link – nur jetzt sichtbar. Bitte kopieren und selbst weitergeben.'));
  if (hinweis) {
    const h = el('p', 'sv-fehler', hinweis);
    h.setAttribute('role', 'alert');
    box.append(h);
  }
  const f = el('input');
  f.readOnly = true;
  f.value = adresse;
  f.setAttribute('aria-label', 'Nur-Lese-Link');
  const status = el('span', 'sv-kopier-status');
  status.setAttribute('aria-live', 'polite');
  const kopieren = (beschriftung: string, inhalt: string, fertig: string): HTMLButtonElement => {
    const b = el('button', undefined, beschriftung);
    b.type = 'button';
    b.addEventListener('click', () => {
      if (!navigator.clipboard) {
        f.select();
        status.textContent = 'Bitte mit Strg+C kopieren';
        return;
      }
      navigator.clipboard.writeText(inhalt).then(() => { status.textContent = fertig; }, () => { status.textContent = 'Kopieren fehlgeschlagen'; });
    });
    return b;
  };
  const weg = el('button', undefined, 'Ausblenden');
  weg.type = 'button';
  weg.addEventListener('click', () => platz.replaceChildren());
  box.append(f, kopieren('Link kopieren', adresse, 'Link kopiert'), kopieren('Text kopieren', text, 'Text kopiert'), weg, status);
  platz.append(box);
  f.focus();
  f.select();
}

/** Ansichtslink aus dem Viewer im Anlegen-Formular (optional). Nur der Browser liest ihn (ADR 0016): an den Server geht
 *  er nie, er steckt nur im Fragment des fertigen Links. */
const STARTANSICHT_UNBRAUCHBAR =
  'Die Startansicht wurde nicht übernommen: Bitte einen Ansichtslink aus dem Viewer einfügen (Adresse mit #cam=… oder #ly=…). ' +
  'Der Link öffnet jetzt die Voreinstellung des Projekts.';
const STARTANSICHT_ZU_LANG =
  `Die Startansicht wurde nicht übernommen: Sie ist länger als ${ANSICHT_MAX} Zeichen (viele sichtbare Ebenen). ` +
  'Im Viewer weniger Ebenen einblenden und den Link neu kopieren. Der Link öffnet jetzt die Voreinstellung des Projekts.';

function startansichtFeld(): { label: HTMLLabelElement; eingabe: HTMLInputElement } {
  const eingabe = el('input');
  eingabe.type = 'text';
  eingabe.autocomplete = 'off';
  eingabe.spellcheck = false;
  return { label: feld('Startansicht (optional): Ansichtslink aus dem Viewer einfügen', 'sv-pe-teilen-ansicht', eingabe), eingabe };
}

/** Ansicht aus dem Feld und, wenn eine Eingabe nicht brauchbar war, der Hinweis dazu. */
function startansichtLesen(wert: string): { ansicht: string | null; hinweis?: string } {
  if (wert.trim() === '') return { ansicht: null };
  const { ansicht, verworfen, zuLang } = ansichtLesen(wert);
  if (!ansicht) return { ansicht: null, hinweis: zuLang ? STARTANSICHT_ZU_LANG : STARTANSICHT_UNBRAUCHBAR };
  return verworfen.length
    ? { ansicht, hinweis: `Die Startansicht wurde nur teilweise übernommen (nicht: ${verworfen.join(', ')}). Bitte den Link prüfen.` }
    : { ansicht };
}

export interface TeilenUmgebung { pfad: string; melde: Melde; status: Melde; neu: (fokus?: string) => Promise<void>; linkPlatz: HTMLElement; anlegenFehler: HTMLElement }

/** Auswahl eines Ansichtsprofils (P08-S6) mit sichtbarem Label über feld(). */
function profilAuswahl(text: string, id: string, wert: ProfilWahl): { label: HTMLLabelElement; auswahl: HTMLSelectElement } {
  const auswahl = el('select');
  for (const [p, text] of Object.entries(PROFIL_TEXT)) {
    const o = el('option', undefined, text);
    o.value = p;
    auswahl.append(o);
  }
  auswahl.value = wert;
  return { label: feld(text, id, auswahl), auswahl };
}

/** Standardansicht des Projekts: Vorbelegung neuer Links; bestehende Links behalten ihre Ansicht. */
function standardAuswahl(pfad: string, standard: ProfilWahl, u: Pick<TeilenUmgebung, 'melde' | 'status' | 'neu'>): HTMLElement {
  const { label, auswahl } = profilAuswahl('Standardansicht für neue Links', 'sv-pe-teilen-standard', standard);
  auswahl.addEventListener('change', () => {
    auswahl.disabled = true;
    anfrage('PATCH', `${pfad}/einstellungen`, { ansichtsprofil: auswahl.value })
      .then(async () => {
        await u.neu('sv-pe-teilen-standard');
        u.status(`Standardansicht für neue Links: ${PROFIL_TEXT[auswahl.value as ProfilWahl]}. Bestehende Links behalten ihre Ansicht.`);
      })
      // Abgelehnt: Auswahl zurück auf den gespeicherten Stand, damit sie nicht etwas anzeigt, das nicht gilt.
      .catch((e: unknown) => { u.melde(kontoFehlerText(e)); auswahl.value = standard; auswahl.disabled = false; });
  });
  return label;
}

/** `linkPlatz` liegt außerhalb des neu gezeichneten Inhalts der Seite, damit der Link das Neuladen übersteht.
 *  `standard`: Standardansicht des Projekts (P08-S6), Vorbelegung im Formular. */
export async function teilenAbschnitt(erlaubt: boolean, u: TeilenUmgebung, standard: ProfilWahl = 'ansehen'): Promise<HTMLElement> {
  const { pfad, melde, status, neu, linkPlatz, anlegenFehler } = u;
  const inhalt: HTMLElement[] = [el('p', 'sv-hinweis', EHRLICH_TEXT)];
  if (erlaubt) inhalt.push(el('p', 'sv-hinweis', PROFIL_HILFE), standardAuswahl(pfad, standard, u));
  if (!erlaubt) {
    inhalt.push(el('p', undefined, 'Ausgeschaltet. Unter „Schalter“ „Nur-Lese-Links erlaubt“ einschalten, dann lassen sich hier Links anlegen.'));
    inhalt.push(anlegenFehler);
  }
  const liste = await anfrage<TeilenListe>('GET', `${pfad}/teilen`);
  const jetzt = Date.now() / 1000;
  if (liste.links.length === 0) inhalt.push(el('p', undefined, 'Noch keine Nur-Lese-Links.'));
  else {
    const { table, tbody } = tabelle(['Name', 'Zustand', 'Ansicht', 'Gültig bis', 'Passwort', 'Geöffnet', 'Zuletzt', ''], 'sv-tabelle');
    for (const l of liste.links as TeilenLink[]) {
      const z = zustand(l, jetzt);
      let aktion: string | HTMLElement = '';
      if (z === 'aktiv') {
        aktion = knopf('Zurückziehen', async () => {
          await anfrage('DELETE', `${pfad}/teilen/${l.id}`);
          await neu('sv-pe-teilen');
          status(`Link „${l.name}“ zurückgezogen – er wirkt ab sofort nicht mehr.`);
        }, melde, `Link „${l.name}“ zurückziehen? Wer ihn hat, sieht das Projekt danach nicht mehr.`);
        aktion.setAttribute('aria-label', `Link ${l.name} zurückziehen`);
      }
      tbody.append(zeile(l.name, ZUSTAND_TEXT[z], l.profil ? PROFIL_TEXT[l.profil] : '–', datumText(l.ablauf), l.passwort ? 'ja' : 'nein', String(l.abrufe), datumText(l.letzterAbruf), aktion));
    }
    inhalt.push(tabelleRegion('Nur-Lese-Links', table));
  }
  if (erlaubt) {
    const form = el('form', 'sv-einladen');
    const name = el('input');
    name.required = true;
    name.maxLength = 80;
    name.autocomplete = 'off';
    const tage = el('input');
    tage.type = 'number';
    tage.min = '1';
    tage.max = String(TAGE_MAX);
    tage.value = String(TAGE_STANDARD);
    tage.required = true;
    const pw = el('input');
    pw.type = 'text';
    pw.autocomplete = 'off';
    pw.minLength = PASSWORT_MIN;
    pw.maxLength = 128;
    const start = startansichtFeld();
    const profilFeld = profilAuswahl('Ansicht für Gäste', 'sv-pe-teilen-profil', standard);
    const anlegen = el('button', undefined, 'Link anlegen');
    anlegen.type = 'submit';
    form.append(feld('Für wen (Name, nur zur Übersicht)', 'sv-pe-teilen-name', name), feld(`Gültig (Tage, höchstens ${TAGE_MAX})`, 'sv-pe-teilen-tage', tage),
      feld(`Passwort (optional, ab ${PASSWORT_MIN} Zeichen)`, 'sv-pe-teilen-pw', pw), profilFeld.label, start.label, anlegen, anlegenFehler);
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      melde('');
      anlegenFehler.textContent = '';
      anlegenFehler.hidden = true;
      anlegen.disabled = true;
      const koerper: Record<string, unknown> = { name: name.value, tage: Number(tage.value), profil: profilFeld.auswahl.value };
      if (pw.value !== '') koerper.passwort = pw.value;
      const { ansicht, hinweis } = startansichtLesen(start.eingabe.value);
      anfrage<TeilenAngelegt>('POST', `${pfad}/teilen`, koerper)
        .then(async (a) => {
          const adresse = linkAdresse(location.origin, a.token, ansicht);
          await neu();
          zeigeNeuenLink(linkPlatz, adresse, teilenText(adresse, a.link.name, datumText(a.link.ablauf), a.link.passwort), hinweis);
          status(`Link „${a.link.name}“ angelegt.`);
        })
        .catch((e: unknown) => {
          anlegenFehler.textContent = kontoFehlerText(e);
          anlegenFehler.hidden = false;
          anlegenFehler.scrollIntoView({ block: 'nearest' });
        })
        .finally(() => { anlegen.disabled = false; });
    });
    inhalt.push(form);
  }
  const s = abschnitt('Nur-Lese-Links', ...inhalt);
  const h = s.querySelector('h2');
  if (h) {
    h.id = 'sv-pe-teilen';
    h.tabIndex = -1;
  }
  return s;
}
