// Einstellungen → „API-Zugänge": persönliche API-Tokens anlegen,
// auflisten, widerrufen — ausschließlich über /api/v1/konto/* (nie per Bearer, E-T3: dieser Pfad
// zählt nur das Sitzungs-Cookie) — und darunter die Git-Namens-Einwilligung (E-G1, „In der
// Git-Historie mit meinem Namen erscheinen"). Reines createElement/textContent-DOM (strenge CSP)
// wie verwaltung.ts; `mcpKonfiguration` und `kontoFehlerText` sind die reinen, für sich getesteten
// Funktionen hier (vitest, siehe web/tests/portal-api-zugaenge.test.ts).
import { anfrage, PlattformFehler } from '../plattformApi';
import { datumText } from './verwaltungModell';

export interface TokenEintrag {
  agent?: boolean;
  id: number;
  name: string;
  umfang: 'lesen' | 'schreiben';
  erstelltAm: number;
  laeuftAb: number;
  zuletztBenutzt: number | null;
}

type Melde = (text: string) => void;
type Neu = () => Promise<void>;

const GUELTIGKEITEN = [30, 90, 365] as const;
const GUELTIGKEIT_VORGABE = 90;
const UMFANG_TEXT: Record<TokenEintrag['umfang'], string> = { lesen: 'nur lesen', schreiben: 'lesen und schreiben' };

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

const fehlerText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** Textanfang der Server-403 bei zu alter Sitzung (server web.NEU_ANMELDEN_PRAEFIX). */
const NEU_ANMELDEN_PRAEFIX = 'Bitte zuerst neu anmelden, um ';
export const NEU_ANMELDEN_HINWEIS =
  'Aus Sicherheitsgründen braucht dieser Schritt eine frische Anmeldung (höchstens 10 Minuten alt). ' +
  'Bitte abmelden, wieder anmelden und es dann erneut versuchen.';

/** Befund B-TE-M1: Token anlegen und die Git-Namens-Einwilligung einschalten
 *  verlangen eine frische Anmeldung. Die Server-403 dafür (erkannt an Status **und** Textanfang —
 *  eine fehlende CSRF-Kennung ist ebenfalls 403) wird zu einer klaren Anleitung; alles andere
 *  bleibt der Servertext. Rein, für sich getestet (web/tests/portal-api-zugaenge.test.ts). */
export function kontoFehlerText(e: unknown): string {
  if (e instanceof PlattformFehler && e.status === 403 && e.message.startsWith(NEU_ANMELDEN_PRAEFIX)) {
    return NEU_ANMELDEN_HINWEIS;
  }
  return fehlerText(e);
}

function feld(text: string, id: string, eingabe: HTMLInputElement | HTMLSelectElement): HTMLLabelElement {
  eingabe.id = id;
  const l = el('label', 'sv-feld');
  l.htmlFor = id;
  l.append(el('span', undefined, text), eingabe);
  return l;
}

function abschnitt(titel: string, ...inhalt: HTMLElement[]): HTMLElement {
  const s = el('section', 'sv-abschnitt');
  s.append(el('h2', undefined, titel), ...inhalt);
  return s;
}

function tabelle(kopf: string[], klasse: string): { table: HTMLTableElement; tbody: HTMLTableSectionElement } {
  const table = el('table', klasse);
  const tr = el('tr');
  for (const t of kopf) tr.append(el('th', undefined, t));
  const thead = el('thead');
  thead.append(tr);
  const tbody = el('tbody');
  table.append(thead, tbody);
  return { table, tbody };
}

function zeile(...zellen: (string | HTMLElement)[]): HTMLTableRowElement {
  const tr = el('tr');
  for (const z of zellen) {
    const td = el('td');
    if (typeof z === 'string') td.textContent = z;
    else td.append(z);
    tr.append(td);
  }
  return tr;
}

/** Zwei-Klick-Bestätigung ohne `window.confirm`: der erste Klick „schärft" den Knopf (Text
 *  wechselt, automatischer Rücksprung nach 4 s ohne zweiten Klick) — erst der zweite Klick löst
 *  die Aktion aus. */
