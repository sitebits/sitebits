// sitebits — Client für die API v1 einer siteBits-Instanz.
//
// Null Laufzeit-Abhängigkeiten: nur `fetch` (Node ≥ 22, Deno, Browser). Die Typen kommen aus der
// öffentlichen OpenAPI-Beschreibung (`docs/public/api/openapi.json` → `schema.d.ts`, erzeugt mit
// `npm run typen`); jede Operation dort hat hier genau eine Funktion (`OPERATIONEN`, geprüft in
// test/typen.test.ts). Anmeldung per persönlichem API-Token im Header `Authorization: Bearer …`.
// Antworten ohne beschriebenes Schema sind `unknown` — erst prüfen, dann verwenden.

import type { components, paths } from './schema.d.ts';

export type Schemas = components['schemas'];
export type Sitzung = Schemas['Sitzung'];
export type Projektkarte = Schemas['Projektkarte'];
export type Projekt = Schemas['Projekt'];
export type ProjektBasis = Schemas['ProjektBasis'];
export type Poi = Schemas['Poi'];
export type PoiDaten = Schemas['PoiDaten'];
export type Messung = Schemas['Messung'];
export type Auswertung = Schemas['Auswertung'];
export type AuswertungMitWorker = Schemas['AuswertungMitWorker'];
export type Ergebnis = Schemas['Ergebnis'];
export type ErgebnisZeile = Schemas['ErgebnisZeile'];
export type Fehler = Schemas['Fehler'];

type Pfad = keyof paths;
type Methode = 'get' | 'post' | 'put' | 'patch' | 'delete' | 'head';
type Op<P extends Pfad, M extends Methode> = NonNullable<paths[P][M]>;
type Json<R> = R extends { content: { 'application/json': infer J } } ? J : never;
type Antworten<O> = O extends { responses: infer R } ? R : never;
/** JSON-Körper der Erfolgsantwort (200, 201 oder 202). */
type Erfolg<O> = Json<Antworten<O>[Extract<keyof Antworten<O>, 200 | 201 | 202>]>;
/** JSON-Körper der Anfrage. */
type Koerper<O> = O extends { requestBody?: { content: { 'application/json': infer B } } } ? B : never;
/** Abfrageparameter. */
type Abfrage<O> = O extends { parameters: { query?: infer Q } } ? NonNullable<Q> : never;

export type Anfrage<P extends Pfad, M extends Methode> = Koerper<Op<P, M>>;
export type Antwort<P extends Pfad, M extends Methode> = Erfolg<Op<P, M>>;

