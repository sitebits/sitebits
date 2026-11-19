// Läufer der gemeinsamen Fall-Tabelle (docs/public/api/fehlerfaelle.json) für den Client:
// Jeder Fehlerfall kommt als SitebitsFehler mit festem `code` und `hinweis` an. Ob die Instanz den
// Code wirklich so liefert, prüft der Server-Läufer (server/tests/test_fehlercodes.py).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { Sitebits, SitebitsFehler } from '../src/index.ts';

interface Fall { fall: string; status: number; code: string }

const { faelle } = JSON.parse(
  readFileSync(new URL('../../../docs/public/api/fehlerfaelle.json', import.meta.url), 'utf8'),
) as { faelle: Fall[] };

for (const f of faelle) {
  test(`Fall ${f.fall}: SitebitsFehler trägt Code und Hinweis`, async () => {
    const hinweis = `Hinweis zu ${f.code}`;
    const fetch = (async () => new Response(
      JSON.stringify({ dienst: 'scanview', fehler: 'Satz für Menschen', code: f.code, hinweis }),
      { status: f.status, headers: { 'Content-Type': 'application/json' } },
    )) as typeof globalThis.fetch;
    const c = new Sitebits({ basis: 'https://bits.example.org', token: 'sv_test', fetch });
    await assert.rejects(c.projekt('ost'), (e: unknown) => {
      assert.ok(e instanceof SitebitsFehler);
      assert.equal(e.status, f.status);
      assert.equal(e.code, f.code);
      assert.equal(e.hinweis, hinweis);
      assert.match(e.message, new RegExp(`\\[${f.code}\\]`));
      return true;
    });
  });
}

test('alte Instanz ohne Code: code und hinweis sind null', async () => {
  const fetch = (async () => new Response(JSON.stringify({ dienst: 'scanview', fehler: 'x' }),
    { status: 403 })) as typeof globalThis.fetch;
  const c = new Sitebits({ basis: 'https://bits.example.org', token: 'sv_test', fetch });
  await assert.rejects(c.projekt('ost'), (e: unknown) =>
    e instanceof SitebitsFehler && e.code === null && e.hinweis === null);
});
