// Byte-Bereichs-Getter für COPCSource, der die COPC-Hierarchie in EINER Anfrage vorab lädt.
//
// Befund (Wasserfall-Analyse „Ladeverhalten“): Giro3D 2.0.4 lädt die Hierarchie
// in COPCSource.initializeOnce() über loadSubtree(), das jede Hierarchie-Seite per `await` in einer
// for…of-Schleife nacheinander holt. Die Hierarchie ist nur zwei Ebenen tief (Wurzelseite → 143
// Unterseiten beim Referenzpaket Kirche), die Unterseiten wären nach der Wurzel alle bekannt — trotzdem kostet
// jede Seite eine volle Netz-Latenz. Bei 50 Mbit/s + 40 ms sind das ≈ 6,4 s vor dem ersten Bild.
//
// Die Hierarchie steht in der EVLR copc/1000, und die EVLRs liegen zusammenhängend am Dateiende
// (LAS 1.4: evlrOffset im Header, danach bis zum Dateiende). Dieser Getter liest evlrOffset aus der
// Header-Antwort (copc.js holt zuerst Bytes 0…65535), lädt beim ersten Zugriff hinter evlrOffset den
// ganzen Bereich [evlrOffset, Dateigröße) in einer Anfrage und bedient alle weiteren Zugriffe dort
// aus dem Speicher. Punktdaten-Zugriffe (vor evlrOffset) gehen unverändert direkt ans Netz.
//
// Ohne Giro3D-Import, damit Vitest das Verhalten prüfen kann; die eigentliche Range-Anfrage wird
// hineingereicht (viewer.ts: Giro3D-Fetcher wie in dessen createGetter).

/** Liest die Bytes [begin, end) — `end` exklusiv, wie copc.js' Getter. */
export type RangeGetter = (begin: number, end: number) => Promise<Uint8Array>;

/** Obergrenze für den vorab geladenen EVLR-Bereich; größer → kein Vorabladen, Verhalten wie bisher. */
export const DEFAULT_MAX_PREFETCH_BYTES = 64 * 1024 * 1024;

/** Mindestlänge, um evlrOffset (u64 @ 235) und evlrCount (u32 @ 243) lesen zu können. */
const EVLR_FIELDS_END = 247;

/** EVLR-Lage aus einem LAS-1.4-Header, sonst null (kein LASF, Version < 1.4, zu kurz). */
export function parseEvlrInfo(head: Uint8Array): { evlrOffset: number; evlrCount: number } | null {
  if (head.length < EVLR_FIELDS_END) return null;
  if (String.fromCharCode(head[0]!, head[1]!, head[2]!, head[3]!) !== 'LASF') return null;
  const major = head[24]!;
  const minor = head[25]!;
  if (major !== 1 || minor < 4) return null;
  const dv = new DataView(head.buffer, head.byteOffset, head.byteLength);
  return { evlrOffset: Number(dv.getBigUint64(235, true)), evlrCount: dv.getUint32(243, true) };
}

/**
 * Getter, der den EVLR-Bereich [evlrOffset, fileSize) beim ersten Zugriff dorthin einmal lädt.
 * Scheitert das Vorabladen (Netzfehler, falsche Länge), fällt er dauerhaft auf Einzelanfragen zurück.
 * Rückgaben sind Kopien (slice): der Worker-Pfad von Giro3D überträgt `chunk.buffer` an einen Worker
 * und würde sonst den Zwischenspeicher mit ablösen.
 */
export function createPrefetchingGetter(
  fetchRange: RangeGetter,
  fileSize: number,
  maxPrefetchBytes = DEFAULT_MAX_PREFETCH_BYTES,
): RangeGetter {
  let evlrOffset: number | null = null;
  let tail: Promise<Uint8Array> | null = null;
  let failed = false;

  const fromTail = async (begin: number, end: number): Promise<Uint8Array | null> => {
    if (evlrOffset === null || failed || begin < evlrOffset || end > fileSize) return null;
    const start = evlrOffset;
    tail ??= fetchRange(start, fileSize).then((data) => {
      if (data.length !== fileSize - start) throw new Error('EVLR-Vorabladen: unerwartete Länge');
      return data;
    });
    try {
      const data = await tail;
      return data.slice(begin - start, end - start);
    } catch {
      failed = true;
      tail = null;
      return null;
    }
  };

  return async (begin, end) => {
    const cached = await fromTail(begin, end);
    if (cached) return cached;
    const data = await fetchRange(begin, end);
    if (begin === 0 && evlrOffset === null) {
      const info = parseEvlrInfo(data);
      if (
        info &&
        info.evlrCount > 0 &&
        info.evlrOffset > 0 &&
        info.evlrOffset < fileSize &&
        fileSize - info.evlrOffset <= maxPrefetchBytes
      ) {
        evlrOffset = info.evlrOffset;
      }
    }
    return data;
  };
}

/**
 * Schranke vor den Netzanfragen der Punktwolke: Solange ein Panorama bis zum ersten Bild
 * lädt, gehen keine neuen COPC-Bereichsanfragen ans Netz — sie warten in `wait()` und laufen nach
 * `open()` unverändert weiter (Giro3D sieht nur eine langsamere Antwort). Bereits laufende Anfragen
 * bleiben unberührt.
 */
export interface Gate {
  readonly closed: boolean;
  close(): void;
  open(): void;
  wait(): Promise<void>;
}

export function createGate(): Gate {
  let pending: { promise: Promise<void>; release: () => void } | null = null;
  return {
    get closed() {
      return pending !== null;
    },
    close() {
      if (pending) return;
      let release!: () => void;
      const promise = new Promise<void>((r) => (release = r));
      pending = { promise, release };
    },
    open() {
      pending?.release();
      pending = null;
    },
    wait: () => pending?.promise ?? Promise.resolve(),
  };
}
