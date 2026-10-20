import { istEintrag } from './abgleich';
// Messungen im Plattformmodus: MeasureStore bleibt unverändert und vergibt
// weiter lokale IDs (M1, M2 …); der Abgleich ordnet ihnen Server-IDs zu. Messungen aus dem Link werden einer
// Servermessung mit gleicher Geometrie (± 2 mm) zugeordnet, die übrigen bleiben nur lokal, bis der Nutzer sie
// verändert — Öffnen eines Links schreibt nie. Ohne Recht auf gemeinsame Messungen: kein Schreiben, einmaliger Hinweis.
// Neue Messungen tragen eine zufällige Client-ID (wiederholter POST ist idempotent); eine
// Konflikt-Übernahme leert den Rückgängig-Stapel, sonst löschte Strg+Z die Fassung des Kollegen am Server.
import { entscheiden, vorschlagAnzeige } from './vorschlagApi';
import { Abgleich } from './abgleich';
import type { AbgleichOps, Uhr } from './abgleich';
import { MeasureError } from './measureStore';
import type { Measurement, MeasureStore } from './measureStore';
import { anfrage, PlattformFehler, projektPfad, START_MS } from './plattformApi';
import type { MessungDaten, ServerEintrag } from './plattformApi';
import type { Versand } from './serverPois';

export const TOLERANZ_M = 0.002;
export const LOKALE_MESSUNGEN =
  'Mit Ihrer Rolle werden Messungen nicht im Projekt gespeichert – sie stehen nur im Link dieser Ansicht.';

export function messungDaten(m: Measurement): MessungDaten {
  const d: MessungDaten = { kind: m.kind, points: m.points.map((p): [number, number, number] => [p[0], p[1], p[2]]) };
  if (m.kind === 'area') {
    d.areaM2 = m.areaM2;
    d.planarityM = m.planarityM;
  }
  if (m.pointSources) d.pointSources = m.pointSources.map((s) => s ? {...s} : null);
  return d;
}

export function gleicheGeometrie(m: Measurement, d: MessungDaten): boolean {
  return (
    m.kind === d.kind &&
    m.points.length === d.points.length &&
    m.points.every((p, i) => p.every((v, j) => Math.abs(v - (d.points[i]?.[j] ?? Number.NaN)) <= TOLERANZ_M))
  );
}

