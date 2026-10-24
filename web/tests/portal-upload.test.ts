import { createHash, webcrypto } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import {
  contentRange, digestKopf, fortsetzenSchluessel, gemerkt, groesseText, importOptionen, importText, merken, offsetAus,
  pruefeEingabe, stueckDigest, stueckPlan, uploadText, vergessen, type Eingabe,
} from '../src/portal/uploadModell';
import { senden, stand } from '../src/portal/uploadSenden';
import { seitenTitel } from '../src/portal/rahmen';

const subtle = webcrypto.subtle as unknown as SubtleCrypto;

describe('Stückplanung', () => {
  it('feste Stücke, das letzte trägt den Rest', () => {
    expect(stueckPlan(10, 4)).toEqual([{ a: 0, b: 3 }, { a: 4, b: 7 }, { a: 8, b: 9 }]);
    expect(stueckPlan(8, 4)).toEqual([{ a: 0, b: 3 }, { a: 4, b: 7 }]);
    expect(stueckPlan(0, 4)).toEqual([]);
  });
  it('Fortsetzen beginnt an der Stückgrenze des Stands', () => {
    expect(stueckPlan(10, 4, 4)).toEqual([{ a: 4, b: 7 }, { a: 8, b: 9 }]);
    expect(stueckPlan(10, 4, 5)[0]).toEqual({ a: 4, b: 7 });
    expect(stueckPlan(10, 4, 10)).toEqual([]);
  });
  it('64-MiB-Stücke einer 5-GB-Datei', () => {
    const plan = stueckPlan(5e9, 64 * 1024 * 1024);
    expect(plan.length).toBe(Math.ceil(5e9 / (64 * 1024 * 1024)));
    expect(plan.at(-1)!.b).toBe(5e9 - 1);
  });
});

describe('Kopfzeilen', () => {
  it('Content-Range wie der Server sie liest', () => {
    expect(contentRange(0, 1023, 3000)).toBe('bytes 0-1023/3000');
  });
  it('Content-Digest = SHA-256 des Stücks, base64 (gegen Node crypto)', async () => {
    const daten = new TextEncoder().encode('Stück eins – mit Umlauten');
    const soll = `sha-256=:${createHash('sha256').update(daten).digest('base64')}:`;
    expect(await stueckDigest(daten.buffer, subtle)).toBe(soll);
    expect(digestKopf(new Uint8Array(32).buffer)).toBe(`sha-256=:${'A'.repeat(43)}=:`);
  });
  it('Upload-Offset nur als Zahl', () => {
    expect(offsetAus('1024')).toBe(1024);
    expect(offsetAus('-1')).toBeNull();
    expect(offsetAus(null)).toBeNull();
    expect(offsetAus('12abc')).toBeNull();
  });
});

class Speicher {
  daten = new Map<string, string>();
  getItem(k: string): string | null { return this.daten.get(k) ?? null; }
  setItem(k: string, v: string): void { this.daten.set(k, v); }
  removeItem(k: string): void { this.daten.delete(k); }
}

describe('Fortsetzen-Zuordnung', () => {
  const datei = { name: 'modell.glb', size: 1234, lastModified: 1700000000000 };
  const m = { uploadId: 'a'.repeat(32), projekt: 'neu', titel: 'T', quelle: 'splats' as const, crs: '', quellHoehe: '', zielHoehe: '' };
  it('dieselbe Datei findet ihren Upload wieder, eine geänderte nicht', () => {
    const s = new Speicher();
    merken(fortsetzenSchluessel(datei), m, s);
    expect(gemerkt(fortsetzenSchluessel(datei), s)).toEqual(m);
    expect(gemerkt(fortsetzenSchluessel({ ...datei, lastModified: 1 }), s)).toBeNull();
    expect(gemerkt(fortsetzenSchluessel({ ...datei, size: 1235 }), s)).toBeNull();
    vergessen(fortsetzenSchluessel(datei), s);
    expect(gemerkt(fortsetzenSchluessel(datei), s)).toBeNull();
  });
  it('kaputter oder fremder Eintrag gilt als nicht gemerkt', () => {
    const s = new Speicher();
    s.setItem('x', '{kaputt');
    expect(gemerkt('x', s)).toBeNull();
    s.setItem('y', JSON.stringify({ ...m, uploadId: '../x' }));
    expect(gemerkt('y', s)).toBeNull();
  });
  it('gesperrter Speicher wirft nie', () => {
    const boese = { getItem: () => { throw new Error('gesperrt'); }, setItem: () => { throw new Error('voll'); }, removeItem: () => { throw new Error('x'); } };
    expect(() => merken('k', m, boese)).not.toThrow();
    expect(gemerkt('k', boese)).toBeNull();
    expect(() => vergessen('k', boese)).not.toThrow();
  });
});

