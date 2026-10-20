// Inhalt des Dialogs „Bedienung“ (Maus und Finger je Ansicht) und der Satz beim Ansichtswechsel. Rein (Vitest);
// jede Zeile beschreibt, was der Code tut — nicht, was wünschenswert wäre:
//  - Orbit: MapControls (viewer.ts) mit mausTasten() aus drehpunkt.ts; Umschalt/Strg + links nimmt in three.js die
//    jeweils andere Geste. Rad zoomt zum Drehpunkt (zoomToCursor aus). Finger: fingerGesten() aus drehpunkt.ts,
//    ein Finger wie links ziehen, zwei Finger zoomen und nehmen die andere Geste (Vorgabe: wie Sketchfab).
//  - Doppelklick setzt im Orbit den Drehpunkt (viewer.ts), im Panorama wechselt er zum Nachbarn in Klickrichtung (main.ts). Klick auf einen Standpunkt-Marker fährt dorthin
//    (main.ts, nur mit Werkzeug „Navigieren“).
//  - Panorama: Ziehen mit der Haupttaste bzw. einem Finger schaut um, das Rad ändert den Bildwinkel (panorama.ts);
//    kein Zwei-Finger-Zoom. Nachbarn als Standpunkt-Marken, Klick darauf oder Doppelklick in ihre Richtung wechselt den Standpunkt.
//  - Begehen: nur Maus und Tastatur (begehen.ts): Klick fängt den Zeiger, Rad = Tempo, rechte Taste = zielen.
import type { OrbitLinks } from './bedienEinstellungen';

export type Ansicht = 'orbit' | 'panorama' | 'begehen';
export interface HilfeZeile { geste: string; wirkung: string }
export interface HilfeAbschnitt { ansicht: Ansicht; titel: string; aktiv: boolean; zeilen: HilfeZeile[] }
export interface HilfeKontext {
  ansicht: Ansicht;
  /** Empfängeransicht: Knöpfe der Befundkarte, kein Begehen. */
  empfang: boolean;
  orbitLinks: OrbitLinks;
  /** Die Vollbild-API steht zur Verfügung (vollbildVerfuegbar). */
  vollbild: boolean;
  /** Läuft schon als Web-App ohne Adresszeile (display-mode: standalone). */
  eigenesFenster: boolean;
}

export const ansichtVon = (z: { begehen: boolean; panorama: boolean }): Ansicht => (z.begehen ? 'begehen' : z.panorama ? 'panorama' : 'orbit');

const TITEL: Record<Ansicht, string> = { orbit: 'Punktwolke (Orbit)', panorama: 'Panorama', begehen: 'Begehen' };
const z = (geste: string, wirkung: string): HilfeZeile => ({ geste, wirkung });

function zeilen(a: Ansicht, k: HilfeKontext): HilfeZeile[] {
  if (a === 'orbit') {
    const drehen = 'dreht um den Drehpunkt';
    const schieben = 'verschiebt die Ansicht';
    const linksDreht = k.orbitLinks === 'drehen';
    const out = [
      z('Linke Maustaste ziehen', linksDreht ? drehen : schieben),
      z('Rechte Maustaste oder Umschalt + linke Maustaste ziehen', linksDreht ? schieben : drehen),
      z('Mausrad', 'zoomt zum Drehpunkt'),
      z('Doppelklick auf die Punktwolke', 'setzt dort den Drehpunkt'),
      z('Klick oder Tippen auf einen Standpunkt-Marker (Werkzeug Navigieren)', 'fährt dorthin; mit Panorama öffnet es sich'),
      z('Ein Finger ziehen', linksDreht ? drehen : schieben),
      z('Zwei Finger auseinander oder zusammen', 'zoomt'),
      z('Zwei Finger gemeinsam ziehen', linksDreht ? schieben : drehen),
      z('Fotos: „Fotos zu einer Stelle finden“', 'Punkt oder Klickpolygon auf der Oberfläche wählen; auch Flächenmessung und Schnittbox sind möglich. Verdeckung prüft nur geladene Wolkenknoten und sichtbare Meshes (LOD); dünne Hindernisse können fehlen. Ohne Tiefentreffer bleibt ein Foto als sichtbar, ungeprüft markiert.'),
      z('Trenner der Fotoansicht', 'ziehen oder mit Pfeiltasten verschieben; hochkant liegt das Foto unten. „Ansicht teilen aus“ öffnet das schwebende Foto-Fenster.'),
    ];
    if (k.empfang) out.push(z('Knöpfe der Befundkarte', 'drehen, neigen und zoomen in Schritten (wie die Pfeiltasten)'));
    return out;
  }
  if (a === 'panorama') {
    const out = [
      z('Ziehen (Maus oder ein Finger)', 'schaut sich um'),
      z('Mausrad', 'ändert den Bildwinkel'),
      z('Klick oder Tippen auf eine Standpunkt-Marke', 'wechselt zu diesem Standpunkt'),
      z('Doppelklick in eine Richtung', 'wechselt zum nächsten Standpunkt in dieser Richtung'),
    ];
    if (k.empfang) {
      out.push(z('Knöpfe der Befundkarte', 'drehen, neigen und Bildwinkel in Schritten (Näher/Weiter statt zwei Finger)'));
      out.push(z('Frei umsehen', 'verlässt das Panorama'));
    } else out.push(z('Esc', 'zurück zur Punktwolke (auch „Panorama verlassen“ im Panel Ansicht)'));
    return out;
  }
  return [
    z('Klick in die 3D-Ansicht', 'fängt den Mauszeiger'),
    z('Maus bewegen', 'schaut sich um (Zeiger gefangen)'),
    z('Mausrad', 'ändert das Lauftempo'),
    z('Linke Maustaste', 'setzt am Fadenkreuz mit dem gewählten Werkzeug'),
    z('Rechte Maustaste halten', 'zielen (feiner, Kanten einrasten)'),
    z('Esc', 'gibt den Zeiger frei, ein zweites Esc beendet das Begehen'),
    z('Finger', 'keine Fingersteuerung – Tastatur oder Controller'),
  ];
}

