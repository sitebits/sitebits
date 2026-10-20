// Tastenhinweise anzeigen und zählen (DOM-Adapter zu hinweise.ts): eine Leiste unten rechts, für Hilfstechnik
// verborgen (dieselben Angaben stehen im Dialog „Bedienung“ und in der Beschreibung der 3D-Ansicht). Im Orbit steht
// vorab eine Zeile zu Maus bzw. Fingern; auf reinen Touch-Geräten erscheinen Tastenhinweise erst, wenn eine Taste
// gedrückt wird. Verdeckt sie das
// fokussierte Element oder ein Panel (die Minikarte liegt per Vorgabe unten rechts), rückt sie nach unten links. Der Zähler liegt je Konto im Browser (Schlüssel aus main.ts).
import { bewegungReduziert } from './bedienEinstellungen';
import type { BedienEinstellungen } from './bedienEinstellungen';
import type { PadAktion } from './gamepad';
import { echteTastatur, gezaehlt, hinweisFuerAktion, padAktiv, hinweisTaste, ladeHinweise, padTypVon, seiteFuer, sichtbareHinweise, speichereHinweise, zurueckgesetzt } from './hinweise';
import type { HinweisId, PadTyp } from './hinweise';
import type { KeyAction, ToolId } from './keymap';
import { padBelegung } from './tastenbelegung';
import type { AktionId, Belegung } from './tastenbelegung';

export interface HinweisAnzeige {
  /** Eine Ausführung zählen und neu zeichnen. */
  zaehle(id: HinweisId): void;
  /** Tastenaktion aus dem Verteiler (Zielen, Drehen im Panorama-Fadenkreuz). */
  beobachte(a: KeyAction): void;
  /** Controller-Knopf hat gewirkt: Knopfnamen statt Tasten zeigen, Zielen zählen. */
  pad(a: PadAktion, runter: boolean): void;
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
  belegung: () => Belegung;
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
  /** Typ des zuletzt benutzten Controllers; null = zuletzt Tastatur (oder nie ein Controller). */
  let padTyp: PadTyp | null = null;
  let letzterText = '';

  // Gemessen wird immer am rechten Platz (sonst kippte die Leiste hin und her); Panels sind die Rahmen der
  // Panel-Docks (ein geschlossener Rahmen hat kein Rechteck).
  const lage = (): void => {
    leiste.classList.remove('sv-links');
    if (leiste.classList.contains('sv-leer')) return;
    const fokus = document.activeElement;
    const fokusRechteck = fokus instanceof Element && fokus !== document.body ? fokus.getBoundingClientRect() : null;
    const panels = Array.from(document.querySelectorAll('.sv-frame'), (f) => f.getBoundingClientRect());
    if (seiteFuer(leiste.getBoundingClientRect(), document.documentElement.clientWidth, fokusRechteck, panels) === 'links') leiste.classList.add('sv-links');
  };

  const padId = (): string => {
    try {
      const liste = navigator.getGamepads?.() ?? [];
      for (const p of Array.from(liste)) if (p?.connected && p.mapping === 'standard') return p.id;
    } catch {
      /* Gamepad-API gesperrt */
    }
    return '';
  };

  const aktualisiere = (): void => {
    const begehen = o.begehen.aktiv;
    const gamepad = padAktiv(padTyp, begehen, padId());
    const pad = gamepad && padTyp !== null ? { typ: padTyp, belegung: padBelegung(o.belegung()) } : null;
    const taste = (id: HinweisId): string =>
      // Im Panorama ist Begehen nicht verfügbar: kein Hinweis darauf.
      id === 'begehen' && o.pano.active ? '' : hinweisTaste(id, { kuerzel: o.kuerzel, pad });
    const touch = nurTouch.matches;
    const liste = sichtbareHinweise(
      {
        begehen, panoramaZiel: o.panoramaZiel(), werkzeug: o.toolbar.tool(), auswahl: o.pois.ausgewaehlt() !== null, empfang: o.empfang?.() ?? false, gamepad,
        panorama: o.pano.active, zeiger: touch ? 'finger' : 'maus', tastatur: !touch || leiste.classList.contains('sv-tastatur'),
        orbitLinks: o.einstellungen().orbitLinks,
      },
      zaehler,
      { immer: o.einstellungen().hinweiseImmer, taste },
    );
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
  nurTouch.addEventListener('change', aktualisiere);
  o.panels.onChange(() => requestAnimationFrame(lage));
  // Controller abgesteckt: zurück zu den Tasten.
  window.addEventListener('gamepaddisconnected', () => {
    padTyp = null;
    aktualisiere();
  });
  // Tastatur benutzt: wieder Tasten statt Controller-Knöpfe zeigen; auf reinen Touch-Geräten erscheinen die
  // Tastenhinweise erst ab jetzt — nicht durch eine Bildschirmtastatur (echteTastatur).
  window.addEventListener('keydown', (ev) => {
    const t = ev.target;
    const imTextfeld = t instanceof HTMLElement && (t.isContentEditable || t.matches('input, textarea, select'));
    if (echteTastatur(ev, imTextfeld) && !leiste.classList.contains('sv-tastatur')) {
      leiste.classList.add('sv-tastatur');
      if (nurTouch.matches) aktualisiere();
    }
    if (padTyp === null) return;
    padTyp = null;
    aktualisiere();
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
    pad: (a, runter) => {
      const typ = padTypVon(padId());
      const neu = padTyp !== typ;
      padTyp = typ;
      if (a === 'zielen' && runter) zaehle('zielen');
      if (neu) aktualisiere();
    },
    aktualisiere,
    zuruecksetzen: () => {
      zaehler = zurueckgesetzt();
      o.schreib(o.schluessel, speichereHinweise(zaehler));
      aktualisiere();
    },
  };
}
