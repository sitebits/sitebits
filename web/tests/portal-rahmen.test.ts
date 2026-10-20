import { afterEach, describe, expect, it, vi } from 'vitest';
import { entwuerfeLoeschen, feedbackHref, INSTANZ_ZEITGRENZE_MS, ladeInstanz, nurPfad, OHNE_INSTANZ, pruefeInstanz, rechtsLinks, seitenTitel } from '../src/portal/rahmen';
import { ENTWURF_DB } from '../src/entwurfKonstanten';

// Feedback-Weg je Instanz; nie Query/Fragment in die Mail (Einladungs-Token, weiter=).
// WCAG 2.4.2: jede Seite hat einen eigenen Titel.
const MAIL = 'mailto:barrierefreiheit@stelle.example.org';
const SEITE = { titel: 'Anmelden – siteBits', origin: 'https://scanview.example.org', pfad: '/anmelden' };
const ERKLAERUNG = {
  schema: 1, stelle: 'Beispiel-Hochschule', geltungsbereich: 'https://scanview.example.org', stand: 'teilweise',
  erstellt: '2026-10-01', methode: 'Selbstbewertung', methode_text: 'Text',
  nicht_barrierefrei: [{ inhalt: 'a', grund: 'b', alternative: 'c' }],
  durchsetzung: { text: 't', stelle: 's', url: 'https://ombudsstelle.example.org' },
};

describe('seitenTitel', () => {
  it('liefert je Seite einen eigenen Titel', () => {
    const pfade = ['/anmelden', '/einladung', '/verlauf', '/befunde', '/verwaltung', '/projekt-einstellungen', '/barrierefreiheit', '/'];
    const titel = pfade.map(seitenTitel);
    expect(new Set(titel).size).toBe(pfade.length);
    expect(seitenTitel('/anmelden')).toBe('Anmelden – siteBits');
    expect(seitenTitel('/projekt-einstellungen')).toBe('Projekt-Einstellungen – siteBits');
    expect(seitenTitel('/barrierefreiheit')).toBe('Erklärung zur Barrierefreiheit – siteBits');
    expect(seitenTitel('/befunde')).toBe('Befunde – siteBits');
    expect(seitenTitel('/irgendwas')).toBe('Projekte – siteBits');
  });
});

describe('feedbackHref', () => {
  it('ohne Kontakt: Abschnitt der Seite Barrierefreiheit', () => {
    expect(feedbackHref(null, SEITE)).toBe('/barrierefreiheit#melden');
  });
  it('Formular: Adresse unverändert', () => {
    const f = { art: 'formular' as const, ziel: 'https://stelle.example.org/melden' };
    expect(feedbackHref(f, SEITE)).toBe('https://stelle.example.org/melden');
  });
  it('mailto: Betreff und Seite kodiert, nur Pfad', () => {
    const href = feedbackHref({ art: 'mailto', ziel: MAIL }, SEITE);
    expect(href.startsWith(`${MAIL}?subject=`)).toBe(true);
    const q = new URLSearchParams(href.slice(href.indexOf('?') + 1));
    expect(q.get('subject')).toBe('Barriere melden: Anmelden – siteBits');
    expect(q.get('body')).toContain('Seite: https://scanview.example.org/anmelden');
  });
  it('nie Einladungs-Token, weiter= oder Kopfzeilen in der Mail', () => {
    for (const pfad of ['/einladung#t=GEHEIMTOKEN123', '/anmelden?weiter=%2Fverlauf%3Fp%3Dx', '/verlauf?p=a#b']) {
      const href = feedbackHref({ art: 'mailto', ziel: MAIL }, { ...SEITE, pfad });
      expect(href).not.toContain('GEHEIM');
      expect(href).not.toContain('weiter');
      expect(href).not.toContain('p%3Da');
    }
    const boese = feedbackHref({ art: 'mailto', ziel: MAIL }, { ...SEITE, titel: 'x\r\nBcc: a@b.example' });
    expect(boese).not.toMatch(/[\r\n]/);
    expect(boese).toContain('%0D%0ABcc');
  });
  it('nurPfad schneidet Query und Fragment ab', () => {
    expect(nurPfad('/einladung#t=x')).toBe('/einladung');
    expect(nurPfad('/verlauf?p=a')).toBe('/verlauf');
    expect(nurPfad('')).toBe('/');
  });
});

describe('pruefeInstanz', () => {
  it('übernimmt gültige Angaben', () => {
    const i = pruefeInstanz({ dienst: 'scanview', kontakt: { art: 'mailto', ziel: MAIL }, erklaerung: ERKLAERUNG,
      produkt_einschraenkungen: [{ inhalt: 'x', grund: 'y', alternative: 'z' }] });
    expect(i.kontakt).toEqual({ art: 'mailto', ziel: MAIL });
    expect(i.erklaerung?.stelle).toBe('Beispiel-Hochschule');
    expect(i.produktEinschraenkungen).toHaveLength(1);
  });
  it('verwirft gefährliche oder kaputte Kontakte', () => {
    for (const kontakt of [
      { art: 'mailto', ziel: 'javascript:alert(1)' },
      { art: 'mailto', ziel: `${MAIL}?cc=x@y.example` },
      { art: 'formular', ziel: 'http://stelle.example.org' },
      { art: 'formular', ziel: 'javascript:alert(1)' },
      'mailto:x@y.example', null, 42,
    ]) {
      expect(pruefeInstanz({ kontakt, erklaerung: null, produkt_einschraenkungen: [] }).kontakt).toBeNull();
    }
  });
  it('verwirft eine unvollständige Erklärung und Müll', () => {
    expect(pruefeInstanz({ kontakt: null, erklaerung: { ...ERKLAERUNG, stand: 'fast' } }).erklaerung).toBeNull();
    expect(pruefeInstanz({ kontakt: null, erklaerung: { ...ERKLAERUNG, durchsetzung: { ...ERKLAERUNG.durchsetzung, url: 'javascript:x' } } }).erklaerung).toBeNull();
    expect(pruefeInstanz(null)).toEqual(OHNE_INSTANZ);
    expect(pruefeInstanz({ produkt_einschraenkungen: [{ inhalt: 1 }] }).produktEinschraenkungen).toEqual([]);
  });
});

