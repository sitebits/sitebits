// Verlauf: Ereignisse des Bearbeitungsprotokolls lesbar machen — Art, Gegenstand,
// Vorher/Nachher je Feld. Reine Logik (Vitest); die Seite selbst baut verlauf.ts.
import { artText as eintragArtText, fristText, statusText } from '../fadenModell';
import { rolleText } from './rechte';

export interface Ereignis {
  id: number;
  zeit: number;
  nutzerId: number | null;
  nutzer: string | null;
  kanal: 'web' | 'cli' | 'api';
  projekt: string | null;
  art: string;
  objekt: string | null;
  vorher: unknown;
  nachher: unknown;
  /** Name des API-Tokens bei kanal 'api' (nie der Wert). */
  token?: string | null;
}

export interface Seite {
  ereignisse: Ereignis[];
  weiter: number | null;
}

export interface Unterschied {
  feld: string;
  vorher: string;
  nachher: string;
}

const ARTEN: Record<string, string> = {
  'meta.geaendert': 'Titel/Beschreibung geändert',
  'poi.angelegt': 'POI angelegt',
  'poi.geaendert': 'POI geändert',
  'poi.geloescht': 'POI gelöscht',
  'poi.wiederhergestellt': 'POI wiederhergestellt',
  'messung.angelegt': 'Messung angelegt',
  'messung.geaendert': 'Messung geändert',
  'messung.geloescht': 'Messung gelöscht',
  'messung.wiederhergestellt': 'Messung wiederhergestellt',
  'recht.vergeben': 'Recht vergeben',
  'recht.entzogen': 'Recht entzogen',
  'konto.angelegt': 'Konto angelegt',
  'konto.zurueckgesetzt': 'Zugang zurückgesetzt',
  'konto.gesperrt': 'Konto gesperrt',
  'konto.entsperrt': 'Konto entsperrt',
  'konto.abgemeldet': 'Überall abgemeldet',
  'einladung.angelegt': 'Einladung angelegt',
  'einladung.widerrufen': 'Einladung widerrufen',
  'gewerk.angelegt': 'Gewerk angelegt',
  'gewerk.geaendert': 'Gewerk geändert',
  'gewerk.geloescht': 'Gewerk gelöscht',
  'beteiligter.gewerk': 'Gewerk zugeordnet',
  'kategorie.angelegt': 'Kategorie angelegt',
  'kategorie.geaendert': 'Kategorie geändert',
  'einstellung.geaendert': 'Schalter geändert',
  'projekt.eingerichtet': 'Projekt eingerichtet',
  'eintrag.angelegt': 'Eintrag im Befund-Faden',
  'eintrag.zurueckgezogen': 'Eintrag zurückgezogen',
  'poi.status': 'Status geändert',
  'poi.zustaendig': 'Zuständigkeit geändert',
  'poi.frist': 'Frist geändert',
  'teilen.angelegt': 'Nur-Lese-Link angelegt',
  'teilen.widerrufen': 'Nur-Lese-Link zurückgezogen',
};

const VORSCHLAG_ARTEN: Record<string,string> = {
  vorgeschlagen: 'vorgeschlagen (KI)', vorschlag_geaendert: 'Vorschlag geändert', vorschlag_geloescht: 'Vorschlag gelöscht',
  loeschen_vorgeschlagen: 'Löschen vorgeschlagen', angenommen: 'Vorschlag angenommen', verworfen: 'Vorschlag verworfen', loeschen_verworfen: 'Löschvorschlag verworfen',
};
for (const [art,wort] of Object.entries(VORSCHLAG_ARTEN)) {
  ARTEN[`poi.${art}`] = `POI ${wort}`; ARTEN[`messung.${art}`] = `Messung ${wort}`;
}
ARTEN['eintrag.vorgeschlagen'] = 'Änderung im Befund-Faden vorgeschlagen (KI)';
ARTEN['eintrag.vorschlag_verworfen'] = 'Vorschlag im Befund-Faden verworfen';

const MESSART: Record<string, string> = { distance: 'Strecke', polyline: 'Polylinie', area: 'Fläche', height: 'Höhe' };

const FELD: Record<string, string> = {
  title: 'Titel',
  category: 'Kategorie',
  note: 'Notiz',
  position: 'Position',
  station: 'Standpunkt',
  titel: 'Titel',
  beschreibung: 'Beschreibung',
  kind: 'Art',
  points: 'Eckpunkte',
  areaM2: 'Fläche [m²]',
  planarityM: 'Ebenheit [m]',
  rechte: 'Rechte',
  admin: 'Admin',
  zweck: 'Zweck',
  sitzungenBeendet: 'Beendete Sitzungen',
  name: 'Name',
  kurz: 'Kurzzeichen',
  bauherrenseite: 'Bauherrenseite',
  aktiv: 'Aktiv',
  reihenfolge: 'Position',
  rueckmeldungen: 'Rückmeldungen erlaubt',
  exportBearbeiter: 'Export für Bearbeiter',
  vorlage: 'Vorlage',
  kategorien: 'Kategorien',
  gewerk: 'Gewerk',
  ohneGewerk: 'Ohne Gewerk danach',
  befundFaeden: 'Befund-Fäden',
  nurLeseLinks: 'Nur-Lese-Links',
  gewerk_id: 'Gewerk',
  nutzer_id: 'Person',
  art: 'Art',
  text: 'Text',
  wert: 'Wert',
  intern: 'Intern',
};