/** Jede Funktion des Clients ↔ genau eine Operation der OpenAPI-Beschreibung. */
export const OPERATIONEN = {
  sitzung: ['get', '/api/v1/sitzung'],
  version: ['get', '/api/v1/version'],
  projekte: ['get', '/api/v1/projekte'],
  projekt: ['get', '/api/v1/projekte/{pid}'],
  projektAendern: ['patch', '/api/v1/projekte/{pid}'],
  paket: ['get', '/api/v1/projekte/{pid}/paket'],
  freigaben: ['get', '/api/v1/projekte/{pid}/freigaben'],
  beteiligte: ['get', '/api/v1/projekte/{pid}/beteiligte'],
  gewerke: ['get', '/api/v1/projekte/{pid}/gewerke'],
  pois: ['get', '/api/v1/projekte/{pid}/pois'],
  poiAnlegen: ['post', '/api/v1/projekte/{pid}/pois'],
  poiAendern: ['patch', '/api/v1/projekte/{pid}/pois/{eid}'],
  poiLoeschen: ['delete', '/api/v1/projekte/{pid}/pois/{eid}'],
  poiAnnehmen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/annehmen'],
  poiVerwerfen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/verwerfen'],
  messungen: ['get', '/api/v1/projekte/{pid}/messungen'],
  messungAnlegen: ['post', '/api/v1/projekte/{pid}/messungen'],
  messungAendern: ['patch', '/api/v1/projekte/{pid}/messungen/{eid}'],
  messungLoeschen: ['delete', '/api/v1/projekte/{pid}/messungen/{eid}'],
  messungAnnehmen: ['post', '/api/v1/projekte/{pid}/messungen/{eid}/annehmen'],
  messungVerwerfen: ['post', '/api/v1/projekte/{pid}/messungen/{eid}/verwerfen'],
  zuPruefen: ['get', '/api/v1/projekte/{pid}/zu-pruefen'],
  befunde: ['get', '/api/v1/projekte/{pid}/befunde'],
  faden: ['get', '/api/v1/projekte/{pid}/pois/{eid}/faden'],
  fadenEintrag: ['post', '/api/v1/projekte/{pid}/pois/{eid}/faden'],
  fadenAnnehmen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/faden/{schluessel}/annehmen'],
  fadenVerwerfen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/faden/{schluessel}/verwerfen'],
  fadenZurueckziehen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/faden/{schluessel}/zurueckziehen'],
  fadenBenachrichtigung: ['get', '/api/v1/projekte/{pid}/pois/{eid}/faden/benachrichtigung'],
  anhangHochladen: ['post', '/api/v1/projekte/{pid}/pois/{eid}/faden/anhaenge'],
  anhangStand: ['get', '/api/v1/projekte/{pid}/pois/{eid}/faden/anhaenge/{aid}/stand'],
  anhangLaden: ['get', '/api/v1/projekte/{pid}/pois/{eid}/faden/anhaenge/{aid}'],
  fadenBenachrichtigungSetzen: ['put', '/api/v1/projekte/{pid}/pois/{eid}/faden/benachrichtigung'],
  exportJson: ['get', '/api/v1/projekte/{pid}/export.json'],
  exportCsv: ['get', '/api/v1/projekte/{pid}/export.csv'],
  maengelliste: ['get', '/api/v1/projekte/{pid}/maengelliste'],
  maengellisteXlsx: ['get', '/api/v1/projekte/{pid}/maengelliste.xlsx'],
  protokoll: ['get', '/api/v1/projekte/{pid}/protokoll'],
  protokollJsonl: ['get', '/api/v1/projekte/{pid}/protokoll.jsonl'],
  auswertungen: ['get', '/api/v1/projekte/{pid}/auswertungen'],
  auswertungStarten: ['post', '/api/v1/projekte/{pid}/auswertungen'],
  auswertung: ['get', '/api/v1/projekte/{pid}/auswertungen/{aid}'],
  auswertungLoeschen: ['delete', '/api/v1/projekte/{pid}/auswertungen/{aid}'],
  ergebnis: ['get', '/api/v1/projekte/{pid}/auswertungen/{aid}/ergebnis'],
  bericht: ['get', '/api/v1/projekte/{pid}/auswertungen/{aid}/bericht'],
  freigeben: ['post', '/api/v1/projekte/{pid}/auswertungen/{aid}/freigabe'],
  uploads: ['get', '/api/v1/uploads'],
  uploadAnlegen: ['post', '/api/v1/uploads'],
  upload: ['get', '/api/v1/uploads/{uid}'],
  uploadLoeschen: ['delete', '/api/v1/uploads/{uid}'],
  uploadStand: ['head', '/api/v1/uploads/{uid}/dateien/{nr}'],
  uploadStueck: ['put', '/api/v1/uploads/{uid}/dateien/{nr}'],
  uploadAbschliessen: ['post', '/api/v1/uploads/{uid}/abschliessen'],
  importe: ['get', '/api/v1/importe'],
  importStarten: ['post', '/api/v1/importe'],
  importLesen: ['get', '/api/v1/importe/{aid}'],
  importAbbrechen: ['delete', '/api/v1/importe/{aid}'],
} as const satisfies Record<string, readonly [Methode, Pfad]>;

type Ops = typeof OPERATIONEN;
type Res<N extends keyof Ops> = Antwort<Ops[N][1], Ops[N][0]>;
type Req<N extends keyof Ops> = Anfrage<Ops[N][1], Ops[N][0]>;
type Query<N extends keyof Ops> = Abfrage<Op<Ops[N][1], Ops[N][0]>>;