const EINGABE: Eingabe = { projekt: 'fassade-nord', titel: '', quelle: 'mesh', datei: { name: 'm.glb', size: 10 }, crs: '', quellHoehe: '', zielHoehe: '' };

describe('Formular', () => {
  it('gültige Eingabe ohne CRS', () => {
    expect(pruefeEingabe(EINGABE)).toEqual({});
    expect(importOptionen(EINGABE)).toEqual({ projekt: 'fassade-nord' });
  });
  it.each([
    [{ projekt: 'Fassade' }, 'projekt'], [{ projekt: '-x' }, 'projekt'], [{ projekt: '' }, 'projekt'],
    [{ datei: null }, 'datei'], [{ datei: { name: 'a.zip', size: 3 } }, 'datei'], [{ datei: { name: 'a.spz', size: 3 } }, 'datei'],
    [{ datei: { name: 'a.obj', size: 3 } }, 'datei'], [{ datei: { name: 'a.glb', size: 0 } }, 'datei'],
    [{ crs: '25833' }, 'crs'], [{ quellHoehe: 'dhhn2016' }, 'hoehe'], [{ titel: 'x'.repeat(121) }, 'titel'],
  ])('%o → Fehler in %s', (aenderung, feld) => {
    expect(Object.keys(pruefeEingabe({ ...EINGABE, ...aenderung }))).toEqual([feld]);
  });
  it('Archiv-Hinweis nennt den Ordnerweg', () => {
    expect(pruefeEingabe({ ...EINGABE, datei: { name: 'all.zip', size: 3 } }).datei).toMatch(/Ordner entpacken/);
  });
  it('Splats nehmen PLY, SPZ, SPLAT', () => {
    for (const n of ['a.ply', 'a.SPZ', 'a.splat']) expect(pruefeEingabe({ ...EINGABE, quelle: 'splats', datei: { name: n, size: 3 } })).toEqual({});
  });
  it('Optionen mit CRS und Höhen, Höhen nur mit CRS', () => {
    expect(importOptionen({ ...EINGABE, titel: ' Nord ', crs: 'EPSG:25833', quellHoehe: 'dhhn2016', zielHoehe: 'dhhn92' }))
      .toEqual({ projekt: 'fassade-nord', titel: 'Nord', crs: 'EPSG:25833', quellHoehe: 'dhhn2016', zielHoehe: 'dhhn92' });
    expect(importOptionen({ ...EINGABE, quellHoehe: 'dhhn2016' })).toEqual({ projekt: 'fassade-nord' });
  });
});

describe('Zustandstexte', () => {
  it('Importe', () => {
    expect(importText({ status: 'wartend', stufe: null, warteplatz: 2 })).toBe('wartet (Platz 2)');
    expect(importText({ status: 'laufend', stufe: 'konvertiert', warteplatz: null })).toBe('wird umgewandelt');
    expect(importText({ status: 'laufend', stufe: 'prueft', warteplatz: null })).toBe('wird geprüft');
    expect(importText({ status: 'laufend', stufe: 'freischalten', warteplatz: null })).toBe('wird freigeschaltet');
    expect(importText({ status: 'laufend', stufe: null, warteplatz: null })).toBe('läuft');
    expect(importText({ status: 'fertig', stufe: null, warteplatz: null })).toBe('freigeschaltet');
    expect(importText({ status: 'fehlgeschlagen', stufe: null, warteplatz: null })).toBe('fehlgeschlagen');
  });
  it('Uploads und Größen', () => {
    expect(uploadText({ status: 'offen', stand: 50, groesse: 200 })).toBe('unvollständig (25 %)');
    expect(uploadText({ status: 'abgeschlossen', stand: 200, groesse: 200 })).toBe('vollständig, geprüft');
    expect(groesseText(512)).toBe('512 Byte');
    expect(groesseText(5_000_000_000)).toBe('5 GB');
  });
  it('Seitentitel', () => {
    expect(seitenTitel('/uploads')).toBe('Uploads – siteBits');
  });
});