describe('ladeInstanz', () => {
  afterEach(() => vi.unstubAllGlobals());
  const antwort = (status: number, daten: unknown): Response =>
    new Response(JSON.stringify(daten), { status, headers: { 'Content-Type': 'application/json' } });

  it('liefert die geprüften Angaben', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => antwort(200, { kontakt: { art: 'mailto', ziel: MAIL }, erklaerung: null, produkt_einschraenkungen: [] })));
    expect((await ladeInstanz())?.kontakt?.ziel).toBe(MAIL);
  });
  it('null bei Serverfehler und bei Netzfehler', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => antwort(500, { fehler: 'x' })));
    expect(await ladeInstanz()).toBeNull();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    expect(await ladeInstanz()).toBeNull();
  });
  it('gibt bei hängender Anfrage nach der Zeitgrenze null zurück', async () => {
    vi.useFakeTimers();
    try {
      // Ein fetch, der nie antwortet, sondern nur auf den Abbruch reagiert.
      vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_ok, fehl) => {
        init?.signal?.addEventListener('abort', () => fehl(new DOMException('abgebrochen', 'AbortError')));
      })));
      const ergebnis = ladeInstanz();
      await vi.advanceTimersByTimeAsync(INSTANZ_ZEITGRENZE_MS);
      expect(await ergebnis).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('entwuerfeLoeschen (Abmelden)', () => {
  const fabrik = (verhalten: 'success' | 'error' | 'blocked' | 'nie' | 'wirft') => {
    const namen: string[] = [];
    const idb = {
      deleteDatabase: (name: string) => {
        namen.push(name);
        if (verhalten === 'wirft') throw new Error('gesperrt');
        const r: Record<string, (() => void) | null> = { onsuccess: null, onerror: null, onblocked: null };
        if (verhalten !== 'nie') queueMicrotask(() => r[`on${verhalten}`]?.());
        return r;
      },
    } as unknown as IDBFactory;
    return { idb, namen };
  };
  it.each(['success', 'error', 'blocked', 'wirft'] as const)('löscht die Entwurfsdatenbank und ist sofort fertig bei %s (wartet nicht auf die Frist)', async (v) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const { idb, namen } = fabrik(v);
      let fertig = false;
      void entwuerfeLoeschen(idb).then(() => {
        fertig = true;
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(fertig).toBe(true);
      expect(namen).toEqual([ENTWURF_DB]);
    } finally {
      vi.useRealTimers();
    }
  });
  it('ohne IndexedDB sofort fertig', async () => {
    await expect(entwuerfeLoeschen(undefined)).resolves.toBeUndefined();
  });
  it('hängt höchstens eine Sekunde', async () => {
    vi.useFakeTimers();
    try {
      let fertig = false;
      void entwuerfeLoeschen(fabrik('nie').idb).then(() => {
        fertig = true;
      });
      await vi.advanceTimersByTimeAsync(999);
      expect(fertig).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      expect(fertig).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

// Impressum und Datenschutzhinweise: nur Verlinkung; dieselben Regeln wie der Server (rechtliches.py).
describe('Impressum und Datenschutz', () => {
  const IMPRESSUM = 'https://stelle.example.org/impressum';
  const DATENSCHUTZ = '/datenschutz.html';
  it('pruefeInstanz übernimmt https-Adressen und Pfade derselben Origin', () => {
    const i = pruefeInstanz({ kontakt: null, erklaerung: null, impressum: IMPRESSUM, datenschutz: DATENSCHUTZ });
    expect(i.impressum).toBe(IMPRESSUM);
    expect(i.datenschutz).toBe(DATENSCHUTZ);
    expect(OHNE_INSTANZ.impressum).toBeNull();
    expect(OHNE_INSTANZ.datenschutz).toBeNull();
  });
  it('verwirft gefährliche, fremde oder kaputte Links', () => {
    for (const wert of [
      'javascript:alert(1)', 'http://stelle.example.org/i', 'mailto:recht@stelle.example.org', 'data:text/html,x',
      'https://nutzer:pw@stelle.example.org/', 'https://', '//boese.example/i', '/\\boese.example/i',
      'impressum.html', '/mit leerzeichen', '/zeile\n', '/impressüm', `/${'a'.repeat(2000)}`, 42, null, undefined,
    ]) {
      const i = pruefeInstanz({ impressum: wert, datenschutz: wert });
      expect(i.impressum, String(wert)).toBeNull();
      expect(i.datenschutz, String(wert)).toBeNull();
    }
  });
  it('rechtsLinks: nur gesetzte Links, Impressum vor Datenschutz', () => {
    expect(rechtsLinks(IMPRESSUM, DATENSCHUTZ)).toEqual([
      { href: IMPRESSUM, text: 'Impressum' },
      { href: DATENSCHUTZ, text: 'Datenschutz' },
    ]);
    expect(rechtsLinks(null, DATENSCHUTZ)).toEqual([{ href: DATENSCHUTZ, text: 'Datenschutz' }]);
    expect(rechtsLinks(null, null)).toEqual([]);
  });
});
