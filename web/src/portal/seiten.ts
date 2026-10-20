import { zuPruefenLink } from './projektAktionen';
// DOM-Aufbau des Portals — ausschließlich per createElement/textContent (strenge CSP: kein
// innerHTML, kein Inline-Script/Style). Reine Formatierungs-/Suchlogik liegt in karten.ts (dort
// getestet); hier nur Verdrahtung von DOM, Netzwerk (api.ts) und WebAuthn (webauthn.ts).
import { ApiFehler, post, type Projekt, type Sitzung } from './api';
import { zeigeApiZugaenge } from './apiZugaenge';
import { draufsichtUrl, filtere, formatAusdehnung, formatGroesse, formatPunkte, formatStandpunkte, trefferText, viewerLink } from './karten';
import { ueberfaelligText } from '../fadenModell';
import { bearbeitenKnopf, befundeLink, einstellungenLink, exportLinks, verlaufLink } from './projektAktionen';
import { rechteImPortal } from './rechte';
import { creationOptions, credentialToJson, passkeyMoeglich, requestOptions } from './webauthn';
import { zielPfad } from './weiter';
import { debugAn, diagnose, setzeDebug } from '../debug';

const WILLKOMMEN_KEY = 'sv-willkommen';

const WILLKOMMEN_TEXTE = [
  'Willkommen bei siteBits.',
  'Hier sehen Sie Ihre Laserscan-Projekte direkt im Browser – Punktwolke, Standpunkte und Panoramen, ohne Installation.',
  'Probieren Sie aus: auf einen Standpunkt springen (Doppelklick auf eine Kugel), Strecken und Flächen messen (Werkzeugleiste oben), zwischen Punktwolke und Panorama überblenden.',
  'Zurück zur Übersicht kommen Sie jederzeit über ‚← Projekte‘ oben links.',
];

const KURZANLEITUNG = [
  'Maus links drehen, rechts verschieben, Rad zoomen.',
  'Taste m Messen, Esc abbrechen.',
  'Messwerte in Metern im Koordinatensystem des Projekts („lokal“ = Scanner-System ohne Georeferenz).',
];

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  klasse?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function nachricht(e: unknown): string {
  if (e instanceof Error && e.message) return e.message;
  return 'Unerwarteter Fehler.';
}

// Gemeinsamer Fehlertext-Baustein für Anmelden/Einladung: ein anfangs verstecktes <p class="sv-fehler">
// plus eine zeige()-Funktion, die es befüllt und einblendet — vermeidet den identischen
// fehlerEl/zeigeFehler-Block in beiden Formularen.
function fehleranzeige(): { el: HTMLParagraphElement; zeige: (text: string) => void } {
  const element = el('p', 'sv-fehler');
  element.hidden = true;
  element.setAttribute('role', 'alert');
  return {
    el: element,
    zeige: (text: string): void => {
      element.textContent = text;
      element.hidden = false;
    },
  };
}

function feld(
  label: string,
  id: string,
  typ: string,
  autoComplete?: 'email' | 'current-password' | 'new-password' | 'off',
): { wrapper: HTMLLabelElement; input: HTMLInputElement } {
  const wrapper = el('label', 'sv-feld');
  wrapper.htmlFor = id;
  wrapper.appendChild(el('span', undefined, label));
  const input = document.createElement('input');
  input.type = typ;
  input.id = id;
  input.name = id;
  input.required = true;
  input.autocomplete = autoComplete ?? (typ === 'email' ? 'email' : typ === 'password' ? 'current-password' : 'off');
  wrapper.appendChild(input);
  return { wrapper, input };
}

function willkommenGesehen(): boolean {
  try {
    return localStorage.getItem(WILLKOMMEN_KEY) === '1';
  } catch {
    return false;
  }
}

function willkommenMerken(): void {
  try {
    localStorage.setItem(WILLKOMMEN_KEY, '1');
  } catch {
    // Kein Storage verfügbar (z. B. privates Fenster) — die Box erscheint beim nächsten Laden
    // erneut. Das ist die bewusste, harmlose Ausweichlösung.
  }
}

