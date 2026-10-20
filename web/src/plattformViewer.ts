// Plattformmodus des Viewers: fragt die eigene Rolle ab, lädt Messungen/POIs des
// Projekts vom Server und hängt die Server-Persistenz an. Was die Rolle darf, sagt allein die Erlaubnisliste
// in rechte.ts (Bearbeiter schreiben, Rückmelder ändern nur eigene Rückmeldungen, alle anderen sehen nur).
// Knopf „Browser-POIs in das Projekt übernehmen“ (erst nach
// geladenem Serverstand, Marke = Fingerabdruck des übernommenen Stands, gesetzt erst nach bestätigtem
// Abgleich). Bei pagehide / verborgenem Tab wird sofort mit keepalive gesendet. main.ts ruft
// verbindePlattform nur im Plattformmodus auf; null → Server kennt das Projekt nicht, alles wie heute.
import { befundeLesen } from './fadenApi';
import { kategorienAus } from './fadenModell';
import { FadenStaende } from './fadenStaende';
import type { Measurement, MeasureStore } from './measureStore';
import type { Panorama } from './panorama';
import { anfrage, projektPfad, START_MS } from './plattformApi';
import type { ProjektInfo, Rolle } from './plattformApi';
import type { PoiPersistenz } from './poiPersistenz';
import { kategorieVorschlaege, PoiPersistence } from './poiStore';
import type { PoiStorage } from './poiStore';
import type { PoiPanel } from './pois';
import { NUR_LESEN, rechte } from './rechte';
import type { ViewerRechte } from './rechte';
import { MessungSync } from './serverMessungen';
import { ServerPoiPersistenz } from './serverPois';
import { leerenBeimVerlassen, statistikSender, StatistikZaehler } from './statistik';

export interface PlattformViewer {
  messungPruefen?(id: string, aktion: 'annehmen' | 'verwerfen'): Promise<void>;
  readonly rolle: Rolle;
  /** Wirksame Rechte im Viewer (Erlaubnisliste der Rolle, ohne eigene Nutzer-ID ggf. nur lesen). */
  readonly rechte: ViewerRechte;
  readonly nutzerId: number | null;
  /** Vorschläge im Feld Kategorie (aktive Projektkategorien, sonst die feste Liste). */
  readonly kategorien: readonly string[];
  readonly poiPersistenz: PoiPersistenz;
  /** Befund-Fäden des Projekts (Schalter an), sonst null: alles wie ohne Fäden. */
  readonly faeden: { pruefen: boolean; projectId: string; staende: FadenStaende; kategorien: readonly string[]; standGeaendert(serverId: string, version: number): void } | null;
  messungenGeaendert(): void;
  /** Deep-Link-Filter (hashGeometrie.ts): nur Messungen, die nicht an eine Servermessung gebunden sind. */
  messungImLink(m: Measurement): boolean;
  /** true = „Alle löschen“ wirkt am Server für alle Projektbeteiligten (Bearbeiter/Admin). */
  messungenGemeinsam(): boolean;
  verdrahte(o: { pois: PoiPanel; pano: Panorama; messungenNeuZeichnen: () => void }): void;
}

/** Derselbe Schlüssel wie in pois.ts (PoiPersistence im Einzelmodus). */
export const poiSchluessel = (pid: string): string => `scanview:pois:${pid}`;
export const uebernommenSchluessel = (pid: string): string => `${poiSchluessel(pid)}:uebernommen`;