/** Abschnitte „Maus und Finger“: die aktive Ansicht zuerst, dazu die Zeilen zu Trenner (Empfang) und Vollbild. */
export function mausUndFinger(k: HilfeKontext): HilfeAbschnitt[] {
  const alle: readonly Ansicht[] = k.empfang ? ['orbit', 'panorama'] : ['orbit', 'panorama', 'begehen'];
  const reihe = alle.filter((a) => a !== k.ansicht);
  const ansichten: Ansicht[] = [...(k.empfang && k.ansicht === 'begehen' ? [] : [k.ansicht]), ...reihe];
  const abschnitte = ansichten.map((a) => ({ ansicht: a, titel: TITEL[a], aktiv: a === k.ansicht, zeilen: zeilen(a, k) }));
  const erster = abschnitte[0];
  if (erster) {
    // Trenner (aufteilung.ts) gibt es nur in der Empfängeransicht am Handy bei ausgeklappter Karte.
    if (k.empfang) {
      erster.zeilen.push(z('Trenner zwischen Ansicht und Befundkarte (Handy)', 'ziehen verschiebt die Aufteilung, Doppeltipp schaltet „3D groß“ um; mit Fokus Pfeiltasten in Schritten'));
    }
    if (k.vollbild) erster.zeilen.push(z('Knopf Vollbild', 'ganzer Bildschirm, Esc beendet'));
    else if (!k.eigenesFenster) {
      erster.zeilen.push(z('Vollbild auf dem iPhone', 'im Browser „Teilen → Zum Home-Bildschirm“ wählen; von dort startet siteBits ohne Adresszeile'));
    }
  }
  return abschnitte;
}

/** Satz zur Punktwolke (Orbit); hängt auch an „Begehen beendet“ (begehen.ts), statt eine zweite Meldung zu sein. */
export const punktwolkeSatz = (links: OrbitLinks): string =>
  `Punktwolke: Sie sehen das Modell von außen. ${links === 'drehen' ? 'Ziehen' : 'Rechts ziehen'} dreht um den Drehpunkt, Doppelklick setzt ihn.`;

/** Kurzer Satz für die Live-Region beim Wechsel der Ansicht; null ohne Wechsel und rund ums Begehen: das sagt Start
 *  und Ende selbst an (mit punktwolkeSatz am Ende) — eine zweite Meldung im selben Augenblick ersetzte die erste. */
export function ansichtWechselText(vorher: Ansicht, nachher: Ansicht, links: OrbitLinks): string | null {
  if (vorher === nachher || vorher === 'begehen' || nachher === 'begehen') return null;
  if (nachher === 'panorama') return 'Panorama: Sie stehen am Standpunkt und schauen sich um. Zurück mit Esc.';
  return punktwolkeSatz(links);
}

type VollbildDok = { fullscreenEnabled?: boolean; webkitFullscreenEnabled?: boolean };
type VollbildEl = { requestFullscreen?: unknown; webkitRequestFullscreen?: unknown };
/** Vollbild per API: Desktop, Android, iPad (ältere mit Präfix). Das iPhone erlaubt es Webseiten nicht. */
export function vollbildVerfuegbar(doc: VollbildDok, el: VollbildEl): boolean {
  if (doc.fullscreenEnabled === true && typeof el.requestFullscreen === 'function') return true;
  return doc.webkitFullscreenEnabled === true && typeof el.webkitRequestFullscreen === 'function';
}
