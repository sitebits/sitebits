// Stufen eines Panoramas laden — ohne three.js, I/O injiziert (Vitest mit Fakes).
//  - Schlüssel "<station>/<face>/<level>".
//  - setStation(): neue Generation; laufende Anfragen werden abgebrochen (AbortController), verspätete
//    Ergebnisse verworfen (Bild geschlossen, nie angezeigt), Texturen anderer Standpunkte freigegeben.
//  - want(): ersetzt die Warteschlange (Aufruferreihenfolge = Priorität) und heftet genau die gewünschten
//    Stufen im Budget an — eine gewünschte Stufe wird nie verdrängt; passt eine neue nicht, ist sie
//    "failed" (reason "Texturbudget") statt ein Nachlade-Pingpong auszulösen.
//  - Höchstens MAX_INFLIGHT Anfragen gleichzeitig: von 6 HTTP/1.1-Verbindungen je Host bleiben 2 für
//    das COPC-Streaming der Punktwolke.
//  - Fehlgeschlagene Stufen (404, Dekodierfehler, Ausnahme beim Hochladen) werden je Standpunkt gemerkt
//    und nicht erneut angefragt (kein Endlos-Nachladen); reset() leert die Liste. Am Budget gescheiterte
//    Stufen stehen getrennt davon: want() fragt sie wieder an, sobald sie neben den angehefteten Stufen
//    passen (frei gewordener Platz) — bei unveränderter Lage nicht (kein Pingpong).
//  - want() bricht Downloads ab, die nicht mehr gewünscht sind (Blick gedreht); das ist kein Fehler.
//    Verspätete Antworten abgebrochener Downloads werden verworfen (Controller-Identität, nicht nur
//    Generation — createImageBitmap ist nicht abbrechbar).
//  - Eine Ausnahme im Erfolgszweig (makeTexture, Budget, Ereignis-Rückruf) verliert weder den
//    Download-Platz noch das Bild: Bild/Textur frei, "failed", Warteschlange läuft weiter.
//  - reset(): nach WebGL-Kontextverlust alles verwerfen (GPU-Inhalt ist weg).
import { TextureBudget } from './textureBudget';
import type { FaceName } from './stations';

export const MAX_INFLIGHT = 4;

export interface LevelRequest {
  station: string;
  face: FaceName;
  level: number;
  url: string;
}

export interface LoaderIo<B, T> {
  fetchImage(url: string, signal: AbortSignal): Promise<B>;
  makeTexture(img: B): T;
  discardImage(img: B): void;
  disposeTexture(tex: T): void;
  bytesOf(level: number): number;
}

export type LoaderEvent =
  | { type: 'loaded'; station: string; face: FaceName; level: number }
  | { type: 'failed'; station: string; face: FaceName; level: number; reason: string };

export const levelKey = (station: string, face: FaceName, level: number): string => `${station}/${face}/${level}`;

export class PanoLoader<B, T> {
  private readonly budget: TextureBudget<T>;
  private station: string | null = null;
  private generation = 0;
  private queue: LevelRequest[] = [];
  private wanted = new Set<string>();
  private readonly running = new Map<string, AbortController>();
  private readonly failed = new Set<string>();
  /** Am Budget gescheitert: Schlüssel → Bytes der Stufe (für die erneute Machbarkeitsprüfung). */
  private readonly overBudget = new Map<string, number>();

  constructor(
    private readonly io: LoaderIo<B, T>,
    limitBytes: number,
    private readonly onEvent: (e: LoaderEvent) => void,
  ) {
    this.budget = new TextureBudget<T>(limitBytes, (t) => io.disposeTexture(t));
  }

  get usedBytes(): number {
    return this.budget.usedBytes;
  }

  get inflight(): number {
    return this.running.size;
  }

  keys(): string[] {
    return this.budget.keys();
  }

  /** Alle gescheiterten Stufen des Standpunkts — nicht ladbar und (derzeit) nicht ins Budget passend. */
  failures(): string[] {
    return [...this.failed, ...this.overBudget.keys()];
  }

