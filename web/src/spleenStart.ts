// Beigabe für Neugierige in der Konsole: erst im Leerlauf und als eigener Chunk, damit der Start nichts davon trägt.
let gestartet = false;

export function starteSpleen(): void {
  if (gestartet || import.meta.env.MODE === 'test' || navigator.webdriver) return;
  gestartet = true;
  const los = (): void => { void import('./spleen').then((m) => m.zeigeSpleen()).catch(() => undefined); };
  if ('requestIdleCallback' in window) window.requestIdleCallback(los, { timeout: 5000 });
  else setTimeout(los, 2000);
}

starteSpleen();
