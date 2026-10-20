// POIs im Plattformmodus: Mit Schreibrecht sind die änderbaren Server-POIs die eigenen
// POIs des Stores (Quelle 'browser'), jede Speicherung stößt einen Abgleich an; darf die Rolle nur eigene POIs
// ändern (Option `eigen`), werden fremde als schreibgeschützte Quelle 'package' eingereiht und nie
// abgeglichen. Ohne Schreibrecht sind alle Server-POIs 'package', eigene POIs bleiben wie bisher im
// localStorage (lokal). Zu jedem Server-POI merkt sich die Persistenz Server-ID, Revision, Urhebername,
// Änderungszeit und ob er änderbar ist (meta), auch für die schreibgeschützten.
// Ein Server-POI, dessen ID schon ein Paket-POI trägt, bekommt lokal eine neue ID, bleibt aber
// über den Abgleich an seine Server-ID gebunden (Schlüssel ≠ Server-ID) — Öffnen schreibt nie. Das gilt auch
// für schreibgeschützte Server-POIs: Server-Angaben (meta) hängen nie an einem POI aus pois.json.
import { entscheiden, vorschlagAnzeige } from './vorschlagApi';
import { Abgleich, istEintrag } from './abgleich';
import type { AbgleichOps, Uhr } from './abgleich';
import { anfrage, PlattformFehler, projektPfad, START_MS } from './plattformApi';
import type { PoiDaten, ServerEintrag } from './plattformApi';
import type { BefundMeta, PoiLadeErgebnis, PoiPersistenz } from './poiPersistenz';
import type { Poi, PoiStore } from './poiStore';

export const poiDaten = (p: Poi): PoiDaten => ({
  title: p.title,
  category: p.category,
  note: p.note,
  position: [p.position[0], p.position[1], p.position[2]],
  station: p.station,
  ...(p.positionSource !== undefined ? {positionSource:p.positionSource} : {}),
});

/** Schalter für keepalive (nur beim Flush, wenn die Seite verborgen/verlassen wird — Körpergrenze 64 KiB). */
export interface Versand {
  keepalive: boolean;
}

export function poiOps(projectId: string, fetchFn: typeof fetch = fetch, versand: Versand = { keepalive: false }): AbgleichOps<PoiDaten> {
  const basis = `${projektPfad(projectId)}/pois`;
  return {
    anlegen: (daten, id) => anfrage<ServerEintrag<PoiDaten>>('POST', basis, { id, daten }, fetchFn, versand.keepalive),
    aendern: (sid, daten, version) =>
      anfrage<ServerEintrag<PoiDaten>>('PATCH', `${basis}/${encodeURIComponent(sid)}`, { daten, version }, fetchFn, versand.keepalive),
    loeschen: (sid, version) =>
      anfrage<void>('DELETE', `${basis}/${encodeURIComponent(sid)}?version=${version}`, undefined, fetchFn, versand.keepalive),
  };
}

const alsPoi = (e: ServerEintrag<PoiDaten>, source: Poi['source']): Poi => ({ id: e.id, ...e.daten, source, vorschlag: vorschlagAnzeige(e) });

/** Freie lokale ID für einen schreibgeschützten Server-POI: seine Server-ID, bei Kollision eine abgeleitete
 *  (gültige POI-ID, höchstens 64 Zeichen). */
function freieId(belegt: ReadonlySet<string>, id: string): string {
  if (!belegt.has(id)) return id;
  for (let n = 2; ; n++) {
    const kandidat = `${id.slice(0, 56)}-k${n}`;
    if (!belegt.has(kandidat)) return kandidat;
  }
}

/** Reiht einen Server-POI als eigenen ein und gibt seine lokale ID zurück: dieselbe, bei Kollision mit einer
 *  Paket-ID eine neue, null wenn er inhaltsgleich zum Paket-POI ist (PoiStore überspringt ihn dann). */
