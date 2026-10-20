// Persistenz-Schnittstelle der POIs: PoiPersistence (localStorage, poiStore.ts)
// erfüllt sie unverändert, ServerPoiPersistenz (serverPois.ts) im Plattformmodus. load darf asynchron sein
// (attachPois wartet mit await), save bleibt synchron: null = gespeichert bzw. übernommen, sonst Hinweistext.
import type { PoiStore } from './poiStore';

export interface PoiLadeErgebnis {
  loaded: number;
  renamed: number;
  warning: string | null;
}

/** Server-Angaben zu einem POI (nur Plattformmodus). */
export interface BefundMeta {
  serverId: string;
  /** Revision des Datensatzes, auf dem der POI gesetzt wurde. */
  paketRevision: number | null;
  /** Anzeigename des Urhebers. */
  erstelltVonName: string | null;
  /** Zeitpunkt der letzten Änderung (Server). */
  geaendertAm: number | null;
  /** true = der angemeldete Nutzer darf diesen POI ändern bzw. löschen. */
  eigen: boolean;
}

export interface PoiPersistenz {
  pruefen?(lokal: string, aktion: 'annehmen' | 'verwerfen'): Promise<void>;
  load(store: PoiStore): PoiLadeErgebnis | Promise<PoiLadeErgebnis>;
  save(store: PoiStore, revision: number): string | null;
  /** Server-Angaben zum POI mit dieser lokalen ID, null = keine bekannt. */
  meta?(lokalId: string): BefundMeta | null;
  /** Lokale ID zum Server-POI (sie weicht bei Kollision mit einer Paket-ID ab), null = nicht geladen. */
  lokalId?(serverId: string): string | null;
  /** true = der Server hat einen Stand dieses POIs bestätigt. */
  bestaetigt?(lokalId: string): boolean;
  /** Antwort des Servers auf das Senden eines POIs: fehler null = bestätigt, sonst der Grund der Ablehnung. */
  onErgebnis?(cb: (lokalId: string, fehler: string | null) => void): void;
}
