import { describe, expect, it } from 'vitest';
import { fromB64url, toB64url } from '../src/portal/b64url';

describe('b64url', () => {
  it('Rundreise ohne Padding', () => {
    const bytes = new Uint8Array([0, 251, 255, 1, 2]);
    const s = toB64url(bytes.buffer);
    expect(s).toBe('APv_AQI');
    expect(new Uint8Array(fromB64url(s))).toEqual(bytes);
  });
});