function einreihen(store: PoiStore, p: Poi): string | null {
  const vorher = new Set(store.list().filter((x) => x.source === 'browser').map((x) => x.id));
  store.importBrowserPois([p]);
  if (store.get(p.id)?.source === 'browser') return p.id;
  return store.list().find((x) => x.source === 'browser' && !vorher.has(x.id))?.id ?? null;
}

export class ServerPoiPersistenz implements PoiPersistenz {
  readonly abgleich: Abgleich<Poi, PoiDaten>;
  private store: PoiStore | null = null;
  private geladen = false;
  private neuZeichnen: () => void = () => undefined;
  private readonly versand: Versand = { keepalive: false };
  /** lokale ID → Server-Angaben; Server-ID → lokale ID. */
  private readonly metaJe = new Map<string, BefundMeta>();
  private readonly lokalJe = new Map<string, string>();
  private readonly ergebnisCbs: ((lokalId: string, fehler: string | null) => void)[] = [];

  constructor(
    private readonly o: {
      projectId: string;
      schreibbar: boolean;
      vorschlaege?: boolean;
      /** Nur diese Server-POIs darf der Nutzer ändern (fehlt = alle). Gilt nur mit Schreibrecht. */
      eigen?: (e: ServerEintrag<PoiDaten>) => boolean;
      lokal: PoiPersistenz;
      melde: (text: string) => void;
      fetchFn?: typeof fetch;
      uhr?: Uhr;
    },
  ) {
    this.abgleich = new Abgleich<Poi, PoiDaten>(
      {
        liste: () => (this.store ? this.store.list().filter((p) => p.source === 'browser') : []),
        schluessel: (p) => p.id,
        daten: poiDaten,
        clientId: (p) => p.id,
        uebernehmen: (lokal, aktuell) => {
          const s = this.store;
          if (!s) return null;
          if (s.get(lokal)?.source === 'browser') s.remove(lokal);
          this.vergiss(lokal);
          // Ein fremder Stand (heute antwortet der Server darauf nie mit 409 samt Körper) bleibt schreibgeschützt
          // und ungebunden — sonst stünde er änderbar im Store und der Abgleich schriebe auf einen fremden POI.
          if (aktuell && !this.eigen(aktuell)) {
            this.fremdeEinreihen(s, [aktuell]);
            this.neuZeichnen();
            return null;
          }
          const neu = aktuell ? einreihen(s, alsPoi(aktuell, 'browser')) : null;
          if (neu !== null && aktuell) this.merke(neu, aktuell, this.eigen(aktuell));
          this.neuZeichnen();
          return neu;
        },
        ergebnis: (lokal, fehler) => {
          for (const cb of this.ergebnisCbs) cb(lokal, fehler);
        },
      },
      this.merkendeOps(poiOps(o.projectId, o.fetchFn, this.versand)),
      o.melde,
      o.uhr,
    );
  }

  async load(store: PoiStore): Promise<PoiLadeErgebnis> {
    this.store = store;
    let liste: ServerEintrag<PoiDaten>[];
    try {
      const r = await anfrage<{ pois: ServerEintrag<PoiDaten>[] }>(
        'GET',
        `${projektPfad(this.o.projectId)}/pois${this.o.vorschlaege ? '?vorschlaege=1' : ''}`,
        undefined,
        this.o.fetchFn,
        false,
        START_MS,
      );
      liste = r.pois;
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      if (!this.o.schreibbar) {
        const r = await this.o.lokal.load(store);
        return { ...r, warning: [`POIs des Projekts nicht geladen (${text}).`, r.warning].filter(Boolean).join(' ') };
      }
      return { loaded: 0, renamed: 0, warning: `POIs des Projekts nicht geladen (${text}) – Änderungen werden nicht gespeichert, bitte die Seite neu laden.` };
    }
    if (!this.o.schreibbar) {
      this.fremdeEinreihen(store, liste);
      return this.o.lokal.load(store);
    }
    const eigen = this.eigen;
    const fremde = liste.filter((e) => !eigen(e));
    const eigene = liste.filter(eigen);
    if (fremde.length) this.fremdeEinreihen(store, fremde);
    // Alle schreibgeschützten IDs (pois.json und eben eingereihte fremde): ein eigener POI mit einer davon
    // wird lokal umbenannt (einreihen).
    const paket = new Set(store.list().filter((p) => p.source === 'package').map((p) => p.id));
    const frei = eigene.filter((e) => !paket.has(e.id));
    let geladen = store.importBrowserPois(frei.map((e) => alsPoi(e, 'browser'))).added;
    for (const e of frei) {
      const p = store.get(e.id);
      if (p?.source !== 'browser') continue;
      this.abgleich.kenne(e.id, e.id, e.version, p);
      this.merke(e.id, e, true);
    }
    for (const e of eigene) {
      if (!paket.has(e.id)) continue;
      const lokal = einreihen(store, alsPoi(e, 'browser'));
      const p = lokal === null ? undefined : store.get(lokal);
      if (lokal === null || !p) continue;
      this.abgleich.kenne(lokal, e.id, e.version, p);
      this.merke(lokal, e, true);
      geladen++;
    }
    this.geladen = true;
    // renamed bleibt 0: die lokale Umbenennung ist gebunden, attachPois soll deshalb nicht speichern.
    return { loaded: geladen, renamed: 0, warning: null };
  }

