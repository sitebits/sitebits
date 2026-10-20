import { describe, expect, it } from 'vitest';
import { PlattformFehler } from '../src/plattformApi';
import { EINWILLIGUNG_TEXT, KI_DATENSCHUTZ_HINWEIS, kontoFehlerText, mcpKonfiguration, NEU_ANMELDEN_HINWEIS } from '../src/portal/apiZugaenge';

describe('mcpKonfiguration', () => {
  it('gültiges JSON mit URL und Token in mcpServers.scanview.env', () => {
    const json = mcpKonfiguration('https://x', 'sv_abc');
    const d = JSON.parse(json) as { mcpServers: { scanview: { command: string; env: { SCANVIEW_URL: string; SCANVIEW_TOKEN: string } } } };
    expect(d.mcpServers.scanview.env.SCANVIEW_URL).toBe('https://x');
    expect(d.mcpServers.scanview.env.SCANVIEW_TOKEN).toBe('sv_abc');
    expect(d.mcpServers.scanview.command).toBe('scanview-mcp');
  });

  it('exakte Struktur (genau diese Felder)', () => {
    const d = JSON.parse(mcpKonfiguration('https://y', 'sv_z'));
    expect(d).toEqual({
      mcpServers: { scanview: { command: 'scanview-mcp', env: { SCANVIEW_URL: 'https://y', SCANVIEW_TOKEN: 'sv_z' } } },
    });
  });

  it('ist eingerückt (lesbar zum Einfügen in Claude Desktop)', () => {
    expect(mcpKonfiguration('https://x', 't')).toContain('\n');
  });
});

describe('kontoFehlerText (Security-Review Teil E, M1)', () => {
  it('403 „Bitte zuerst neu anmelden, um …“ → Anleitung abmelden und wieder anmelden', () => {
    for (const text of [
      'Bitte zuerst neu anmelden, um einen API-Zugang anzulegen.',
      'Bitte zuerst neu anmelden, um die Einwilligung einzuschalten.',
    ]) {
      expect(kontoFehlerText(new PlattformFehler(403, text))).toBe(NEU_ANMELDEN_HINWEIS);
    }
    expect(NEU_ANMELDEN_HINWEIS).toMatch(/abmelden, wieder anmelden/);
  });

  it('andere 403 (z. B. CSRF) und andere Status behalten den Servertext', () => {
    expect(kontoFehlerText(new PlattformFehler(403, 'Anfrage abgelehnt.'))).toBe('Anfrage abgelehnt.');
    expect(kontoFehlerText(new PlattformFehler(422, 'Bitte zuerst neu anmelden, um x.'))).toBe('Bitte zuerst neu anmelden, um x.');
    expect(kontoFehlerText(new Error('kaputt'))).toBe('kaputt');
    expect(kontoFehlerText('roh')).toBe('roh');
  });
});

describe('EINWILLIGUNG_TEXT (Security-Review Teil E, N1)', () => {
  it('sagt, dass der Schalter zur Exportzeit wirkt und Exportiertes bleibt', () => {
    expect(EINWILLIGUNG_TEXT).toContain('ab jetzt exportiert');
    expect(EINWILLIGUNG_TEXT).toContain('auch frühere, noch nicht exportierte');
    expect(EINWILLIGUNG_TEXT).toContain('Bereits exportierte Einträge bleiben unverändert');
    expect(EINWILLIGUNG_TEXT).not.toContain('künftige');
  });
});

describe('KI_DATENSCHUTZ_HINWEIS (Security-Review Teil E, M3)', () => {
  it('nennt Anbieter, fremde Namen und den Verweis auf die Anleitung', () => {
    expect(KI_DATENSCHUTZ_HINWEIS).toContain('an den Anbieter des KI-Programms');
    expect(KI_DATENSCHUTZ_HINWEIS).toContain('Namen anderer Beteiligter');
    expect(KI_DATENSCHUTZ_HINWEIS).toContain('„Datenschutz“');
  });

  it('sagt, dass Namen und Notizen ab Werk ausgeblendet sind (B-TE-M3)', () => {
    expect(KI_DATENSCHUTZ_HINWEIS).toContain('blendet der MCP-Server ab Werk aus');
    expect(KI_DATENSCHUTZ_HINWEIS).not.toContain('auch Namen anderer Beteiligter, Notizen');
  });
});

import { agentAuswahl } from '../src/portal/apiZugaenge';
it('KI-Auswahl ist bei Lesen gesperrt und wird beim Wechsel zurückgesetzt',() => {
  expect(agentAuswahl('schreiben',true)).toEqual({disabled:false,checked:true});
  expect(agentAuswahl('lesen',true)).toEqual({disabled:true,checked:false});
  expect(agentAuswahl('schreiben',agentAuswahl('lesen',true).checked)).toEqual({disabled:false,checked:false});
});
