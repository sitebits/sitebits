import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import sprueche from '../../spleen/sprueche.json';
import { bildAnzahl, ladeBild, waehleSpruch } from '../src/spleen';

describe('spleen', () => {
  it('findet alle Punktbilder der kanonischen Quelle', () => {
    expect(bildAnzahl).toBe(readdirSync(new URL('../../spleen/bilder', import.meta.url)).length);
  });

  it('lädt genau das gezogene Bild', async () => {
    const bild = await ladeBild(() => 0);
    expect(bild.length).toBeGreaterThan(100);
    expect(bild).toMatch(/^[⠀-⣿\n]+$/);
  });

  it('wählt eigene Sätze in der Browsersprache', () => {
    expect(waehleSpruch('de-DE', () => 0)).toBe(sprueche.eigen[0]!.de);
    expect(waehleSpruch('en-US', () => 0)).toBe(sprueche.eigen[0]!.en);
  });

  it('zeigt Zitate im Original mit Autor und Quelle', () => {
    const z = sprueche.zitate.at(-1)!;
    expect(waehleSpruch('de', () => 0.9999)).toBe(`${z.text}\n— ${z.von}, ${z.quelle}`);
  });

  it('Paketkopie im Import gleicht der Quelle', () => {
    const quelle = readFileSync(new URL('../../spleen/sprueche.json', import.meta.url), 'utf8');
    const kopie = readFileSync(new URL('../../import/src/scanview_import/spleen_daten/sprueche.json', import.meta.url), 'utf8');
    expect(kopie).toBe(quelle);
  });
});