/** Kurzer Fingerabdruck eines Textes (FNV-1a 32 bit + Länge) — Marke „dieser Stand ist übernommen“. */
export function fingerabdruck(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${s.length}:${h.toString(16)}`;
}

/** Gespeicherter Browser-Stand, wenn er übernommen werden kann (nicht leer, genau dieser Stand noch nicht
 *  übernommen — neue Browser-POIs werden also wieder angeboten). */
export function lokaleUebernahme(speicher: PoiStorage | null, pid: string): string | null {
  if (!speicher) return null;
  try {
    const roh = speicher.getItem(poiSchluessel(pid));
    if (!roh) return null;
    if (speicher.getItem(uebernommenSchluessel(pid)) === fingerabdruck(roh)) return null;
    const d = JSON.parse(roh) as { pois?: unknown };
    return Array.isArray(d.pois) && d.pois.length > 0 ? roh : null;
  } catch {
    return null;
  }
}

function sichererSpeicher(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Übernahme ausführen: importieren, sofort abgleichen, Marke nur setzen, wenn der Server alles bestätigt hat.
 *  'nichts' = nichts importiert, 'offen' = noch nicht (vollständig) gespeichert — der Knopf bleibt. */
interface UebernahmeZiel {
  roh: string;
  pois: Pick<ServerPoiPersistenz, 'abgleich' | 'offen'>;
  speicher: PoiStorage | null;
  pid: string;
}

/** Marke setzen, wenn der Server alles bestätigt hat (nichts offen, nichts abgelehnt). true = gesetzt. */
export function markeWennFertig(o: UebernahmeZiel): boolean {
  if (o.pois.offen() > 0 || o.pois.abgleich.abgelehnteAnzahl() > 0) return false;
  try {
    o.speicher?.setItem(uebernommenSchluessel(o.pid), fingerabdruck(o.roh));
  } catch {
    // Speicher voll/gesperrt: der Knopf erscheint beim nächsten Laden erneut; ein erneutes Übernehmen
    // importiert nur POIs, die es als eigene noch nicht gibt, und überschreibt nichts.
  }
  return true;
}

/** Nur POIs des Browser-Stands, die es als eigene noch nicht gibt (ein zweiter Klick nach
 *  „offen“ darf inzwischen bearbeitete oder am Server gebundene POIs nicht mit dem alten Stand ersetzen —
 *  PoiStore.importBrowserPois ersetzt gleiche IDs). null = nichts mehr zu importieren. */
function nochNichtDa(roh: string, vorhanden: (id: string) => boolean): string | null {
  const d = JSON.parse(roh) as { pois?: unknown };
  if (!Array.isArray(d.pois)) return roh;
  const neu = d.pois.filter((p: unknown) => {
    const id = p !== null && typeof p === 'object' ? (p as { id?: unknown }).id : undefined;
    return typeof id !== 'string' || !vorhanden(id);
  });
  return neu.length ? JSON.stringify({ ...d, pois: neu }) : null;
}

export async function uebernimm(
  o: UebernahmeZiel & { importText: (text: string) => number; vorhanden: (id: string) => boolean },
): Promise<'fertig' | 'offen' | 'nichts'> {
  const text = nochNichtDa(o.roh, o.vorhanden);
  if (text !== null && o.importText(text) <= 0) return 'nichts';
  await o.pois.abgleich.sofort();
  return markeWennFertig(o) ? 'fertig' : 'offen';
}

function zeigeUebernahme(
  panel: PoiPanel,
  pid: string,
  speicher: Storage | null,
  pois: ServerPoiPersistenz,
  notify: (text: string) => void,
): void {
  const roh = lokaleUebernahme(speicher, pid);
  const root = document.getElementById('poi-panel');
  if (!roh || !root || !pois.serverStandGeladen()) return;
  const knopf = document.createElement('button');
  knopf.type = 'button';
  knopf.textContent = 'Browser-POIs in das Projekt übernehmen';
  knopf.title = 'POIs, die Sie früher nur in diesem Browser angelegt haben, einmalig für alle Projektbeteiligten speichern';
  // Nach „offen“ läuft der Abgleich weiter (Wiederholung); sobald alles bestätigt ist, Marke setzen und Knopf weg.
  let wache: ReturnType<typeof setInterval> | null = null;
  const fertig = (): void => {
    if (wache !== null) clearInterval(wache);
    wache = null;
    knopf.remove();
    notify('Browser-POIs in das Projekt übernommen.');
  };
  knopf.addEventListener('click', () => {
    knopf.disabled = true;
    const vorhanden = (id: string): boolean => panel.store.get(id)?.source === 'browser';
    void uebernimm({ roh, importText: (t) => panel.importText(t), vorhanden, pois, speicher, pid }).then((r) => {
      if (r === 'fertig') {
        fertig();
        return;
      }
      knopf.disabled = false;
      if (r !== 'offen') return;
      notify('Übernahme noch nicht vollständig gespeichert – sie wird automatisch fortgesetzt, bitte die Seite offen lassen.');
      wache ??= setInterval(() => {
        if (markeWennFertig({ roh, pois, speicher, pid })) fertig();
      }, 5_000);
    });
  });
  root.append(knopf);
}

/** Hinweis, wenn die Rolle nur eigene POIs ändern darf, der Server aber die eigene Nutzer-ID nicht nennt. */
export const OHNE_NUTZER_ID = 'Rückmeldungen nicht möglich – bitte neu anmelden.';

/** Browser-Umgebung (Ereignisse, localStorage); in Tests austauschbar. */
export interface Umgebung {
  fenster: Pick<EventTarget, 'addEventListener'>;
  dok: Pick<EventTarget, 'addEventListener'> & { readonly visibilityState: string };
  speicher: Storage | null;
}

/** Rechte der Rolle; darf sie nur eigene POIs ändern, braucht sie die eigene Nutzer-ID — ohne sie
 *  (alte Sitzung oder älterer Server) wäre nichts als eigen erkennbar, also sicherer Rückfall: nur lesen.
 *  Rückfall und Hinweis folgen aus derselben Bedingung, damit keine Rolle stumm auf Lesen fällt. */
export function wirksameRechte(rolle: string, nutzerId: number | null): { rechte: ViewerRechte; hinweis: string | null } {
  const r = rechte(rolle);
  const ohneId = r.poisAnlegen && !r.alleAendern && nutzerId === null;
  return ohneId ? { rechte: { ...NUR_LESEN }, hinweis: OHNE_NUTZER_ID } : { rechte: r, hinweis: null };
}

export async function verbindePlattform(o: {
  projectId: string;
  store: MeasureStore;
  notify: (text: string) => void;
  /** Eigene Konto-ID aus der Sitzung (platform.ts), null = unbekannt. */
  nutzerId: number | null;
  fetchFn?: typeof fetch;
  umgebung?: Umgebung;
}): Promise<PlattformViewer | null> {
  let info: ProjektInfo;
  try {
    info = await anfrage<ProjektInfo>('GET', projektPfad(o.projectId), undefined, o.fetchFn, false, START_MS);
  } catch (e) {
    o.notify(`Projekt auf dem Server nicht abrufbar (${e instanceof Error ? e.message : String(e)}) – POIs und Messungen nur in diesem Browser.`);
    return null;
  }
  const { rechte: r, hinweis: rechteHinweis } = wirksameRechte(info.rolle, o.nutzerId);
  if (rechteHinweis !== null) o.notify(rechteHinweis);
  const { fenster, dok, speicher } = o.umgebung ?? { fenster: window, dok: document, speicher: sichererSpeicher() };
  const nutzerId = o.nutzerId;
  const pois = new ServerPoiPersistenz({
    projectId: o.projectId,
    schreibbar: r.poisAnlegen,
    vorschlaege: r.alleAendern,
    eigen: r.alleAendern ? undefined : (e) => nutzerId !== null && e.erstelltVon === nutzerId,
    lokal: new PoiPersistence(speicher, poiSchluessel(o.projectId)),
    melde: o.notify,
    fetchFn: o.fetchFn,
  });
  const messungen = new MessungSync({ store: o.store, projectId: o.projectId, schreibbar: r.messungenTeilen, vorschlaege: r.alleAendern, melde: o.notify, fetchFn: o.fetchFn });
  const hinweis = await messungen.laden();
  if (hinweis) o.notify(hinweis);
  // Nutzungszähler (statistik.ts). Gezählt wird nur, was wächst — Löschen zählt nicht.
  const zaehler = new StatistikZaehler(statistikSender(o.projectId, o.fetchFn));
  zaehler.zaehle('oeffnen');
  leerenBeimVerlassen(zaehler, fenster, dok);
  let messAnzahl = o.store.list().length;
  let poiAnzahl = 0;
  const zaehlendePois: PoiPersistenz = {
    load: async (s) => {
      const r = await pois.load(s);
      poiAnzahl = s.list().length;
      return r;
    },
    save: (s, revision) => {
      const n = s.list().length;
      if (n > poiAnzahl) zaehler.zaehle('poi');
      poiAnzahl = n;
      return pois.save(s, revision);
    },
    // Ohne Durchreichen wären Server-ID, Urheber und Bestätigung im Plattformmodus unbekannt.
    pruefen: r.alleAendern ? (l,a) => pois.pruefen(l,a) : undefined,
    meta: (l) => pois.meta(l),
    lokalId: (sid) => pois.lokalId(sid),
    bestaetigt: (l) => pois.bestaetigt(l),
    onErgebnis: (cb) => pois.onErgebnis(cb),
  };
  // Seite verborgen oder verlassen: Sammelfenster überspringen, mit keepalive senden (überlebt das Entladen).
  const flush = (): void => {
    void pois.flush();
    void messungen.flush();
  };
  fenster.addEventListener('pagehide', flush);
  dok.addEventListener('visibilitychange', () => {
    if (dok.visibilityState === 'hidden') flush();
  });
  fenster.addEventListener('beforeunload', (ev) => {
    if (pois.offen() + messungen.offen() === 0) return;
    ev.preventDefault();
    // Ältere Browser zeigen die Rückfrage nur mit gesetztem returnValue.
    (ev as BeforeUnloadEvent).returnValue = '';
  });
  return {
    messungPruefen: r.alleAendern ? (id,a) => messungen.pruefen(id,a) : undefined,
    rolle: info.rolle,
    rechte: { ...r },
    nutzerId,
    kategorien: kategorieVorschlaege(info.kategorien),
    poiPersistenz: zaehlendePois,
    faeden: info.befund_faeden === true
      ? {
          pruefen: r.alleAendern,
          projectId: o.projectId,
          staende: new FadenStaende((f) => befundeLesen(o.projectId, f)),
          kategorien: kategorienAus(info.kategorien),
          standGeaendert: (sid, version) => pois.standGeaendert(sid, version),
        }
      : null,
    messungImLink: (m) => messungen.imLink(m),
    messungenGemeinsam: () => messungen.gemeinsam(),
    messungenGeaendert: () => {
      const n = o.store.list().length;
      if (n > messAnzahl) zaehler.zaehle('messung');
      messAnzahl = n;
      messungen.geaendert();
    },
    verdrahte: ({ pois: panel, pano, messungenNeuZeichnen }) => {
      pois.setNeuZeichnen(() => panel.refresh());
      messungen.setNeuZeichnen(messungenNeuZeichnen);
      if (r.uebernahme) zeigeUebernahme(panel, o.projectId, speicher, pois, o.notify);
      let station = pano.state?.station ?? null;
      pano.onChange(() => {
        const jetzt = pano.state?.station ?? null;
        if (jetzt !== null && jetzt !== station) zaehler.zaehle('standpunkt');
        station = jetzt;
      });
    },
  };
}
