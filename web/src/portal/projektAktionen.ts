// Karten-Aktionen für Bearbeiter und Admins: „Bearbeiten“ (Titel/Beschreibung mit
// version/409), „Export CSV/JSON“ (Download-Links auf die API) und „Verlauf“. Nur createElement/textContent
// (strenge CSP); Betrachter bekommen diese Aktionen nicht (seiten.ts).
import { anfrage, PlattformFehler, projektPfad } from '../plattformApi';
import { zuPruefen } from '../vorschlagApi';
import { anzahl } from './zuPruefenModell';
import type { Projekt } from './api';
import { BESCHREIBUNG_MAX, geaenderteFelder, pruefeMeta, TITEL_MAX, uebernehmeKonflikt } from './metaRegeln';

export const KONFLIKT_HINWEIS =
  'Inzwischen von jemand anderem geändert – der aktuelle Stand steht jetzt im Formular. Bitte prüfen und erneut speichern.';

function link(text: string, href: string, download = false): HTMLAnchorElement {
  const a = document.createElement('a');
  a.href = href;
  a.textContent = text;
  if (download) a.setAttribute('download', '');
  return a;
}

export function zuPruefenLink(p: Projekt): HTMLAnchorElement {
  const a = link('Zu prüfen', `/zu-pruefen?p=${encodeURIComponent(p.id)}`);
  void zuPruefen(p.id).then(l => { a.textContent = `Zu prüfen (${anzahl(l)})`; }).catch(() => { a.title = 'Anzahl konnte nicht geladen werden'; });
  return a;
}

export const verlaufLink = (p: Projekt): HTMLAnchorElement => link('Verlauf', `/verlauf?p=${encodeURIComponent(p.id)}`);

/** Liste „Fällig/überfällig“ (nur bei eingeschalteten Befund-Fäden; lesen darf jede Rolle im Projekt). */
export const befundeLink = (p: Projekt): HTMLAnchorElement => link('Befunde', `/befunde?p=${encodeURIComponent(p.id)}`);

export const einstellungenLink = (p: Projekt): HTMLAnchorElement =>
  link('Projekt-Einstellungen', `/projekt-einstellungen?p=${encodeURIComponent(p.id)}`);

export const exportLinks = (p: Projekt): HTMLAnchorElement[] => [
  link('Export CSV', `${projektPfad(p.id)}/export.csv`, true),
  link('Export JSON', `${projektPfad(p.id)}/export.json`, true),
  link('Mängelliste XLSX', `${projektPfad(p.id)}/maengelliste.xlsx`, true),
  link('Mängelliste drucken', `/maengelliste?p=${encodeURIComponent(p.id)}`),
];

function feld(text: string, eingabe: HTMLElement): HTMLLabelElement {
  const l = document.createElement('label');
  l.className = 'sv-feld';
  const s = document.createElement('span');
  s.textContent = text;
  l.append(s, eingabe);
  return l;
}

function formular(p: Projekt, gespeichert: (neu: Projekt) => void, fertig: () => void): HTMLFormElement {
  const form = document.createElement('form');
  form.className = 'sv-bearbeiten';
  const titel = document.createElement('input');
  titel.maxLength = TITEL_MAX;
  titel.value = p.titel;
  titel.setAttribute('aria-label', 'Titel');
  const beschreibung = document.createElement('textarea');
  beschreibung.maxLength = BESCHREIBUNG_MAX;
  beschreibung.rows = 4;
  beschreibung.value = p.beschreibung;
  beschreibung.setAttribute('aria-label', 'Beschreibung');
  const hinweis = document.createElement('p');
  hinweis.className = 'sv-fehler';
  hinweis.hidden = true;
  const zeige = (text: string): void => {
    hinweis.textContent = text;
    hinweis.hidden = false;
  };
  const speichern = document.createElement('button');
  speichern.type = 'submit';
  speichern.textContent = 'Speichern';
  const abbrechen = document.createElement('button');
  abbrechen.type = 'button';
  abbrechen.textContent = 'Abbrechen';
  abbrechen.addEventListener('click', () => {
    form.remove();
    fertig();
  });
  const datenHinweis = document.createElement('p');
  datenHinweis.className = 'sv-hinweis';
  datenHinweis.textContent = 'Für alle Projektbeteiligten sichtbar und dauerhaft im Verlauf – bitte keine Personendaten (Namen, Telefonnummern).';
  const knoepfe = document.createElement('div');
  knoepfe.className = 'sv-bearbeiten-knoepfe';
  knoepfe.append(speichern, abbrechen);
  form.append(
    feld('Titel (leer = Name aus dem Paket)', titel),
    feld('Beschreibung (optional)', beschreibung),
    // Titel und Beschreibung landen unveränderlich im Bearbeitungsprotokoll (siehe ADR 0011).
    datenHinweis,
    knoepfe,
    hinweis,
  );
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    hinweis.hidden = true;
    const fehler = pruefeMeta(titel.value, beschreibung.value);
    if (fehler) {
      zeige(fehler);
      return;
    }
    // Nur geänderte Felder schicken (gegen den zuletzt bekannten Stand `p`,
    // der nach einem Konflikt weiter unten aktualisiert wird) — sonst schreibt ein Speichern, das
    // nur die Beschreibung ändern will, den unangetasteten Titel als stillen Override fest.
    const felder = geaenderteFelder(p, { titel: titel.value, beschreibung: beschreibung.value });
    if (Object.keys(felder).length === 0) {
      form.remove();
      fertig();
      return;
    }
    speichern.disabled = true;
    anfrage<Projekt>('PATCH', projektPfad(p.id), { ...felder, version: p.version }).then(
      (neu) => {
        form.remove();
        gespeichert(neu);
      },
      (e: unknown) => {
        speichern.disabled = false;
        if (e instanceof PlattformFehler && e.status === 409 && e.aktuell !== null && typeof e.aktuell === 'object') {
          // Der aktuelle Stand geht dauerhaft in `p` (nicht nur ins Formular)
          // — sonst bleibt `p.version` nach „Abbrechen“ veraltet und ein erneutes „Bearbeiten“
          // läuft garantiert wieder in einen 409.
          uebernehmeKonflikt(p, e.aktuell as Projekt);
          titel.value = p.titel;
          beschreibung.value = p.beschreibung;
          zeige(KONFLIKT_HINWEIS);
          return;
        }
        zeige(e instanceof Error ? e.message : 'Speichern fehlgeschlagen.');
      },
    );
  });
  return form;
}

export function bearbeitenKnopf(karte: HTMLElement, p: Projekt, gespeichert: (neu: Projekt) => void): HTMLButtonElement {
  const knopf = document.createElement('button');
  knopf.type = 'button';
  knopf.textContent = 'Bearbeiten';
  knopf.addEventListener('click', () => {
    if (karte.querySelector('.sv-bearbeiten')) return;
    const form = formular(p, gespeichert, () => knopf.focus());
    karte.append(form);
    form.querySelector('input')?.focus();
  });
  return knopf;
}