function widerrufKnopf(aktion: () => Promise<void>, melde: Melde): HTMLButtonElement {
  const TEXT = 'Widerrufen';
  const b = el('button', undefined, TEXT);
  b.type = 'button';
  let scharf = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const entschaerfen = (): void => {
    scharf = false;
    b.textContent = TEXT;
    b.classList.remove('sv-gefahr-scharf');
  };
  b.addEventListener('click', () => {
    if (!scharf) {
      scharf = true;
      b.textContent = 'Wirklich widerrufen?';
      b.classList.add('sv-gefahr-scharf');
      clearTimeout(timer);
      timer = setTimeout(entschaerfen, 4000);
      return;
    }
    clearTimeout(timer);
    melde('');
    b.disabled = true;
    aktion()
      .catch((e: unknown) => melde(fehlerText(e)))
      .finally(() => {
        b.disabled = false;
        entschaerfen();
      });
  });
  return b;
}

function tokenTabelle(tokens: TokenEintrag[], neu: Neu, melde: Melde, nachWiderruf: (id: number) => void): HTMLElement {
  if (tokens.length === 0) return el('p', 'sv-hinweis', 'Noch keine API-Zugänge angelegt.');
  const { table, tbody } = tabelle(['Name', 'KI', 'Umfang', 'Läuft ab', 'Zuletzt benutzt', ''], 'sv-tabelle');
  for (const t of tokens) {
    const widerrufen = widerrufKnopf(async () => {
      // Die Liste lädt auch bei einer fehlgeschlagenen Anfrage neu (z. B. ein Token, das
      // inzwischen schon abgelaufen oder anderswo widerrufen wurde, wäre sonst weiter sichtbar).
      try {
        await anfrage<void>('DELETE', `/api/v1/konto/tokens/${t.id}`);
        nachWiderruf(t.id);
      } finally {
        await neu();
      }
    }, melde);
    tbody.append(zeile(t.name, t.agent ? 'KI' : '–', UMFANG_TEXT[t.umfang], datumText(t.laeuftAb), datumText(t.zuletztBenutzt), widerrufen));
  }
  return table;
}

function zeigeToken(platz: HTMLElement, basis: string, token: string, onAusblenden: () => void): void {
  platz.replaceChildren();
  const box = el('div', 'sv-token-box');
  box.append(el('p', undefined, 'Token – nur jetzt sichtbar. Bitte kopieren, es lässt sich danach nicht mehr anzeigen.'));
  const feldEl = el('input');
  feldEl.readOnly = true;
  feldEl.value = token;
  feldEl.setAttribute('aria-label', 'API-Token');
  const status = el('span', 'sv-kopier-status');
  status.setAttribute('aria-live', 'polite');
  const kopierknopf = (text: string, inhalt: string, fertig: string): HTMLButtonElement => {
    const b = el('button', undefined, text);
    b.type = 'button';
    b.addEventListener('click', () => {
      if (!navigator.clipboard) {
        feldEl.select();
        status.textContent = 'Bitte mit Strg+C kopieren';
        return;
      }
      navigator.clipboard.writeText(inhalt).then(
        () => {
          status.textContent = fertig;
        },
        () => {
          status.textContent = 'Kopieren fehlgeschlagen';
        },
      );
    });
    return b;
  };
  const kopieren = kopierknopf('Token kopieren', token, 'Token kopiert');
  const mcpKopieren = kopierknopf('MCP-Konfiguration kopieren', mcpKonfiguration(basis, token), 'MCP-Konfiguration kopiert');
  const ausblenden = el('button', undefined, 'Ausblenden');
  ausblenden.type = 'button';
  ausblenden.addEventListener('click', () => {
    platz.replaceChildren();
    onAusblenden();
  });
  box.append(
    el('p', 'sv-hinweis', 'Das Token handelt mit deinen Rechten. Nicht in Chats einfügen oder weitergeben – bei Verdacht sofort widerrufen.'),
    feldEl, kopieren, mcpKopieren, ausblenden, status,
  );
  platz.append(box);
}

