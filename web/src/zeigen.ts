// Schlanke Oberfläche der Profile „ansehen“ und „messen“ (profil.ts, P08-S6); `?zeigen=1` ist der Alias für
// „ansehen“ (Show-Modus für fremde Handys: QR-Code am Stand, Gast-Link). Alles, was zum Arbeiten gehört
// (Werkzeugleiste, Panels, Karte, Hinweise), blendet die Klasse `sv-zeigen` am body aus (styles/zeigen.css) und
// main.ts macht es zusätzlich `inert`; übrig bleiben die 3D-Ansicht und eine schmale Leiste unten: Objektname,
// Textur/Grau, Messen (eine Strecke; im Profil „messen“ die vier Messwerkzeuge), ein kleines Einstellmenü und der
// Hinweis auf siteBits. Der Viewer darunter läuft unverändert — die Profile nehmen nur Bedienelemente weg.

/** Show-Modus aus der Adresse: genau `zeigen=1`. */
export function zeigenAn(search: string): boolean {
  return new URLSearchParams(search).get('zeigen') === '1';
}

/** Kinder des body, die in den schlanken Profilen bleiben. Dieselbe Liste steht in styles/zeigen.css im `:not(…)`
 *  (CSS kann sie nicht einbinden; tests/zeigen.test.ts hält beide gleich). */
export const ZEIGEN_BLEIBT = '#view, #msg, #ansage, .sv-zeigen-leiste, .sv-rueckfrage, dialog, script';

/** Zweite Sicherung neben display:none (zeigen.css): alle übrigen Kinder von `body` werden `inert`, also nicht
 *  fokussierbar und für Hilfstechnik nicht da — auch später angehängte (Beobachter auf die Kinderliste). */
export function sichereAusgeblendete(body: HTMLElement, Beobachter: typeof MutationObserver = MutationObserver): MutationObserver {
  const sichere = (k: Element): void => {
    if (k instanceof HTMLElement && !k.matches(ZEIGEN_BLEIBT)) k.inert = true;
  };
  for (const k of body.children) sichere(k);
  const b = new Beobachter((liste) => {
    for (const m of liste) for (const n of m.addedNodes) if (n.parentNode === body && n instanceof Element) sichere(n);
  });
  b.observe(body, { childList: true });
  return b;
}

export type Messwerkzeug = 'distance' | 'polyline' | 'area' | 'height';
const WERKZEUG_TEXT: Record<Messwerkzeug, [string, string]> = {
  distance: ['Strecke', 'Zwei Punkte antippen: Strecke in Metern'],
  polyline: ['Linienzug', 'Punkte antippen, mit Enter oder Doppeltipp abschließen'],
  area: ['Fläche', 'Eckpunkte antippen, mit Enter oder Doppeltipp abschließen'],
  height: ['Höhe', 'Zwei Punkte antippen: Höhenunterschied in Metern'],
};

/** Einstellmenü der schlanken Profile. Jeder Eintrag ist optional und erscheint nur, wenn das Paket ihn hergibt. */
export interface EinstellOptionen {
  /** Heller statt dunkler Hintergrund. */
  hintergrund?: { setzen(hell: boolean): void };
  /** Sonnenlicht mit Schatten (nur Meshes mit Georeferenz). */
  licht?: { an: boolean; setzen(an: boolean): void };
  /** Punktgröße der Wolke in Pixeln (1–8). */
  punktgroesse?: { wert: number; setzen(px: number): void };
  /** Vollbild umschalten; nur, wo der Browser es für Elemente kann (iPhone: nein). */
  vollbild?: { an(): boolean; umschalten(): void };
  /** Info zum Projekt (Zeilen, schlichter Text). */
  info: string[];
  /** Steuerungshilfe öffnen. */
  hilfe(): void;
}

