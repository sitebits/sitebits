// Seite /projekt-einstellungen?p=<id> (Projektleitung und Admin): Beteiligte, Einladen, offene
// Einladungen, Gewerke, Kategorien, Schalter, Vorlage (nur Admin, solange nicht eingerichtet) und der Weg
// zum Verlauf. Alles über /api/v1/projekte/<id>/… — serverseitig geprüft und protokolliert. Nur
// createElement/textContent (strenge CSP). Eine Spalte, auch am Tablet und Handy.
import { anfrage, PlattformFehler, projektPfad } from '../plattformApi';
import { rolleText } from './rechte';
import { abschnitt, el, feld, fehlerText, knopf, tabelle, tabelleRegion, zeigeLink, zeile } from './verwaltung';
import { datumText } from './verwaltungModell';
import { teilenAbschnitt } from './teilen';
import { freigabenAbschnitt } from './freigaben';
import type { FreigabenAntwort } from './freigaben';
import {
  darfVerwalten, einladungErgebnisText, hinzufuegen, NUR_LEITUNG_TEXT, umschalten, verschiebe, vergebbareRollen, VORLAGEN, zeileAenderbar,
} from './projektEinstellungenModell';
import type { Beteiligter, EinladungsAntwort, Einstellungen, Gewerk, Kategorie, ProjektEinladung } from './projektEinstellungenModell';

interface ProjektKarte { id: string; titel: string; rolle: string; einstellungen: Einstellungen; kategorien: Kategorie[] }
interface Daten { info: ProjektKarte; beteiligte: Beteiligter[]; einladungen: ProjektEinladung[]; gewerke: Gewerk[] }
type Melde = (text: string) => void;
/** Neu laden; `fokus` = ID des Elements, das danach den Fokus bekommt (Überschrift des Abschnitts). */
type Neu = (fokus?: string) => Promise<void>;
interface Umgebung { pfad: string; neu: Neu; melde: Melde; status: Melde; linkPlatz: HTMLElement; anlegenFehler: HTMLElement }

function ueberschrift(s: HTMLElement, id: string): HTMLElement {
  const h = s.querySelector('h2');
  if (h) {
    h.id = id;
    h.tabIndex = -1;
  }
  return s;
}

function auswahl(werte: readonly { wert: string; text: string }[], wert: string): HTMLSelectElement {
  const s = el('select');
  for (const w of werte) {
    const o = el('option', undefined, w.text);
    o.value = w.wert;
    s.append(o);
  }
  s.value = wert;
  return s;
}

const gewerkWerte = (gewerke: readonly Gewerk[]): { wert: string; text: string }[] => [
  { wert: '', text: 'ohne Gewerk' },
  ...gewerke.map((g) => ({ wert: String(g.id), text: `${g.name} (${g.kurz})` })),
];
const gewerkWert = (s: HTMLSelectElement): number | null => (s.value === '' ? null : Number(s.value));

function beteiligteAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  if (d.beteiligte.length === 0) return ueberschrift(abschnitt('Beteiligte', el('p', undefined, 'Noch keine Beteiligten.')), 'sv-pe-beteiligte');
  const { table, tbody } = tabelle(['Name', 'E-Mail', 'Rolle', 'Gewerk', 'Letzte Anmeldung', 'Aktion'], 'sv-tabelle');
  for (const b of d.beteiligte) {
    const aktion = el('div', 'sv-aktionen');
    let rolleZelle: string | HTMLElement = rolleText(b.rolle);
    let gewerkZelle: string | HTMLElement = b.gewerk ? `${b.gewerk.name} (${b.gewerk.kurz})` : '–';
    if (zeileAenderbar(b, d.info.rolle)) {
      const r = auswahl(vergebbareRollen(d.info.rolle), b.rolle);
      r.setAttribute('aria-label', `Rolle von ${b.nutzer}`);
      const g = auswahl(gewerkWerte(d.gewerke), b.gewerk ? String(b.gewerk.id) : '');
      g.setAttribute('aria-label', `Gewerk von ${b.nutzer}`);
      for (const s of [r, g]) {
        s.addEventListener('change', () => {
          u.melde('');
          s.disabled = true;
          anfrage('PUT', `${u.pfad}/beteiligte/${b.nutzerId}`, { rolle: r.value, gewerk: gewerkWert(g) })
            .then(() => u.status(`${b.nutzer}: gespeichert.`))
            .catch((e: unknown) => u.melde(fehlerText(e)))
            .finally(() => {
              s.disabled = false;
            });
        });
      }
      rolleZelle = r;
      gewerkZelle = g;
      aktion.append(
        knopf('Entfernen', async () => {
          await anfrage('PUT', `${u.pfad}/beteiligte/${b.nutzerId}`, { rolle: null, gewerk: null });
          await u.neu('sv-pe-beteiligte');
          u.status(`${b.nutzer} aus dem Projekt entfernt.`);
        }, u.melde, `${b.nutzer} aus dem Projekt entfernen?`),
      );
    } else aktion.append(el('span', 'sv-hinweis', b.admin ? 'Admin' : 'nur durch die Instanzverwaltung änderbar'));
    const name = b.gesperrt ? `${b.nutzer} (gesperrt)` : b.nutzer;
    tbody.append(zeile(name, b.email ?? '–', rolleZelle, gewerkZelle, datumText(b.letzteAnmeldung ?? null), aktion));
  }
  return ueberschrift(abschnitt('Beteiligte', tabelleRegion('Beteiligte', table)), 'sv-pe-beteiligte');
}

function einladenAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  const form = el('form', 'sv-einladen');
  const name = el('input');
  name.required = true;
  name.maxLength = 120;
  name.autocomplete = 'off';
  const email = el('input');
  email.type = 'email';
  email.required = true;
  email.maxLength = 320;
  const rolle = auswahl(vergebbareRollen(d.info.rolle), 'betrachter');
  const gewerk = auswahl(gewerkWerte(d.gewerke), '');
  // Der Name eines Auswahlfelds im Label enthielte sonst die Optionstexte.
  rolle.setAttribute('aria-label', 'Rolle');
  gewerk.setAttribute('aria-label', 'Gewerk (optional)');
  const absenden = el('button', undefined, 'Einladen');
  absenden.type = 'submit';
  form.append(feld('Name', 'sv-pe-name', name), feld('E-Mail', 'sv-pe-email', email), feld('Rolle', 'sv-pe-rolle', rolle), feld('Gewerk (optional)', 'sv-pe-gewerk', gewerk), absenden);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    u.melde('');
    absenden.disabled = true;
    const eingeladen = name.value;
    anfrage<EinladungsAntwort>('POST', `${u.pfad}/einladungen`, { name: name.value, email: email.value, rolle: rolle.value, gewerk: gewerkWert(gewerk) })
      .then(async (a) => {
        if (a.ergebnis === 'eingeladen') zeigeLink(u.linkPlatz, a.link, eingeladen);
        else u.linkPlatz.replaceChildren();
        await u.neu();
        u.status(einladungErgebnisText(a));
        u.linkPlatz.querySelector<HTMLInputElement>('input')?.focus();
      })
      .catch((e: unknown) => u.melde(fehlerText(e)))
      .finally(() => {
        absenden.disabled = false;
      });
  });
  return abschnitt(
    'Beteiligte einladen',
    el('p', 'sv-hinweis', 'Die Plattform verschickt keine E-Mails: Den Einladungslink selbst weitergeben (7 Tage gültig, einmal verwendbar). Hat die E-Mail schon ein Konto, gilt der Zugriff sofort – ohne Link.'),
    form,
  );
}

function offeneAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  if (d.einladungen.length === 0) return abschnitt('Offene Einladungen', el('p', undefined, 'Keine offenen Einladungen.'));
  const gewerkName = new Map(d.gewerke.map((g) => [g.id, g.name]));
  const { table, tbody } = tabelle(['Name', 'E-Mail', 'Rolle', 'Gewerk', 'Gültig bis', ''], 'sv-tabelle');
  for (const e of d.einladungen) {
    const widerrufen = knopf('Widerrufen', async () => {
      await anfrage('POST', `${u.pfad}/einladungen/${encodeURIComponent(e.id)}/widerrufen`);
      await u.neu('sv-pe-offen');
      u.status(`Einladung für ${e.name} widerrufen.`);
    }, u.melde);
    widerrufen.setAttribute('aria-label', `Einladung für ${e.name} widerrufen`);
    tbody.append(zeile(e.name, e.email, e.rolle ? rolleText(e.rolle) : '–', e.gewerk === null ? '–' : (gewerkName.get(e.gewerk) ?? '–'), datumText(e.ablauf), widerrufen));
  }
  return ueberschrift(abschnitt('Offene Einladungen', tabelleRegion('Offene Einladungen', table)), 'sv-pe-offen');
}

function gewerkeAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  const inhalt: HTMLElement[] = [el('p', 'sv-hinweis', 'Wer zu einem Gewerk der Bauherrenseite gehört, sieht später auch interne Einträge.')];
  if (d.gewerke.length === 0) inhalt.push(el('p', undefined, 'Noch keine Gewerke.'));
  else {
    const { table, tbody } = tabelle(['Name', 'Kurzzeichen', 'Bauherrenseite', 'Aktion'], 'sv-tabelle');
    for (const g of d.gewerke) {
      const name = el('input');
      name.value = g.name;
      name.maxLength = 80;
      name.setAttribute('aria-label', `Name von Gewerk ${g.name}`);
      const kurz = el('input');
      kurz.value = g.kurz;
      kurz.maxLength = 4;
      kurz.size = 4;
      kurz.setAttribute('aria-label', `Kurzzeichen von Gewerk ${g.name}`);
      const bh = el('input');
      bh.type = 'checkbox';
      bh.checked = g.bauherrenseite;
      bh.setAttribute('aria-label', `${g.name} gehört zur Bauherrenseite`);
      const aktion = el('div', 'sv-aktionen');
      const speichern = knopf('Speichern', async () => {
        await anfrage('PATCH', `${u.pfad}/gewerke/${g.id}`, { name: name.value, kurz: kurz.value, bauherrenseite: bh.checked });
        u.status(`Gewerk „${name.value}“ gespeichert.`);
      }, u.melde);
      speichern.setAttribute('aria-label', `Gewerk ${g.name} speichern`);
      const loeschen = knopf('Löschen', async () => {
        await anfrage('DELETE', `${u.pfad}/gewerke/${g.id}`);
        await u.neu('sv-pe-gewerke');
        u.status(`Gewerk „${g.name}“ gelöscht.`);
      }, u.melde, `Gewerk „${g.name}“ löschen? Zugeordnete Beteiligte sind danach ohne Gewerk.`);
      loeschen.setAttribute('aria-label', `Gewerk ${g.name} löschen`);
      aktion.append(speichern, loeschen);
      tbody.append(zeile(name, kurz, bh, aktion));
    }
    inhalt.push(tabelleRegion('Gewerke', table));
  }
  const form = el('form', 'sv-gewerk-neu');
  const name = el('input');
  name.required = true;
  name.maxLength = 80;
  const kurz = el('input');
  kurz.required = true;
  kurz.maxLength = 4;
  const bh = el('input');
  bh.type = 'checkbox';
  const anlegen = el('button', undefined, 'Gewerk anlegen');
  anlegen.type = 'submit';
  form.append(feld('Name des Gewerks', 'sv-pe-gewerk-name', name), feld('Kurzzeichen (höchstens 4 Zeichen)', 'sv-pe-gewerk-kurz', kurz), feld('Bauherrenseite', 'sv-pe-gewerk-bh', bh), anlegen);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    u.melde('');
    anlegen.disabled = true;
    anfrage<Gewerk>('POST', `${u.pfad}/gewerke`, { name: name.value, kurz: kurz.value, bauherrenseite: bh.checked })
      .then(async (g) => {
        await u.neu('sv-pe-gewerk-name');
        u.status(`Gewerk „${g.name}“ angelegt.`);
      })
      .catch((e: unknown) => u.melde(fehlerText(e)))
      .finally(() => {
        anlegen.disabled = false;
      });
  });
  inhalt.push(form);
  return ueberschrift(abschnitt('Gewerke', ...inhalt), 'sv-pe-gewerke');
}

function kategorienAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  let liste: Kategorie[] = d.info.kategorien.map((k) => ({ ...k }));
  const ol = el('ol', 'sv-kategorien');
  const senden = async (neu: Kategorie[], fokus: string): Promise<void> => {
    const a = await anfrage<{ kategorien: Kategorie[] }>('PUT', `${u.pfad}/kategorien`, { kategorien: neu });
    liste = a.kategorien;
    zeichne();
    u.status('Kategorien gespeichert.');
    ol.querySelector<HTMLElement>(`[data-fokus="${CSS.escape(fokus)}"]`)?.focus();
  };
  const zeichne = (): void => {
    ol.replaceChildren();
    liste.forEach((k, i) => {
      const li = el('li');
      const an = el('input');
      an.type = 'checkbox';
      an.checked = k.aktiv;
      an.id = `sv-pe-kat-${i}`;
      an.dataset.fokus = `aktiv-${k.name}`;
      const label = el('label', undefined, k.aktiv ? k.name : `${k.name} (ausgeschaltet)`);
      label.htmlFor = an.id;
      an.addEventListener('change', () => {
        u.melde('');
        senden(umschalten(liste, i), `aktiv-${k.name}`).catch((e: unknown) => {
          an.checked = !an.checked;
          u.melde(fehlerText(e));
        });
      });
      const hoch = knopf('▲', () => senden(verschiebe(liste, i, -1), `hoch-${k.name}`), u.melde);
      hoch.setAttribute('aria-label', `${k.name} nach oben`);
      hoch.dataset.fokus = `hoch-${k.name}`;
      const runter = knopf('▼', () => senden(verschiebe(liste, i, 1), `runter-${k.name}`), u.melde);
      runter.setAttribute('aria-label', `${k.name} nach unten`);
      runter.dataset.fokus = `runter-${k.name}`;
      li.append(an, label, hoch, runter);
      ol.append(li);
    });
  };
  const form = el('form', 'sv-kategorie-neu');
  const neu = el('input');
  neu.maxLength = 60;
  const hinzu = el('button', undefined, 'Kategorie hinzufügen');
  hinzu.type = 'submit';
  form.append(feld('Neue Kategorie', 'sv-pe-kategorie', neu), hinzu);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    u.melde('');
    const ergebnis = hinzufuegen(liste, neu.value);
    if (!Array.isArray(ergebnis)) {
      u.melde(ergebnis.fehler);
      neu.focus();
      return;
    }
    senden(ergebnis, 'neu')
      .then(() => {
        neu.value = '';
        neu.focus();
      })
      .catch((e: unknown) => u.melde(fehlerText(e)));
  });
  zeichne();
  return ueberschrift(abschnitt(
    'Kategorien',
    el('p', 'sv-hinweis', 'Vorschläge beim Markieren im Viewer, in dieser Reihenfolge. Ausgeschaltete Kategorien bleiben an vorhandenen Befunden stehen, werden aber nicht mehr angeboten. „Rückmeldung“ ist fest.'),
    ol,
    form,
  ), 'sv-pe-kategorien');
}