// Schließbare Box oberhalb der Karten, kein Modal. Wird sowohl beim ersten Besuch
// (wenn sv-willkommen im localStorage fehlt) als auch über den „?“-Knopf jederzeit erneut
// aufgerufen — deshalb hier zustandslos: einfach den übergebenen Platzhalter neu befüllen.
export function zeigeWillkommen(root: HTMLElement, ausloeser?: HTMLButtonElement): void {
  root.replaceChildren();
  const box = el('div', 'sv-willkommen');
  box.id = 'sv-willkommen-box';

  const kopf = el('div', 'sv-willkommen-kopf');
  kopf.appendChild(el('h2', undefined, 'Willkommen'));
  const schliessen = el('button', 'sv-willkommen-schliessen', '×');
  schliessen.type = 'button';
  schliessen.setAttribute('aria-label', 'Willkommen schließen');
  schliessen.addEventListener('click', () => {
    willkommenMerken();
    root.replaceChildren();
    if (ausloeser) {
      ausloeser.setAttribute('aria-expanded', 'false');
      ausloeser.focus();
    }
  });
  kopf.appendChild(schliessen);
  box.appendChild(kopf);

  for (const text of WILLKOMMEN_TEXTE) box.appendChild(el('p', undefined, text));

  const details = document.createElement('details');
  details.appendChild(el('summary', undefined, 'Kurzanleitung'));
  const liste = document.createElement('ul');
  for (const punkt of KURZANLEITUNG) liste.appendChild(el('li', undefined, punkt));
  details.appendChild(liste);
  details.append(el('h3', undefined, 'KI-Vorschläge prüfen'), el('p', undefined, 'Ab der Rolle Bearbeiter führt „Zu prüfen“ auf der Projektkarte zu offenen Vorschlägen für POIs, Messungen und Befund-Fäden. Annehmen macht den Vorschlag gültig, Verwerfen entfernt ihn. „Alle annehmen“ und „Alle verwerfen“ verlangen eine Bestätigung und melden anschließend Erfolge und übersprungene Einträge. Im Viewer lassen sich Vorschläge am Ort prüfen oder im Ebenenbereich ausblenden. Exporte lassen Vorschläge aus.'));
  box.appendChild(details);

  root.appendChild(box);
}

// Schließbare Box neben der Willkommen-Box (gleiches Muster: Zahnrad-Knopf, Platzhalter wird
// neu befüllt statt eines Modals). Abschnitt „Erweitert" mit dem
// Debug-Schalter (localStorage, Portal und /ansicht teilen sich den Origin) und „Diagnose kopieren".
export function zeigeEinstellungen(root: HTMLElement, ausloeser?: HTMLButtonElement): void {
  root.replaceChildren();
  const box = el('div', 'sv-einstellungen');
  box.id = 'sv-einstellungen-box';

  const kopf = el('div', 'sv-einstellungen-kopf');
  kopf.appendChild(el('h2', undefined, 'Einstellungen'));
  const schliessen = el('button', 'sv-einstellungen-schliessen', '×');
  schliessen.type = 'button';
  schliessen.setAttribute('aria-label', 'Einstellungen schließen');
  schliessen.addEventListener('click', () => {
    root.replaceChildren();
    if (ausloeser) {
      ausloeser.setAttribute('aria-expanded', 'false');
      ausloeser.focus();
    }
  });
  kopf.appendChild(schliessen);
  box.appendChild(kopf);

  // API-Zugänge (persönliche Tokens) und darunter die Git-Namens-Einwilligung
  // (E-G1) — eigener Platzhalter, damit die Netzwerk-Antwort die restliche Box nicht blockiert.
  const apiZugaengePlatz = el('div');
  box.appendChild(apiZugaengePlatz);
  void zeigeApiZugaenge(apiZugaengePlatz, location.origin);

  const details = document.createElement('details');
  details.className = 'sv-erweitert';
  details.appendChild(el('summary', undefined, 'Erweitert'));

  const schalter = el('label', 'sv-schalter');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = debugAn();
  checkbox.addEventListener('change', () => setzeDebug(checkbox.checked));
  schalter.appendChild(checkbox);
  schalter.appendChild(el(
    'span', undefined,
    'Debug-Modus – ausführliches Protokoll in der Browser-Konsole (F12) und Messanzeige im Viewer',
  ));
  details.appendChild(schalter);

  const diagnoseZeile = el('div', 'sv-diagnose-zeile');
  const diagnoseStatus = el('span', 'sv-kopier-status');
  diagnoseStatus.setAttribute('aria-live', 'polite');
  const diagnoseBtn = el('button', undefined, 'Diagnose kopieren');
  diagnoseBtn.type = 'button';
  diagnoseBtn.addEventListener('click', () => {
    const merkeStatus = (text: string): void => {
      diagnoseStatus.textContent = text;
      window.setTimeout(() => {
        diagnoseStatus.textContent = '';
      }, 2000);
    };
    if (!navigator.clipboard) {
      merkeStatus('Kopieren nicht verfügbar');
      return;
    }
    navigator.clipboard.writeText(JSON.stringify(diagnose(), null, 2)).then(
      () => merkeStatus('Diagnose kopiert'),
      () => merkeStatus('Kopieren fehlgeschlagen'),
    );
  });
  diagnoseZeile.appendChild(diagnoseBtn);
  diagnoseZeile.appendChild(diagnoseStatus);
  details.appendChild(diagnoseZeile);

  box.appendChild(details);
  root.appendChild(box);
}