/** Fehlerantwort der Instanz (HTTP 4xx/5xx) oder kein erreichbarer Dienst (`status` 0). */
export class SitebitsFehler extends Error {
  readonly status: number;
  readonly methode: string;
  readonly pfad: string;
  /** Satz der Instanz für Menschen (`fehler`), sonst null. */
  readonly fehler: string | null;
  /** Nur bei 409 nach Bearbeitungskonflikt: der aktuelle Stand des Objekts. */
  readonly aktuell: unknown;

  constructor(status: number, methode: string, pfad: string, fehler: string | null,
    aktuell: unknown = null, ursache?: unknown) {
    super(`${methode} ${pfad}: ${status === 0 ? 'nicht erreichbar' : `HTTP ${status}`}`
      + (fehler ? ` – ${fehler}` : ''), ursache === undefined ? undefined : { cause: ursache });
    this.name = 'SitebitsFehler';
    this.status = status;
    this.methode = methode;
    this.pfad = pfad;
    this.fehler = fehler;
    this.aktuell = aktuell;
  }
}

export interface Optionen {
  /** Adresse der Instanz, z. B. `https://bits.example.org` (ohne `/api/v1`). */
  basis: string;
  /** Persönliches API-Token (`sv_…`). */
  token: string;
  /** Eigenes fetch (Tests, Proxys); Vorgabe: globales fetch. */
  fetch?: typeof fetch;
}

type Format = 'json' | 'text' | 'bytes';
type Werte = Record<string, string | number | boolean | null | undefined>;

function fuellen(pfad: string, werte: Record<string, string>): string {
  return pfad.replace(/\{(\w+)\}/g, (_m, name: string) => {
    const wert = werte[name];
    if (wert === undefined || wert === '') throw new TypeError(`Pfadparameter ${name} fehlt`);
    return encodeURIComponent(wert);
  });
}