function schalterAbschnitt(d: Daten, u: Umgebung): HTMLElement {
  const schalter = (id: string, text: string, hilfe: string, wert: boolean, schluessel: 'rueckmeldungen' | 'exportBearbeiter' | 'befundFaeden' | 'nurLeseLinks' | 'panoramenNurFreigegeben' | 'benachrichtigung'): HTMLElement => {
    const cb = el('input');
    cb.type = 'checkbox';
    cb.id = id;
    cb.checked = wert;
    const hilfeEl = el('span', 'sv-hinweis', hilfe);
    hilfeEl.id = `${id}-hilfe`;
    cb.setAttribute('aria-describedby', hilfeEl.id);
    const label = el('label', 'sv-schalter', undefined);
    label.htmlFor = id;
    label.append(cb, el('span', undefined, text));
    cb.addEventListener('change', () => {
      u.melde('');
      cb.disabled = true;
      anfrage('PATCH', `${u.pfad}/einstellungen`, { [schluessel]: cb.checked })
        .then(async () => {
          // Der Abschnitt „Nur-Lese-Links“ hängt am Schalter: neu zeichnen, Fokus zurück auf den Schalter.
          if (schluessel === 'nurLeseLinks' || schluessel === 'panoramenNurFreigegeben') await u.neu(id);
          u.status(`${text}: ${cb.checked ? 'an' : 'aus'}.`);
        })
        .catch((e: unknown) => {
          cb.checked = !cb.checked;
          u.melde(fehlerText(e));
        })
        .finally(() => {
          cb.disabled = false;
        });
    });
    const w = el('div', 'sv-schalter-zeile');
    w.append(label, hilfeEl);
    return w;
  };
  return abschnitt(
    'Schalter',
    schalter('sv-pe-rueckmeldungen', 'Rückmeldungen erlaubt', 'Aus: Rückmelder sehen das Projekt nur noch wie Betrachter.', d.info.einstellungen.rueckmeldungen, 'rueckmeldungen'),
    schalter('sv-pe-export', 'Export für Bearbeiter', 'Aus: nur Projektleitung und Admins exportieren CSV und JSON. Ein Ablaufschalter, keine Datenschutzgrenze: Bearbeiter sehen dieselben Daten weiterhin in der Liste.', d.info.einstellungen.exportBearbeiter, 'exportBearbeiter'),
    schalter('sv-pe-befundfaeden', 'Befund-Fäden', 'An: jeder Befund hat einen Verlauf mit Kommentaren, Status, Zuständigkeit und Frist, dazu die Liste „Fällig/überfällig“. Aus: Befunde wie bisher, Rückmeldungen als eigene Markierung.', d.info.einstellungen.befundFaeden === true, 'befundFaeden'),
    schalter('sv-pe-nurlese', 'Nur-Lese-Links erlaubt', 'An: die Projektleitung kann Links anlegen, mit denen Gäste ohne Konto Punktwolke und Meshes ansehen. Aus: alle Links wirken sofort nicht mehr.', d.info.einstellungen.nurLeseLinks === true, 'nurLeseLinks'),
    schalter('sv-pe-panofrei', 'Panoramen nur freigegeben zeigen', 'An: Betrachter und Rückmelder sehen Panoramen erst nach der Freigabe unten. Aus: Panoramen älterer Pakete ohne Anonymisierung sind für alle Beteiligten sichtbar (Übergang). Neu importierte Panoramen sind immer erst nach Freigabe sichtbar.', d.info.einstellungen.panoramenNurFreigegeben === true, 'panoramenNurFreigegeben'),
    schalter('sv-pe-mail', 'Benachrichtigungen per Mail', 'An: Beteiligte, die in ihren Einstellungen eingewilligt haben, bekommen bei Neuem an ihren Befunden eine kurze Mail ohne Projektinhalte. Wirkt nur, wenn die Instanz Mails verschickt.', d.info.einstellungen.benachrichtigung !== false, 'benachrichtigung'),
  );
}

function vorlageAbschnitt(u: Umgebung): HTMLElement {
  const s = auswahl(VORLAGEN, 'bestandsaufnahme');
  const anwenden = knopf('Vorlage anwenden', async () => {
    await anfrage('POST', `${u.pfad}/einrichten`, { vorlage: s.value });
    await u.neu('sv-pe-kategorien');
    u.status('Vorlage angewendet.');
  }, u.melde, 'Vorlage anwenden? Sie lässt sich danach nicht mehr wechseln; Kategorien und Schalter bleiben änderbar. Panoramen sehen Betrachter und Rückmelder danach erst nach einer Freigabe (Schalter „Panoramen nur freigegeben zeigen“).');
  return abschnitt(
    'Vorlage',
    el('p', 'sv-hinweis', 'Nur beim Einrichten: legt die Startkategorien fest. Ohne Wahl gilt „Bestandsaufnahme“, sobald jemand Kategorien oder Schalter ändert.'),
    feld('Vorlage', 'sv-pe-vorlage', s),
    anwenden,
  );
}

function verlaufAbschnitt(pid: string): HTMLElement {
  const a = el('a', undefined, 'Verlauf des Projekts ansehen');
  a.href = `/verlauf?p=${encodeURIComponent(pid)}`;
  return abschnitt('Verlauf', el('p', undefined, 'Einladungen, Rechte, Gewerke, Kategorien und Schalter stehen mit Zeit und Person im Verlauf des Projekts.'), a);
}