function baueKarte(p: Projekt): HTMLElement {
  const karte = el('article', 'sv-karte');

  const bild = el('div', 'sv-karte-bild');
  if (p.draufsicht) {
    const img = document.createElement('img');
    img.src = draufsichtUrl(p.id);
    img.alt = `Draufsicht: ${p.titel}`;
    img.loading = 'lazy';
    bild.appendChild(img);
  } else {
    bild.appendChild(el('span', undefined, 'keine Draufsicht'));
  }
  karte.appendChild(bild);

  karte.appendChild(el('h2', 'sv-karte-titel', p.titel));
  if (p.beschreibung.trim()) karte.appendChild(el('p', 'sv-karte-beschreibung', p.beschreibung));

  const zeile = [formatPunkte(p.punkte), formatStandpunkte(p.standpunkte), formatAusdehnung(p.ausdehnung), p.crs, formatGroesse(p.groesseBytes)];
  karte.appendChild(el('p', 'sv-karte-zeile', zeile.join(' · ')));
  if (p.scandatum) karte.appendChild(el('p', 'sv-karte-datum', `Scandatum: ${p.scandatum}`));
  // Befund-Fäden: „3 überfällig“, wenn ich oder mein Gewerk zuständig bin (Zahl vom Server).
  const ueberfaellig = p.befund_faeden === true ? ueberfaelligText(p.ueberfaellig_meine) : '';
  if (ueberfaellig) karte.appendChild(el('p', 'sv-karte-ueberfaellig', ueberfaellig));

  const aktionen = el('div', 'sv-karte-aktionen');

  const oeffnen = document.createElement('a');
  oeffnen.href = viewerLink(p.id);
  oeffnen.textContent = 'Öffnen';
  aktionen.appendChild(oeffnen);

  if (p.draufsicht) {
    const draufsichtLink = document.createElement('a');
    draufsichtLink.href = draufsichtUrl(p.id);
    draufsichtLink.textContent = 'Draufsicht (PNG)';
    draufsichtLink.setAttribute('download', '');
    aktionen.appendChild(draufsichtLink);
  }

  const status = el('span', 'sv-kopier-status');
  status.setAttribute('aria-live', 'polite');

  const kopierenBtn = el('button', undefined, 'Link kopieren');
  kopierenBtn.type = 'button';
  kopierenBtn.addEventListener('click', () => {
    const merkeStatus = (text: string): void => {
      status.textContent = text;
      window.setTimeout(() => {
        status.textContent = '';
      }, 2000);
    };
    if (!navigator.clipboard) {
      merkeStatus('Kopieren nicht verfügbar');
      return;
    }
    const absolut = new URL(viewerLink(p.id), location.origin).toString();
    navigator.clipboard.writeText(absolut).then(
      () => merkeStatus('Link kopiert'),
      () => merkeStatus('Kopieren fehlgeschlagen'),
    );
  });
  aktionen.appendChild(kopierenBtn);
  aktionen.appendChild(status);

  // Titel/Beschreibung ändern, Export, Verlauf nur laut Erlaubnisliste der Rolle (unbekannte Rollen sehen
  // nichts davon; der Server prüft trotzdem jede Anfrage selbst).
  const rechte = rechteImPortal(p.rolle, p.einstellungen);
  if (rechte.bearbeiten) {
    aktionen.appendChild(
      bearbeitenKnopf(karte, p, (neu) => {
        Object.assign(p, neu); // auch die Suchliste in zeigeStart sieht den neuen Titel
        karte.replaceWith(baueKarte(p));
      }),
    );
  }
  if (p.befund_faeden === true) aktionen.appendChild(befundeLink(p));
  if (rechte.export) for (const a of exportLinks(p)) aktionen.appendChild(a);
  if (rechte.verlauf) { aktionen.appendChild(verlaufLink(p)); aktionen.appendChild(zuPruefenLink(p)); }
  if (rechte.einstellungen) aktionen.appendChild(einstellungenLink(p));

  karte.appendChild(aktionen);
  return karte;
}