  async pruefen(lokal: string, aktion: 'annehmen' | 'verwerfen'): Promise<void> {
    if (!this.o.vorschlaege) return;
    await this.abgleich.sofort();
    if (this.offen() || this.abgleich.abgelehnteAnzahl()) throw new Error('Noch nicht gespeicherte Änderungen – bitte nach dem Abgleich erneut prüfen.');
    const meta = this.metaJe.get(lokal);
    const version = this.abgleich.version(lokal);
    if (!meta || version === undefined) throw new Error('Bitte den Projektstand neu laden.');
    try {
      const neu = await entscheiden<PoiDaten>('pois',this.o.projectId,meta.serverId,version,aktion,this.o.fetchFn);
      this.abgleich.geprueft(lokal,neu);
      this.neuZeichnen();
    } catch (e) {
      if (e instanceof PlattformFehler && e.status === 409 && istEintrag<PoiDaten>(e.aktuell)) {
        this.abgleich.geprueft(lokal,e.aktuell);
        throw new Error(`${e.message} Der aktuelle Stand ist jetzt geladen – bitte erneut prüfen.`);
      }
      throw e;
    }
  }

  save(store: PoiStore, revision: number): string | null {
    if (!this.o.schreibbar) return this.o.lokal.save(store, revision);
    if (!this.geladen) return 'POIs nicht gespeichert: der Projektstand konnte nicht geladen werden – bitte die Seite neu laden.';
    this.store = store;
    this.abgleich.geaendert();
    return null;
  }

  /** Serverstand geladen (Bearbeiter) — erst dann ist Speichern und die Übernahme der Browser-POIs möglich. */
  serverStandGeladen(): boolean {
    return this.o.schreibbar && this.geladen;
  }

  /** Offene Änderungen sofort und mit keepalive senden (pagehide / Tab verborgen). */
  flush(): Promise<void> {
    if (!this.serverStandGeladen()) return Promise.resolve();
    this.versand.keepalive = true;
    return this.abgleich.sofort().finally(() => {
      this.versand.keepalive = false;
    });
  }

  onErgebnis(cb: (lokalId: string, fehler: string | null) => void): void {
    this.ergebnisCbs.push(cb);
  }

  setNeuZeichnen(fn: () => void): void {
    this.neuZeichnen = fn;
  }

  offen(): number {
    return this.o.schreibbar ? this.abgleich.offen() : 0;
  }

  meta(lokalId: string): BefundMeta | null {
    const m = this.metaJe.get(lokalId);
    return m ? { ...m } : null;
  }

  lokalId(serverId: string): string | null {
    return this.lokalJe.get(serverId) ?? null;
  }

