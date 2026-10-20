// Dekodierte Knoten der Strahlsuche über Klicks hinweg (rayPick.ts) — reine Logik ohne three.js/Giro3D (Vitest).
//
// Vorher dekodierte jede Suche ihre Knoten neu: auf dem Prüfpaket „Wand mit Öffnung“ 190–530 ms je Suche bei
// 500 ms Budget (FINE_BUDGET_MS). Zwei schnelle Setz-Eingaben oder die zweite Suche mit Tiefenfenster nach einer
// Rückfrage rissen das Budget → „grobe Stufe“ bzw. kein Punkt (gemessen 07.10.2026, zielpunkt.spec.ts). Geteilt:
// gleichzeitige Suchen warten auf dasselbe Laden, Folgesuchen in derselben Gegend 10–200 ms.

/** LRU-Merker mit Obergrenze in Bytes. Noch ladende Einträge zählen nicht und werden nie verdrängt;
 *  Fehler werden nicht gemerkt. Ein Eintrag über der Obergrenze wird geliefert, aber nicht behalten. */
export interface KnotenCache<T> {
  get: (key: string, laden: () => Promise<T>) => Promise<T>;
  /** Belegte Bytes der fertigen Einträge. */
  bytes: () => number;
}

export function knotenCache<T>(maxBytes: number, bytesOf: (v: T) => number): KnotenCache<T> {
  type Eintrag = { p: Promise<T>; bytes: number; fertig: boolean };
  // Map hält die Einfügereihenfolge: vorn der am längsten unbenutzte Eintrag.
  const map = new Map<string, Eintrag>();
  let summe = 0;
  const verdraengen = (): void => {
    for (const [key, e] of map) {
      if (summe <= maxBytes) return;
      if (!e.fertig) continue;
      map.delete(key);
      summe -= e.bytes;
    }
  };
  return {
    get(key, laden) {
      const da = map.get(key);
      if (da) {
        map.delete(key);
        map.set(key, da);
        return da.p;
      }
      const e: Eintrag = { p: laden(), bytes: 0, fertig: false };
      map.set(key, e);
      e.p.then(
        (v) => {
          if (map.get(key) !== e) return;
          e.fertig = true;
          e.bytes = bytesOf(v);
          // Zu groß zum Behalten: nicht erst alle anderen verdrängen.
          if (e.bytes > maxBytes) {
            map.delete(key);
            return;
          }
          summe += e.bytes;
          verdraengen();
        },
        () => {
          if (map.get(key) === e) map.delete(key);
        },
      );
      return e.p;
    },
    bytes: () => summe,
  };
}
