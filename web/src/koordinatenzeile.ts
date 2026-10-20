// Statuszeile „Koordinate am Cursor“ (F23): unten links, standardmäßig aus, Taste und Ansichtslink (ko=1).
// Während der Bewegung zeigt sie die Vorschau des GPU-Picks (dieselbe wie am Fadenkreuz, nur Anzeige), höchstens
// alle VORSCHAU_TAKT_MS. Ruht der Zeiger RUHE_MS, holt eine feine Suche den Punkt wie ein Messklick (im Panorama
// mit den Panorama-Optionen) und hält ihn fest — kopiert wird immer dieser festgehaltene Punkt. Eigener Weg
// neben measure.ts: kein Eintrag in dessen Warteschlange oder Pick-Zähler. Kein Pick bei gedrückter Taste, beim
// Ziehen eines Griffs und unter Pointer-Lock (Begehen). Texte nur per textContent.
import { beschriftung, koordinateAnzeige, koordinateKopie } from './koordinaten';
import type { Frame, Vec3 } from './manifest';
import { panoPickOptions } from './rayPick';
import type { PickSource } from './rayPick';

export const VORSCHAU_TAKT_MS = 100;
export const RUHE_MS = 300;
const LEER = '–';

export interface Koordinatenzeile {
  an(): boolean;
  setzeAn(an: boolean): void;
  /** Festgehaltenen Punkt kopieren und ansagen; ohne Punkt nur ansagen. */
  kopiere(): Promise<void>;
  /** Festgehaltener Punkt (E2E). */
  punkt(): Vec3 | null;
}

export function attachKoordinatenzeile(o: {
  canvas: HTMLElement;
  frame: Frame;
  source: PickSource;
  inPanorama: () => boolean;
  /** Pick ruht: Griff wird gezogen, Pointer-Lock (Begehen). */
  gesperrt: () => boolean;
  melde: (text: string) => void;
  /** Nach dem Ein-/Ausschalten (Ansichtslink). */
  onToggle: (an: boolean) => void;
  anfangs: boolean;
}): Koordinatenzeile {
  const b = beschriftung(o.frame.crs, o.frame.undulation);
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'sv-koordinaten';
  el.title = 'Koordinate kopieren';
  el.setAttribute('aria-label', 'Koordinate am Zeiger, Klick kopiert');
  document.body.append(el);

  let ein = o.anfangs;
  let festgehalten: Vec3 | null = null;
  let letzteVorschau = 0;
  let ruhe: ReturnType<typeof setTimeout> | undefined;
  let lauf = 0;

  const zeige = (p: Vec3 | null): void => {
    el.textContent = p ? koordinateAnzeige(p, b) : LEER;
  };
  const sichtbar = (): void => {
    el.hidden = !ein;
    if (!ein) {
      clearTimeout(ruhe);
      lauf++;
    }
  };

  const abbrechen = (): void => {
    clearTimeout(ruhe);
    lauf++;
  };
  o.canvas.addEventListener('pointermove', (ev) => {
    if (!ein) return;
    // Ziehen (Orbit, Griff) oder Begehen: kein Feinpick an einer Stelle, an der der Zeiger nicht mehr steht.
    if (ev.buttons !== 0 || o.gesperrt()) return abbrechen();
    const { clientX: x, clientY: y } = ev;
    const t = performance.now();
    if (!o.inPanorama() && t - letzteVorschau >= VORSCHAU_TAKT_MS) {
      letzteVorschau = t;
      const v = o.source.vorschauAtScreen(x, y).punktWelt;
      zeige(v ? [v[0], v[1], v[2]] : null);
    }
    clearTimeout(ruhe);
    const n = ++lauf;
    ruhe = setTimeout(() => {
      if (!ein || o.gesperrt()) return;
      void o.source.pickAtScreen(x, y, o.inPanorama() ? panoPickOptions() : {}).then((r) => {
        if (n !== lauf) return; // Zeiger hat sich inzwischen bewegt
        festgehalten = r.point ? [r.point[0], r.point[1], r.point[2]] : null;
        zeige(festgehalten);
      });
    }, RUHE_MS);
  });
  o.canvas.addEventListener('pointerdown', abbrechen);
  // Auf dem Weg zur Zeile hat die Vorschau weitergezählt; angezeigt wird wieder, was ein Klick kopiert.
  o.canvas.addEventListener('pointerleave', () => {
    abbrechen();
    zeige(festgehalten);
  });

  const kopiere = async (): Promise<void> => {
    if (!festgehalten) return o.melde('Keine Koordinate festgehalten');
    const text = koordinateKopie(festgehalten, b);
    try {
      await navigator.clipboard.writeText(text);
      o.melde(`Koordinate kopiert: ${koordinateAnzeige(festgehalten, b)}`);
    } catch {
      o.melde('Kopieren nicht möglich');
    }
  };
  el.addEventListener('click', () => void kopiere());

  zeige(null);
  sichtbar();
  return {
    an: () => ein,
    setzeAn: (an) => {
      if (an === ein) return;
      ein = an;
      sichtbar();
      o.melde(an ? 'Koordinatenanzeige ein' : 'Koordinatenanzeige aus');
      o.onToggle(an);
    },
    kopiere,
    punkt: () => festgehalten,
  };
}
