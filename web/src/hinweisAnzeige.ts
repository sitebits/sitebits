// Tastenhinweise anzeigen und zählen (DOM-Adapter zu hinweise.ts): eine Leiste unten rechts, für Hilfstechnik
// verborgen (dieselben Angaben stehen im Dialog „Bedienung“ und in der Beschreibung der 3D-Ansicht). Im Orbit steht
// vorab eine Zeile zu Maus bzw. Fingern; auf reinen Touch-Geräten erscheinen Tastenhinweise erst, wenn eine Taste
// gedrückt wird. Verdeckt sie das
// fokussierte Element oder ein Panel (die Minikarte liegt per Vorgabe unten rechts), rückt sie nach unten links. Der Zähler liegt je Konto im Browser (Schlüssel aus main.ts).
import { bewegungReduziert } from './bedienEinstellungen';
import type { BedienEinstellungen } from './bedienEinstellungen';
import { echteTastatur, gezaehlt, hinweisFuerAktion, hinweisTaste, ladeHinweise, seiteFuer, sichtbareHinweise, speichereHinweise, zurueckgesetzt } from './hinweise';
import type { HinweisId } from './hinweise';
import type { KeyAction, ToolId } from './keymap';
import type { AktionId } from './tastenbelegung';

export interface HinweisAnzeige {
  /** Eine Ausführung zählen und neu zeichnen. */
  zaehle(id: HinweisId): void;
  /** Tastenaktion aus dem Verteiler (Zielen, Drehen im Panorama-Fadenkreuz). */
  beobachte(a: KeyAction): void;
  /** Controller wird benutzt (true) bzw. Tastatur oder Maus wieder (false): mit Controller tritt die Leiste zurück,
   *  die Controller-Leiste (padSteuerung.ts) nennt dann die Knöpfe. */
  padAktiv(an: boolean): void;
  /** Belegung, Einstellungen oder Zustand geändert. */
  aktualisiere(): void;
  /** Alle Zähler dieses Kontos leeren. */
  zuruecksetzen(): void;
}

