/// <reference types="node" />
import { inflateSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { symbolPng, symbolSvg, webAppDateien, webAppManifest } from '../scripts/webApp';

/** PNG zerlegen: Signatur, Chunks (Typ, Länge), IHDR-Maße, entpackte Pixelzeilen. */
function lies(png: Buffer): { typen: string[]; breite: number; hoehe: number; roh: Buffer } {
  expect(png.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  const typen: string[] = [];
  const idat: Buffer[] = [];
  let breite = 0;
  let hoehe = 0;
  for (let i = 8; i < png.length;) {
    const n = png.readUInt32BE(i);
    const typ = png.subarray(i + 4, i + 8).toString('latin1');
    const daten = png.subarray(i + 8, i + 8 + n);
    typen.push(typ);
    if (typ === 'IHDR') {
      breite = daten.readUInt32BE(0);
      hoehe = daten.readUInt32BE(4);
    }
    if (typ === 'IDAT') idat.push(daten);
    i += 12 + n;
  }
  return { typen, breite, hoehe, roh: inflateSync(Buffer.concat(idat)) };
}

describe('Web-App-Manifest', () => {
  it('eigenständig, Start im Portal, Farben aus den Tokens, Symbole mit festen Pfaden', () => {
    const m = webAppManifest();
    expect(m).toMatchObject({ name: 'siteBits', short_name: 'siteBits', display: 'standalone', start_url: '/', scope: '/', lang: 'de', theme_color: '#111111', background_color: '#111111' });
    const pfade = Object.keys(webAppDateien());
    for (const i of m.icons) expect(pfade).toContain(i.src);
    expect(m.icons.some((i) => i.purpose === 'maskable' && i.sizes === '512x512')).toBe(true);
    // Keine fremde Adresse (Egress): nur Pfade dieser Instanz.
    expect(JSON.stringify(m)).not.toMatch(/https?:/);
  });

  it('liefert Manifest, SVG und PNG 180/192/512 mit Medientyp', () => {
    const d = webAppDateien();
    expect(Object.keys(d).sort()).toEqual(['/icons/scanview-180.png', '/icons/scanview-192.png', '/icons/scanview-512.png', '/icons/scanview.svg', '/manifest.webmanifest']);
    expect(d['/manifest.webmanifest']!.typ).toBe('application/manifest+json');
    expect(d['/icons/scanview.svg']!.typ).toBe('image/svg+xml');
    expect(d['/icons/scanview-180.png']!.typ).toBe('image/png');
  });
});

describe('Symbol', () => {
  it('PNG: gültig, richtige Größe, RGBA, reproduzierbar', () => {
    for (const n of [180, 192, 512]) {
      const p = lies(symbolPng(n));
      expect(p.typen).toEqual(['IHDR', 'IDAT', 'IEND']);
      expect([p.breite, p.hoehe]).toEqual([n, n]);
      expect(p.roh.length).toBe(n * (1 + n * 4));
    }
    expect(symbolPng(192).equals(symbolPng(192))).toBe(true);
  });

  it('PNG: Ecke im Hintergrund, Mitte in Akzentfarbe (Drehpunkt), Ring dazwischen', () => {
    const n = 192;
    const { roh } = lies(symbolPng(n));
    const px = (x: number, y: number): number[] => {
      const i = y * (1 + n * 4) + 1 + x * 4;
      return [roh[i]!, roh[i + 1]!, roh[i + 2]!, roh[i + 3]!];
    };
    expect(px(2, 2)).toEqual([0x11, 0x11, 0x11, 255]);
    expect(px(n / 2, n / 2)).toEqual([0x33, 0xd1, 0xff, 255]);
    expect(px(Math.round(n / 2 + 0.3 * n), n / 2)).toEqual([0x33, 0xd1, 0xff, 255]);
    expect(px(Math.round(n / 2 + 0.18 * n), n / 2)).toEqual([0x11, 0x11, 0x11, 255]);
  });

  it('SVG: dieselbe Form, ohne Skript und fremde Verweise', () => {
    const s = symbolSvg();
    expect(s).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
    expect(s).toContain('#33d1ff');
    expect(s).not.toMatch(/<script|href=/);
  });
});