export function zeigeStart(
  root: HTMLElement,
  nutzer: Sitzung,
  projekteListe: Projekt[],
): void {
  root.replaceChildren();
  const seite = el('div', 'sv-seite');

  const kopf = el('header', 'sv-kopf');
  kopf.appendChild(el('h1', 'sv-kopf-titel', 'siteBits – Projekte'));
  // Aktionsleiste rechts in der Kopfzeile — Zahnrad „Einstellungen → Erweitert" hängt
  // hier neben der Hilfe, ohne dass dieser Bereich umgebaut werden musste.
  const aktionen = el('div', 'sv-kopf-aktionen');
  aktionen.appendChild(el('span', 'sv-kopf-nutzer', nutzer.name));
  if (nutzer.admin) {
    const verwaltung = document.createElement('a');
    verwaltung.href = '/verwaltung';
    verwaltung.textContent = 'Verwaltung';
    verwaltung.className = 'sv-kopf-link';
    aktionen.appendChild(verwaltung);
  }
  const hilfeBtn = el('button', undefined, '?');
  hilfeBtn.type = 'button';
  hilfeBtn.setAttribute('aria-label', 'Hilfe anzeigen');
  hilfeBtn.setAttribute('aria-controls', 'sv-willkommen-box');
  hilfeBtn.setAttribute('aria-expanded', String(!willkommenGesehen()));
  aktionen.appendChild(hilfeBtn);
  const einstellungenBtn = el('button', undefined, '⚙');
  einstellungenBtn.type = 'button';
  einstellungenBtn.setAttribute('aria-label', 'Einstellungen anzeigen');
  einstellungenBtn.setAttribute('aria-controls', 'sv-einstellungen-box');
  einstellungenBtn.setAttribute('aria-expanded', 'false');
  aktionen.appendChild(einstellungenBtn);
  kopf.appendChild(aktionen);
  seite.appendChild(kopf);

  const willkommenPlatz = el('div', 'sv-willkommen-platz');
  seite.appendChild(willkommenPlatz);
  hilfeBtn.addEventListener('click', () => {
    hilfeBtn.setAttribute('aria-expanded', 'true');
    zeigeWillkommen(willkommenPlatz, hilfeBtn);
  });
  if (!willkommenGesehen()) zeigeWillkommen(willkommenPlatz, hilfeBtn);

  const einstellungenPlatz = el('div', 'sv-einstellungen-platz');
  seite.appendChild(einstellungenPlatz);
  einstellungenBtn.addEventListener('click', () => {
    einstellungenBtn.setAttribute('aria-expanded', 'true');
    zeigeEinstellungen(einstellungenPlatz, einstellungenBtn);
  });

  const sucheZeile = el('div', 'sv-suche-zeile');
  const sucheEingabe = document.createElement('input');
  sucheEingabe.type = 'search';
  sucheEingabe.className = 'sv-suche';
  sucheEingabe.placeholder = 'Suche nach Titel oder Beschreibung …';
  sucheEingabe.setAttribute('aria-label', 'Projekte durchsuchen');
  sucheZeile.appendChild(sucheEingabe);
  const trefferStatus = el('p', 'sv-suche-status');
  trefferStatus.setAttribute('role', 'status');
  sucheZeile.appendChild(trefferStatus);
  seite.appendChild(sucheZeile);

  const raster = el('div', 'sv-karten-raster');
  seite.appendChild(raster);

  const renderKarten = (liste: Projekt[]): void => {
    raster.replaceChildren();
    if (liste.length === 0) {
      raster.appendChild(el('p', 'sv-karten-leer', 'Keine Projekte gefunden.'));
      return;
    }
    for (const p of liste) raster.appendChild(baueKarte(p));
  };
  renderKarten(projekteListe);
  sucheEingabe.addEventListener('input', () => {
    const treffer = filtere(projekteListe, sucheEingabe.value);
    renderKarten(treffer);
    trefferStatus.textContent = trefferText(treffer.length);
  });

  root.appendChild(seite);
}

async function passkeyAnmelden(): Promise<void> {
  const { challengeId, optionen } = await post<{ challengeId: string; optionen: unknown }>('/auth/passkey/optionen', {});
  const credential = (await navigator.credentials.get(requestOptions(optionen))) as PublicKeyCredential | null;
  if (!credential) throw new Error('Kein Passkey ausgewählt.');
  await post('/auth/passkey/anmelden', { challengeId, credential: credentialToJson(credential) });
}

