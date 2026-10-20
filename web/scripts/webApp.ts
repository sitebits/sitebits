/// <reference types="node" />
// Web-App-Manifest und Symbol für „Zum Home-Bildschirm“ (iPhone: kein Vollbild per API, nur als Web-App ohne
// Adresszeile). Alles wird beim Build erzeugt (vite.config.ts, Plugin scanview-web-app) — kein eingecheckter
// Binärblob, keine fremden Assets. Das Symbol ist selbst gezeichnet: Ring (Orbit) um einen Punkt (Drehpunkt) in der
// Akzentfarbe auf dem Hintergrund der Oberfläche (styles/tokens.css). PNG ohne Abhängigkeit: RGBA, zlib aus Node.
import { crc32, deflateSync } from 'node:zlib';

const HINTERGRUND = [0x11, 0x11, 0x11] as const; // --sv-bg
const AKZENT = [0x33, 0xd1, 0xff] as const; // --sv-accent
/** Form in Anteilen der Kantenlänge: Ringradius, halbe Ringstärke, Punktradius. */
const RING_R = 0.3;
const RING_HALB = 0.03;
const PUNKT_R = 0.07;

export interface ManifestSymbol { src: string; sizes: string; type: string; purpose: 'any' | 'maskable' }
export interface WebAppManifest {
  name: string; short_name: string; description: string; lang: string; start_url: string; scope: string;
  display: 'standalone'; background_color: string; theme_color: string; icons: ManifestSymbol[];
}

export function webAppManifest(): WebAppManifest {
  return {
    name: 'siteBits',
    short_name: 'siteBits',
    description: 'Punktwolken, Panoramen und Befunde im Browser',
    lang: 'de',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    background_color: '#111111',
    theme_color: '#111111',
    icons: [
      { src: '/icons/scanview.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
      { src: '/icons/scanview-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/scanview-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      // Vollflächiger Hintergrund, die Form liegt im sicheren Kreis (80 %): taugt auch als maskierbares Symbol.
      { src: '/icons/scanview-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}

const hex = (c: readonly number[]): string => `#${c.map((x) => x.toString(16).padStart(2, '0')).join('')}`;

export function symbolSvg(): string {
  const n = 512;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}">` +
    `<rect width="${n}" height="${n}" rx="${n * 0.1875}" fill="${hex(HINTERGRUND)}"/>` +
    `<circle cx="${n / 2}" cy="${n / 2}" r="${n * RING_R}" fill="none" stroke="${hex(AKZENT)}" stroke-width="${n * RING_HALB * 2}"/>` +
    `<circle cx="${n / 2}" cy="${n / 2}" r="${n * PUNKT_R}" fill="${hex(AKZENT)}"/></svg>\n`;
}

function chunk(typ: string, daten: Buffer): Buffer {
  const kopf = Buffer.alloc(8);
  kopf.writeUInt32BE(daten.length, 0);
  kopf.write(typ, 4, 'latin1');
  const pruef = Buffer.alloc(4);
  pruef.writeUInt32BE(crc32(Buffer.concat([kopf.subarray(4), daten])) >>> 0, 0);
  return Buffer.concat([kopf, daten, pruef]);
}

/** Quadratisches PNG (RGBA, deckend) mit Kantenlänge n; Kanten geglättet über die Abdeckung je Pixel. */
export function symbolPng(n: number): Buffer {
  const zeile = 1 + n * 4;
  const roh = Buffer.alloc(n * zeile);
  const m = n / 2;
  const klemme = (x: number): number => Math.min(1, Math.max(0, x));
  for (let y = 0; y < n; y++) {
    roh[y * zeile] = 0; // Filter „None“
    for (let x = 0; x < n; x++) {
      const d = Math.hypot(x + 0.5 - m, y + 0.5 - m);
      const ring = klemme(n * RING_HALB - Math.abs(d - n * RING_R) + 0.5);
      const punkt = klemme(n * PUNKT_R - d + 0.5);
      const a = Math.max(ring, punkt);
      const i = y * zeile + 1 + x * 4;
      for (let k = 0; k < 3; k++) roh[i + k] = Math.round(HINTERGRUND[k]! + (AKZENT[k]! - HINTERGRUND[k]!) * a);
      roh[i + 3] = 255;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(n, 0);
  ihdr.writeUInt32BE(n, 4);
  ihdr[8] = 8; // Bit je Kanal
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(roh, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Alle ausgelieferten Dateien: Pfad (ab Wurzel) → Medientyp und Inhalt. */
export function webAppDateien(): Record<string, { typ: string; inhalt: string | Buffer }> {
  return {
    '/manifest.webmanifest': { typ: 'application/manifest+json', inhalt: `${JSON.stringify(webAppManifest(), null, 2)}\n` },
    '/icons/scanview.svg': { typ: 'image/svg+xml', inhalt: symbolSvg() },
    '/icons/scanview-180.png': { typ: 'image/png', inhalt: symbolPng(180) },
    '/icons/scanview-192.png': { typ: 'image/png', inhalt: symbolPng(192) },
    '/icons/scanview-512.png': { typ: 'image/png', inhalt: symbolPng(512) },
  };
}