export async function zeigeProjektEinstellungen(root: HTMLElement, pid: string): Promise<void> {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-projekt-einstellungen');
  const kopf = el('header', 'sv-kopf');
  const zurueck = el('a', undefined, '← Projekte');
  zurueck.href = '/';
  const titel = el('h1', 'sv-kopf-titel', 'Projekt-Einstellungen');
  kopf.append(zurueck, titel);
  const meldung = el('p', 'sv-fehler');
  meldung.hidden = true;
  meldung.setAttribute('role', 'alert');
  const statusEl = el('p', 'sv-status');
  statusEl.setAttribute('role', 'status');
  const linkPlatz = el('div');
  // Dasselbe Element bei jedem Neuaufbau wieder ans Formular hängen: andere Aktionen
  // dürfen den Fehler beim Link-Anlegen nicht löschen. Erst erneutes Absenden leert ihn.
  const anlegenFehler = el('p', 'sv-fehler');
  anlegenFehler.hidden = true;
  anlegenFehler.setAttribute('role', 'alert');
  const inhalt = el('div', 'sv-spalte');
  seite.append(kopf, meldung, statusEl, linkPlatz, inhalt);
  root.append(seite);
  // Jede Aktion beginnt mit melde(''): Fehler und der Erfolgstext der vorigen Aktion verschwinden.
  const melde: Melde = (t) => {
    meldung.textContent = t;
    meldung.hidden = t === '';
    if (t === '') statusEl.textContent = '';
  };
  const status: Melde = (t) => {
    statusEl.textContent = t;
  };
  const pfad = projektPfad(pid);
  let mitPruefung = false;
  const neu: Neu = async (fokus) => {
    let info: ProjektKarte;
    try {
      info = await anfrage<ProjektKarte>('GET', pfad);
    } catch (e) {
      inhalt.replaceChildren();
      melde(e instanceof PlattformFehler && e.status === 404 ? 'Projekt nicht gefunden.' : fehlerText(e));
      return;
    }
    titel.textContent = `Projekt-Einstellungen – ${info.titel}`;
    if (!darfVerwalten(info.rolle)) {
      inhalt.replaceChildren();
      melde(NUR_LEITUNG_TEXT);
      return;
    }
    try {
      const [b, e, g] = await Promise.all([
        anfrage<{ beteiligte: Beteiligter[] }>('GET', `${pfad}/beteiligte`),
        anfrage<{ einladungen: ProjektEinladung[] }>('GET', `${pfad}/einladungen`),
        anfrage<{ gewerke: Gewerk[] }>('GET', `${pfad}/gewerke`),
      ]);
      const d: Daten = { info, beteiligte: b.beteiligte, einladungen: e.einladungen, gewerke: g.gewerke };
      const u: Umgebung = { pfad, neu, melde, status, linkPlatz, anlegenFehler };
      const teilenS = await teilenAbschnitt(info.einstellungen.nurLeseLinks === true, u);
      const freigaben = await anfrage<FreigabenAntwort>('GET', `${pfad}/freigaben${mitPruefung ? '?pruefen=true' : ''}`);
      mitPruefung = false;
      inhalt.replaceChildren(
        beteiligteAbschnitt(d, u),
        einladenAbschnitt(d, u),
        offeneAbschnitt(d, u),
        gewerkeAbschnitt(d, u),
        kategorienAbschnitt(d, u),
        schalterAbschnitt(d, u),
        freigabenAbschnitt(freigaben, {
          pfad, admin: info.rolle === 'admin', melde, status, neu,
          pruefen: async (f) => { mitPruefung = true; await neu(f); },
        }),
        teilenS,
        ...(info.rolle === 'admin' && info.einstellungen.vorlage === null ? [vorlageAbschnitt(u)] : []),
        verlaufAbschnitt(pid),
      );
      if (fokus) document.getElementById(fokus)?.focus();
    } catch (err) {
      inhalt.replaceChildren();
      melde(fehlerText(err));
    }
  };
  await neu();
}
