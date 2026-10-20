// Debug-Overlay im Viewer: reine Darstellung — die Werte kommen
// ausschließlich über `quelle()`, kein eigener Zugriff auf window.__scanview hier (Trennung/
// Testbarkeit; die Verdrahtung an den echten Viewer liegt in platform.ts). Inline-Style statt
// eigener CSS-Datei: /ansicht erlaubt laut Caddy-CSP `style-src 'self' 'unsafe-inline'` (siehe
// deploy/platform/scanview.caddy, Kommentar „Inline-Style von lil-gui") — die strenge
// Portal-CSP (kein Inline-Style) betrifft nur portal.css, nicht den Viewer.

export interface DebugWerte {
  fps: number;
  punkte: number;
  anfragenLaufend: number;
  anfragenFehler: number;
  letzteMs: number | null;
  /** Zweite Zeile, z. B. Mesh-Stufe, Grafikspeicher, Gerätegrenzen (Gerätetest iPhone). */
  details?: string;
}

import { istTouch } from './geraet';

const INTERVALL_MS = 500;

const zahl = new Intl.NumberFormat('de-DE');

function formatiere(w: DebugWerte): string {
  const letzte = w.letzteMs === null ? '–' : `${Math.round(w.letzteMs)} ms`;
  return [
    `FPS ${Math.round(w.fps)}`,
    `Punkte ${zahl.format(Math.round(w.punkte))}`,
    `Anfragen ${w.anfragenLaufend} (${w.anfragenFehler} Fehler)`,
    `Letzte ${letzte}`,
  ].join(' · ') + (w.details ? `\n${w.details}` : '');
}

/**
 * Hängt eine kleine Textübersicht (Element `div.sv-debug`) in `parent` ein, aktualisiert 2×/s per
 * setTimeout (kein setInterval — ein langsamer Tick überlappt sich sonst nicht mit dem nächsten).
 * Gibt eine Abbau-Funktion zurück, die den Timer stoppt und das Element entfernt.
 */
export function zeigeDebugOverlay(parent: HTMLElement, quelle: () => DebugWerte, klein: boolean = istTouch()): () => void {
  const el = document.createElement('div');
  el.className = 'sv-debug';
  // Unten links in der Ecke, klein; ein Tipp klappt auf eine Zeile (nur FPS) ein und wieder aus. Auf Touch-Geräten
  // startet sie eingeklappt — rechts unten lag sie auf dem Panel und verdeckte dessen Schalter (iPhone, 17.10.).
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  el.style.cssText = [
    'position:fixed', 'left:calc(4px + env(safe-area-inset-left, 0px))', 'bottom:calc(4px + env(safe-area-inset-bottom, 0px))',
    'z-index:1000', 'cursor:pointer', 'user-select:none', 'max-width:calc(100vw - 8px)', 'overflow:hidden',
    'padding:2px 6px', 'border-radius:4px', 'background:#000b', 'color:#0f0',
    `font:${klein ? 10 : 12}px/1.3 ui-monospace,monospace`, 'white-space:pre',
  ].join(';');
  parent.appendChild(el);

  let eingeklappt = klein;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abgebaut = false;

  const zeichne = (): void => {
    const w = quelle();
    el.textContent = eingeklappt ? `FPS ${Math.round(w.fps)} ▸` : formatiere(w);
    el.setAttribute('aria-label', eingeklappt ? 'Leistungsanzeige aufklappen' : 'Leistungsanzeige einklappen');
  };
  const umschalten = (ev: Event): void => { ev.stopPropagation(); eingeklappt = !eingeklappt; zeichne(); };
  el.addEventListener('click', umschalten);
  el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); umschalten(ev); } });
  const tick = (): void => {
    if (abgebaut) return;
    zeichne();
    timer = setTimeout(tick, INTERVALL_MS);
  };
  tick();

  return () => {
    abgebaut = true;
    if (timer !== undefined) clearTimeout(timer);
    el.remove();
  };
}
