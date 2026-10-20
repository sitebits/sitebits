import { parsePointSource } from './pointSources';
import type { PointSource } from './pointSources';
// POIs: Titel, Kategorie, Notiz, Weltposition (Float64) — reine Logik (Vitest).
// Quellen: 'package' (mitgeliefertes <paket>/pois.json, schreibgeschützt) und 'browser' (hier angelegt,
// im localStorage des Betrachters, per Export/Import geteilt). Eingelesene Dateien sind fremde Daten:
// strikt geprüft (Typen, Längen, Anzahl, Projekt-ID); angezeigt werden Texte nur per textContent (pois.ts).
import type { Vec3 } from './manifest';
import { STATION_ID_RE } from './stations';

export const POI_CATEGORIES = ['Raum', 'Anlage', 'Zugang', 'Mangel', 'Sonstiges'] as const;
/** Kategorie der Rolle Rückmelder (vom Server erzwungen). Bewusst nicht unter den Vorschlägen für Bearbeiter. */
export const RUECKMELDUNG = 'R\u00fcckmeldung';
/** Kategorien werden wie am Server in der zusammengesetzten Unicode-Form (NFC) gespeichert und verglichen —
 *  eine zerlegte Schreibweise (ü als u + Trema) gilt als gleich. */
export const kategorieNormal = (k: string): string => k.normalize('NFC').trim();
export const istRueckmeldung = (k: string): boolean => kategorieNormal(k) === RUECKMELDUNG;
/** CSS-Klassen eines Markers: Form nach Art (Rückmeldung = Sprechblase, sonst Befund = Raute), dazu die Auswahl. */
export const markerKlassen = (kategorie: string, gewaehlt: boolean): string =>
  ['scanview-poi-label', istRueckmeldung(kategorie) ? 'sv-marker-rueckmeldung' : 'sv-marker-befund', ...(gewaehlt ? ['sv-gewaehlt'] : [])].join(' ');
export const POI_LIMITS = { title: 200, category: 60, note: 2000, count: 5000 } as const;

/** Vorschläge im Feld Kategorie: im Plattformmodus die aktiven Kategorien des Projekts in ihrer
 *  Reihenfolge, sonst (Einzelmodus, älterer Server, unlesbare Antwort) die feste Liste. „Rückmeldung“
 *  ist nie dabei. Die Antwort des Servers ist fremde Eingabe: nur Texte bis zur Feldgrenze. */
export function kategorieVorschlaege(roh: unknown): string[] {
  if (!Array.isArray(roh)) return [...POI_CATEGORIES];
  const aus: string[] = [];
  for (const k of roh) {
    if (k === null || typeof k !== 'object') continue;
    const { name, aktiv } = k as { name?: unknown; aktiv?: unknown };
    if (typeof name !== 'string' || aktiv !== true) continue;
    const n = kategorieNormal(name);
    if (n === '' || n.length > POI_LIMITS.category || istRueckmeldung(n) || aus.includes(n)) continue;
    aus.push(n);
  }
  return aus;
}
export const POI_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

import { exportGueltig } from './vorschlagApi';
import type { VorschlagAnzeige } from './vorschlagApi';
export interface Poi {
  vorschlag?: VorschlagAnzeige;
  positionSource?: PointSource|null;
  id: string;
  title: string;
  category: string;
  note: string;
  /** Weltkoordinaten (Float64), Meter. */
  position: Vec3;
  /** Standpunkt, an dem der POI angelegt wurde (Sprungziel), sonst null. */
  station: string | null;
  source: 'package' | 'browser';
}

export type PoiDraft = Pick<Poi, 'title' | 'category' | 'note' | 'position' | 'station' | 'positionSource'>;

export interface PoiFile {
  schemaVersion: 1;
  projectId: string;
  revision: number;
  frame: 'world';
  unit: 'm';
  pois: Omit<Poi, 'source'>[];
}

export class PoiError extends Error {}

const LABEL = { title: 'Titel', category: 'Kategorie', note: 'Notiz' } as const;

function checkText(value: unknown, key: keyof typeof LABEL, where: string, required: boolean): string {
  if (value === undefined && !required) return '';
  if (typeof value !== 'string') throw new PoiError(`${where}: ${LABEL[key]} muss Text sein.`);
  const t = key === 'category' ? kategorieNormal(value) : value.trim();
  if (required && t === '') throw new PoiError(`${where}: ${LABEL[key]} fehlt.`);
  if (t.length > POI_LIMITS[key]) throw new PoiError(`${where}: ${LABEL[key]} länger als ${POI_LIMITS[key]} Zeichen.`);
  return t;
}