/** Technische Felder eines Faden-Eintrags im Protokoll (Kennungen, Zeiten): im Verlauf ohne Aussage. */
const EINTRAG_TECHNIK = new Set(['schluessel', 'poi', 'erstellt_von', 'erstellt_am', 'kanal', 'zurueckgezogen_am']);

const VORLAGE: Record<string, string> = { bestandsaufnahme: 'Bestandsaufnahme', bauprojekt: 'Bauprojekt' };

const ZWECK: Record<string, string> = { neu: 'Neuer Zugang', zuruecksetzen: 'Zugang zurückgesetzt' };

const zahl = (x: number): string => new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(x);

const alsObjekt = (x: unknown): Record<string, unknown> | null =>
  x !== null && typeof x === 'object' && !Array.isArray(x) ? (x as Record<string, unknown>) : null;

export const artText = (art: string): string => ARTEN[art] ?? art;

export function wertText(feld: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '–';
  if (feld === 'points' && Array.isArray(v)) return `${v.length} Punkte`;
  if (feld === 'kind' && typeof v === 'string') return MESSART[v] ?? v;
  if (feld === 'vorlage' && typeof v === 'string') return VORLAGE[v] ?? v;
  if (Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === 'string')) return v.join(', ');
  if (Array.isArray(v) && v.every((x) => typeof x === 'number')) return v.map((x) => zahl(x as number)).join(' / ');
  if (typeof v === 'number') return zahl(v);
  if (typeof v === 'boolean') return v ? 'ja' : 'nein';
  // Rechte einer Einladung: Projekt → Rolle, mit Anzeigenamen statt der gespeicherten Werte.
  if (feld === 'rechte') {
    const r = alsObjekt(v);
    if (r) return Object.entries(r).map(([pid, rolle]) => `${pid}: ${typeof rolle === 'string' ? rolleText(rolle) : '–'}`).join(', ') || '–';
  }
  if (feld === 'zweck' && typeof v === 'string') return ZWECK[v] ?? v;
  if (feld === 'art' && typeof v === 'string') return eintragArtText(v);
  const link = alsObjekt(v);
  if (feld === 'wert' && link && typeof link.url === 'string' && typeof link.titel === 'string') return `${link.titel} – ${link.url}`;
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

/** Rechte-Ereignisse tragen die Rolle als einfachen Wert; sie erscheint mit ihrem Anzeigenamen. */
const rolleWert = (v: unknown): string => (typeof v === 'string' && v !== '' ? rolleText(v) : wertText('', v));

export function unterschiede(vorher: unknown, nachher: unknown, art = ''): Unterschied[] {
  const alt = alsObjekt(vorher);
  const neu = alsObjekt(nachher);
  if (alt === null && neu === null) {
    if (vorher === nachher) return [];
    const text = art.startsWith('recht.') ? rolleWert
      : art === 'poi.status' ? (v: unknown) => (typeof v === 'string' ? statusText(v) : wertText('', v))
        : art === 'poi.frist' ? (v: unknown) => (typeof v === 'string' ? fristText(v) : wertText('', v))
          : (v: unknown) => wertText('', v);
    return [{ feld: 'Wert', vorher: text(vorher), nachher: text(nachher) }];
  }
  const felder = [...new Set([...Object.keys(alt ?? {}), ...Object.keys(neu ?? {})])]
    .filter((f) => !(art.startsWith('eintrag.') && EINTRAG_TECHNIK.has(f)));
  return felder
    .filter((f) => JSON.stringify(alt?.[f] ?? null) !== JSON.stringify(neu?.[f] ?? null))
    .map((f) => ({ feld: FELD[f] ?? f, vorher: wertText(f, alt?.[f]), nachher: wertText(f, neu?.[f]) }));
}

/** Ereignisarten, deren Gegenstand ein Konto ist (Objekt = Konto-ID). */
const KONTO_BEZUG = ['recht.', 'konto.', 'beteiligter.'];

export function bezeichnung(e: Ereignis, namen?: ReadonlyMap<number, string>): string {
  const d = alsObjekt(e.nachher) ?? alsObjekt(e.vorher);
  if (e.art.startsWith('poi.') && typeof d?.title === 'string') return `„${d.title}“`;
  if (e.art.startsWith('messung.') && typeof d?.kind === 'string') return MESSART[d.kind] ?? d.kind;
  if (e.art.startsWith('meta.')) return '';
  // Faden-Einträge: Objekt „<POI>/<Schlüssel>“ — der Schlüssel ist keine Angabe für Menschen.
  if (e.art.startsWith('eintrag.') && e.objekt !== null) return `Befund ${e.objekt.split('/', 1)[0]}`;
  if ((e.art.startsWith('gewerk.') || e.art.startsWith('kategorie.')) && typeof d?.name === 'string') return `„${d.name}“`;
  if (KONTO_BEZUG.some((p) => e.art.startsWith(p)) && e.objekt !== null && /^\d+$/.test(e.objekt)) {
    return namen?.get(Number(e.objekt)) ?? `Konto ${e.objekt}`;
  }
  return e.objekt ?? '';
}

export const werText = (e: Ereignis): string => {
  const wer = e.nutzer ?? (e.kanal === 'cli' ? 'Kommandozeile' : 'unbekannt');
  // Änderungen per API-Token erkennbar machen, mit Token-Namen (ADR 0008).
  if (e.kanal !== 'api') return wer;
  return e.token ? `${wer} (per API, Token „${e.token}“)` : `${wer} (per API)`;
};

export const zeitText = (sekunden: number): string =>
  new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin' }).format(new Date(sekunden * 1000));