export function zeigeAnmelden(root: HTMLElement): void {
  root.replaceChildren();
  const box = el('div', 'sv-anmelden');
  box.appendChild(el('h1', undefined, 'Anmelden'));

  const { el: fehlerEl, zeige: zeigeFehler } = fehleranzeige();

  const weiterZiel = zielPfad(new URLSearchParams(location.search).get('weiter'), location.origin);
  const nachAnmeldung = (): void => {
    location.href = weiterZiel;
  };

  if (passkeyMoeglich()) {
    const passkeyBtn = el('button', 'sv-primaer', 'Mit Passkey anmelden');
    passkeyBtn.type = 'button';
    passkeyBtn.addEventListener('click', () => {
      fehlerEl.hidden = true;
      passkeyBtn.disabled = true;
      passkeyAnmelden().then(nachAnmeldung, (e: unknown) => {
        zeigeFehler(nachricht(e));
        passkeyBtn.disabled = false;
      });
    });
    box.appendChild(passkeyBtn);
    box.appendChild(el('div', 'sv-trenner', 'oder'));
  }

  const form = document.createElement('form');
  const emailFeld = feld('E-Mail', 'sv-email', 'email');
  const passwortFeld = feld('Passwort', 'sv-passwort-anmelden', 'password');
  form.appendChild(emailFeld.wrapper);
  form.appendChild(passwortFeld.wrapper);
  const absenden = el('button', undefined, 'Mit Passwort anmelden');
  absenden.type = 'submit';
  form.appendChild(absenden);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    fehlerEl.hidden = true;
    absenden.disabled = true;
    post('/auth/passwort/anmelden', { email: emailFeld.input.value, passwort: passwortFeld.input.value }).then(
      nachAnmeldung,
      (e: unknown) => {
        zeigeFehler(nachricht(e));
        absenden.disabled = false;
      },
    );
  });
  box.appendChild(form);
  box.appendChild(fehlerEl);
  root.appendChild(box);
}

async function passkeyEinladungAbschliessen(token: string, geraet: string): Promise<void> {
  const { challengeId, optionen } = await post<{ challengeId: string; optionen: unknown }>(
    '/auth/einladung/passkey/optionen',
    { token },
  );
  const credential = (await navigator.credentials.create(creationOptions(optionen))) as PublicKeyCredential | null;
  if (!credential) throw new Error('Passkey konnte nicht erstellt werden.');
  await post('/auth/einladung/passkey/abschluss', {
    token,
    challengeId,
    credential: credentialToJson(credential),
    geraet,
  });
}

