// Verwaltungsseite /verwaltung (nur Admin): Einladung anlegen (Link genau einmal sichtbar),
// offene Einladungen widerrufen, Nutzer sperren/entsperren/überall abmelden/Zugang zurücksetzen, Rechte-Matrix
// Nutzer × Projekt, Statistik der letzten 30 Tage, Lesezugriffe (lesezugriffe.ts). Alles über /api/v1/admin/* (serverseitig geprüft und
// protokolliert); nur createElement/textContent (strenge CSP).
import { anfrage } from '../plattformApi';
import { lesezugriffeAbschnitt } from './lesezugriffe';
import { datumText, einladungsText, istEigenesKonto, istRolle, rechteAusAuswahl, ROLLEN_AUSWAHL, sparkline, statusText, tagText, zelle } from './verwaltungModell';
import type { AdminNutzer, AdminProjekt, OffeneEinladung, StatistikProjekt } from './verwaltungModell';

interface Daten {
  nutzer: AdminNutzer[];
  projekte: AdminProjekt[];
  einladungen: OffeneEinladung[];
  statistik: StatistikProjekt[];
}
/** Leerer Text blendet die Meldung aus (jede Aktion beginnt damit, damit kein alter Fehler stehen bleibt). */
type Melde = (text: string) => void;
type Neu = () => Promise<void>;

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

export const fehlerText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function knopf(text: string, aktion: () => Promise<void>, melde: Melde, rueckfrage?: string): HTMLButtonElement {
  const b = el('button', undefined, text);
  b.type = 'button';
  b.addEventListener('click', () => {
    if (rueckfrage && !window.confirm(rueckfrage)) return;
    melde('');
    b.disabled = true;
    aktion()
      .catch((e: unknown) => melde(fehlerText(e)))
      .finally(() => {
        b.disabled = false;
      });
  });
  return b;
}

export function tabelle(kopf: string[], klasse: string): { table: HTMLTableElement; tbody: HTMLTableSectionElement } {
  const table = el('table', klasse);
  const tr = el('tr');
  for (const t of kopf) tr.append(el('th', undefined, t));
  const thead = el('thead');
  thead.append(tr);
  const tbody = el('tbody');
  table.append(thead, tbody);
  return { table, tbody };
}

export function abschnitt(titel: string, ...inhalt: HTMLElement[]): HTMLElement {
  const s = el('section', 'sv-abschnitt');
  s.append(el('h2', undefined, titel), ...inhalt);
  return s;
}

// Datentabellen dürfen laut WCAG 1.4.10 zweidimensional bleiben; die Seite selbst darf trotzdem nicht
// waagerecht scrollen — eigener Scroll-Container mit Fokus und Namen statt eines Seitenumbruchs
// (axe-Regel scrollable-region-focusable).
export function tabelleRegion(name: string, table: HTMLTableElement): HTMLElement {
  const region = el('div', 'sv-tabelle-region');
  region.setAttribute('role', 'region');
  region.setAttribute('aria-label', name);
  region.tabIndex = 0;
  region.append(table);
  return region;
}

export function zeile(...zellen: (string | HTMLElement)[]): HTMLTableRowElement {
  const tr = el('tr');
  for (const z of zellen) {
    const td = el('td');
    if (typeof z === 'string') td.textContent = z;
    else td.append(z);
    tr.append(td);
  }
  return tr;
}

