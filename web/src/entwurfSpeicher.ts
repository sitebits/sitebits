// IndexedDB-Adapter für lokale Befundentwürfe (Datenbank sv-befundentwurf, ein Datensatz je Konto und Projekt:
// geprüfter Entwurf plus verkleinertes Vorschaubild als Blob). Jeder Zugriff ist abgefangen: ohne IndexedDB
// (gesperrt, privates Fenster) gibt es keinen Speicher, Fehler beim Lesen/Schreiben werden zu „kein Entwurf“.
// Löscht das Portal die Datenbank beim Abmelden (auch aus einem anderen Tab), gibt dieser Viewer seine
// Verbindung sofort frei und öffnet sie in dieser Sitzung nicht wieder — spätere Schreibversuche entfallen,
// sonst legte ein offener Viewer die gelöschten Daten neu an. Vitest mit nachgebauter IndexedDB, Rest Playwright.
import { ENTWURF_DB, ENTWURF_STORE, pruefeEntwurf } from './befundEntwurf';
import type { Entwurf } from './befundEntwurf';

export interface EntwurfSpeicher {
  /** null = kein Datensatz; 'ungueltig' = ein Datensatz liegt vor, ist aber nicht anzubieten (löschen). */
  lies(schluessel: string): Promise<{ entwurf: Entwurf; bild: Blob | null } | 'ungueltig' | null>;
  /** bild undefined = ein vorhandenes Vorschaubild behalten, null = keins. */
  schreibe(e: Entwurf, bild?: Blob | null): Promise<void>;
  loesche(schluessel: string): Promise<void>;
}

interface Datensatz {
  entwurf: unknown;
  bild: unknown;
}

/** Höchstens so lange auf das Öffnen warten; danach gilt: kein Speicher. */
const OEFFNEN_MS = 3000;

function oeffne(idb: IDBFactory): Promise<IDBDatabase | null> {
  return new Promise((fertig) => {
    let beendet = false;
    const zeit = setTimeout(() => ende(null), OEFFNEN_MS);
    const ende = (db: IDBDatabase | null): void => {
      beendet = true;
      clearTimeout(zeit);
      fertig(db);
    };
    try {
      const r = idb.open(ENTWURF_DB, 1);
      r.onupgradeneeded = () => {
        // Nach der Frist (z. B. hinter einem Löschen beim Abmelden) nichts mehr anlegen.
        if (beendet) {
          r.transaction?.abort();
          return;
        }
        if (!r.result.objectStoreNames.contains(ENTWURF_STORE)) r.result.createObjectStore(ENTWURF_STORE);
      };
      r.onsuccess = () => {
        // Zu spät: niemand nutzt diese Verbindung; offen bliebe sie ohne Aufsicht und blockierte ein Löschen.
        if (beendet) r.result.close();
        else ende(r.result);
      };
      r.onerror = () => {
        if (!beendet) ende(null);
      };
      // Blockiert (eine ältere Verbindung in einem anderen Tab): auf Erfolg oder die Frist warten.
      r.onblocked = () => undefined;
    } catch {
      ende(null);
    }
  });
}

/** null ohne nutzbares IndexedDB. */
export async function oeffneEntwurfSpeicher(idb: IDBFactory | undefined = globalThis.indexedDB): Promise<EntwurfSpeicher | null> {
  if (!idb) return null;
  const db = await oeffne(idb);
  if (!db) return null;
  // Von fremder Hand ohne Ablage angelegt: nicht nutzbar (ein Neuanlegen bräuchte eine höhere Version).
  if (!db.objectStoreNames.contains(ENTWURF_STORE)) {
    db.close();
    return null;
  }
  let offen = true;
  db.onversionchange = () => {
    offen = false;
    db.close();
  };
  db.onclose = () => {
    offen = false;
  };

  /** Eine Transaktion; Ergebnis erst nach deren Abschluss. Fehler und geschlossene Datenbank → undefined. */
  const transaktion = <T>(modus: IDBTransactionMode, arbeit: (s: IDBObjectStore, setze: (x: T) => void) => void): Promise<T | undefined> =>
    new Promise((fertig) => {
      if (!offen) {
        fertig(undefined);
        return;
      }
      let ergebnis: T | undefined;
      try {
        const tx = db.transaction(ENTWURF_STORE, modus);
        tx.oncomplete = () => fertig(ergebnis);
        tx.onerror = () => fertig(undefined);
        tx.onabort = () => fertig(undefined);
        arbeit(tx.objectStore(ENTWURF_STORE), (x) => {
          ergebnis = x;
        });
      } catch {
        fertig(undefined);
      }
    });

  return {
    lies: async (schluessel) => {
      const roh = await transaktion<unknown>('readonly', (s, setze) => {
        const r = s.get(schluessel);
        r.onsuccess = () => setze(r.result);
      });
      if (roh === undefined) return null;
      const d = (typeof roh === 'object' && roh !== null ? roh : {}) as Partial<Datensatz>;
      const entwurf = pruefeEntwurf(d.entwurf, schluessel);
      if (!entwurf) return 'ungueltig';
      return { entwurf, bild: d.bild instanceof Blob ? d.bild : null };
    },
    schreibe: async (e, bild) => {
      await transaktion<void>('readwrite', (s) => {
        if (bild !== undefined) {
          s.put({ entwurf: e, bild } satisfies Datensatz, e.schluessel);
          return;
        }
        // Vorhandenes Bild in derselben Transaktion übernehmen.
        const r = s.get(e.schluessel);
        r.onsuccess = () => {
          const alt = r.result as Partial<Datensatz> | undefined;
          s.put({ entwurf: e, bild: alt?.bild instanceof Blob ? alt.bild : null } satisfies Datensatz, e.schluessel);
        };
      });
    },
    loesche: async (schluessel) => {
      await transaktion<void>('readwrite', (s) => {
        s.delete(schluessel);
      });
    },
  };
}
