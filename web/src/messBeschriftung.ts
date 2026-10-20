// Messbeschriftungen in der Empfängeransicht: Bedienelemente haben Vorrang. Eine Beschriftung, die unter der
// Werkzeugleiste oder „← Projekte“ läge oder über den Rand der 3D-Ansicht hinausliefe (angeschnitten), wird ganz
// ausgeblendet statt halb gezeigt. Verschieben statt Ausblenden ginge nicht sauber: CSS2DRenderer schreibt die
// Position (style.transform) bei jedem Bild neu. Geprüft wird nach jedem gezeichneten Bild (after-render folgt in
// Giro3D auf labelRenderer.render), die Klasse sv-verdeckt wirkt nur unter body.sv-empfang (empfang.css) — in der
// Werkzeugansicht ändert sich also nichts, auch nicht durch eine stehengebliebene Klasse. Rein: verdeckt() (Vitest).
export interface Rechteck { left: number; top: number; right: number; bottom: number }

/** Spiel in px: bündig anliegend gilt noch als frei (Rundung der Layoutwerte). */
const SPIEL = 1;

const leer = (r: Rechteck): boolean => r.right - r.left <= 0 || r.bottom - r.top <= 0;
const ueberlappt = (a: Rechteck, b: Rechteck): boolean =>
  a.left < b.right - SPIEL && b.left < a.right - SPIEL && a.top < b.bottom - SPIEL && b.top < a.bottom - SPIEL;

/** Beschriftung nicht ganz in der Fläche oder (auch teilweise) unter einem Bedienelement. */
export function verdeckt(beschriftung: Rechteck, flaeche: Rechteck, hindernisse: readonly Rechteck[]): boolean {
  const innen =
    beschriftung.left >= flaeche.left - SPIEL && beschriftung.right <= flaeche.right + SPIEL &&
    beschriftung.top >= flaeche.top - SPIEL && beschriftung.bottom <= flaeche.bottom + SPIEL;
  return !innen || hindernisse.some((h) => !leer(h) && ueberlappt(beschriftung, h));
}

/** Bedienelemente, die über der 3D-Ansicht liegen und Vorrang haben. */
const HINDERNISSE = '#toolbar, .sv-zurueck';

/** Nach jedem Bild: Messbeschriftungen in `flaeche` (#view) gegen die Bedienelemente prüfen, solange `aktiv()`. */
export function attachMessBeschriftung(o: {
  instance: { addEventListener(typ: 'after-render', cb: () => void): void };
  flaeche: HTMLElement;
  aktiv: () => boolean;
}): void {
  o.instance.addEventListener('after-render', () => {
    if (!o.aktiv()) return;
    const beschriftungen = o.flaeche.querySelectorAll<HTMLElement>('.scanview-measure-label');
    if (beschriftungen.length === 0) return;
    const f = o.flaeche.getBoundingClientRect();
    const h = [...document.querySelectorAll(HINDERNISSE)].map((e) => e.getBoundingClientRect());
    for (const b of beschriftungen) {
      // Vom Renderer ausgeblendet (hinter der Kamera): nichts zu prüfen.
      if (b.style.display === 'none') continue;
      b.classList.toggle('sv-verdeckt', verdeckt(b.getBoundingClientRect(), f, h));
    }
  });
}