  setStation(id: string | null): void {
    if (id === this.station) return;
    this.abortAll();
    this.failed.clear();
    this.overBudget.clear();
    this.station = id;
    this.budget.retain((k) => id !== null && k.startsWith(`${id}/`));
  }

  want(requests: readonly LevelRequest[]): void {
    const mine = requests.filter((r) => r.station === this.station);
    this.wanted = new Set(mine.map((r) => levelKey(r.station, r.face, r.level)));
    for (const k of this.budget.keys()) this.budget.pin(k, this.wanted.has(k));
    for (const [k, c] of [...this.running]) {
      if (this.wanted.has(k)) continue;
      c.abort();
      this.running.delete(k);
    }
    for (const [k, bytes] of [...this.overBudget]) {
      if (!this.wanted.has(k) || this.budget.fits(bytes)) this.overBudget.delete(k);
    }
    this.queue = mine.filter((r) => {
      const k = levelKey(r.station, r.face, r.level);
      return !this.budget.has(k) && !this.failed.has(k) && !this.overBudget.has(k) && !this.running.has(k);
    });
    this.pump();
  }

  best(station: string, face: FaceName, levels: readonly number[]): { level: number; texture: T } | null {
    for (let i = levels.length - 1; i >= 0; i--) {
      const level = levels[i]!;
      const texture = this.budget.get(levelKey(station, face, level));
      if (texture !== undefined) return { level, texture };
    }
    return null;
  }

  reset(): void {
    this.abortAll();
    this.failed.clear();
    this.overBudget.clear();
    this.budget.clear();
  }

  private abortAll(): void {
    this.generation++;
    for (const c of this.running.values()) c.abort();
    this.running.clear();
    this.queue = [];
  }

  private emit(e: LoaderEvent): void {
    try {
      this.onEvent(e);
    } catch (err) {
      // Fehler des Verbrauchers dürfen den Lader nicht anhalten; sichtbar bleiben sie in der Konsole.
      console.error('PanoLoader: Ereignis-Rückruf warf', err);
    }
  }

  private pump(): void {
    while (this.running.size < MAX_INFLIGHT && this.queue.length > 0) {
      const r = this.queue.shift()!;
      const key = levelKey(r.station, r.face, r.level);
      if (this.budget.has(key) || this.failed.has(key) || this.overBudget.has(key) || this.running.has(key)) continue;
      const ctrl = new AbortController();
      this.running.set(key, ctrl);
      const gen = this.generation;
      const { station, face, level } = r;
      // Nur der Download, dem dieser Controller gehört, darf noch etwas bewirken.
      const current = (): boolean => gen === this.generation && this.running.get(key) === ctrl && !ctrl.signal.aborted;
      this.io.fetchImage(r.url, ctrl.signal).then(
        (img) => {
          if (!current()) {
            this.io.discardImage(img);
            return;
          }
          this.running.delete(key);
          let event: LoaderEvent;
          try {
            let tex: T;
            try {
              tex = this.io.makeTexture(img);
            } catch (e) {
              this.io.discardImage(img);
              throw e;
            }
            const bytes = this.io.bytesOf(level);
            if (this.budget.add(key, tex, bytes, this.wanted.has(key))) {
              event = { type: 'loaded', station, face, level };
            } else {
              this.overBudget.set(key, bytes);
              event = { type: 'failed', station, face, level, reason: 'Texturbudget' };
            }
          } catch (e) {
            this.failed.add(key);
            event = { type: 'failed', station, face, level, reason: e instanceof Error ? e.message : String(e) };
          }
          this.emit(event);
          this.pump();
        },
        (e: unknown) => {
          if (!current()) return; // abgebrochen (Standpunktwechsel, reset, nicht mehr gewünscht) — kein Fehler
          this.running.delete(key);
          this.failed.add(key);
          this.emit({ type: 'failed', station, face, level, reason: e instanceof Error ? e.message : String(e) });
          this.pump();
        },
      );
    }
  }
}
