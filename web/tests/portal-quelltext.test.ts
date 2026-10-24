import { afterEach, describe, expect, it, vi } from 'vitest';
import { fussLinks, quelltextLink } from '../src/portal/karten';
import { sitzung } from '../src/portal/api';

// Die Quelltext-Adresse (EUPL) kommt je Instanz vom Server (/api/sitzung → quelltext_url);
// ohne Einstellung wird kein Link gezeigt, fest im Code steht keine Adresse.
const URL = 'https://quelltext.example.org/scanview';

describe('Quelltext-Link', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('übernimmt nur https-Adressen', () => {
    expect(quelltextLink(URL)).toBe(URL);
    expect(quelltextLink(null)).toBeNull();
    expect(quelltextLink(undefined)).toBeNull();
    expect(quelltextLink('')).toBeNull();
    expect(quelltextLink('javascript:alert(1)')).toBeNull();
    expect(quelltextLink('http://quelltext.example.org/')).toBeNull();
    expect(quelltextLink(42)).toBeNull();
  });

  it('Fußzeile: mit Adresse Lizenzen + Quelltext, ohne nur Lizenzen', () => {
    expect(fussLinks(URL)).toEqual([
      { href: '/THIRD_PARTY_LICENSES.txt', text: 'Lizenzen' },
      { href: URL, text: 'Quelltext (EUPL-1.2)' },
    ]);
    expect(fussLinks(null)).toEqual([{ href: '/THIRD_PARTY_LICENSES.txt', text: 'Lizenzen' }]);
  });

  it('sitzung() liefert quelltextUrl aus der Serverantwort (gesetzt und leer)', async () => {
    const antwort = (daten: unknown): Response => new Response(JSON.stringify(daten), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
    const nutzer = { name: 'N', email: 'n@b.de', admin: false };
    vi.stubGlobal('fetch', vi.fn(async () => antwort({ dienst: 'scanview', nutzer, quelltext_url: URL })));
    expect(await sitzung()).toEqual({ ...nutzer, quelltextUrl: URL, importAn: false });
    vi.stubGlobal('fetch', vi.fn(async () => antwort({ dienst: 'scanview', nutzer, quelltext_url: null })));
    expect(await sitzung()).toEqual({ ...nutzer, quelltextUrl: null, importAn: false });
    vi.stubGlobal('fetch', vi.fn(async () => antwort({ dienst: 'scanview', nutzer })));
    expect(await sitzung()).toEqual({ ...nutzer, quelltextUrl: null, importAn: false });
    // Hochladen/neues Projekt nur, wenn der Server es ausdrücklich meldet.
    vi.stubGlobal('fetch', vi.fn(async () => antwort({ dienst: 'scanview', nutzer, import: true })));
    expect((await sitzung())?.importAn).toBe(true);
    vi.stubGlobal('fetch', vi.fn(async () => antwort({ dienst: 'scanview', nutzer, import: 'ja' })));
    expect((await sitzung())?.importAn).toBe(false);
  });
});
