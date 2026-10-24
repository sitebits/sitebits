// Drift und Abdeckung: schema.d.ts entspricht der versionierten OpenAPI-Datei, und jede Operation
// dort hat genau eine Client-Funktion (und umgekehrt). Schlägt nach jeder API-Änderung an, bis
// `npm run typen` gelaufen und der Client nachgezogen ist.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import openapiTS, { astToString, COMMENT_HEADER } from 'openapi-typescript';
import { OPERATIONEN, Sitebits } from '../src/index.ts';

const OPENAPI = new URL('../../../docs/public/api/openapi.json', import.meta.url);
const SCHEMA = new URL('../src/schema.d.ts', import.meta.url);

interface Beschreibung { paths: Record<string, Record<string, unknown>> }

test('schema.d.ts passt zur OpenAPI-Datei', async () => {
  const erwartet = COMMENT_HEADER + astToString(await openapiTS(OPENAPI));
  assert.ok(readFileSync(SCHEMA, 'utf8') === erwartet,
    'src/schema.d.ts passt nicht zu docs/public/api/openapi.json — neu erzeugen: npm run typen');
});

test('jede Operation genau eine Client-Funktion', () => {
  const d = JSON.parse(readFileSync(OPENAPI, 'utf8')) as Beschreibung;
  const api = new Set(Object.entries(d.paths).flatMap(([p, ops]) => Object.keys(ops).map(m => `${m} ${p}`)));
  const client = Object.values(OPERATIONEN).map(([m, p]) => `${m} ${p}`);
  assert.equal(new Set(client).size, client.length, 'doppelte Operation im Client');
  assert.deepEqual([...api].filter(o => !client.includes(o)).sort(), [], 'Operation ohne Client-Funktion');
  assert.deepEqual(client.filter(o => !api.has(o)).sort(), [], 'Client-Funktion ohne Operation');
});

test('jede Operation ist eine Methode der Klasse', () => {
  for (const name of Object.keys(OPERATIONEN)) {
    assert.equal(typeof (Sitebits.prototype as unknown as Record<string, unknown>)[name], 'function', name);
  }
});
