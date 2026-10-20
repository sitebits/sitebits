import sprueche from '../../spleen/sprueche.json';

// Nicht eager: jedes Punktbild wird ein eigener kleiner Chunk, geladen wird nur das eine gezogene.
const bildLader = Object.values(import.meta.glob<string>('../../spleen/bilder/*.txt', {
  query: '?raw', import: 'default',
}));

export const bildAnzahl = bildLader.length;

export function waehleSpruch(sprache: string, zufall: () => number = Math.random): string {
  const index = Math.floor(zufall() * (sprueche.eigen.length + sprueche.zitate.length));
  const eigen = sprueche.eigen[index];
  if (eigen) return eigen[sprache.toLowerCase().startsWith('de') ? 'de' : 'en'];
  const zitat = sprueche.zitate[index - sprueche.eigen.length]!;
  return `${zitat.text}\n— ${zitat.von}, ${zitat.quelle}`;
}

export async function ladeBild(zufall: () => number = Math.random): Promise<string> {
  return bildLader[Math.floor(zufall() * bildLader.length)]!();
}

export async function zeigeSpleen(): Promise<void> {
  const bild = await ladeBild();
  console.log('%c%s\n%s\n\n%s', 'font-family:monospace;line-height:1.1', bild,
    '© 2026 André Stiebitz · CC BY 4.0', waehleSpruch(navigator.language));
}