export function agentAuswahl(umfang: string, checked: boolean): { disabled: boolean; checked: boolean } {
  return {disabled: umfang !== 'schreiben', checked: umfang === 'schreiben' && checked};
}

function tokenFormular(neu: Neu, melde: Melde, onErstellt: (id: number, token: string) => void): HTMLElement {
  const form = el('form', 'sv-einladen');
  const name = el('input');
  name.required = true;
  name.maxLength = 60;
  const umfang = el('select');
  for (const [wert, text] of Object.entries(UMFANG_TEXT)) {
    const o = el('option', undefined, text);
    o.value = wert;
    umfang.append(o);
  }
  const agent = el('input');
  agent.type = 'checkbox';
  const agentStand = (): void => { Object.assign(agent,agentAuswahl(umfang.value,agent.checked)); };
  umfang.addEventListener('change', agentStand);
  agentStand();
  const gueltigkeit = el('select');
  for (const tage of GUELTIGKEITEN) {
    const o = el('option', undefined, `${tage} Tage`);
    o.value = String(tage);
    // `.selected` allein setzt nur die aktuelle Auswahl, nicht
    // die von `form.reset()` wiederhergestellte Vorgabe — dafür braucht es `defaultSelected`
    // (spiegelt das `selected`-Attribut). Ohne das fiel die Gültigkeit nach dem Anlegen eines
    // Tokens auf die erste Option (30 Tage) zurück statt bei der Vorgabe (90) zu bleiben.
    o.selected = tage === GUELTIGKEIT_VORGABE;
    o.defaultSelected = tage === GUELTIGKEIT_VORGABE;
    gueltigkeit.append(o);
  }
  const absenden = el('button', undefined, 'Token anlegen');
  absenden.type = 'submit';
  form.append(
    feld('Name', 'sv-token-name', name),
    feld('Umfang', 'sv-token-umfang', umfang),
    feld('für einen KI-Agenten (Einträge werden Vorschläge)', 'sv-token-agent', agent),
    el('p', 'sv-hinweis', 'Nur für lesen und schreiben verfügbar. Ein normaler Token macht einen Agenten nicht erkennbar.'),
    feld('Gültigkeit', 'sv-token-tage', gueltigkeit),
    absenden,
  );
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    melde('');
    absenden.disabled = true;
    anfrage<{ token: string; info: TokenEintrag }>('POST', '/api/v1/konto/tokens', {
      agent: umfang.value === 'schreiben' && agent.checked,
      name: name.value,
      umfang: umfang.value,
      tage: Number(gueltigkeit.value),
    })
      .then(async (r) => {
        onErstellt(r.info.id, r.token);
        form.reset();
        agentStand();
        await neu();
      })
      .catch((e: unknown) => melde(kontoFehlerText(e)))
      .finally(() => {
        absenden.disabled = false;
      });
  });
  return form;
}

// Wer einen Zugang für einen KI-Assistenten anlegt, muss wissen,
// was an dessen Anbieter geht. Seit B-TE-M3 blendet der MCP-Server Namen anderer und Freitexte
// ab Werk aus; nur der Betreiber-Schalter „voll“ gibt sie frei.
export const KI_DATENSCHUTZ_HINWEIS =
  'Über KI-Assistenten (MCP) gehen Projektinhalte wie Titel, Koordinaten und Messungen an den Anbieter des ' +
  'KI-Programms; Namen anderer Beteiligter und Notizen blendet der MCP-Server ab Werk aus. Nur mit einem vom ' +
  'Verantwortlichen freigegebenen Anbieter nutzen (Einzelheiten: Abschnitt „Datenschutz“ in der Anleitung des ' +
  'MCP-Servers).';

export const EINWILLIGUNG_TEXT =
  'Gilt für alle Bearbeitungen, die ab jetzt exportiert werden – auch frühere, noch nicht exportierte. ' +
  'Bereits exportierte Einträge bleiben unverändert und lassen sich nicht zurücknehmen – auch nicht durch ' +
  'Ausschalten oder Löschen des Kontos. Der Export kann an einen Git-Server (z. B. Forgejo) übertragen werden.';

