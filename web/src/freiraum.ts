// Freie Stelle für schwebende Hinweise (Meldungszeile, Scan-Hinweis) zwischen Panels und Leisten — Desktop und
// Tablet; auf dem Handy legt toolbar.css feste Zonen fest. Reine Geometrie, der DOM-Teil steht in main.ts.

export interface Rechteck { left: number; top: number; right: number; bottom: number }

/** Breiteste freie waagrechte Spanne im Band [oben, unten] eines Fensters der Breite `breite`, mit `rand` Abstand
 *  zu Hindernissen und Fensterrand. Hindernisse außerhalb des Bands und leere Rechtecke zählen nicht. Ohne Platz:
 *  eine Spanne der Breite 0 am linken Rand. */
export function freieSpanne(breite: number, oben: number, unten: number, hindernisse: readonly Rechteck[], rand = 8): { left: number; right: number } {
  const belegt = hindernisse
    .filter((h) => h.right > h.left && h.bottom > h.top && h.top < unten && h.bottom > oben)
    .map((h) => [h.left - rand, h.right + rand] as const)
    .sort((a, b) => a[0] - b[0]);
  let beste = { left: rand, right: rand };
  let x = rand;
  for (const [l, r] of [...belegt, [breite - rand, Infinity] as const]) {
    if (l - x > beste.right - beste.left) beste = { left: x, right: l };
    x = Math.max(x, r);
  }
  return beste;
}
