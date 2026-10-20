import { describe, expect, it } from 'vitest';
import { probeRange } from '../src/rangeProbe';

const res = (status: number, headers: Record<string, string>) =>
  Promise.resolve(new Response(new Uint8Array(16), { status, headers }));

describe('probeRange', () => {
  it('ok bei 206 und Content-Range', async () => {
    const r = await probeRange('u', () => res(206, { 'Content-Range': 'bytes 0-15/12345' }));
    expect(r).toEqual({ ok: true, size: 12345 });
  });
  it('lehnt 200 (kein Range-Support) ab', async () => {
    const r = await probeRange('u', () => res(200, {}));
    expect(r.ok).toBe(false);
  });
  it('lehnt fehlendes Content-Range ab', async () => {
    const r = await probeRange('u', () => res(206, {}));
    expect(r.ok).toBe(false);
  });
});

/** Antwort, deren Körper-Abbruch beobachtbar ist. */
function cancellable(status: number, headers: Record<string, string>) {
  const state = { cancelled: false };
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      c.enqueue(new Uint8Array(16));
    },
    cancel() {
      state.cancelled = true;
    },
  });
  return { state, response: new Response(body, { status, headers }) };
}

describe('probeRange Fehlerpfade (I3)', () => {
  it('Netzfehler → verständliche Meldung statt „Failed to fetch“', async () => {
    const r = await probeRange('http://x/cloud.copc.laz', () => Promise.reject(new TypeError('Failed to fetch')));
    expect(r).toEqual({ ok: false, reason: 'Server nicht erreichbar (http://x/cloud.copc.laz).' });
  });
  it('verwirft den Körper bei 200 (kein Range-Support, sonst Volldownload)', async () => {
    const { state, response } = cancellable(200, {});
    const r = await probeRange('u', () => Promise.resolve(response));
    expect(r.ok).toBe(false);
    await Promise.resolve();
    expect(state.cancelled).toBe(true);
  });
  it('verwirft den Körper auch nach erfolgreicher Probe', async () => {
    const { state, response } = cancellable(206, { 'Content-Range': 'bytes 0-15/99' });
    const r = await probeRange('u', () => Promise.resolve(response));
    expect(r).toEqual({ ok: true, size: 99 });
    await Promise.resolve();
    expect(state.cancelled).toBe(true);
  });
});