export function zeigeLink(platz: HTMLElement, link: string, name: string, zuruecksetzen = false): void {
  platz.replaceChildren();
  const box = el('div', 'sv-einladungslink');
  box.append(el('p', undefined, 'Einladungslink – nur jetzt sichtbar. Bitte kopieren und selbst weitergeben (7 Tage gültig, einmal verwendbar).'));
  const feld = el('input');
  feld.readOnly = true;
  feld.value = link;
  feld.setAttribute('aria-label', 'Einladungslink');
  const status = el('span', 'sv-kopier-status');
  status.setAttribute('aria-live', 'polite');
  const kopierknopf = (text: string, inhalt: string, fertig: string): HTMLButtonElement => {
    const b = el('button', undefined, text);
    b.type = 'button';
    b.addEventListener('click', () => {
      if (!navigator.clipboard) {
        feld.select();
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
  const kopieren = kopierknopf('Link kopieren', link, 'Link kopiert');
  // Die Plattform verschickt keine Mails — fertiger Text zum Einfügen in Mail/Messenger.
  const textKopieren = kopierknopf('Einladungstext kopieren', einladungsText(link, name, zuruecksetzen), 'Text kopiert');
  // Ausblenden entfernt den Link ganz aus der Seite (er ist danach nicht mehr abrufbar).
  const ausblenden = el('button', undefined, 'Ausblenden');
  ausblenden.type = 'button';
  ausblenden.addEventListener('click', () => platz.replaceChildren());
  box.append(feld, kopieren, textKopieren, ausblenden, status);
  platz.append(box);
}

export function feld(text: string, id: string, eingabe: HTMLInputElement | HTMLSelectElement): HTMLLabelElement {
  eingabe.id = id;
  const l = el('label', 'sv-feld');
  l.htmlFor = id;
  l.append(el('span', undefined, text), eingabe);
  return l;
}

function einladungsFormular(d: Daten, linkPlatz: HTMLElement, neu: Neu, melde: Melde): HTMLElement {
  const form = el('form', 'sv-einladen');
  const name = el('input');
  name.required = true;
  name.maxLength = 120;
  const email = el('input');
  email.type = 'email';
  email.required = true;
  const istAdmin = el('input');
  istAdmin.type = 'checkbox';
  const auswahl = new Map<string, HTMLSelectElement>();
  const rechte = el('fieldset', 'sv-rechte-auswahl');
  rechte.append(el('legend', undefined, 'Rechte je Projekt'));
  for (const p of d.projekte) {
    const s = el('select');
    for (const r of ROLLEN_AUSWAHL) {
      const o = el('option', undefined, r.text);
      o.value = r.wert;
      s.append(o);
    }
    auswahl.set(p.id, s);
    rechte.append(feld(p.titel, `sv-ein-recht-${p.id}`, s));
  }
  const absenden = el('button', undefined, 'Einladung anlegen');
  absenden.type = 'submit';
  form.append(
    feld('Name', 'sv-ein-name', name),
    feld('E-Mail', 'sv-ein-email', email),
    feld('Admin (sieht und verwaltet alles)', 'sv-ein-admin', istAdmin),
    rechte,
    absenden,
  );
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    melde('');
    absenden.disabled = true;
    const werte = Object.fromEntries([...auswahl].map(([pid, s]) => [pid, s.value]));
    const eingeladen = name.value;
    anfrage<{ link: string }>('POST', '/api/v1/admin/einladungen', {
      name: eingeladen,
      email: email.value,
      admin: istAdmin.checked,
      rechte: rechteAusAuswahl(werte),
    })
      .then(async (r) => {
        zeigeLink(linkPlatz, r.link, eingeladen);
        form.reset();
        await neu();
      })
      .catch((e: unknown) => melde(fehlerText(e)))
      .finally(() => {
        absenden.disabled = false;
      });
  });
  return abschnitt('Einladung anlegen', form);
}

function offeneEinladungen(d: Daten, neu: Neu, melde: Melde): HTMLElement {
  if (d.einladungen.length === 0) return abschnitt('Offene Einladungen', el('p', undefined, 'Keine offenen Einladungen.'));
  const { table, tbody } = tabelle(['Name', 'E-Mail', 'Zweck', 'Gültig bis', ''], 'sv-tabelle');
  for (const e of d.einladungen) {
    const widerrufen = knopf(
      'Widerrufen',
      async () => {
        await anfrage<void>('POST', `/api/v1/admin/einladungen/${encodeURIComponent(e.id)}/widerrufen`);
        await neu();
      },
      melde,
    );
    tbody.append(zeile(e.name, e.email, e.zweck === 'neu' ? 'neues Konto' : 'Zugang zurücksetzen', datumText(e.ablauf), widerrufen));
  }
  return abschnitt('Offene Einladungen', tabelleRegion('Offene Einladungen', table));
}

function nutzerTabelle(d: Daten, eigeneEmail: string, linkPlatz: HTMLElement, neu: Neu, melde: Melde): HTMLElement {
  const { table, tbody } = tabelle(['Name', 'E-Mail', 'Rolle', 'Status', 'Letzte Anmeldung', 'Aktionen'], 'sv-tabelle');
  for (const n of d.nutzer) {
    const aktionen = el('div', 'sv-aktionen');
    const pfad = `/api/v1/admin/nutzer/${n.id}`;
    tbody.append(zeile(n.name, n.email, n.admin ? 'Admin' : '–', statusText(n), datumText(n.letzteAnmeldung), aktionen));
    // Eigenes Konto: sich selbst sperren/abmelden/zurücksetzen würde die eigene Sitzung beenden (Final-Fix C/D).
    if (istEigenesKonto(n, eigeneEmail)) {
      aktionen.append(el('span', 'sv-hinweis', 'eigenes Konto'));
      continue;
    }
    // Anonymisierte Konten sind endgültig — keine Aktionen (Server lehnt Entsperren/Zurücksetzen ab).
    if (n.anonymisiert) {
      aktionen.append(el('span', 'sv-hinweis', 'anonymisiert – endgültig'));
      continue;
    }
    aktionen.append(
      n.gesperrt
        ? knopf('Entsperren', async () => { await anfrage<void>('POST', `${pfad}/entsperren`); await neu(); }, melde)
        : knopf('Sperren', async () => { await anfrage<void>('POST', `${pfad}/sperren`); await neu(); }, melde, `${n.name} sperren und überall abmelden?`),
      knopf('Überall abmelden', async () => { await anfrage<void>('POST', `${pfad}/abmelden-ueberall`); await neu(); }, melde, `${n.name} auf allen Geräten abmelden?`),
      knopf(
        'Zugang zurücksetzen',
        async () => {
          const r = await anfrage<{ link: string }>('POST', '/api/v1/admin/einladungen', { name: n.name, email: n.email, zuruecksetzen: true });
          zeigeLink(linkPlatz, r.link, n.name, true);
          await neu();
        },
        melde,
        `Rücksetz-Link für ${n.name} erzeugen? Beim Einlösen werden Passkeys, Passwort und Sitzungen ersetzt.`,
      ),
    );
  }
  return abschnitt('Nutzer', tabelleRegion('Nutzer', table));
}

function rechteMatrix(d: Daten, melde: Melde): HTMLElement {
  const { table, tbody } = tabelle(['Nutzer', ...d.projekte.map((p) => p.titel)], 'sv-tabelle sv-matrix');
  for (const n of d.nutzer) {
    const zellen: (string | HTMLElement)[] = [n.name];
    for (const p of d.projekte) {
      const wert = zelle(n, p.id);
      if (wert === 'admin') {
        zellen.push('Admin');
        continue;
      }
      if (n.anonymisiert) {
        zellen.push('–');
        continue;
      }
      const s = el('select');
      s.setAttribute('aria-label', `${n.name} – ${p.titel}`);
      for (const r of ROLLEN_AUSWAHL) {
        const o = el('option', undefined, r.text);
        o.value = r.wert;
        s.append(o);
      }
      s.value = wert;
      s.addEventListener('change', () => {
        const vorher = zelle(n, p.id);
        melde('');
        s.disabled = true;
        anfrage<{ rolle: string | null }>('PUT', `/api/v1/admin/nutzer/${n.id}/rechte/${encodeURIComponent(p.id)}`, { rolle: s.value || null })
          .then(() => {
            if (istRolle(s.value)) n.rechte[p.id] = s.value;
            else delete n.rechte[p.id];
          })
          .catch((e: unknown) => {
            s.value = vorher === 'admin' ? '' : vorher;
            melde(fehlerText(e));
          })
          .finally(() => {
            s.disabled = false;
          });
      });
      zellen.push(s);
    }
    tbody.append(zeile(...zellen));
  }
  return abschnitt('Rechte', tabelleRegion('Rechte', table));
}

function statistikTabelle(d: Daten): HTMLElement {
  const { table, tbody } = tabelle(
    ['Projekt', 'Öffnungen', 'Standpunkt-Sprünge', 'Messungen', 'POIs', 'Öffnungen je Tag', 'Zuletzt geöffnet'],
    'sv-tabelle sv-statistik',
  );
  for (const p of d.statistik) {
    const verlauf = el('span', 'sv-sparkline', sparkline(p.verlauf.map((t) => t.oeffnen)));
    // Ein <span> hat sonst keine eigene Rolle („generic“) — aria-label wird dort laut WAI-ARIA-
    // Namensberechnung nicht zuverlässig als Zugänglichkeitsname übernommen; role="img" behebt das.
    verlauf.setAttribute('role', 'img');
    verlauf.setAttribute('aria-label', 'Öffnungen je Tag, ältester Tag zuerst');
    const tr = zeile(p.titel, String(p.summen.oeffnen), String(p.summen.standpunkt), String(p.summen.messung), String(p.summen.poi), verlauf, tagText(p.zuletztGeoeffnet));
    tr.dataset.projekt = p.id;
    tbody.append(tr);
  }
  return abschnitt('Statistik (30 Tage)', tabelleRegion('Statistik (30 Tage)', table));
}

export async function zeigeVerwaltung(root: HTMLElement, nutzer: { name: string; email: string; admin: boolean }): Promise<void> {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-verwaltung');
  const kopf = el('header', 'sv-kopf');
  const zurueck = el('a', undefined, '← Projekte');
  zurueck.href = '/';
  kopf.append(zurueck, el('h1', 'sv-kopf-titel', 'Verwaltung'));
  const meldung = el('p', 'sv-fehler');
  meldung.hidden = true;
  meldung.setAttribute('aria-live', 'polite');
  const melde: Melde = (text) => {
    meldung.textContent = text;
    meldung.hidden = text === '';
  };
  const linkPlatz = el('div');
  const inhalt = el('div');
  seite.append(kopf, meldung, linkPlatz, inhalt);
  root.append(seite);
  if (!nutzer.admin) {
    melde('Diese Seite ist nur für Admins.');
    return;
  }
  const neu: Neu = async () => {
    melde('');
    try {
      const [n, e, s] = await Promise.all([
        anfrage<{ nutzer: AdminNutzer[]; projekte: AdminProjekt[] }>('GET', '/api/v1/admin/nutzer'),
        anfrage<{ einladungen: OffeneEinladung[] }>('GET', '/api/v1/admin/einladungen'),
        anfrage<{ projekte: StatistikProjekt[] }>('GET', '/api/v1/admin/statistik?tage=30'),
      ]);
      const d: Daten = { nutzer: n.nutzer, projekte: n.projekte, einladungen: e.einladungen, statistik: s.projekte };
      inhalt.replaceChildren(
        einladungsFormular(d, linkPlatz, neu, melde),
        offeneEinladungen(d, neu, melde),
        nutzerTabelle(d, nutzer.email, linkPlatz, neu, melde),
        rechteMatrix(d, melde),
        statistikTabelle(d),
        lesezugriffeAbschnitt(d.projekte, d.nutzer),
      );
    } catch (e) {
      melde(fehlerText(e));
    }
  };
  await neu();
}
