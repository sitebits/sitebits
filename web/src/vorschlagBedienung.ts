// Gemeinsame Prüfknöpfe; Fehler bleiben am Ort sichtbar, Erfolg setzt den Serverstand durch den Adapter.
export function pruefknoepfe(root: HTMLElement, entscheide: (a: 'annehmen' | 'verwerfen') => Promise<void>, melde: (t: string) => void): void {
  const knoepfe: HTMLButtonElement[] = [];
  for (const a of ['annehmen', 'verwerfen'] as const) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = a === 'annehmen' ? 'Annehmen' : 'Verwerfen';
    b.addEventListener('click', ev => {
      ev.stopPropagation();
      knoepfe.forEach(x => { x.disabled = true; });
      void entscheide(a).then(() => melde(a === 'annehmen' ? 'Vorschlag angenommen.' : 'Vorschlag verworfen.'), e => melde(e instanceof Error ? e.message : String(e))).finally(() => knoepfe.forEach(x => { x.disabled = false; }));
    });
    knoepfe.push(b);
    root.append(b);
  }
}
/** Vor dem Export ist sichtbar, wie viele Vorschläge ausgelassen werden. */
export function exportMitHinweis(n: number, ausgabe: () => void): void {
  if (!n) {
    ausgabe();
    return;
  }
  const d = document.createElement('dialog');
  const h = document.createElement('h2');
  h.id = 'sv-export-titel';
  h.textContent = 'Export';
  const p = document.createElement('p');
  p.textContent = `${n} Vorschläge werden im Export ausgelassen.`;
  d.setAttribute('aria-labelledby', h.id);
  const weiter = document.createElement('button');
  weiter.type = 'button';
  weiter.textContent = 'Export herunterladen';
  weiter.addEventListener('click', () => { d.close(); ausgabe(); });
  const abbrechen = document.createElement('button');
  abbrechen.type = 'button';
  abbrechen.textContent = 'Abbrechen';
  abbrechen.addEventListener('click', () => d.close());
  d.addEventListener('close', () => d.remove());
  d.append(h, p, weiter, abbrechen);
  document.body.append(d);
  d.showModal();
}