function gitNameSchalter(zeigen: boolean, melde: Melde): HTMLElement {
  const box = el('div');
  const schalter = el('label', 'sv-schalter');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = zeigen;
  checkbox.addEventListener('change', () => {
    melde('');
    const vorher = !checkbox.checked;
    checkbox.disabled = true;
    anfrage<{ gitNameZeigen: boolean }>('PATCH', '/api/v1/konto', { gitNameZeigen: checkbox.checked })
      .catch((e: unknown) => {
        checkbox.checked = vorher;
        melde(kontoFehlerText(e));
      })
      .finally(() => {
        checkbox.disabled = false;
      });
  });
  schalter.append(checkbox, el('span', undefined, 'In der Git-Historie mit meinem Namen erscheinen'));
  box.append(
    schalter,
    // Befund B-TE-N1: der Schalter wird zur Exportzeit gelesen (gitexport._autor) —
    // der Text muss genau das sagen, was die Einwilligung bewirkt.
    el('p', 'sv-hinweis', EINWILLIGUNG_TEXT),
  );
  return box;
}

/** Reines JSON-Schnipsel für Claude Desktop (das MCP-Paket, dessen README erklärt
 *  die Einrichtung) — für sich getestet, siehe web/tests/portal-api-zugaenge.test.ts. */
export function mcpKonfiguration(basis: string, token: string): string {
  return JSON.stringify(
    { mcpServers: { scanview: { command: 'scanview-mcp', env: { SCANVIEW_URL: basis, SCANVIEW_TOKEN: token } } } },
    null,
    2,
  );
}

export async function zeigeApiZugaenge(platz: HTMLElement, basis: string): Promise<void> {
  platz.replaceChildren();
  const meldung = el('p', 'sv-fehler');
  meldung.hidden = true;
  meldung.setAttribute('aria-live', 'polite');
  const melde: Melde = (text) => {
    meldung.textContent = text;
    meldung.hidden = text === '';
  };

  const tabellenPlatz = el('div');
  const tokenPlatz = el('div');
  const gitNamePlatz = el('div');

  // Merkt sich die ID des Tokens, der gerade (nur jetzt sichtbar)
  // in `tokenPlatz` steht. Wird genau der in der Tabelle widerrufen, verschwindet die Box mit —
  // sonst stünde ein bereits ungültiger Wert weiter offen sichtbar da.
  let angezeigt: number | null = null;

  const nachWiderruf = (id: number): void => {
    if (angezeigt === id) {
      tokenPlatz.replaceChildren();
      angezeigt = null;
    }
  };

  const neu: Neu = async () => {
    melde('');
    try {
      const d = await anfrage<{ tokens: TokenEintrag[] }>('GET', '/api/v1/konto/tokens');
      tabellenPlatz.replaceChildren(tokenTabelle(d.tokens, neu, melde, nachWiderruf));
    } catch (e) {
      melde(fehlerText(e));
    }
  };

  const onErstellt = (id: number, token: string): void => {
    angezeigt = id;
    zeigeToken(tokenPlatz, basis, token, () => {
      angezeigt = null;
    });
  };

  const ladeGitName = async (): Promise<void> => {
    try {
      const d = await anfrage<{ gitNameZeigen: boolean }>('GET', '/api/v1/konto');
      gitNamePlatz.replaceChildren(gitNameSchalter(d.gitNameZeigen, melde));
    } catch (e) {
      melde(fehlerText(e));
    }
  };

  platz.append(
    abschnitt(
      'API-Zugänge – für Skripte und KI-Assistenten',
      el('p', 'sv-hinweis', KI_DATENSCHUTZ_HINWEIS),
      meldung, tabellenPlatz, tokenFormular(neu, melde, onErstellt), tokenPlatz,
    ),
    gitNamePlatz,
  );
  await Promise.all([neu(), ladeGitName()]);
}
