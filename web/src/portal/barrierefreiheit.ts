// Seite „Barrierefreiheit“ (WCAG 2.4.2 Seitentitel): Erklärung der Stelle, die die Instanz
// betreibt, oder — solange keine hinterlegt ist — ein Hinweis für den Betreiber und die bekannten
// Einschränkungen des Produkts. Öffentlich, ohne Anmeldung. Nur createElement/textContent.
import type { Erklaerung, Instanz, NichtBarrierefrei } from './rahmen';

export const LADEFEHLER = 'Die Angaben zur Barrierefreiheit konnten nicht geladen werden. Bitte laden Sie die Seite später erneut.';
export const HINWEIS_ERKLAERUNG = 'Der Betreiber dieser scanview-Instanz hat noch keine eigene Erklärung zur Barrierefreiheit hinterlegt. Unten stehen die bekannten Einschränkungen des Produkts.';
export const HINWEIS_KONTAKT = 'Für diese Instanz ist noch kein Kontakt für Rückmeldungen hinterlegt. Hinweis für den Betreiber: Einstellung SCANVIEW_BARRIEREFREIHEIT_KONTAKT (siehe Betriebsdokumentation).';
// Auch bei einem Ladefehler von /api/barrierefreiheit muss #melden als Sprungziel existieren —
// die Fußzeile verlinkt bei fehlendem Kontakt auf jeder Seite hierher (feedbackHref in rahmen.ts).
export const KONTAKT_NICHT_ABRUFBAR = 'Kontakt derzeit nicht abrufbar — bitte die Betreiberin/den Betreiber dieser Instanz kontaktieren.';

const STAND: Record<Erklaerung['stand'], string> = {
  vollstaendig: 'vollständig vereinbar',
  teilweise: 'teilweise vereinbar',
  nicht: 'nicht vereinbar',
};

export function standText(stand: Erklaerung['stand']): string {
  return STAND[stand];
}

export function datumText(iso: string): string {
  const [j, m, t] = iso.split('-');
  return `${t}.${m}.${j}`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function link(href: string, text: string): HTMLAnchorElement {
  const a = el('a', undefined, text);
  a.href = href;
  return a;
}

function liste(eintraege: NichtBarrierefrei[]): HTMLUListElement {
  const ul = el('ul', 'sv-barriere-liste');
  for (const e of eintraege) {
    const li = el('li');
    const kopf = el('p');
    kopf.append(el('strong', undefined, e.inhalt));
    li.append(kopf, el('p', undefined, `Grund: ${e.grund}`), el('p', undefined, `Alternative: ${e.alternative}`));
    ul.append(li);
  }
  return ul;
}

// Sprungziel der Fußzeile (feedbackHref in rahmen.ts, Fallback ohne konfigurierten Kontakt) — muss
// in jedem Zustand der Seite existieren, auch bei einem Ladefehler, sonst liefe der Anker ins Leere.
function meldenUeberschrift(): HTMLHeadingElement {
  const melden = el('h2', undefined, 'Barriere melden');
  melden.id = 'melden';
  melden.tabIndex = -1;
  return melden;
}

function springeZuMeldenFallsAdressiert(melden: HTMLElement): void {
  if (location.hash === '#melden') melden.focus();
}

export function zeigeBarrierefreiheit(root: HTMLElement, instanz: Instanz | null): void {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-barrierefreiheit');
  const kopf = el('header', 'sv-kopf');
  kopf.append(link('/', '← Projekte'), el('h1', 'sv-kopf-titel', 'Erklärung zur Barrierefreiheit'));
  const inhalt = el('div', 'sv-abschnitt');
  seite.append(kopf, inhalt);
  root.append(seite);

  if (instanz === null) {
    const f = el('p', 'sv-fehler', LADEFEHLER);
    f.setAttribute('role', 'alert');
    inhalt.append(f);
    const melden = meldenUeberschrift();
    inhalt.append(melden, el('p', 'sv-hinweis-betreiber', KONTAKT_NICHT_ABRUFBAR));
    springeZuMeldenFallsAdressiert(melden);
    return;
  }
  const e = instanz.erklaerung;
  if (e) {
    inhalt.append(
      el('p', undefined, `Diese Erklärung gilt für ${e.geltungsbereich}. Verantwortlich: ${e.stelle}.`),
      el('h2', undefined, 'Stand der Vereinbarkeit'),
      el('p', undefined, `Dieses Angebot ist ${standText(e.stand)} mit den Anforderungen an die Barrierefreiheit (BITV 2.0 bzw. Landesrecht, EN 301 549).`),
      el('h2', undefined, 'Nicht barrierefreie Inhalte'),
      liste(e.nicht_barrierefrei),
      el('h2', undefined, 'Erstellung dieser Erklärung'),
      el('p', undefined, `Erstellt bzw. zuletzt aktualisiert am ${datumText(e.erstellt)}. Methode: ${e.methode}.`),
      el('p', undefined, e.methode_text),
    );
  } else {
    inhalt.append(
      el('p', 'sv-hinweis-betreiber', HINWEIS_ERKLAERUNG),
      el('h2', undefined, 'Bekannte Einschränkungen von siteBits'),
      liste(instanz.produktEinschraenkungen),
    );
  }
  const melden = meldenUeberschrift();
  inhalt.append(melden);
  if (instanz.kontakt) {
    const ziel = instanz.kontakt.art === 'mailto' ? instanz.kontakt.ziel.slice('mailto:'.length) : instanz.kontakt.ziel;
    const p = el('p', undefined, 'Wenn Ihnen Barrieren auffallen, melden Sie sie bitte: ');
    p.append(link(instanz.kontakt.ziel, ziel));
    inhalt.append(p);
  } else {
    inhalt.append(el('p', 'sv-hinweis-betreiber', HINWEIS_KONTAKT));
  }
  if (e) {
    const p = el('p', undefined, `${e.durchsetzung.text} `);
    p.append(link(e.durchsetzung.url, e.durchsetzung.stelle));
    inhalt.append(el('h2', undefined, 'Durchsetzungsverfahren'), p);
  }
  springeZuMeldenFallsAdressiert(melden);
}
