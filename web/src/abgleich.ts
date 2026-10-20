// Abgleich lokaler Änderungen mit dem Server. Ein Lauf vergleicht den aktuellen
// lokalen Stand mit dem zuletzt vom Server bestätigten und schickt die Unterschiede nacheinander: erst
// DELETE, dann POST/PATCH (mit version). Netzfehler, 5xx, 401 und 429 lassen alles Offene stehen, ein neuer
// Lauf startet nach 2/5/15/30/60 s — nichts geht still verloren. 409 → Serverstand gewinnt
// (ziel.uebernehmen) mit Hinweis; 404 beim Ändern → die Fassung des Nutzers wird neu angelegt; andere 4xx →
// Meldung, derselbe Inhalt wird nicht endlos wiederholt. Wiederholungen sind idempotent: 409 beim
// Anlegen/Ändern, dessen aktueller Serverstand genau dem gesendeten Inhalt entspricht, gilt als gespeichert —
// die erste Anfrage kam an, nur die Antwort ging verloren. Verglichen wird mit ALLEN seit der letzten Bestätigung
// gesendeten Inhalten: hat der Nutzer inzwischen weiter geändert, wird der ältere, angekommene Stand
// bestätigt und der neue im selben Lauf nachgeschickt — statt ihn als fremden Konflikt zu überschreiben. Reine Logik: fetch steckt in den Ops, die Uhr ist
// austauschbar (Vitest, environment: node).
import { PlattformFehler } from './plattformApi';
import type { ServerEintrag } from './plattformApi';

export interface AbgleichOps<D> {
  anlegen(daten: D, id: string | null): Promise<ServerEintrag<D>>;
  aendern(serverId: string, daten: D, version: number): Promise<ServerEintrag<D>>;
  loeschen(serverId: string, version: number): Promise<void>;
}

export interface AbgleichZiel<L, D> {
  liste(): readonly L[];
  schluessel(x: L): string;
  daten(x: L): D;
  /** ID, die beim Anlegen mitgeschickt wird (POIs: eigene ID), null = Server vergibt (Messungen). */
  clientId(x: L): string | null;
  /** Serverstand nach einem Konflikt lokal übernehmen (aktuell null = lokal entfernen). Gibt den lokalen
   *  Schlüssel des übernommenen Eintrags zurück, null wenn keiner entstand. */
  uebernehmen(lokal: string, aktuell: ServerEintrag<D> | null): string | null;
  /** Antwort des Servers auf das Senden eines Eintrags: null = bestätigt, sonst der Grund der Ablehnung (4xx
   *  außer Konflikt, z. B. eine Obergrenze). Störungen (offline, Anmeldung) melden nichts, der Eintrag bleibt offen. */
  ergebnis?(lokal: string, fehler: string | null): void;
}

export interface Uhr {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

export const standardUhr: Uhr = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => globalThis.clearTimeout(id as ReturnType<typeof setTimeout>),
};

export const SAMMELN_MS = 400;
export const WIEDERHOLEN_MS = [2_000, 5_000, 15_000, 30_000, 60_000] as const;
export const ABGLEICH_TEXT = {
  offline: 'Nicht gespeichert – keine Verbindung zum Server. Ein neuer Versuch läuft automatisch, bitte die Seite offen lassen.',
  sitzung: 'Nicht gespeichert – die Anmeldung ist abgelaufen. Bitte in einem neuen Tab anmelden, danach wird automatisch gespeichert.',
  wieder: 'Verbindung wieder da – alle Änderungen gespeichert.',
  konflikt: 'Inzwischen von jemand anderem geändert – dessen Stand wird angezeigt.',
  neuAngelegt: 'War inzwischen von jemand anderem gelöscht – Ihre Fassung wurde neu angelegt.',
} as const;

/** Stabile Textform für Vergleiche: Schlüssel sortiert, undefined entfällt. */
export function kanonisch(d: unknown): string {
  return JSON.stringify(d, (_k, v: unknown) =>
    v !== null && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  );
}

const istStoerung = (e: PlattformFehler): boolean => e.status === 0 || e.status === 401 || e.status === 429 || e.status >= 500;

