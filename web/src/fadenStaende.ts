// Stände der Befund-Fäden im Viewer (rein bis auf die übergebene Lesefunktion, Vitest): Kennzeichen je Server-ID
// für Befundliste und Marker und die Treffer des gewählten Filters. Beides kommt aus GET …/befunde — die Regeln für
// „überfällig“, „mein Gewerk“ und „ich“ kennt nur der Server, der Viewer wiederholt sie nicht.
import { FILTER_LEER, filterAktiv, kennzeichen, kennzeichenKurz } from './fadenModell';
import type { Befunde, Filter, Stand } from './fadenModell';

export class FadenStaende {
  private je = new Map<string, { stand: Stand; ueberfaellig: boolean }>();
  /** Server-IDs, die zum Filter passen; null = kein Filter. */
  private treffer: Set<string> | null = null;
  private f: Filter = FILTER_LEER;
  private lauf = 0;
  private readonly hoerer: (() => void)[] = [];

  constructor(private readonly lies: (f: Filter) => Promise<Befunde>) {}

  get filter(): Filter {
    return this.f;
  }

  /** Filter setzen und neu laden; Rückgabe wie laden(). */
  setzeFilter(f: Filter): Promise<string | null> {
    this.f = f;
    return this.laden();
  }

  /** Stände (und bei Filter die Treffer) neu laden. null = geladen, sonst der Fehlertext; der bisherige Stand bleibt. */
  async laden(): Promise<string | null> {
    const n = ++this.lauf;
    try {
      const filter = this.f;
      const [alle, gefiltert] = await Promise.all([this.lies(FILTER_LEER), filterAktiv(filter) ? this.lies(filter) : null]);
      if (n !== this.lauf) return null;
      this.je = new Map(alle.befunde.map((b) => [b.poi.id, { stand: b.stand, ueberfaellig: b.ueberfaellig }]));
      this.treffer = gefiltert ? new Set(gefiltert.befunde.map((b) => b.poi.id)) : null;
      for (const cb of this.hoerer) cb();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }

  /** Kennzeichen als Text (Status, Frist, überfällig); null ohne bekannten Stand. */
  kennzeichen(serverId: string | undefined): string | null {
    const x = serverId === undefined ? undefined : this.je.get(serverId);
    return x ? kennzeichen(x.stand, x.ueberfaellig) : null;
  }

  /** Kurzform für das Schild am Marker; null ohne bekannten Stand. */
  kurz(serverId: string | undefined): string | null {
    const x = serverId === undefined ? undefined : this.je.get(serverId);
    return x ? kennzeichenKurz(x.stand, x.ueberfaellig) : null;
  }

  /** Passt der Befund zum Filter? Ohne Filter alle; mit Filter nur Treffer des Servers (lokale POIs nie). */
  passt(serverId: string | undefined): boolean {
    if (this.treffer === null) return true;
    return serverId !== undefined && this.treffer.has(serverId);
  }

  onAenderung(cb: () => void): void {
    this.hoerer.push(cb);
  }
}
