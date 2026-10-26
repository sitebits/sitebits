// E-Mail-Benachrichtigung (F8) im Portal: Einwilligung in den Einstellungen, Bestätigung der Adresse
// (Link aus der Bestätigungsmail: /#mail-bestaetigen=<token>, nur angemeldet im selben Konto) und die
// öffentliche Seite /abmelden#t=<token> (Abmelden ohne Anmeldung, per Knopf — Mail-Scanner rufen Links
// ab, ohne zu klicken). Ohne eingerichteten Postdienst antwortet der Server 404: dann fehlt alles hier.
// Nur createElement/textContent (strenge CSP).
import { anfrage, PlattformFehler } from '../plattformApi';

export interface KontoZustand { eingeschaltet: boolean; bestaetigt: boolean; bestaetigungOffen: boolean; email?: string }

const PFAD = '/api/v1/konto/benachrichtigung';
export const BESTAETIGEN_PRAEFIX = '#mail-bestaetigen=';
const TOKEN_RE = /^[A-Za-z0-9_.-]{10,600}$/;

/** Ein Satz zum Zustand (für Status und Tests). */
export function zustandText(z: KontoZustand): string {
  if (!z.eingeschaltet) return 'Aus: Du bekommst keine E-Mails.';
  if (z.bestaetigt) return `An: E-Mails gehen an ${z.email ?? 'deine Adresse'}. Inhalt nur „es gibt Neues“ mit Link, ohne Projektdaten.`;
  if (z.bestaetigungOffen) return 'Fast fertig: Bitte den Link in der Bestätigungsmail öffnen (2 Tage gültig).';
  return 'An, aber die Adresse ist nicht bestätigt: Schalter einmal aus- und wieder einschalten, dann kommt eine neue Bestätigungsmail.';
}

/** Token aus `#mail-bestaetigen=…` oder `#t=…`, sonst null. */
export function tokenAus(hash: string, praefix: string): string | null {
  if (!hash.startsWith(praefix)) return null;
  const t = decodeURIComponent(hash.slice(praefix.length));
  return TOKEN_RE.test(t) ? t : null;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

const fehlerText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** Abschnitt in den Einstellungen; bleibt leer, wenn die Instanz keine Mails verschickt. */
export async function zeigeBenachrichtigung(platz: HTMLElement): Promise<void> {
  let z: KontoZustand;
  try {
    z = await anfrage<KontoZustand>('GET', PFAD);
  } catch (e) {
    if (e instanceof PlattformFehler && e.status === 404) return;
    platz.replaceChildren(el('p', 'sv-fehler', fehlerText(e)));
    return;
  }
  const abschnitt = el('section', 'sv-benachrichtigung');
  abschnitt.append(el('h3', undefined, 'E-Mail-Benachrichtigungen'));
  const cb = el('input');
  cb.type = 'checkbox';
  cb.id = 'sv-mail-an';
  cb.checked = z.eingeschaltet;
  const label = el('label', 'sv-schalter');
  label.htmlFor = cb.id;
  label.append(cb, el('span', undefined, 'E-Mail, wenn es an meinen Befunden Neues gibt'));
  const status = el('p', 'sv-hinweis', zustandText(z));
  status.id = 'sv-mail-status';
  status.setAttribute('role', 'status');
  cb.setAttribute('aria-describedby', status.id);
  cb.addEventListener('change', () => {
    cb.disabled = true;
    anfrage<KontoZustand>('PUT', PFAD, { eingeschaltet: cb.checked })
      .then((neu) => { status.textContent = zustandText(neu); })
      .catch((e: unknown) => { cb.checked = !cb.checked; status.textContent = fehlerText(e); })
      .finally(() => { cb.disabled = false; });
  });
  abschnitt.append(label, status, el('p', 'sv-hinweis',
    'Abbestellen geht jederzeit hier, je Befund im Verlauf oder über den Link in jeder Mail. Antworten auf Mails kommen nicht an – geantwortet wird im Verlauf.'));
  platz.replaceChildren(abschnitt);
}

/** Startseite mit `#mail-bestaetigen=…`: bestätigen (angemeldet), Fragment entfernen, Ergebnis melden. */
export async function bestaetigeAusHash(melde: (text: string) => void): Promise<void> {
  const token = tokenAus(location.hash, BESTAETIGEN_PRAEFIX);
  if (!location.hash.startsWith(BESTAETIGEN_PRAEFIX)) return;
  history.replaceState(null, '', location.pathname + location.search);
  if (!token) {
    melde('Der Bestätigungslink ist unvollständig.');
    return;
  }
  try {
    await anfrage('POST', `${PFAD}/bestaetigen`, { token });
    melde('E-Mail-Adresse bestätigt. Benachrichtigungen sind an.');
  } catch (e) {
    melde(fehlerText(e));
  }
}

/** Öffentliche Seite /abmelden#t=<token>. */
export function zeigeAbmelden(root: HTMLElement, hash: string): void {
  const token = tokenAus(hash, '#t=');
  history.replaceState(null, '', location.pathname);
  root.replaceChildren();
  const box = el('main', 'sv-einladung');
  box.append(el('h1', undefined, 'E-Mails abbestellen'));
  const status = el('p', 'sv-status');
  status.setAttribute('role', 'status');
  if (!token) {
    box.append(el('p', 'sv-fehler', 'Der Link ist unvollständig. Bitte den ganzen Link aus der Mail öffnen.'));
    root.append(box);
    return;
  }
  const knopf = el('button', 'sv-primaer', 'Abbestellen');
  knopf.type = 'button';
  knopf.addEventListener('click', () => {
    knopf.disabled = true;
    anfrage<{ abgemeldet: 'faden' | 'alle' }>('POST', `/api/mail/abmelden/${encodeURIComponent(token)}`)
      .then((r) => {
        status.textContent = r.abgemeldet === 'faden'
          ? 'Erledigt: Zu diesem Befund bekommst du keine E-Mails mehr.'
          : 'Erledigt: Du bekommst keine E-Mails mehr von dieser Plattform.';
        knopf.hidden = true;
      })
      .catch((e: unknown) => {
        status.textContent = e instanceof PlattformFehler && e.status === 404 ? 'Der Link ist ungültig oder abgelaufen. Abbestellen geht auch angemeldet in den Einstellungen.' : fehlerText(e);
        knopf.disabled = false;
      });
  });
  box.append(el('p', undefined, 'Mit dem Knopf bestellst du die E-Mails ab, für die dieser Link gilt. Eine Anmeldung ist dafür nicht nötig.'), knopf, status);
  root.append(box);
}