export function istEintrag<D>(x: unknown): x is ServerEintrag<D> {
  if (x === null || typeof x !== 'object') return false;
  const o = x as { id?: unknown; version?: unknown };
  return typeof o.id === 'string' && typeof o.version === 'number' && 'daten' in o;
}

interface Bestaetigt {
  serverId: string;
  version: number;
  inhalt: string;
}

export class Abgleich<L, D> {
  private readonly bestaetigt = new Map<string, Bestaetigt>();
  private readonly nurLokal = new Map<string, L>();
  private readonly abgelehnt = new Map<string, string>();
  /** Je Schlüssel die gesendeten, noch nicht beantworteten Inhalte (Antwort evtl. verloren) und die beim
   *  Anlegen mitgeschickte Client-ID — damit ein lokal gelöschter, vielleicht angekommener Eintrag am Server
   *  gelöscht werden kann. */
  private readonly gesendet = new Map<string, { inhalte: Set<string>; clientId: string | null }>();
  private laeuft: Promise<void> | null = null;
  private nochmal = false;
  private sammelTimer: unknown = null;
  private wiederTimer: unknown = null;
  private fehlversuche = 0;
  private gestoert: 'offline' | 'sitzung' | null = null;

  constructor(
    private readonly ziel: AbgleichZiel<L, D>,
    private readonly ops: AbgleichOps<D>,
    private readonly melde: (text: string) => void,
    private readonly uhr: Uhr = standardUhr,
  ) {}

  /** Anfangsstand: lokaler Eintrag x entspricht Server-Eintrag serverId in version. */
  kenne(lokal: string, serverId: string, version: number, x: L): void {
    this.bestaetigt.set(lokal, { serverId, version, inhalt: kanonisch(this.ziel.daten(x)) });
  }

  /** Geprüften Serverstand übernehmen, ohne ihn als lokale Änderung zurückzuspielen.
   * Aufrufer wartet zuvor auf sofort() und prüft offen() — keine laufende Übertragung. */
  geprueft(lokal: string, aktuell: ServerEintrag<D> | null): void {
    this.bestaetigt.delete(lokal);
    this.gesendet.delete(lokal);
    this.abgelehnt.delete(lokal);
    this.nurLokal.delete(lokal);
    const neu = this.ziel.uebernehmen(lokal, aktuell);
    if (neu !== null && aktuell) {
      const x = this.ziel.liste().find(x => this.ziel.schluessel(x) === neu);
      if (x) this.kenne(neu, aktuell.id, aktuell.version, x);
    }
  }

  /** Eintrag bleibt lokal, solange er (als Objekt) unverändert ist — Deep-Link-Messungen. */
  nurLokalLassen(lokal: string, x: L): void {
    this.nurLokal.set(lokal, x);
  }

  /** true = der Server hat einen Stand dieses Eintrags bestätigt (er gehört zum Serverstand). Ein nur
   *  gesendeter, unbeantworteter Eintrag zählt nicht — die Antwort kann ausbleiben. */
  gebunden(lokal: string): boolean {
    return this.bestaetigt.has(lokal);
  }

  /** true = x entspricht genau dem vom Server bestätigten Stand (keine offene lokale Änderung). */
  unveraendertGebunden(lokal: string, x: L): boolean {
    const b = this.bestaetigt.get(lokal);
    return b !== undefined && b.inhalt === kanonisch(this.ziel.daten(x));
  }

  version(lokal: string): number | undefined {
    return this.bestaetigt.get(lokal)?.version;
  }

  /** Anzahl lokaler Änderungen, die der Server noch nicht bestätigt hat (ohne vom Server abgelehnte). */
  offen(): number {
    let n = 0;
    const da = new Set<string>();
    for (const x of this.ziel.liste()) {
      const k = this.ziel.schluessel(x);
      da.add(k);
      if (this.nurLokal.get(k) === x) continue;
      const inhalt = kanonisch(this.ziel.daten(x));
      if ((this.bestaetigt.get(k)?.inhalt !== inhalt && this.abgelehnt.get(k) !== inhalt) || this.gesendet.has(k)) n++;
    }
    for (const k of new Set([...this.bestaetigt.keys(), ...this.gesendet.keys()])) if (!da.has(k)) n++;
    return n;
  }

