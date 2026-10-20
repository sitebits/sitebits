// Fadenkreuz im Begehen (DOM): Kreuz in Bildmitte, Ring am projizierten Vorschaupunkt und eine Platte mit
// dem Live-Wert darunter. Die Vorschau zeigt nur an — gemessen wird allein über die feine Suche.
// Im Panorama gilt der Zustand `neutral`: nur das Kreuz, ohne Ring und Platte (ein Panorama hat keine Tiefe
// des sichtbaren Bildes, also keine Vorschau und keinen Live-Wert).
// Texte nur per textContent, Stile in styles/fadenkreuz.css; Größe und Positionen per CSSOM.

export type FadenkreuzZustand = 'treffer' | 'kein' | 'rueckfrage' | 'neutral';

export interface Fadenkreuz {
  zeige(an: boolean): void;
  groesse(px: number): void;
  /** Zielen: Kreuz in der Form `.sv-zielen`. */
  zielen(an: boolean): void;
  /** `text` ist der gesprochene Text; `kurz` (Platte) fällt auf ihn zurück. `frisch: false` = Anzeige aus
   *  einer wiederverwendeten Vorschau: der Zeitstempel `data-t` bleibt dann stehen. */
  aktualisiere(o: {
    zustand: FadenkreuzZustand;
    punktBildschirm: { x: number; y: number } | null;
    text: string | null;
    kurz?: string | null;
    frisch?: boolean;
  }): void;
  aktuellerText(): string | null;
}

export function createFadenkreuz(root: HTMLElement): Fadenkreuz {
  const kreuz = document.createElement('div');
  kreuz.className = 'sv-fadenkreuz';
  kreuz.setAttribute('aria-hidden', 'true');
  kreuz.hidden = true;
  const ring = document.createElement('div');
  ring.className = 'sv-zielmarke';
  ring.setAttribute('aria-hidden', 'true');
  ring.hidden = true;
  const platte = document.createElement('div');
  platte.className = 'sv-livewert';
  platte.setAttribute('aria-hidden', 'true');
  platte.hidden = true;
  const symbol = document.createElement('span');
  symbol.className = 'sv-livewert-symbol';
  const wert = document.createElement('span');
  wert.className = 'sv-livewert-text';
  platte.append(symbol, wert);
  root.append(kreuz, ring, platte);

  let px = 0;
  let text: string | null = null;
  let an = false;

  return {
    zeige(auf) {
      an = auf;
      kreuz.hidden = !auf;
      if (!auf) {
        ring.hidden = true;
        platte.hidden = true;
        text = null;
      }
    },
    zielen(z) {
      kreuz.classList.toggle('sv-zielen', z);
    },
    groesse(n) {
      if (n === px) return;
      px = n;
      root.style.setProperty('--sv-fadenkreuz', `${n}px`);
    },
    aktualisiere(o) {
      if (!an) return;
      text = o.text;
      kreuz.dataset.zustand = o.zustand;
      const p = o.zustand === 'treffer' ? o.punktBildschirm : null;
      ring.hidden = p === null;
      if (p) {
        ring.style.left = `${p.x}px`;
        ring.style.top = `${p.y}px`;
      }
      const anzeige = o.kurz ?? o.text;
      platte.hidden = anzeige === null;
      platte.dataset.zustand = o.zustand;
      // Farbe nie allein: bei kein Treffer Symbol und Wort.
      symbol.textContent = o.zustand === 'kein' ? '⊘' : '';
      wert.textContent = anzeige ?? '';
      if (o.frisch !== false) platte.dataset.t = String(performance.now());
    },
    aktuellerText: () => text,
  };
}
