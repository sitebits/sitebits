// Eingabequelle des Begehens aus dem Gamepad: je Bild einmal abgefragt (nur solange das Begehen läuft),
// erstes verbundenes Pad mit Standardbelegung. Die reine Auswertung liegt in gamepad.ts.
import type { EingabeQuelle } from './begehen';
import { kanten, lies, stickSchritt } from './gamepad';
import type { PadAktion, PadRoh } from './gamepad';

export const MELDUNG_KEIN_STANDARD = 'Controller ohne Standardbelegung wird nicht unterstützt';

interface PadAblesbar extends PadRoh { connected: boolean; mapping: string }

export function erzeugePadQuelle(o: {
  belegung: () => Record<PadAktion, number>;
  invertY: () => boolean;
  melde: (t: string) => void;
  /** Flanke eines Knopfs: `runter` beim Drücken, sonst beim Loslassen. */
  kante: (a: PadAktion, runter: boolean) => void;
  /** Pads lesen (Vorgabe: navigator.getGamepads). */
  pads?: () => ArrayLike<PadAblesbar | null> | null;
}): { quelle: (dt: number) => EingabeQuelle | null; zuruecksetzen: () => void } {
  const lese = o.pads ?? ((): ArrayLike<PadAblesbar | null> | null => {
    try {
      return navigator.getGamepads?.() ?? null;
    } catch {
      return null;
    }
  });
  let vorher: ReadonlySet<PadAktion> | null = null; // null: noch keine Flanken (gerade begonnen)
  let drehBereit = true;
  let neigBereit = true;
  let gemeldet = false;

  const zuruecksetzen = (): void => {
    // Beim Loslassen eines Pads mitten im Begehen dürfen gehaltene Aktionen nicht hängen bleiben.
    if (vorher) for (const a of vorher) o.kante(a, false);
    vorher = null;
    drehBereit = true;
    neigBereit = true;
  };

  const quelle = (): EingabeQuelle | null => {
    const liste = lese();
    let standard: PadAblesbar | null = null;
    let fremd = false;
    for (let i = 0; liste && i < liste.length; i++) {
      const p = liste[i];
      if (!p || !p.connected) continue;
      if (p.mapping === 'standard') {
        standard = p;
        break;
      }
      fremd = true;
    }
    if (!standard) {
      if (fremd && !gemeldet) {
        gemeldet = true;
        o.melde(MELDUNG_KEIN_STANDARD);
      }
      zuruecksetzen();
      return null;
    }
    const lage = lies(standard, o.belegung(), { invertY: o.invertY() });
    // Erstes Bild nach dem Beginn: bereits gehaltene Knöpfe lösen nichts aus.
    const alt = vorher ?? lage.gedrueckt;
    const k = kanten(alt, lage.gedrueckt);
    vorher = lage.gedrueckt;
    for (const a of k.hoch) o.kante(a, false);
    for (const a of k.runter) o.kante(a, true);
    // Eine Flanke hat das Begehen beendet (zuruecksetzen lief): in diesem Bild nichts mehr bewegen.
    if (vorher === null) return null;
    const d = stickSchritt(lage.dreh, drehBereit);
    const n = stickSchritt(lage.neig, neigBereit);
    drehBereit = d.bereit;
    neigBereit = n.bereit;
    const g = lage.gedrueckt;
    return {
      eingabe: {
        vor: lage.vor,
        seit: lage.seit,
        hoch: (g.has('hoch') ? 1 : 0) - (g.has('runter') ? 1 : 0),
        schnell: false,
      },
      dreh: lage.dreh,
      neig: lage.neig,
      schritte: { dreh: d.schritt, neig: n.schritt },
    };
  };
  return { quelle, zuruecksetzen };
}