  /** Anzahl lokaler Einträge, deren aktuellen Inhalt der Server abgelehnt hat (422 u. ä.). */
  abgelehnteAnzahl(): number {
    let n = 0;
    for (const x of this.ziel.liste()) {
      const k = this.ziel.schluessel(x);
      if (this.abgelehnt.get(k) === kanonisch(this.ziel.daten(x))) n++;
    }
    return n;
  }

  /** Sammelfenster überspringen und sofort abgleichen (Seite wird verborgen oder verlassen). */
  sofort(): Promise<void> {
    if (this.sammelTimer !== null) {
      this.uhr.clearTimeout(this.sammelTimer);
      this.sammelTimer = null;
    }
    return this.lauf();
  }

  /** Nach jeder lokalen Änderung aufrufen; mehrere Änderungen innerhalb von SAMMELN_MS ergeben einen Lauf. */
  geaendert(): void {
    if (this.sammelTimer !== null) return;
    this.sammelTimer = this.uhr.setTimeout(() => {
      this.sammelTimer = null;
      void this.lauf();
    }, SAMMELN_MS);
  }

  /** Ein Lauf zur Zeit; ein Aufruf während eines Laufs sorgt für einen weiteren Durchgang danach. */
  lauf(): Promise<void> {
    if (this.laeuft) {
      this.nochmal = true;
      return this.laeuft;
    }
    const p = (async (): Promise<void> => {
      try {
        do {
          this.nochmal = false;
          await this.einmal();
        } while (this.nochmal && this.gestoert === null);
      } finally {
        this.laeuft = null;
      }
    })();
    this.laeuft = p;
    return p;
  }

