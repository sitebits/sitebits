// Eigener Basis-Transcoder (vendor/basis): ohne Laufzeit-Codeerzeugung, Hashes wie im README,
// und passend zum KTX2Loader der installierten three-Version.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

const vendor = (name: string): string => fileURLToPath(new URL(`../vendor/basis/${name}`, import.meta.url));
const sha256 = (pfad: string): string => createHash('sha256').update(readFileSync(pfad)).digest('hex');

it('eigener Transcoder erzeugt keinen Code zur Laufzeit (CSP ohne unsafe-eval)', () => {
  const js = readFileSync(vendor('basis_transcoder.js'), 'utf8');
  expect(js).not.toMatch(/(^|[^A-Za-z_.])(new Function|Function\(|eval\()/);
  expect(js).toContain('var BASIS');
});

it('eingecheckte Dateien entsprechen den Hashes im README', () => {
  const readme = readFileSync(vendor('README.md'), 'utf8');
  for (const name of ['basis_transcoder.js', 'basis_transcoder.wasm']) {
    expect(readme).toContain(sha256(vendor(name)));
  }
});

it('three bringt noch den Upstream-Transcoder v1_50_0 mit, zu dem der eigene Bau passt', () => {
  // Ändert ein three-Update den Transcoder, kann sich die Schnittstelle zum KTX2Loader ändern:
  // dann vendor/basis/README.md (Quelle/Commit) nachziehen, bauen.sh --schreiben, Hashes hier.
  const require = createRequire(import.meta.url);
  const upstream = {
    'basis_transcoder.js': '8478b5b6d6b74e7d3082b89f6417321d8d1dc0307f2b30d4484bb11b441696a1',
    'basis_transcoder.wasm': '6cf17dc889352c42e9acf8897107978d127005fe3386c36a0e3845e27967630a',
  };
  for (const [name, hash] of Object.entries(upstream)) {
    expect(sha256(require.resolve(`three/examples/jsm/libs/basis/${name}`)), name).toBe(hash);
  }
});