function checkPosition(value: unknown, where: string): Vec3 {
  if (!Array.isArray(value) || value.length !== 3 || !value.every((n) => typeof n === 'number' && Number.isFinite(n)))
    throw new PoiError(`${where}: position braucht drei endliche Zahlen (Welt, m).`);
  return [value[0] as number, value[1] as number, value[2] as number];
}

function checkStation(value: unknown, where: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || !STATION_ID_RE.test(value)) throw new PoiError(`${where}: ungültiger Standpunkt.`);
  return value;
}

export function parsePoiFile(json: unknown, projectId: string, source: Poi['source']): Poi[] {
  const d = json as Record<string, unknown> | null;
  if (!d || typeof d !== 'object') throw new PoiError('pois.json ist kein Objekt.');
  if (d.schemaVersion !== 1)
    throw new PoiError(`pois.json: Schema-Version ${String(d.schemaVersion)} wird nicht unterstützt (erwartet 1).`);
  if (d.projectId !== projectId)
    throw new PoiError(`pois.json gehört zu Projekt „${String(d.projectId)}“, geladen ist „${projectId}“.`);
  if (!Array.isArray(d.pois)) throw new PoiError('pois.json: Liste "pois" fehlt.');
  if (d.pois.length > POI_LIMITS.count) throw new PoiError(`pois.json: mehr als ${POI_LIMITS.count} POIs.`);
  const seen = new Set<string>();
  return d.pois.map((raw: unknown, i: number): Poi => {
    const p = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const where = `pois.json, Eintrag ${i + 1}`;
    const id = p.id;
    if (typeof id !== 'string' || !POI_ID_RE.test(id)) throw new PoiError(`${where}: ungültige ID.`);
    if (seen.has(id)) throw new PoiError(`${where}: ID ${id} doppelt.`);
    seen.add(id);
    return {
      id,
      title: checkText(p.title, 'title', where, true),
      category: checkText(p.category, 'category', where, false),
      note: checkText(p.note, 'note', where, false),
      position: checkPosition(p.position, where),
      station: checkStation(p.station, where),
      ...(p.positionSource !== undefined ? {positionSource:parsePointSource(p.positionSource)} : {}),
      source,
    };
  });
}

const sameContent = (a: Poi, b: Poi): boolean =>
  a.title === b.title &&
  a.category === b.category &&
  a.note === b.note &&
  a.station === b.station &&
  JSON.stringify(a.positionSource ?? null) === JSON.stringify(b.positionSource ?? null) &&
  a.position.every((x, i) => x === b.position[i]);

/** Für die Suche: klein, ß → ss, ohne diakritische Zeichen (ü → u). */
const fold = (s: string): string =>
  s.toLocaleLowerCase('de-DE').replace(/ß/g, 'ss').normalize('NFD').replace(/\p{M}/gu, '');

export class PoiStore {
  private pkg: Poi[] = [];
  private own: Poi[] = [];
  private readonly newId: () => string;

  constructor(
    readonly projectId: string,
    newId?: () => string,
  ) {
    let n = 0;
    this.newId = newId ?? ((): string => `P-${Date.now().toString(36)}-${(++n).toString(36)}`);
  }

  /** Setzt die Paket-POIs. Eigene POIs mit gleicher ID bleiben erhalten: bei gleichem Inhalt entfällt
   *  das Duplikat, bei anderem Inhalt bekommt der eigene POI eine neue ID (kein stiller Verlust, I1). */
  setPackagePois(list: readonly Poi[]): number {
    this.pkg = list.map((p) => ({ ...p, source: 'package' }));
    const pkgById = new Map(this.pkg.map((p) => [p.id, p]));
    let renamed = 0;
    const kept: Poi[] = [];
    for (const p of this.own) {
      const clash = pkgById.get(p.id);
      if (!clash) kept.push(p);
      else if (!sameContent(p, clash)) {
        kept.push({ ...p, id: this.freshId(kept) });
        renamed++;
      }
    }
    this.own = kept;
    return renamed;
  }

