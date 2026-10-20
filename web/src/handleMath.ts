// Griffe der Messungen auf dem Bildschirm: welcher Eckpunkt bzw. Mittelpunkt-Griff liegt unter
// dem Zeiger? Rein (Vitest); die Projektion Welt → Bildschirm macht measure.ts.
// Mittelpunkt-Griffe (Polylinie/Fläche, zwischen zwei Eckpunkten) fügen beim Ziehen/Klicken einen Eckpunkt
// ein; ein Eckpunkt im Umkreis hat immer Vorrang, damit kurze Kanten ihre Eckpunkte nicht verdecken.
export interface Handle {
  /** Messungs-ID. */
  id: string;
  /** Eckpunkt: Index in points; Mittelpunkt: Einfügeindex (zwischen index−1 und index). */
  index: number;
  kind: 'vertex' | 'midpoint';
  x: number;
  y: number;
  /** Sichtbarer Radius des Markers [px] (Kugel in Weltmetern, markerMath.ts): ist er größer als der
   *  Fangradius, greift der ganze sichtbare Marker. */
  r?: number;
}

/** Fangradius für Griffe [px] (größer als die 4-px-Klickregel, kleiner als der 16-px-Pickkreis). */
export const HANDLE_HIT_PX = 8;

export function nearestHandle(handles: readonly Handle[], x: number, y: number, maxPx = HANDLE_HIT_PX): Handle | null {
  const pick = (kind: Handle['kind']): Handle | null => {
    let best: Handle | null = null;
    let bestD = Infinity;
    for (const hd of handles) {
      if (hd.kind !== kind) continue;
      const d = Math.hypot(hd.x - x, hd.y - y);
      if (d <= Math.max(maxPx, hd.r ?? 0) && d <= bestD) {
        best = hd;
        bestD = d;
      }
    }
    return best;
  };
  return pick('vertex') ?? pick('midpoint');
}

/** Fang auf den ersten Eckpunkt einer offenen Fläche [px]: Ein Klick so nah daran schließt sie. Großzügiger als
 *  HANDLE_HIT_PX, mit dem Finger am meisten (Rückmeldung iPhone 17.10.: Schließen traf man kaum). */
export const SCHLIESSEN_PX = { maus: 16, touch: 36 } as const;

/** Schließt ein Klick bei (x, y) die offene Fläche? Erst ab drei Eckpunkten; `start` = erster Eckpunkt auf dem
 *  Bildschirm (null = hinter der Kamera). */
export function schliesstFlaeche(start: { x: number; y: number } | null, x: number, y: number, eckpunkte: number,
  touch: boolean): boolean {
  if (!start || eckpunkte < 3) return false;
  return Math.hypot(start.x - x, start.y - y) <= (touch ? SCHLIESSEN_PX.touch : SCHLIESSEN_PX.maus);
}