export function attachHinweisAnzeige(o: {
  /** Speicherschlüssel des Zählers (je Konto). */
  schluessel: string;
  lies: (k: string) => string | null;
  schreib: (k: string, wert: string) => void;
  begehen: { readonly aktiv: boolean; onChange(cb: (aktiv: boolean) => void): void };
  pano: { readonly active: boolean; onChange(cb: () => void): void };
  toolbar: { tool(): string; onChange(cb: (t: ToolId) => void): void };
  pois: { ausgewaehlt(): string | null; onAuswahl(cb: (id: string | null) => void): void };
  panels: { onChange(cb: (id: string, open: boolean) => void): void };
  panoramaZiel: () => boolean;
  /** Empfängeransicht aktiv: dort nur Tastenhilfe und Drehen. Beim Wechsel ruft der Aufrufer aktualisiere(). */
  empfang?: () => boolean;
  einstellungen: () => BedienEinstellungen;
  systemReduziert: () => boolean;
  kuerzel: (a: AktionId) => string;
}): HinweisAnzeige {
  const leiste = document.createElement('div');
  leiste.id = 'hinweise';
  leiste.className = 'sv-hinweise sv-leer';
  leiste.setAttribute('aria-hidden', 'true');
  document.body.append(leiste);

  let zaehler = ladeHinweise(o.lies(o.schluessel));
  /** Reines Touch-Gerät (kein Mauszeiger): Zeile zu Fingern statt Maus, Tastenhinweise erst nach einer Taste. */
  const nurTouch = window.matchMedia('(hover: none) and (pointer: coarse)');
  /** Zuletzt wurde ein Controller benutzt: die Leiste tritt zurück. */
  let padAn = false;
  let letzterText = '';

  // Gemessen wird immer am rechten Platz (sonst kippte die Leiste hin und her); Panels sind die Rahmen der
  // Panel-Docks (ein geschlossener Rahmen hat kein Rechteck).
  const lage = (): void => {
    // Leer (blendet gerade aus): Seite behalten, sonst spränge die verblassende Leiste auf die andere Seite.
    if (leiste.classList.contains('sv-leer')) return;
    leiste.classList.remove('sv-links', 'sv-verdraengt');
    const fokus = document.activeElement;
    const fokusRechteck = fokus instanceof Element && fokus !== document.body ? fokus.getBoundingClientRect() : null;
    // Panels und andere Hinweise unten (Scan-Hinweis, Koordinate) — darauf soll die Leiste nicht liegen.
    const panels = Array.from(document.querySelectorAll('.sv-frame, #scan-hint:not([hidden]), .sv-koordinaten:not([hidden])'), (f) => f.getBoundingClientRect());
    const seite = seiteFuer(leiste.getBoundingClientRect(), document.documentElement.clientWidth, fokusRechteck, panels);
    if (seite === 'links') leiste.classList.add('sv-links');
    if (seite === 'keine') leiste.classList.add('sv-verdraengt');
  };

  const aktualisiere = (): void => {
    const begehen = o.begehen.aktiv;
    const gamepad = padAn;
    const taste = (id: HinweisId): string =>
      // Im Panorama ist Begehen nicht verfügbar: kein Hinweis darauf.
      id === 'begehen' && o.pano.active ? '' : hinweisTaste(id, { kuerzel: o.kuerzel });
    const touch = nurTouch.matches;
    const liste = sichtbareHinweise(
      {
        begehen, panoramaZiel: o.panoramaZiel(), werkzeug: o.toolbar.tool(), auswahl: o.pois.ausgewaehlt() !== null, empfang: o.empfang?.() ?? false, gamepad,
        panorama: o.pano.active, zeiger: touch ? 'finger' : 'maus', tastatur: !touch || leiste.classList.contains('sv-tastatur'),
        orbitLinks: o.einstellungen().orbitLinks,
      },
      zaehler,
      { immer: o.einstellungen().hinweiseImmer, taste },
    ).filter(() => !padAn);
    leiste.classList.toggle('sv-ruhig', bewegungReduziert(o.einstellungen(), o.systemReduziert()));
    const text = liste.map((h) => h.text).join('\n');
    if (text !== letzterText) {
      letzterText = text;
      leiste.replaceChildren(...liste.map((h) => {
        const zeile = document.createElement('div');
        zeile.dataset.hinweis = h.id;
        zeile.textContent = h.text;
        return zeile;
      }));
      leiste.classList.toggle('sv-leer', liste.length === 0);
    }
    lage();
  };

  const zaehle = (id: HinweisId): void => {
    const neu = gezaehlt(zaehler, id);
    if (neu === zaehler) return;
    zaehler = neu;
    o.schreib(o.schluessel, speichereHinweise(zaehler));
    aktualisiere();
  };

  o.begehen.onChange((aktiv) => {
    zaehle(aktiv ? 'begehen' : 'beenden');
    aktualisiere();
  });
  o.pano.onChange(aktualisiere);
  o.toolbar.onChange(aktualisiere);
  o.pois.onAuswahl(aktualisiere);
  // Das Panorama-Fadenkreuz hängt am Fokus der Leinwand; beim Verlassen steht der neue Fokus erst danach fest.
  document.addEventListener('focusin', aktualisiere);
  document.addEventListener('focusout', () => queueMicrotask(aktualisiere));
  window.addEventListener('resize', lage);
  // main.ts::freieLage hat Scan-Hinweis oder Meldung verschoben.
  window.addEventListener('sv-hinweise-lage', lage);
  nurTouch.addEventListener('change', aktualisiere);
  o.panels.onChange(() => requestAnimationFrame(lage));
  // Tastatur benutzt: wieder Tasten statt Controller-Knöpfe zeigen; auf reinen Touch-Geräten erscheinen die
  // Tastenhinweise erst ab jetzt — nicht durch eine Bildschirmtastatur (echteTastatur).
  window.addEventListener('keydown', (ev) => {
    const t = ev.target;
    const imTextfeld = t instanceof HTMLElement && (t.isContentEditable || t.matches('input, textarea, select'));
    if (echteTastatur(ev, imTextfeld) && !leiste.classList.contains('sv-tastatur')) {
      leiste.classList.add('sv-tastatur');
      if (nurTouch.matches) aktualisiere();
    }
  }, true);

  aktualisiere();
  // Die Panel-Rahmen stehen erst nach dem nächsten Bild an ihrem Platz.
  requestAnimationFrame(lage);
  return {
    zaehle,
    beobachte: (a) => {
      const id = hinweisFuerAktion(a, { begehen: o.begehen.aktiv, panoramaZiel: o.panoramaZiel() });
      if (id) zaehle(id);
    },
    padAktiv: (an) => {
      if (padAn === an) return;
      padAn = an;
      aktualisiere();
    },
    aktualisiere,
    zuruecksetzen: () => {
      zaehler = zurueckgesetzt();
      o.schreib(o.schluessel, speichereHinweise(zaehler));
      aktualisiere();
    },
  };
}