export interface ZeigeLeisteOptionen {
  titel: string;
  /** Nur mit Mesh-Ebenen: Umschalter Textur/Grau. `setzen(true)` = Grau. */
  grau?: { setzen(an: boolean): void };
  /** Profil „ansehen“: Messen ein (Strecke) oder aus (zurück zum Navigieren, Strecke weg). `onZustand`: von außen
   *  gewechselt (Taste d, Ende der Messung) → Knopf folgt. */
  messen: { setzen(an: boolean): void; onZustand(cb: (an: boolean) => void): void };
  /** Profil „messen“: die Messwerkzeuge statt des einen Knopfs. `waehlen(null)` = Navigieren; `onWechsel` meldet
   *  Wechsel von außen (Taste, Ende einer Messung). `loeschen` entfernt alle Messungen. */
  werkzeuge?: {
    liste: readonly Messwerkzeug[];
    waehlen(w: Messwerkzeug | null): void;
    onWechsel(cb: (w: Messwerkzeug | null) => void): void;
    loeschen(): void;
  };
  einstellungen?: EinstellOptionen;
}

function knopf(text: string): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  b.setAttribute('aria-pressed', 'false');
  return b;
}

function schalter(text: string, an: boolean, setzen: (an: boolean) => void): HTMLLabelElement {
  const l = document.createElement('label');
  l.className = 'sv-zeigen-zeile';
  const c = document.createElement('input');
  c.type = 'checkbox';
  c.checked = an;
  c.addEventListener('change', () => setzen(c.checked));
  l.append(c, document.createTextNode(text));
  return l;
}

let menueNr = 0;

/** Einstellmenü als Aufklapp-Bereich (Knopf mit aria-expanded/aria-controls, darin schlichte Bedienelemente; kein
 *  role="menu", das verlangte eine eigene Pfeiltasten-Führung). Esc schließt und gibt den Fokus an den Knopf zurück.
 *  `data-tasten-eigen`: die Kürzel des Viewers (keymap.ts) greifen dort nicht (Pfeiltasten am Regler, Ziffern). */
function einstellMenue(o: EinstellOptionen): { knopf: HTMLButtonElement; bereich: HTMLElement } {
  const id = `sv-zeigen-menue-${++menueNr}`;
  const auf = document.createElement('button');
  auf.type = 'button';
  auf.textContent = 'Einstellungen';
  auf.setAttribute('aria-expanded', 'false');
  auf.setAttribute('aria-controls', id);
  const bereich = document.createElement('div');
  bereich.id = id;
  bereich.className = 'sv-zeigen-menue';
  bereich.setAttribute('role', 'group');
  bereich.setAttribute('aria-label', 'Einstellungen');
  bereich.dataset.tastenEigen = '';
  bereich.hidden = true;
  const setzeOffen = (offen: boolean): void => {
    bereich.hidden = !offen;
    auf.setAttribute('aria-expanded', String(offen));
  };
  auf.addEventListener('click', () => {
    const offen = bereich.hidden;
    setzeOffen(offen);
    if (offen) bereich.querySelector<HTMLElement>('input, button')?.focus();
  });
  bereich.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    setzeOffen(false);
    auf.focus();
  });

  if (o.hintergrund) {
    const h = o.hintergrund;
    bereich.append(schalter('Heller Hintergrund', false, (an) => h.setzen(an)));
  }
  if (o.licht) {
    const l = o.licht;
    bereich.append(schalter('Sonnenlicht und Schatten', l.an, (an) => l.setzen(an)));
  }
  if (o.punktgroesse) {
    const pg = o.punktgroesse;
    const l = document.createElement('label');
    l.className = 'sv-zeigen-zeile';
    const r = document.createElement('input');
    r.type = 'range';
    r.min = '1';
    r.max = '8';
    r.step = '1';
    r.value = String(pg.wert);
    r.addEventListener('input', () => pg.setzen(Number(r.value)));
    l.append(document.createTextNode('Punktgröße'), r);
    bereich.append(l);
  }
  if (o.vollbild) {
    const vb = o.vollbild;
    const b = knopf('Vollbild');
    b.addEventListener('click', () => vb.umschalten());
    const folgen = (): void => b.setAttribute('aria-pressed', String(vb.an()));
    document.addEventListener('fullscreenchange', folgen);
    document.addEventListener('webkitfullscreenchange', folgen);
    bereich.append(b);
  }
  const hilfe = document.createElement('button');
  hilfe.type = 'button';
  hilfe.textContent = 'Steuerungshilfe';
  hilfe.addEventListener('click', () => o.hilfe());
  bereich.append(hilfe);
  if (o.info.length) {
    const info = document.createElement('div');
    info.className = 'sv-zeigen-info';
    for (const z of o.info) {
      const p = document.createElement('p');
      p.textContent = z;
      info.append(p);
    }
    bereich.append(info);
  }
  return { knopf: auf, bereich };
}