  private async einmal(): Promise<void> {
    try {
      await this.schritte();
    } catch (e) {
      if (e instanceof PlattformFehler && istStoerung(e)) {
        this.stoerung(e.status === 401 ? 'sitzung' : 'offline');
        return;
      }
      this.melde(`Nicht gespeichert: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (this.gestoert !== null) {
      this.gestoert = null;
      this.fehlversuche = 0;
      this.melde(ABGLEICH_TEXT.wieder);
    }
  }

  private async schritte(): Promise<void> {
    const jetzt = new Map<string, L>();
    for (const x of this.ziel.liste()) jetzt.set(this.ziel.schluessel(x), x);
    // Lokal gelöscht: bestätigte Einträge und solche mit ausstehender Antwort (POST evtl. angekommen → per
    // Client-ID löschen, Version 1; 404 = kam nie an, still).
    for (const k of new Set([...this.bestaetigt.keys(), ...this.gesendet.keys()])) {
      if (jetzt.has(k)) continue;
      const b = this.bestaetigt.get(k);
      const cid = this.gesendet.get(k)?.clientId ?? null;
      const ziel = b ? { serverId: b.serverId, version: b.version } : cid !== null ? { serverId: cid, version: 1 } : null;
      if (ziel === null) {
        this.gesendet.delete(k);
        continue;
      }
      try {
        await this.loeschen(k, ziel.serverId, ziel.version);
        this.bestaetigt.delete(k);
        this.gesendet.delete(k);
      } catch (e) {
        if (!(e instanceof PlattformFehler) || istStoerung(e)) throw e;
        this.gesendet.delete(k);
        if (e.status === 409) this.konflikt(k, e);
        else {
          this.bestaetigt.delete(k);
          if (e.status !== 404) this.melde(`Löschen nicht gespeichert: ${e.message}`);
        }
      }
    }
    for (const [k, x] of jetzt) {
      if (this.nurLokal.get(k) === x) continue;
      this.nurLokal.delete(k);
      const d = this.ziel.daten(x);
      const inhalt = kanonisch(d);
      const b = this.bestaetigt.get(k);
      // Gleich dem bestätigten Stand genügt nicht, solange ein anderer gesendeter Stand unbeantwortet ist
      // (A→B→A: B kann angekommen sein) — dann nochmal senden, ein 409 klärt es.
      if ((b?.inhalt === inhalt && !this.gesendet.has(k)) || this.abgelehnt.get(k) === inhalt) continue;
      try {
        let g = this.gesendet.get(k);
        if (!g) this.gesendet.set(k, (g = { inhalte: new Set(), clientId: null }));
        g.inhalte.add(inhalt);
        let e: ServerEintrag<D>;
        if (b) e = await this.ops.aendern(b.serverId, d, b.version);
        else {
          g.clientId ??= this.ziel.clientId(x);
          e = await this.ops.anlegen(d, g.clientId);
        }
        this.bestaetigt.set(k, { serverId: e.id, version: e.version, inhalt });
        this.abgelehnt.delete(k);
        this.gesendet.delete(k);
        this.ziel.ergebnis?.(k, null);
      } catch (e) {
        if (!(e instanceof PlattformFehler) || istStoerung(e)) throw e;
        if (e.status === 409) {
          if (!this.schonGespeichert(k, e, inhalt)) {
            this.konflikt(k, e);
            this.ziel.ergebnis?.(k, ABGLEICH_TEXT.konflikt);
          } else this.ziel.ergebnis?.(k, null);
          this.gesendet.delete(k);
        } else if (e.status === 404 && b) {
          this.gesendet.delete(k);
          this.bestaetigt.delete(k);
          this.melde(ABGLEICH_TEXT.neuAngelegt);
          this.nochmal = true;
        } else {
          this.gesendet.delete(k);
          this.abgelehnt.set(k, inhalt);
          this.melde(`Nicht gespeichert: ${e.message}`);
          this.ziel.ergebnis?.(k, e.message);
        }
      }
    }
  }

  /** DELETE; 409 mit einem eigenen, unbeantworteten Stand (Antwort verloren) → einmal mit dessen Version. */
  private async loeschen(k: string, serverId: string, version: number): Promise<void> {
    try {
      await this.ops.loeschen(serverId, version);
    } catch (e) {
      if (!(e instanceof PlattformFehler) || e.status !== 409 || !istEintrag<D>(e.aktuell)) throw e;
      const angekommen = kanonisch(e.aktuell.daten);
      if (!this.gesendet.get(k)?.inhalte.has(angekommen)) throw e;
      // Neue Version merken: scheitert der zweite Versuch an einer Störung, nimmt der nächste Lauf sie.
      this.bestaetigt.set(k, { serverId: e.aktuell.id, version: e.aktuell.version, inhalt: angekommen });
      await this.ops.loeschen(e.aktuell.id, e.aktuell.version);
    }
  }

  /** 409, aber der Server hat schon genau diesen Inhalt (wiederholte Anfrage): als bestätigt übernehmen. */
  private schonGespeichert(k: string, e: PlattformFehler, inhalt: string): boolean {
    if (!istEintrag<D>(e.aktuell)) return false;
    const angekommen = kanonisch(e.aktuell.daten);
    if (!this.gesendet.get(k)?.inhalte.has(angekommen)) return false;
    this.bestaetigt.set(k, { serverId: e.aktuell.id, version: e.aktuell.version, inhalt: angekommen });
    this.abgelehnt.delete(k);
    // Ein früher gesendeter Stand kam an, der aktuelle noch nicht → im selben Lauf als Änderung nachschicken.
    if (angekommen !== inhalt) this.nochmal = true;
    return true;
  }

  private konflikt(k: string, e: PlattformFehler): void {
    this.bestaetigt.delete(k);
    this.abgelehnt.delete(k);
    const aktuell = istEintrag<D>(e.aktuell) ? e.aktuell : null;
    const neu = this.ziel.uebernehmen(k, aktuell);
    if (neu !== null && aktuell) {
      const x = this.ziel.liste().find((y) => this.ziel.schluessel(y) === neu);
      if (x !== undefined) this.kenne(neu, aktuell.id, aktuell.version, x);
    }
    this.melde(ABGLEICH_TEXT.konflikt);
  }

  private stoerung(art: 'offline' | 'sitzung'): void {
    if (this.gestoert !== art) this.melde(art === 'sitzung' ? ABGLEICH_TEXT.sitzung : ABGLEICH_TEXT.offline);
    this.gestoert = art;
    const ms = WIEDERHOLEN_MS[Math.min(this.fehlversuche, WIEDERHOLEN_MS.length - 1)] ?? 60_000;
    this.fehlversuche++;
    if (this.wiederTimer !== null) this.uhr.clearTimeout(this.wiederTimer);
    this.wiederTimer = this.uhr.setTimeout(() => {
      this.wiederTimer = null;
      void this.lauf();
    }, ms);
  }
}