function abfrage(werte: Werte | undefined): string {
  if (!werte) return '';
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(werte)) {
    if (v === undefined || v === null) continue;
    q.set(k, typeof v === 'boolean' ? (v ? '1' : '0') : String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

function fehlerAus(daten: unknown): { fehler: string | null; aktuell: unknown } {
  if (typeof daten === 'object' && daten !== null) {
    const d = daten as Record<string, unknown>;
    return { fehler: typeof d.fehler === 'string' ? d.fehler : null, aktuell: d.aktuell ?? null };
  }
  return { fehler: null, aktuell: null };
}

export class Sitebits {
  readonly #basis: string;
  readonly #token: string;
  readonly #fetch: typeof fetch;

  constructor(optionen: Optionen) {
    if (!/^https?:\/\//.test(optionen.basis)) throw new TypeError('basis braucht http:// oder https://');
    if (!optionen.token) throw new TypeError('token fehlt');
    this.#basis = optionen.basis.replace(/\/+$/, '');
    this.#token = optionen.token;
    this.#fetch = optionen.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** Eine Operation aufrufen. Antwort 204 → null. */
  async #rufe(name: keyof Ops, werte: Record<string, string>, opt: {
    query?: Werte | undefined; body?: unknown; format?: Format;
    roh?: Uint8Array<ArrayBuffer>; kopf?: Record<string, string>; antwortKopf?: string;
  } = {}): Promise<unknown> {
    const [methode, muster] = OPERATIONEN[name];
    const pfad = fuellen(muster, werte) + abfrage(opt.query);
    const kopf: Record<string, string> = { ...opt.kopf, Authorization: `Bearer ${this.#token}` };
    const init: RequestInit = { method: methode.toUpperCase(), headers: kopf, redirect: 'error' };
    if (opt.roh !== undefined) {
      kopf['Content-Type'] ??= 'application/octet-stream';
      init.body = opt.roh;
    } else if (opt.body !== undefined) {
      kopf['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opt.body);
    }
    let r: Response;
    try {
      r = await this.#fetch(this.#basis + pfad, init);
    } catch (e) {
      throw new SitebitsFehler(0, init.method ?? methode, pfad, null, null, e);
    }
    if (!r.ok) {
      let daten: unknown = null;
      try { daten = await r.json(); } catch { /* kein JSON */ }
      const { fehler, aktuell } = fehlerAus(daten);
      throw new SitebitsFehler(r.status, methode.toUpperCase(), pfad, fehler, aktuell);
    }
    if (opt.antwortKopf !== undefined) return r.headers.get(opt.antwortKopf);
    if (r.status === 204) return null;
    const format = opt.format ?? 'json';
    if (format === 'text') return r.text();
    if (format === 'bytes') return new Uint8Array(await r.arrayBuffer());
    return r.json();
  }

  // --- Sitzung und Projekte -------------------------------------------------------------------
  sitzung(): Promise<Res<'sitzung'>> { return this.#rufe('sitzung', {}) as Promise<Res<'sitzung'>>; }
  version(): Promise<Res<'version'>> { return this.#rufe('version', {}) as Promise<Res<'version'>>; }
  projekte(): Promise<Res<'projekte'>> { return this.#rufe('projekte', {}) as Promise<Res<'projekte'>>; }
  projekt(pid: string): Promise<Res<'projekt'>> {
    return this.#rufe('projekt', { pid }) as Promise<Res<'projekt'>>;
  }
  projektAendern(pid: string, body: Req<'projektAendern'>): Promise<Res<'projektAendern'>> {
    return this.#rufe('projektAendern', { pid }, { body }) as Promise<Res<'projektAendern'>>;
  }
  paket(pid: string): Promise<Res<'paket'>> { return this.#rufe('paket', { pid }) as Promise<Res<'paket'>>; }
  /** Freigabestand der Panoramen und Fotos (F20); freigeben nur im Portal. */
  freigaben(pid: string): Promise<Res<'freigaben'>> {
    return this.#rufe('freigaben', { pid }) as Promise<Res<'freigaben'>>;
  }
  beteiligte(pid: string): Promise<Res<'beteiligte'>> {
    return this.#rufe('beteiligte', { pid }) as Promise<Res<'beteiligte'>>;
  }
  gewerke(pid: string): Promise<Res<'gewerke'>> { return this.#rufe('gewerke', { pid }) as Promise<Res<'gewerke'>>; }

  // --- POIs ---------------------------------------------------------------------------------
  pois(pid: string, query?: Query<'pois'>): Promise<Res<'pois'>> {
    return this.#rufe('pois', { pid }, { query }) as Promise<Res<'pois'>>;
  }
  poiAnlegen(pid: string, body: Req<'poiAnlegen'>): Promise<Res<'poiAnlegen'>> {
    return this.#rufe('poiAnlegen', { pid }, { body }) as Promise<Res<'poiAnlegen'>>;
  }
  poiAendern(pid: string, eid: string, body: Req<'poiAendern'>): Promise<Res<'poiAendern'>> {
    return this.#rufe('poiAendern', { pid, eid }, { body }) as Promise<Res<'poiAendern'>>;
  }
  /** Weich löschen; null = gelöscht (204), sonst nur Löschvorschlag (202, KI-Agent). */
  poiLoeschen(pid: string, eid: string, version: number): Promise<Res<'poiLoeschen'> | null> {
    return this.#rufe('poiLoeschen', { pid, eid }, { query: { version } }) as Promise<Res<'poiLoeschen'> | null>;
  }
  /** KI-Vorschlag annehmen (nur Menschen); null = Löschvorschlag ausgeführt (204). */
  poiAnnehmen(pid: string, eid: string, body: Req<'poiAnnehmen'>): Promise<Res<'poiAnnehmen'> | null> {
    return this.#rufe('poiAnnehmen', { pid, eid }, { body }) as Promise<Res<'poiAnnehmen'> | null>;
  }
  poiVerwerfen(pid: string, eid: string, body: Req<'poiVerwerfen'>): Promise<Res<'poiVerwerfen'> | null> {
    return this.#rufe('poiVerwerfen', { pid, eid }, { body }) as Promise<Res<'poiVerwerfen'> | null>;
  }

  // --- Messungen ----------------------------------------------------------------------------
  messungen(pid: string, query?: Query<'messungen'>): Promise<Res<'messungen'>> {
    return this.#rufe('messungen', { pid }, { query }) as Promise<Res<'messungen'>>;
  }
  messungAnlegen(pid: string, body: Req<'messungAnlegen'>): Promise<Res<'messungAnlegen'>> {
    return this.#rufe('messungAnlegen', { pid }, { body }) as Promise<Res<'messungAnlegen'>>;
  }
  messungAendern(pid: string, eid: string, body: Req<'messungAendern'>): Promise<Res<'messungAendern'>> {
    return this.#rufe('messungAendern', { pid, eid }, { body }) as Promise<Res<'messungAendern'>>;
  }
  messungLoeschen(pid: string, eid: string, version: number): Promise<Res<'messungLoeschen'> | null> {
    return this.#rufe('messungLoeschen', { pid, eid }, { query: { version } }) as Promise<Res<'messungLoeschen'> | null>;
  }
  messungAnnehmen(pid: string, eid: string, body: Req<'messungAnnehmen'>): Promise<Res<'messungAnnehmen'> | null> {
    return this.#rufe('messungAnnehmen', { pid, eid }, { body }) as Promise<Res<'messungAnnehmen'> | null>;
  }
  messungVerwerfen(pid: string, eid: string, body: Req<'messungVerwerfen'>): Promise<Res<'messungVerwerfen'> | null> {
    return this.#rufe('messungVerwerfen', { pid, eid }, { body }) as Promise<Res<'messungVerwerfen'> | null>;
  }
  zuPruefen(pid: string): Promise<Res<'zuPruefen'>> {
    return this.#rufe('zuPruefen', { pid }) as Promise<Res<'zuPruefen'>>;
  }

  // --- Befunde und Faden --------------------------------------------------------------------
  befunde(pid: string, query?: Query<'befunde'>): Promise<Res<'befunde'>> {
    return this.#rufe('befunde', { pid }, { query }) as Promise<Res<'befunde'>>;
  }
  faden(pid: string, eid: string): Promise<Res<'faden'>> {
    return this.#rufe('faden', { pid, eid }) as Promise<Res<'faden'>>;
  }
  fadenEintrag(pid: string, eid: string, body: Req<'fadenEintrag'>): Promise<Res<'fadenEintrag'>> {
    return this.#rufe('fadenEintrag', { pid, eid }, { body }) as Promise<Res<'fadenEintrag'>>;
  }
  fadenAnnehmen(pid: string, eid: string, schluessel: string, body: Req<'fadenAnnehmen'>): Promise<Res<'fadenAnnehmen'>> {
    return this.#rufe('fadenAnnehmen', { pid, eid, schluessel }, { body }) as Promise<Res<'fadenAnnehmen'>>;
  }
  fadenVerwerfen(pid: string, eid: string, schluessel: string, body: Req<'fadenVerwerfen'> = {}): Promise<Res<'fadenVerwerfen'>> {
    return this.#rufe('fadenVerwerfen', { pid, eid, schluessel }, { body }) as Promise<Res<'fadenVerwerfen'>>;
  }
  fadenZurueckziehen(pid: string, eid: string, schluessel: string): Promise<Res<'fadenZurueckziehen'>> {
    return this.#rufe('fadenZurueckziehen', { pid, eid, schluessel }) as Promise<Res<'fadenZurueckziehen'>>;
  }
  /**
   * Foto (JPEG, PNG) oder PDF an den Faden hängen, höchstens 20 MB. Bilder prüft der Server isoliert
   * und kodiert sie ohne Metadaten neu; bis dahin ist `anhang.status` „pruefung“ (`anhangStand`).
   */
  anhangHochladen(pid: string, eid: string, datei: Uint8Array<ArrayBuffer>, typ: 'image/jpeg' | 'image/png' | 'application/pdf', name?: string): Promise<Res<'anhangHochladen'>> {
    return this.#rufe('anhangHochladen', { pid, eid }, {
      query: name === undefined ? undefined : { name }, roh: datei, kopf: { 'Content-Type': typ },
    }) as Promise<Res<'anhangHochladen'>>;
  }
  anhangStand(pid: string, eid: string, aid: string): Promise<Res<'anhangStand'>> {
    return this.#rufe('anhangStand', { pid, eid, aid }) as Promise<Res<'anhangStand'>>;
  }
  /** Datei eines bereiten Anhangs (JPEG oder PDF) als Bytes. */
  anhangLaden(pid: string, eid: string, aid: string): Promise<Uint8Array> {
    return this.#rufe('anhangLaden', { pid, eid, aid }, { format: 'bytes' }) as Promise<Uint8Array>;
  }
  /** E-Mails zu diesem Befund für das eigene Konto (nur mit eingerichtetem Postdienst, sonst 404). */
  fadenBenachrichtigung(pid: string, eid: string): Promise<Res<'fadenBenachrichtigung'>> {
    return this.#rufe('fadenBenachrichtigung', { pid, eid }) as Promise<Res<'fadenBenachrichtigung'>>;
  }
  fadenBenachrichtigungSetzen(pid: string, eid: string, body: Req<'fadenBenachrichtigungSetzen'>): Promise<Res<'fadenBenachrichtigungSetzen'>> {
    return this.#rufe('fadenBenachrichtigungSetzen', { pid, eid }, { body }) as Promise<Res<'fadenBenachrichtigungSetzen'>>;
  }

  // --- Export und Verlauf -------------------------------------------------------------------
  exportJson(pid: string): Promise<Res<'exportJson'>> {
    return this.#rufe('exportJson', { pid }) as Promise<Res<'exportJson'>>;
  }
  exportCsv(pid: string): Promise<string> {
    return this.#rufe('exportCsv', { pid }, { format: 'text' }) as Promise<string>;
  }
  maengelliste(pid: string): Promise<Res<'maengelliste'>> {
    return this.#rufe('maengelliste', { pid }) as Promise<Res<'maengelliste'>>;
  }
  maengellisteXlsx(pid: string): Promise<Uint8Array> {
    return this.#rufe('maengellisteXlsx', { pid }, { format: 'bytes' }) as Promise<Uint8Array>;
  }
  protokoll(pid: string, query?: Query<'protokoll'>): Promise<Res<'protokoll'>> {
    return this.#rufe('protokoll', { pid }, { query }) as Promise<Res<'protokoll'>>;
  }
  protokollJsonl(pid: string): Promise<string> {
    return this.#rufe('protokollJsonl', { pid }, { format: 'text' }) as Promise<string>;
  }

  // --- Auswertungen -------------------------------------------------------------------------
  auswertungen(pid: string): Promise<Res<'auswertungen'>> {
    return this.#rufe('auswertungen', { pid }) as Promise<Res<'auswertungen'>>;
  }
  auswertungStarten(pid: string, body: Req<'auswertungStarten'>): Promise<Res<'auswertungStarten'>> {
    return this.#rufe('auswertungStarten', { pid }, { body }) as Promise<Res<'auswertungStarten'>>;
  }
  auswertung(pid: string, aid: string): Promise<Res<'auswertung'>> {
    return this.#rufe('auswertung', { pid, aid }) as Promise<Res<'auswertung'>>;
  }
  async auswertungLoeschen(pid: string, aid: string): Promise<void> {
    await this.#rufe('auswertungLoeschen', { pid, aid });
  }
  ergebnis(pid: string, aid: string): Promise<Res<'ergebnis'>> {
    return this.#rufe('ergebnis', { pid, aid }) as Promise<Res<'ergebnis'>>;
  }
  /** Prüfbericht als HTML-Text (Datei für Menschen). */
  bericht(pid: string, aid: string): Promise<string> {
    return this.#rufe('bericht', { pid, aid }, { format: 'text' }) as Promise<string>;
  }
  freigeben(pid: string, aid: string, body: Req<'freigeben'>): Promise<Res<'freigeben'>> {
    return this.#rufe('freigeben', { pid, aid }, { body }) as Promise<Res<'freigeben'>>;
  }

  // --- Uploads und neue Projekte (nur Admin, Import eingeschaltet) --------------------------
  uploads(): Promise<Res<'uploads'>> { return this.#rufe('uploads', {}) as Promise<Res<'uploads'>>; }
  /** Upload ankündigen (Dateien mit Pfad, Größe, SHA-256); Antwort nennt die Stückgröße. */
  uploadAnlegen(body: Req<'uploadAnlegen'>): Promise<Res<'uploadAnlegen'>> {
    return this.#rufe('uploadAnlegen', {}, { body }) as Promise<Res<'uploadAnlegen'>>;
  }
  upload(uid: string): Promise<Res<'upload'>> { return this.#rufe('upload', { uid }) as Promise<Res<'upload'>>; }
  async uploadLoeschen(uid: string): Promise<void> { await this.#rufe('uploadLoeschen', { uid }); }
  /** Stand einer Datei in Byte (Header `Upload-Offset`): dort setzt das nächste Stück an. */
  async uploadStand(uid: string, nr: number): Promise<number> {
    const wert = await this.#rufe('uploadStand', { uid, nr: String(nr) }, { antwortKopf: 'Upload-Offset' });
    const stand = Number(wert);
    if (typeof wert !== 'string' || !Number.isSafeInteger(stand) || stand < 0) {
      throw new SitebitsFehler(0, 'HEAD', `/api/v1/uploads/${uid}/dateien/${nr}`, 'Upload-Offset fehlt oder ist ungültig');
    }
    return stand;
  }
  /**
   * Ein Stück ab `start` schreiben; `gesamt` ist die angekündigte Dateigröße. Der Client setzt
   * `Content-Range` und `Content-Digest` (SHA-256 des Stücks). 409 = anderer Stand, dann
   * `uploadStand` fragen und dort weitermachen.
   */
  async uploadStueck(uid: string, nr: number, start: number, stueck: Uint8Array<ArrayBuffer>, gesamt: number): Promise<Res<'uploadStueck'>> {
    const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', stueck));
    const b64 = btoa(String.fromCharCode(...digest));
    return this.#rufe('uploadStueck', { uid, nr: String(nr) }, {
      roh: stueck,
      kopf: { 'Content-Range': `bytes ${start}-${start + stueck.length - 1}/${gesamt}`, 'Content-Digest': `sha-256=:${b64}:` },
    }) as Promise<Res<'uploadStueck'>>;
  }
  /** Abschließen: der Server rechnet die SHA-256 jeder Datei nach (dauert bei großen Dateien). */
  uploadAbschliessen(uid: string): Promise<Res<'uploadAbschliessen'>> {
    return this.#rufe('uploadAbschliessen', { uid }, { body: {} }) as Promise<Res<'uploadAbschliessen'>>;
  }
  importe(): Promise<Res<'importe'>> { return this.#rufe('importe', {}) as Promise<Res<'importe'>>; }
  importStarten(body: Req<'importStarten'>): Promise<Res<'importStarten'>> {
    return this.#rufe('importStarten', {}, { body }) as Promise<Res<'importStarten'>>;
  }
  importLesen(aid: string): Promise<Res<'importLesen'>> {
    return this.#rufe('importLesen', { aid }) as Promise<Res<'importLesen'>>;
  }
  async importAbbrechen(aid: string): Promise<void> { await this.#rufe('importAbbrechen', { aid }); }

  /**
   * Fragt eine Auswertung ab, bis sie nicht mehr wartet oder läuft. Wirft `SitebitsFehler`
   * (status 0) nach `zeitMs` ohne Ende.
   */
  async aufAuswertungWarten(pid: string, aid: string, opt: {
    intervallMs?: number; zeitMs?: number; warten?: (ms: number) => Promise<void>;
  } = {}): Promise<Res<'auswertung'>> {
    const intervall = opt.intervallMs ?? 2000;
    const ende = Date.now() + (opt.zeitMs ?? 15 * 60_000);
    const warten = opt.warten ?? ((ms: number) => new Promise<void>(fertig => setTimeout(fertig, ms)));
    for (;;) {
      const a = await this.auswertung(pid, aid);
      if (a.status !== 'wartend' && a.status !== 'laufend') return a;
      if (Date.now() >= ende) {
        throw new SitebitsFehler(0, 'GET', `/api/v1/projekte/${pid}/auswertungen/${aid}`,
          `Auswertung nach ${opt.zeitMs ?? 15 * 60_000} ms noch „${a.status}“`);
      }
      await warten(intervall);
    }
  }
}