  /** Neuer eigener POI. `id` (z. B. beim Fortsetzen eines Entwurfs, damit ein schon angekommener POI kein
   *  Doppel ergibt) muss gültig und noch frei sein, sonst PoiError; ohne `id` wird eine neue vergeben. */
  add(d: PoiDraft, id?: string): Poi {
    const where = 'POI';
    if (this.own.length >= POI_LIMITS.count)
      throw new PoiError(`Höchstens ${POI_LIMITS.count} eigene POIs – bitte erst welche löschen.`);
    if (id !== undefined && !POI_ID_RE.test(id)) throw new PoiError(`${where}: ungültige ID.`);
    if (id !== undefined && this.get(id)) throw new PoiError(`${where}: ID ${id} ist schon vergeben.`);
    const p: Poi = {
      id: id ?? this.freshId(),
      title: checkText(d.title, 'title', where, true),
      category: checkText(d.category, 'category', where, false),
      note: checkText(d.note, 'note', where, false),
      position: checkPosition(d.position, where),
      station: checkStation(d.station, where),
      ...(d.positionSource !== undefined ? {positionSource:parsePointSource(d.positionSource)} : {}),
      source: 'browser',
    };
    this.own.push(p);
    return p;
  }

  /** Freie ID für einen POI, der erst noch angelegt wird (Formular offen): steht schon im Entwurf, bevor der
   *  POI gespeichert und gesendet wird. Legt nichts an; `add(d, id)` übernimmt sie, solange sie frei ist. */
  reserviereId(): string {
    return this.freshId();
  }

  /** ID beim Absenden: Eine neu reservierte ID, die inzwischen belegt ist (z. B. ein anderer Client hat über den
   *  Server-Abgleich dieselbe lokale ID geliefert), wird ersetzt. Eine fortgesetzte ID bleibt — ist sie belegt,
   *  wurde der Befund schon gespeichert, und `add` meldet das. */
  idZumAnlegen(id: string, fortgesetzt: boolean): string {
    return fortgesetzt || !this.get(id) ? id : this.freshId();
  }

  update(id: string, patch: Partial<Pick<Poi, 'title' | 'category' | 'note'>>): Poi {
    const p = this.writable(id);
    if (patch.title !== undefined) p.title = checkText(patch.title, 'title', 'POI', true);
    if (patch.category !== undefined) p.category = checkText(patch.category, 'category', 'POI', false);
    if (patch.note !== undefined) p.note = checkText(patch.note, 'note', 'POI', false);
    return p;
  }

  remove(id: string): void {
    this.writable(id);
    this.own = this.own.filter((p) => p.id !== id);
  }

  /** Übernimmt POIs als eigene (Import, localStorage), alles oder nichts: gleiche eigene ID ersetzt;
   *  gleiche Paket-ID mit gleichem Inhalt wird übersprungen (Export „alle“ enthält die Paket-POIs), mit
   *  anderem Inhalt unter neuer ID behalten (I1). Mehr als POI_LIMITS.count eigene POIs → PoiError. */
  importBrowserPois(list: readonly Poi[]): { added: number; renamed: number } {
    const pkgById = new Map(this.pkg.map((p) => [p.id, p]));
    const ownIds = new Set(this.own.map((p) => p.id));
    let fresh = 0;
    for (const p of list) {
      const clash = pkgById.get(p.id);
      if (clash ? !sameContent(p, clash) : !ownIds.has(p.id)) fresh++;
    }
    if (this.own.length + fresh > POI_LIMITS.count)
      throw new PoiError(`Import ergäbe mehr als ${POI_LIMITS.count} eigene POIs – nichts übernommen.`);
    let added = 0;
    let renamed = 0;
    for (const p of list) {
      const clash = pkgById.get(p.id);
      if (clash && sameContent(p, clash)) continue;
      const q: Poi = { ...p, id: clash ? this.freshId() : p.id, source: 'browser' };
      if (clash) renamed++;
      const i = this.own.findIndex((x) => x.id === q.id);
      if (i >= 0) this.own[i] = q;
      else this.own.push(q);
      added++;
    }
    return { added, renamed };
  }

  get(id: string): Poi | undefined {
    return this.pkg.find((p) => p.id === id) ?? this.own.find((p) => p.id === id);
  }

  list(): Poi[] {
    return [...this.pkg, ...this.own].sort((a, b) => a.title.localeCompare(b.title, 'de'));
  }