/** Nachgebauter Server: nimmt Stücke nur am Stand an, prüft Content-Range und Content-Digest. */
function server(groesse: number, stueck: number) {
  const inhalt = new Uint8Array(groesse);
  const z = { stand: 0, puts: 0, netzWegNach: Infinity };
  const f = (async (_url: string, init?: RequestInit): Promise<Response> => {
    if (init?.method === 'HEAD') return new Response(null, { status: 204, headers: { 'Upload-Offset': String(z.stand) } });
    z.puts += 1;
    if (z.puts > z.netzWegNach) throw new TypeError('Netz weg');
    const h = init!.headers as Record<string, string>;
    const t = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(h['Content-Range']!)!;
    const [a, b, n] = [Number(t[1]), Number(t[2]), Number(t[3])];
    const daten = new Uint8Array(init!.body as ArrayBuffer);
    if (n !== groesse || a !== z.stand) return new Response(JSON.stringify({ fehler: 'Stand' }), { status: 409, headers: { 'Upload-Offset': String(z.stand) } });
    if (b - a + 1 !== Math.min(stueck, groesse - a) || daten.length !== b - a + 1) return new Response('{}', { status: 422 });
    if (h['Content-Digest'] !== `sha-256=:${createHash('sha256').update(daten).digest('base64')}:`)
      return new Response(JSON.stringify({ fehler: 'Prüfsumme' }), { status: 422 });
    inhalt.set(daten, a);
    z.stand = b + 1;
    return new Response(JSON.stringify({ stand: z.stand }), { status: 200 });
  }) as typeof fetch;
  return { f, inhalt, z };
}

const sha = (d: Uint8Array): string => createHash('sha256').update(d).digest('hex');

describe('Senden', () => {
  const quelle = Uint8Array.from({ length: 10_000 }, (_v, i) => (i * 7919) % 251);
  const datei = Object.assign(new Blob([quelle]), { name: 'm.glb' });
  const id = 'a'.repeat(32);
  const offen = (): AbortSignal => new AbortController().signal;

  it('Abbruch und Fortsetzen über HEAD ergeben dieselbe SHA-256', async () => {
    const s = server(quelle.length, 1024);
    s.z.netzWegNach = 3;
    await expect(senden(id, 0, datei, 1024, 0, () => undefined, offen(), s.f, subtle)).rejects.toMatchObject({ status: 0 });
    expect(s.z.stand).toBe(3 * 1024);
    s.z.netzWegNach = Infinity;
    const ab = await stand(id, 0, s.f);
    expect(ab).toBe(3 * 1024);
    const meldungen: number[] = [];
    await senden(id, 0, datei, 1024, ab, (n) => meldungen.push(n), offen(), s.f, subtle);
    expect(s.z.stand).toBe(quelle.length);
    expect(sha(s.inhalt)).toBe(sha(quelle));
    expect(s.z.puts).toBe(3 + 1 + Math.ceil((quelle.length - ab) / 1024)); // 3 + ein gescheitertes + Rest
    expect(meldungen.at(-1)).toBe(quelle.length);
  });

  it('409 mit anderem Stand: dort weiter (Stück schon angekommen)', async () => {
    const s = server(quelle.length, 1024);
    await senden(id, 0, Object.assign(new Blob([quelle.subarray(0, 2048)]), { name: 'm.glb' }), 1024, 0, () => undefined, offen(),
      (async (u: string, init?: RequestInit) => {
        const h = { ...(init!.headers as Record<string, string>) };
        h['Content-Range'] = h['Content-Range']!.replace(/\/\d+$/, `/${quelle.length}`);
        return s.f(u, { ...init, headers: h });
      }) as typeof fetch, subtle);
    expect(s.z.stand).toBe(2048);
    await senden(id, 0, datei, 1024, 0, () => undefined, offen(), s.f, subtle);
    expect(s.z.stand).toBe(quelle.length);
    expect(sha(s.inhalt)).toBe(sha(quelle));
  });

  it('Abbrechen per AbortController stoppt', async () => {
    const s = server(quelle.length, 1024);
    const c = new AbortController();
    const f = (async (u: string, init?: RequestInit) => {
      if (s.z.stand >= 2048) { c.abort(); throw new DOMException('abgebrochen', 'AbortError'); }
      return s.f(u, init);
    }) as typeof fetch;
    await expect(senden(id, 0, datei, 1024, 0, () => undefined, c.signal, f, subtle)).rejects.toThrow('abgebrochen');
    expect(s.z.stand).toBe(2048);
  });

  it('falsche Prüfsumme des Servers kommt als Fehlertext an', async () => {
    const f = (async () => new Response(JSON.stringify({ fehler: 'Prüfsumme des Stücks stimmt nicht' }), { status: 422 })) as unknown as typeof fetch;
    await expect(senden(id, 0, datei, 1024, 0, () => undefined, offen(), f, subtle)).rejects.toThrow('Prüfsumme des Stücks stimmt nicht');
  });
});
