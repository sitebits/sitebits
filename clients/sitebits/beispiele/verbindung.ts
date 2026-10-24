// Gemeinsam für die Beispiele: Adresse und Token aus der Umgebung, nie aus der Kommandozeile
// (dort stünde das Token in der Prozessliste und im Shell-Verlauf).
import { Sitebits } from '../src/index.ts';

export function verbinden(): Sitebits {
  const basis = process.env.SITEBITS_URL;
  const token = process.env.SITEBITS_TOKEN;
  if (!basis || !token) {
    console.error('SITEBITS_URL und SITEBITS_TOKEN setzen (Adresse der Instanz, persönliches API-Token).');
    process.exit(2);
  }
  return new Sitebits({ basis, token });
}

/** Leitung oder Admin im Projekt: nur sie dürfen Auswertungen starten. */
export const darfAuswerten = (rolle: string): boolean => rolle === 'projektleitung' || rolle === 'admin';
