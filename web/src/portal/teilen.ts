// Nur-Lese-Links (F28): die öffentliche Seite /teilen#t=<token> (tauscht das Token gegen ein Gast-Cookie,
// fragt bei Bedarf das Link-Passwort) und der Abschnitt „Nur-Lese-Links“ in den Projekt-Einstellungen.
// Nur createElement/textContent (strenge CSP). Logik in teilenModell.ts.
import { anfrage } from '../plattformApi';
import { ApiFehler, post } from './api';
import { kontoFehlerText } from './apiZugaenge';
import { abschnitt, el, feld, fehlerText, knopf, tabelle, tabelleRegion, zeile } from './verwaltung';
import { datumText } from './verwaltungModell';
import {
  EHRLICH_TEXT, gastZiel, linkAdresse, PASSWORT_MIN, TAGE_MAX, TAGE_STANDARD, teilenText, tokenAusFragment, zustand, ZUSTAND_TEXT,
} from './teilenModell';
import type { TeilenAngelegt, TeilenLink, TeilenListe } from './teilenModell';

type Melde = (text: string) => void;

// --- Öffentliche Seite /teilen ---------------------------------------------------------------------

export function zeigeTeilen(root: HTMLElement, hash: string): void {
  const token = tokenAusFragment(hash);
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
        location.replace(gastZiel(a.projekt));
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

function zeigeNeuenLink(platz: HTMLElement, adresse: string, text: string): void {
  platz.replaceChildren();
  const box = el('div', 'sv-einladungslink');
  box.append(el('p', undefined, 'Nur-Lese-Link – nur jetzt sichtbar. Bitte kopieren und selbst weitergeben.'));
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

export interface TeilenUmgebung { pfad: string; melde: Melde; status: Melde; neu: (fokus?: string) => Promise<void>; linkPlatz: HTMLElement; anlegenFehler: HTMLElement }

/** `linkPlatz` liegt außerhalb des neu gezeichneten Inhalts der Seite, damit der Link das Neuladen übersteht. */
export async function teilenAbschnitt(erlaubt: boolean, u: TeilenUmgebung): Promise<HTMLElement> {
  const { pfad, melde, status, neu, linkPlatz, anlegenFehler } = u;
  const inhalt: HTMLElement[] = [el('p', 'sv-hinweis', EHRLICH_TEXT)];
  if (!erlaubt) {
    inhalt.push(el('p', undefined, 'Ausgeschaltet. Unter „Schalter“ „Nur-Lese-Links erlaubt“ einschalten, dann lassen sich hier Links anlegen.'));
    inhalt.push(anlegenFehler);
  }
  const liste = await anfrage<TeilenListe>('GET', `${pfad}/teilen`);
  const jetzt = Date.now() / 1000;
  if (liste.links.length === 0) inhalt.push(el('p', undefined, 'Noch keine Nur-Lese-Links.'));
  else {
    const { table, tbody } = tabelle(['Name', 'Zustand', 'Gültig bis', 'Passwort', 'Geöffnet', 'Zuletzt', ''], 'sv-tabelle');
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
      tbody.append(zeile(l.name, ZUSTAND_TEXT[z], datumText(l.ablauf), l.passwort ? 'ja' : 'nein', String(l.abrufe), datumText(l.letzterAbruf), aktion));
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
    const anlegen = el('button', undefined, 'Link anlegen');
    anlegen.type = 'submit';
    form.append(feld('Für wen (Name, nur zur Übersicht)', 'sv-pe-teilen-name', name), feld(`Gültig (Tage, höchstens ${TAGE_MAX})`, 'sv-pe-teilen-tage', tage),
      feld(`Passwort (optional, ab ${PASSWORT_MIN} Zeichen)`, 'sv-pe-teilen-pw', pw), anlegen, anlegenFehler);
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      melde('');
      anlegenFehler.textContent = '';
      anlegenFehler.hidden = true;
      anlegen.disabled = true;
      const koerper: Record<string, unknown> = { name: name.value, tage: Number(tage.value) };
      if (pw.value !== '') koerper.passwort = pw.value;
      anfrage<TeilenAngelegt>('POST', `${pfad}/teilen`, koerper)
        .then(async (a) => {
          const adresse = linkAdresse(location.origin, a.token);
          await neu();
          zeigeNeuenLink(linkPlatz, adresse, teilenText(adresse, a.link.name, datumText(a.link.ablauf), a.link.passwort));
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