/** Server-ID für eine neue Messung, im Format des Servers (M- + 12 Hex-Zeichen). */
export function neueMessungId(): string {
  const b = crypto.getRandomValues(new Uint8Array(6));
  return `M-${[...b].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
}

/** Einer Servermessung zugeordnete Link-Messung übernimmt die exakten Serverkoordinaten (ID bleibt): der Link
 *  rundet auf mm, und eine spätere Bearbeitung schickte sonst die gerundeten Punkte per PATCH zurück. */
function serverPunkte(store: MeasureStore, m: Measurement, d: MessungDaten): Measurement {
  let x = m;
  try { x = store.setGeometry(m.id,d.points,d.pointSources); }
  catch (e) { if (!(e instanceof MeasureError)) throw e; }
  return x;
}

export function messungOps(
  projectId: string,
  fetchFn: typeof fetch = fetch,
  versand: Versand = { keepalive: false },
): AbgleichOps<MessungDaten> {
  const basis = `${projektPfad(projectId)}/messungen`;
  return {
    anlegen: (daten, id) => anfrage<ServerEintrag<MessungDaten>>('POST', basis, { id, daten }, fetchFn, versand.keepalive),
    aendern: (sid, daten, version) =>
      anfrage<ServerEintrag<MessungDaten>>('PATCH', `${basis}/${encodeURIComponent(sid)}`, { daten, version }, fetchFn, versand.keepalive),
    loeschen: (sid, version) =>
      anfrage<void>('DELETE', `${basis}/${encodeURIComponent(sid)}?version=${version}`, undefined, fetchFn, versand.keepalive),
  };
}

export class MessungSync {
  readonly abgleich: Abgleich<Measurement, MessungDaten>;
  private neuZeichnen: () => void = () => undefined;
  private lokalGemeldet = false;
  private geladen = false;
  private readonly serverIds = new Map<string,string>();
  private readonly versand: Versand = { keepalive: false };
  /** lokale ID (M1 …) → Client-ID für POST; bleibt über Wiederholungen gleich. */
  private readonly clientIds = new Map<string, string>();

  constructor(
    private readonly o: {
      store: MeasureStore;
      projectId: string;
      schreibbar: boolean;
      vorschlaege?: boolean;
      melde: (text: string) => void;
      fetchFn?: typeof fetch;
      uhr?: Uhr;
    },
  ) {
    const store = o.store;
    this.abgleich = new Abgleich<Measurement, MessungDaten>(
      {
        liste: () => store.list(),
        schluessel: (m) => m.id,
        daten: messungDaten,
        clientId: (m) => {
          let id = this.clientIds.get(m.id);
          if (id === undefined) {
            id = neueMessungId();
            this.clientIds.set(m.id, id);
          }
          return id;
        },
        uebernehmen: (lokal, aktuell) => {
          store.remove(lokal);
          this.serverIds.delete(lokal);
          let neu: string | null = null;
          if (aktuell) {
            try {
              const m = store.addGeometry(aktuell.daten.kind, aktuell.daten.points, aktuell.daten.pointSources);
              m.vorschlag = vorschlagAnzeige(aktuell);
              this.serverIds.set(m.id,aktuell.id);
              neu = m.id;
            } catch (e) {
              if (!(e instanceof MeasureError)) throw e;
            }
          }
          // Die Übernahme ist kein rückgängig zu machender Schritt: sonst stellte Strg+Z den eigenen Stand
          // wieder her bzw. entfernte die Fassung des Kollegen — und der nächste Abgleich löschte sie am Server.
          store.resetHistory();
          this.neuZeichnen();
          return neu;
        },
      },
      messungOps(o.projectId, o.fetchFn, this.versand),
      o.melde,
      o.uhr,
    );
  }

  /** Lädt die Messungen des Projekts in den Speicher (vor store.resetHistory()); gibt einen Hinweis zurück. */
  async laden(): Promise<string | null> {
    let liste: ServerEintrag<MessungDaten>[];
    try {
      const r = await anfrage<{ messungen: ServerEintrag<MessungDaten>[] }>(
        'GET',
        `${projektPfad(this.o.projectId)}/messungen${this.o.vorschlaege ? '?vorschlaege=1' : ''}`,
        undefined,
        this.o.fetchFn,
        false,
        START_MS,
      );
      liste = r.messungen;
    } catch (e) {
      return `Messungen des Projekts nicht geladen (${e instanceof Error ? e.message : String(e)}) – Messungen werden nicht gespeichert.`;
    }
    const ausLink = [...this.o.store.list()];
    const zugeordnet = new Set<string>();
    let verworfen = 0;
    for (const e of liste) {
      let m: Measurement | undefined = ausLink.find((x) => !zugeordnet.has(x.id) && gleicheGeometrie(x, e.daten));
      if (m) m = serverPunkte(this.o.store, m, e.daten);
      else {
        try {
          m = this.o.store.addGeometry(e.daten.kind, e.daten.points, e.daten.pointSources);
        } catch (err) {
          if (!(err instanceof MeasureError)) throw err;
          verworfen++;
          continue;
        }
      }
      zugeordnet.add(m.id);
      m.vorschlag = vorschlagAnzeige(e);
      this.serverIds.set(m.id,e.id);
      this.abgleich.kenne(m.id, e.id, e.version, m);
    }
    let nurLokal = 0;
    for (const m of ausLink) {
      if (zugeordnet.has(m.id)) continue;
      this.abgleich.nurLokalLassen(m.id, m);
      nurLokal++;
    }
    this.geladen = true;
    const hinweise: string[] = [];
    if (verworfen) hinweise.push(`${verworfen} Messung(en) des Projekts mit ungültiger Geometrie nicht angezeigt.`);
    if (nurLokal && this.o.schreibbar)
      hinweise.push(`${nurLokal} Messung(en) aus dem Link nur lokal – sie kommen erst ins Projekt, wenn Sie sie bearbeiten.`);
    return hinweise.length ? hinweise.join(' ') : null;
  }

  async pruefen(lokal: string, aktion: 'annehmen' | 'verwerfen'): Promise<void> {
    if (!this.o.vorschlaege) return;
    await this.abgleich.sofort();
    if (this.offen() || this.abgleich.abgelehnteAnzahl()) throw new Error('Noch nicht gespeicherte Änderungen – bitte nach dem Abgleich erneut prüfen.');
    const sid = this.serverIds.get(lokal), version = this.abgleich.version(lokal);
    if (!sid || version === undefined) throw new Error('Bitte den Projektstand neu laden.');
    try {
      const neu = await entscheiden<MessungDaten>('messungen',this.o.projectId,sid,version,aktion,this.o.fetchFn);
      this.abgleich.geprueft(lokal,neu); this.neuZeichnen();
    } catch (e) {
      if (e instanceof PlattformFehler && e.status === 409 && istEintrag<MessungDaten>(e.aktuell)) {
        this.abgleich.geprueft(lokal,e.aktuell);
        throw new Error(`${e.message} Der aktuelle Stand ist jetzt geladen – bitte erneut prüfen.`);
      }
      throw e;
    }
  }

  /** true = Änderungen gehen an den Server (Bearbeiter/Admin, Serverstand geladen) — wirken für alle. */
  gemeinsam(): boolean {
    return this.o.schreibbar && this.geladen;
  }

  /** Gehört m in den Deep-Link? Nur, solange es nicht genau dem vom Server bestätigten Stand entspricht
   *  (Final-Fix C/D): Link-Messungen ohne Gegenstück, Messungen ohne Recht auf gemeinsame Messungen, abgelehnte, noch unbestätigte
   *  neue — und bearbeitete gebundene, bis der Server die Änderung bestätigt (sonst ginge sie bei
   *  Serverausfall + Neuladen verloren). */
  imLink(m: Measurement): boolean {
    return !m.vorschlag && !this.abgleich.unveraendertGebunden(m.id, m);
  }

  geaendert(): void {
    if (!this.o.schreibbar) {
      if (!this.lokalGemeldet) {
        this.lokalGemeldet = true;
        this.o.melde(LOKALE_MESSUNGEN);
      }
      return;
    }
    if (this.geladen) this.abgleich.geaendert();
  }

  /** Offene Änderungen sofort und mit keepalive senden (pagehide / Tab verborgen). */
  flush(): Promise<void> {
    if (!this.o.schreibbar || !this.geladen) return Promise.resolve();
    this.versand.keepalive = true;
    return this.abgleich.sofort().finally(() => {
      this.versand.keepalive = false;
    });
  }

  setNeuZeichnen(fn: () => void): void {
    this.neuZeichnen = fn;
  }

  offen(): number {
    return this.o.schreibbar && this.geladen ? this.abgleich.offen() : 0;
  }
}
