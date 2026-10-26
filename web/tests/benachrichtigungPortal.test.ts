import { describe, expect, it } from 'vitest';
import { BESTAETIGEN_PRAEFIX, tokenAus, zustandText } from '../src/portal/benachrichtigung';

describe('E-Mail-Benachrichtigung im Portal (F8)', () => {
  it('Zustand als Satz', () => {
    expect(zustandText({ eingeschaltet: false, bestaetigt: false, bestaetigungOffen: false })).toMatch(/^Aus/);
    expect(zustandText({ eingeschaltet: true, bestaetigt: true, bestaetigungOffen: false, email: 'a@b.test' })).toContain('a@b.test');
    expect(zustandText({ eingeschaltet: true, bestaetigt: false, bestaetigungOffen: true })).toContain('Bestätigungsmail');
    expect(zustandText({ eingeschaltet: true, bestaetigt: false, bestaetigungOffen: false })).toContain('nicht bestätigt');
  });
  it('Token nur in erwarteter Form', () => {
    expect(tokenAus(`${BESTAETIGEN_PRAEFIX}abcDEF_123-xyz`, BESTAETIGEN_PRAEFIX)).toBe('abcDEF_123-xyz');
    expect(tokenAus('#t=abc.def_ghijk', '#t=')).toBe('abc.def_ghijk');
    expect(tokenAus('#t=<script>alert(1)</script>', '#t=')).toBeNull();
    expect(tokenAus('#t=kurz', '#t=')).toBeNull();
    expect(tokenAus('#anderes=abcdefghijkl', '#t=')).toBeNull();
  });
});