function renderEinladungsFormular(root: HTMLElement, token: string, name: string): void {
  root.replaceChildren();
  const box = el('div', 'sv-einladung');
  box.appendChild(el('h1', undefined, `Hallo ${name}, legen Sie jetzt Ihren Zugang an.`));

  const { el: fehlerEl, zeige: zeigeFehler } = fehleranzeige();
  const nachAbschluss = (): void => {
    location.href = '/';
  };

  if (passkeyMoeglich()) {
    const passkeyBtn = el('button', 'sv-primaer', 'Mit Passkey anmelden (empfohlen)');
    passkeyBtn.type = 'button';
    box.appendChild(passkeyBtn);
    box.appendChild(
      el(
        'p',
        'sv-erklaerung',
        'Ein Passkey ist ein Schlüssel auf Ihrem Gerät – Sie bestätigen mit Fingerabdruck, Gesicht oder Geräte-PIN. Kein Passwort nötig.',
      ),
    );
    const geraetFeld = feld('Gerätename (optional)', 'sv-geraet', 'text');
    geraetFeld.input.required = false;
    box.appendChild(geraetFeld.wrapper);

    passkeyBtn.addEventListener('click', () => {
      fehlerEl.hidden = true;
      passkeyBtn.disabled = true;
      passkeyEinladungAbschliessen(token, geraetFeld.input.value).then(nachAbschluss, (e: unknown) => {
        zeigeFehler(nachricht(e));
        passkeyBtn.disabled = false;
      });
    });
    box.appendChild(el('div', 'sv-trenner', 'oder'));
  }

  const passwortForm = document.createElement('form');
  const passwortFeld = feld('Passwort (mind. 12 Zeichen)', 'sv-passwort-neu', 'password', 'new-password');
  passwortFeld.input.minLength = 12;
  const wiederholungFeld = feld('Passwort wiederholen', 'sv-passwort-wiederholen', 'password', 'new-password');
  passwortForm.appendChild(passwortFeld.wrapper);
  passwortForm.appendChild(wiederholungFeld.wrapper);
  const passwortSenden = el('button', undefined, 'Zugang mit Passwort anlegen');
  passwortSenden.type = 'submit';
  passwortForm.appendChild(passwortSenden);

  if (passkeyMoeglich()) {
    // Passkey ist die empfohlene, primäre Option — das Passwort-Formular bleibt bis zum Klick
    // versteckt, damit „Stattdessen …“ tatsächlich eine Alternative zu etwas Sichtbarem ist.
    const nebenwegBtn = el('button', 'sv-nebenweg', 'Stattdessen ein Passwort festlegen');
    nebenwegBtn.type = 'button';
    passwortForm.hidden = true;
    nebenwegBtn.addEventListener('click', () => {
      passwortForm.hidden = !passwortForm.hidden;
    });
    box.appendChild(nebenwegBtn);
  }
  // Ohne Passkey-Unterstützung (kein sicherer Kontext, kein WebAuthn) ist das Passwort-Formular
  // die einzige Option — direkt sichtbar, kein Klick auf einen Alternativ-Knopf nötig, der ins
  // Leere ginge (es gäbe ja keine erste Option, zu der er die Alternative wäre).
  box.appendChild(passwortForm);

  passwortForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    fehlerEl.hidden = true;
    if (passwortFeld.input.value.length < 12) {
      zeigeFehler('Das Passwort muss mindestens 12 Zeichen haben.');
      return;
    }
    if (passwortFeld.input.value !== wiederholungFeld.input.value) {
      zeigeFehler('Die Passwörter stimmen nicht überein.');
      return;
    }
    passwortSenden.disabled = true;
    post('/auth/einladung/passwort', { token, passwort: passwortFeld.input.value }).then(nachAbschluss, (e: unknown) => {
      zeigeFehler(nachricht(e));
      passwortSenden.disabled = false;
    });
  });

  box.appendChild(fehlerEl);
  root.appendChild(box);
}

const EINLADUNG_UNGUELTIG = 'Einladung ungültig, abgelaufen oder bereits eingelöst.';

function zeigeEinladungFehler(root: HTMLElement, text: string): void {
  root.replaceChildren();
  const box = el('div', 'sv-einladung');
  box.appendChild(el('h1', undefined, 'Einladung'));
  box.appendChild(el('p', 'sv-fehler', text));
  root.appendChild(box);
}

const EINLADUNG_ERNEUT_OEFNEN = 'Bitte öffnen Sie den Einladungslink erneut aus Ihrer Nachricht.';

/**
 * Meldung, die statt des Formulars erscheint, oder null, wenn der Token geprüft werden soll.
 * `token === null`: gar kein Fragment in der Adresse — typisch nach einem Neuladen, weil main.ts
 * das Fragment gleich beim ersten Aufruf aus der Adresszeile entfernt. Dann hilft nur der Link aus
 * der Nachricht, „ungültig“ wäre irreführend.
 * Server verlangt min_length=10 für den Token (TokenIn); ein leerer oder zu kurzer Token kommt
 * aus einem kaputten/abgeschnittenen Link und würde sonst nur ein nichtssagendes 422 „Eingabe
 * ungültig.“ auslösen — hier direkt dieselbe Meldung wie bei einer ungültigen Einladung zeigen,
 * ohne die Anfrage überhaupt zu schicken.
 */
export function einladungHinweis(token: string | null): string | null {
  if (token === null) return EINLADUNG_ERNEUT_OEFNEN;
  if (token.length < 10) return EINLADUNG_UNGUELTIG;
  return null;
}

export function zeigeEinladung(root: HTMLElement, token: string | null): void {
  root.replaceChildren();
  const hinweis = einladungHinweis(token);
  if (hinweis !== null || token === null) {
    zeigeEinladungFehler(root, hinweis ?? EINLADUNG_UNGUELTIG);
    return;
  }

  const box = el('div', 'sv-einladung');
  box.appendChild(el('p', undefined, 'Einladung wird geprüft …'));
  root.appendChild(box);

  post<{ name: string; email: string; zweck: string }>('/auth/einladung/pruefen', { token }).then(
    (e) => renderEinladungsFormular(root, token, e.name),
    (e: unknown) => zeigeEinladungFehler(root, e instanceof ApiFehler ? e.message : nachricht(e)),
  );
}
