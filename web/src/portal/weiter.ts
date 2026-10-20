// Ziel-Pfad nach Anmeldung ("weiter"-Parameter): nur eigene, relative Pfade zulassen. Reines Modul
// mit eigenem Test, weil Browser beim tatsächlichen Navigieren Backslashes wie Schrägstriche
// behandeln — "/\evil.example" sieht wie ein relativer Pfad aus, wird aber effektiv "//evil.example"
// (protokollrelativ). Die brief-Regel ("beginnt mit / und nicht mit //") allein fängt das nicht ab;
// der zusätzliche Origin-Vergleich über denselben URL-Parser wie der Browser schon.
export function zielPfad(pfad: string | null, origin: string): string {
  if (!pfad || !pfad.startsWith('/') || pfad.startsWith('//')) return '/';
  let url: URL;
  try {
    url = new URL(pfad, origin);
  } catch {
    return '/';
  }
  if (url.origin !== origin) return '/';
  // Erst das Normalisieren macht aus "/.//x", "/x/..//x" oder "/%2e//x" einen Pfad mit "//" am Anfang;
  // als Ziel gesetzt wäre das wieder protokollrelativ. Das Ergebnis deshalb selbst noch einmal prüfen.
  const ziel = `${url.pathname}${url.search}${url.hash}`;
  if (ziel.startsWith('//') || new URL(ziel, origin).origin !== origin) return '/';
  return ziel;
}
