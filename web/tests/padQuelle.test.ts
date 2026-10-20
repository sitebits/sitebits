// Eingabequelle aus dem Gamepad: Flanken und Abbruch im selben Bild, mit einem nachgebildeten Pad.
import { describe, expect, it } from 'vitest';
import { PAD_VORGABE } from '../src/gamepad';
import type { PadAktion } from '../src/gamepad';
import { erzeugePadQuelle } from '../src/padQuelle';

const pad = (gedrueckt: number[], vorY = 0) => ({
  connected: true,
  mapping: 'standard',
  id: 'nachgebildet',
  axes: [0, vorY, 0, 0],
  buttons: Array.from({ length: 17 }, (_, i) => ({ pressed: gedrueckt.includes(i), value: gedrueckt.includes(i) ? 1 : 0 })),
});

describe('erzeugePadQuelle', () => {
  it('Zurück beendet: im selben Bild keine Bewegung mehr', () => {
    let lage = pad([], -1);
    const kanten: [PadAktion, boolean][] = [];
    const q = erzeugePadQuelle({
      belegung: () => PAD_VORGABE,
      invertY: () => false,
      melde: () => undefined,
      kante: (a, runter) => {
        kanten.push([a, runter]);
        if (a === 'zurueck' && runter) q.zuruecksetzen();
      },
      pads: () => [lage],
    });
    expect(q.quelle(0.016)?.eingabe.vor).toBe(1);
    lage = pad([PAD_VORGABE.zurueck], -1);
    expect(q.quelle(0.016)).toBeNull();
    expect(kanten).toContainEqual(['zurueck', true]);
  });

  it('ohne Abbruch: Flanke und Eingabe im selben Bild', () => {
    let lage = pad([], -1);
    const kanten: [PadAktion, boolean][] = [];
    const q = erzeugePadQuelle({
      belegung: () => PAD_VORGABE,
      invertY: () => false,
      melde: () => undefined,
      kante: (a, runter) => kanten.push([a, runter]),
      pads: () => [lage],
    });
    q.quelle(0.016);
    lage = pad([PAD_VORGABE.setzen], -1);
    expect(q.quelle(0.016)?.eingabe.vor).toBe(1);
    expect(kanten).toEqual([['setzen', true]]);
  });
});