  /** Eigene Änderung im Befund-Faden (Status, Zuständig, Frist) hat die Version am Server erhöht, nicht den Inhalt:
   *  ohne offene lokale Änderung die neue Version merken — sonst scheiterte das nächste Ändern oder Löschen an 409. */
  standGeaendert(serverId: string, version: number): void {
    const lokal = this.lokalJe.get(serverId);
    const p = lokal === undefined ? undefined : this.store?.get(lokal);
    if (lokal === undefined || p?.source !== 'browser' || !this.abgleich.unveraendertGebunden(lokal, p)) return;
    this.abgleich.kenne(lokal, serverId, version, p);
  }

  /** true = der Server hat einen Stand dieses (eigenen) POIs bestätigt; nur gesendet zählt nicht. */
  bestaetigt(lokalId: string): boolean {
    return this.o.schreibbar && this.abgleich.gebunden(lokalId);
  }

  private get eigen(): (e: ServerEintrag<PoiDaten>) => boolean {
    return this.o.eigen ?? (() => true);
  }

  /** Schreibgeschützte Server-POIs als Quelle 'package' einreihen, mit Server-Angaben (eigen: false). */
  private fremdeEinreihen(store: PoiStore, liste: readonly ServerEintrag<PoiDaten>[]): void {
    const belegt = new Set(store.list().map((p) => p.id));
    const neu = liste.map((e) => {
      const lokal = freieId(belegt, e.id);
      belegt.add(lokal);
      this.merke(lokal, e, false);
      return { ...alsPoi(e, 'package'), id: lokal };
    });
    store.setPackagePois([...store.list().filter((p) => p.source === 'package'), ...neu]);
  }

  private merke(lokal: string, e: ServerEintrag<PoiDaten>, eigen: boolean): void {
    this.metaJe.set(lokal, {
      serverId: e.id,
      paketRevision: e.paketRevision,
      erstelltVonName: e.erstelltVonName ?? null,
      geaendertAm: e.geaendertAm,
      eigen,
    });
    this.lokalJe.set(e.id, lokal);
  }

  private vergiss(lokal: string): void {
    const m = this.metaJe.get(lokal);
    this.metaJe.delete(lokal);
    if (m && this.lokalJe.get(m.serverId) === lokal) this.lokalJe.delete(m.serverId);
  }

  /** 409 mit dem aktuellen Stand: Bestätigt der Abgleich darüber eine Anfrage, deren Antwort verloren ging
   *  (schonGespeichert), liefe sonst nichts, was die Metadaten nachführt. Bei einem echten Konflikt ersetzt
   *  uebernehmen sie anschließend ohnehin. */
  private merkeBei409(f: unknown, lokal: (aktuell: ServerEintrag<PoiDaten>) => string | undefined): void {
    if (!(f instanceof PlattformFehler) || f.status !== 409 || !istEintrag<PoiDaten>(f.aktuell)) return;
    const l = lokal(f.aktuell);
    if (l !== undefined) this.merke(l, f.aktuell, this.eigen(f.aktuell));
  }

  /** Antworten des Servers auf eigene Änderungen halten die Server-Angaben aktuell. Neue POIs werden mit
   *  ihrer lokalen ID als Client-ID angelegt; geänderte und gelöschte über die Server-ID zugeordnet. */
  private merkendeOps(ops: AbgleichOps<PoiDaten>): AbgleichOps<PoiDaten> {
    return {
      anlegen: async (daten, id) => {
        try {
          const e = await ops.anlegen(daten, id);
          this.merke(id ?? e.id, e, this.eigen(e));
          return e;
        } catch (f) {
          this.merkeBei409(f, (a) => id ?? a.id);
          throw f;
        }
      },
      aendern: async (sid, daten, version) => {
        try {
          const e = await ops.aendern(sid, daten, version);
          const lokal = this.lokalJe.get(sid);
          if (lokal !== undefined) this.merke(lokal, e, this.eigen(e));
          return e;
        } catch (f) {
          this.merkeBei409(f, () => this.lokalJe.get(sid));
          throw f;
        }
      },
      loeschen: async (sid, version) => {
        await ops.loeschen(sid, version);
        const lokal = this.lokalJe.get(sid);
        if (lokal !== undefined) this.vergiss(lokal);
      },
    };
  }
}
