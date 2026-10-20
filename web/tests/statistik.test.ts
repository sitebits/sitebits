import { describe, expect, it } from 'vitest';
import { BUENDEL_MS, leerenBeimVerlassen, statistikSender, StatistikZaehler } from '../src/statistik';
import type { Zaehlstand } from '../src/statistik';
import { fakeUhr } from './fakePlattform';

describe('StatistikZaehler', () => {
  it('bündelt und schickt höchstens alle 10 s', () => {
    const gesendet: [Zaehlstand, boolean][] = [];
    const u = fakeUhr();
    const z = new StatistikZaehler(async (s, k) => void gesendet.push([s, k]), u.uhr);
    z.zaehle('oeffnen');
    z.zaehle('standpunkt');
    z.zaehle('standpunkt');
    expect(u.timer.map((t) => t.ms)).toEqual([BUENDEL_MS]);
    expect(gesendet).toEqual([]);
    u.alle();
    expect(gesendet).toEqual([[{ oeffnen: 1, standpunkt: 2, messung: 0, poi: 0 }, false]]);
    u.alle();
    expect(gesendet).toHaveLength(1);
  });
  it('leeren(true) beim Verlassen: sofort, mit keepalive, Timer weg; leer → nichts', () => {
    const gesendet: [Zaehlstand, boolean][] = [];
    const u = fakeUhr();
    const z = new StatistikZaehler(async (s, k) => void gesendet.push([s, k]), u.uhr);
    z.leeren(true);
    expect(gesendet).toEqual([]);
    z.zaehle('poi');
    z.leeren(true);
    expect(gesendet).toEqual([[{ oeffnen: 0, standpunkt: 0, messung: 0, poi: 1 }, true]]);
    expect(u.timer).toEqual([]);
  });
  it('Sendefehler stören die Arbeit nicht', async () => {
    const z = new StatistikZaehler(async () => {
      throw new Error('offline');
    }, fakeUhr().uhr);
    z.zaehle('messung');
    expect(() => z.leeren(true)).not.toThrow();
    await Promise.resolve();
  });
  it('statistikSender: POST mit CSRF-Header und keepalive', async () => {
    const aufrufe: { pfad: string; init?: RequestInit }[] = [];
    const fetchFn = (async (pfad: RequestInfo | URL, init?: RequestInit) => {
      aufrufe.push({ pfad: String(pfad), init });
      return new Response(null, { status: 204 });
    }) as typeof fetch;
    await statistikSender('ost', fetchFn)({ oeffnen: 1, standpunkt: 0, messung: 0, poi: 0 }, true);
    expect(aufrufe[0]!.pfad).toBe('/api/v1/statistik/ost');
    expect(aufrufe[0]!.init!.keepalive).toBe(true);
    expect((aufrufe[0]!.init!.headers as Record<string, string>)['X-Scanview']).toBe('1');
    expect(JSON.parse(String(aufrufe[0]!.init!.body))).toEqual({ oeffnen: 1, standpunkt: 0, messung: 0, poi: 0 });
  });
  it('leerenBeimVerlassen: pagehide und Tab verborgen senden sofort mit keepalive, sichtbar nicht', () => {
    const aufrufe: boolean[] = [];
    const fenster = new EventTarget();
    const dok = Object.assign(new EventTarget(), { visibilityState: 'visible' });
    leerenBeimVerlassen({ leeren: (k) => void aufrufe.push(k) }, fenster, dok);
    dok.dispatchEvent(new Event('visibilitychange'));
    expect(aufrufe).toEqual([]);
    dok.visibilityState = 'hidden';
    dok.dispatchEvent(new Event('visibilitychange'));
    fenster.dispatchEvent(new Event('pagehide'));
    expect(aufrufe).toEqual([true, true]);
  });
});