  search(q: string): Poi[] {
    const tokens = fold(q).split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return this.list();
    return this.list().filter((p) => {
      const hay = fold(`${p.title} ${p.category} ${p.note}`);
      return tokens.every((t) => hay.includes(t));
    });
  }

  toFile(revision: number, which: 'all' | 'browser' = 'all'): PoiFile {
    const src = which === 'all' ? this.list() : this.own;
    return {
      schemaVersion: 1,
      projectId: this.projectId,
      revision,
      frame: 'world',
      unit: 'm',
      pois: src.filter(exportGueltig).map(({ source: _source, vorschlag: _vorschlag, ...poi }) => { void _source; void _vorschlag; return poi; }),
    };
  }

  /** Neue ID, die weder bei Paket- noch bei eigenen POIs (bzw. `own`) vorkommt. */
  private freshId(own: readonly Poi[] = this.own): string {
    for (;;) {
      const id = this.newId();
      if (!this.pkg.some((p) => p.id === id) && !own.some((p) => p.id === id) && !this.own.some((p) => p.id === id)) return id;
    }
  }

  private writable(id: string): Poi {
    if (this.pkg.some((p) => p.id === id)) throw new PoiError('POI aus dem Paket ist schreibgeschützt.');
    const p = this.own.find((x) => x.id === id);
    if (!p) throw new PoiError(`POI ${id} nicht gefunden.`);
    return p;
  }
}

/** Minimaler Ausschnitt von `Storage` (localStorage), damit Laden/Speichern ohne Browser testbar ist. */
export type PoiStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** Bewahrt die eigenen POIs im Browser (I1): ein unlesbarer gespeicherter Stand wird vor dem nächsten
 *  Speichern unter `<key>:defekt[-n]` gesichert (vorhandene Sicherungen bleiben), scheitert das, bleibt
 *  Speichern in dieser Sitzung gesperrt. Fehler kommen als Text zurück — der Aufrufer zeigt sie an. */
export class PoiPersistence {
  private blocked = false;

  constructor(
    private readonly storage: PoiStorage | null,
    readonly key: string,
  ) {}

  load(store: PoiStore): { loaded: number; renamed: number; warning: string | null } {
    if (!this.storage) return { loaded: 0, renamed: 0, warning: null };
    let raw: string | null;
    try {
      raw = this.storage.getItem(this.key);
    } catch (e) {
      this.blocked = true;
      return { loaded: 0, renamed: 0, warning: `Gespeicherte POIs nicht lesbar (${errText(e)}) – Änderungen werden nicht gespeichert.` };
    }
    if (!raw) return { loaded: 0, renamed: 0, warning: null };
    try {
      const r = store.importBrowserPois(parsePoiFile(JSON.parse(raw), store.projectId, 'browser'));
      return { loaded: r.added, renamed: r.renamed, warning: null };
    } catch (e) {
      const why = errText(e);
      const backup = this.backup(raw);
      if (backup) return { loaded: 0, renamed: 0, warning: `Gespeicherte POIs nicht lesbar (${why}) – Rohdaten gesichert unter „${backup}“.` };
      this.blocked = true;
      return {
        loaded: 0,
        renamed: 0,
        warning: `Gespeicherte POIs nicht lesbar (${why}) – Sicherung fehlgeschlagen, Änderungen werden nicht gespeichert.`,
      };
    }
  }

  /** null = gespeichert, sonst Hinweistext. */
  save(store: PoiStore, revision: number): string | null {
    if (this.blocked) return 'POIs nicht im Browser gespeichert: Speichern gesperrt, gespeicherter Stand war unlesbar.';
    if (!this.storage) return 'POIs nicht im Browser gespeichert: Browser-Speicher nicht verfügbar.';
    try {
      this.storage.setItem(this.key, JSON.stringify(store.toFile(revision, 'browser')));
      return null;
    } catch (e) {
      return `POIs nicht im Browser gespeichert: ${errText(e)}`;
    }
  }

  private backup(raw: string): string | null {
    const s = this.storage!;
    try {
      for (let n = 1; n <= 20; n++) {
        const k = n === 1 ? `${this.key}:defekt` : `${this.key}:defekt-${n}`;
        const old = s.getItem(k);
        if (old === raw) return k;
        if (old === null) {
          s.setItem(k, raw);
          return k;
        }
      }
    } catch {
      // Quota o. ä. — unten: keine Sicherung
    }
    return null;
  }
}

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));
