import type { PanelHost } from './panels';
import type { FotoLayout } from './layoutStore';
import { anteilAusZeiger, grenzen, klemme, schritt, TRENNER_PX } from './aufteilung';
import type { Lage } from './aufteilung';
import type { Viewer } from './viewer';

/** Layout, Canvas und Projektion müssen bereits vor dem nächsten Klick übereinstimmen. */
export function synchronisiereFotoLeinwand(v: Viewer): void {
  const { clientWidth: w, clientHeight: h } = v.instance.viewport;
  const size = v.instance.engine.getWindowSize();
  if (w <= 0 || h <= 0 || (size.width === w && size.height === h)) return;
  v.instance.engine.onWindowResize(w, h);
  v.instance.view.setSize(w, h);
  v.instance.notifyChange(v.instance.view.camera);
}

export function attachFotoTeilung(o: { panels: PanelHost; view: HTMLElement; werte: FotoLayout; speichern(): void; groesseAendern(): void }): {
  oeffnen(): void; umschalten(): boolean;
} {
  const seite = document.createElement('section');
  seite.className = 'sv-foto-seite'; seite.id = 'foto-geteilt';
  seite.setAttribute('aria-label', 'Geteilte Fotoansicht');
  const schliessen = document.createElement('button'); schliessen.type = 'button'; schliessen.textContent = 'Foto schließen';
  schliessen.addEventListener('click', () => o.panels.close('foto'));
  const host = document.createElement('div'); host.className = 'sv-foto-geteilt-inhalt';
  seite.append(schliessen, host);
  const trenner = document.createElement('div'); trenner.className = 'sv-foto-trenner'; trenner.tabIndex = 0;
  trenner.dataset.tastenEigen = ''; trenner.setAttribute('role', 'separator');
  trenner.setAttribute('aria-label', 'Anteil der Fotoansicht'); trenner.setAttribute('aria-controls', seite.id);
  document.body.append(seite, trenner);
  let teilen = true, offen = o.panels.isOpen('foto');
  const lage = (): Lage => innerHeight > innerWidth ? 'hoch' : 'quer';
  const laenge = (): number => lage() === 'hoch' ? innerHeight : innerWidth;
  const grenze = () => grenzen(lage(), laenge(), { hoch: { min: 0.25, max: 0.75 }, quer: { min: 0.25, max: 0.75 } });
  const zeichnen = (): void => {
    const an = teilen && offen;
    seite.hidden = trenner.hidden = !an;
    o.view.style.width = ''; o.view.style.height = '';
    if (!an) { o.groesseAendern(); return; }
    const hoch = lage() === 'hoch';
    const anteil = klemme(o.werte[lage()], grenze());
    const px = anteil * laenge();
    seite.dataset.lage = trenner.dataset.lage = lage();
    seite.style.width = hoch ? '100%' : `${px}px`;
    seite.style.height = hoch ? `${px}px` : 'calc(100% - 56px)';
    trenner.style.right = hoch ? '0' : `${px}px`;
    trenner.style.bottom = hoch ? `${px}px` : '0';
    o.view.style[hoch ? 'height' : 'width'] = `calc(100% - ${px + TRENNER_PX}px)`;
    trenner.setAttribute('aria-orientation', hoch ? 'horizontal' : 'vertical');
    trenner.setAttribute('aria-valuemin', String(Math.round(grenze().min * 100)));
    trenner.setAttribute('aria-valuemax', String(Math.round(grenze().max * 100)));
    trenner.setAttribute('aria-valuenow', String(Math.round(anteil * 100)));
    // Giro3Ds ResizeObserver wartet 50 ms. Bis dahin hätte das Canvas noch die
    // alte Größe, und projizierte Klickziele könnten bereits im Foto liegen.
    o.groesseAendern();
  };
  const setzen = (n: number): void => { o.werte[lage()] = klemme(n, grenze()); o.speichern(); zeichnen(); };
  trenner.addEventListener('keydown', (ev) => {
    const n = schritt(lage(), ev.key, o.werte[lage()], grenze());
    if (n === null) return;
    ev.preventDefault(); ev.stopPropagation(); setzen(n);
  });
  let zug: number | null = null;
  trenner.addEventListener('pointerdown', (ev) => { zug = ev.pointerId; trenner.setPointerCapture(ev.pointerId); ev.preventDefault(); });
  trenner.addEventListener('pointermove', (ev) => {
    if (zug !== ev.pointerId) return;
    setzen(anteilAusZeiger(lage(), lage() === 'hoch' ? ev.clientY : ev.clientX, { anfang: 0, laenge: laenge() }));
  });
  const ende = (): void => { zug = null; };
  trenner.addEventListener('pointerup', ende); trenner.addEventListener('pointercancel', ende);
  window.addEventListener('resize', zeichnen);
  // Jeder Öffnungsweg (auch Panel-API/hidden-Observer) muss den Inhalt mitnehmen.
  o.panels.onChange((id, an) => { if (id === 'foto') { offen = an; mount(); } });
  const mount = (): void => { o.panels.auslagern('foto', teilen ? host : null, !teilen); zeichnen(); };
  mount();
  return {
    oeffnen() { o.panels.open('foto'); },
    umschalten() { teilen = !teilen; mount(); return teilen; },
  };
}