export function attachZeigeLeiste(parent: HTMLElement, o: ZeigeLeisteOptionen): HTMLElement {
  const leiste = document.createElement('div');
  leiste.className = 'sv-zeigen-leiste';
  leiste.setAttribute('role', 'toolbar');
  leiste.setAttribute('aria-label', 'Ansicht');
  const titel = document.createElement('span');
  titel.className = 'sv-zeigen-titel';
  titel.textContent = o.titel;
  titel.title = o.titel;
  const knoepfe = document.createElement('div');
  knoepfe.className = 'sv-zeigen-knoepfe';
  if (o.grau) {
    const grau = o.grau;
    const g = knopf('Grau');
    g.title = 'Form ohne Fototextur zeigen';
    g.addEventListener('click', () => {
      const an = g.getAttribute('aria-pressed') !== 'true';
      g.setAttribute('aria-pressed', String(an));
      grau.setzen(an);
    });
    knoepfe.append(g);
  }
  if (o.werkzeuge) {
    const w = o.werkzeuge;
    const knoepfeW = new Map<Messwerkzeug, HTMLButtonElement>();
    const zeige = (aktiv: Messwerkzeug | null): void => {
      for (const [id, b] of knoepfeW) b.setAttribute('aria-pressed', String(id === aktiv));
    };
    for (const id of w.liste) {
      const [text, hilfe] = WERKZEUG_TEXT[id];
      const b = knopf(text);
      b.title = hilfe;
      b.addEventListener('click', () => {
        const neu = b.getAttribute('aria-pressed') === 'true' ? null : id;
        zeige(neu);
        w.waehlen(neu);
      });
      knoepfeW.set(id, b);
      knoepfe.append(b);
    }
    w.onWechsel(zeige);
    const weg = document.createElement('button');
    weg.type = 'button';
    weg.textContent = 'Löschen';
    weg.title = 'Alle Messungen entfernen';
    weg.addEventListener('click', () => w.loeschen());
    knoepfe.append(weg);
  } else {
    const m = knopf('Messen');
    m.title = 'Zwei Punkte antippen: Strecke in Metern';
    const setzeMessen = (an: boolean): void => {
      m.setAttribute('aria-pressed', String(an));
      o.messen.setzen(an);
    };
    m.addEventListener('click', () => setzeMessen(m.getAttribute('aria-pressed') !== 'true'));
    o.messen.onZustand((an) => m.setAttribute('aria-pressed', String(an)));
    knoepfe.append(m);
  }
  const marke = document.createElement('a');
  marke.className = 'sv-zeigen-marke';
  marke.href = 'https://sitebits.de';
  marke.target = '_blank';
  marke.rel = 'noopener';
  marke.textContent = 'siteBits · sitebits.de';
  if (o.einstellungen) {
    const { knopf: auf, bereich } = einstellMenue(o.einstellungen);
    knoepfe.append(auf);
    leiste.append(bereich);
  }
  leiste.append(titel, knoepfe, marke);
  parent.append(leiste);
  return leiste;
}
