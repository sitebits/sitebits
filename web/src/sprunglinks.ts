// Sprunglinks am Seitenanfang: die ersten Tab-Ziele der Seite, sichtbar erst beim Fokus. Werkzeugansicht: „Zur
// 3D-Ansicht“ und „Zu den Werkzeugen“; Empfängeransicht: zuerst „Zur Befundliste“, dann „Zur 3D-Ansicht“. Der
// Sprung setzt nur den Fokus — die Adresszeile bleibt unberührt (sie trägt den Zustand der Ansicht).

export interface Sprunglinks {
  /** Nach einem Wechsel zwischen Werkzeug und Empfang: passende Links zeigen. */
  aktualisiere(): void;
}

export function attachSprunglinks(o: {
  leinwand: HTMLElement;
  /** Fokus in die Werkzeugleiste. */
  werkzeuge: () => void;
  /** Befundliste der Empfängeransicht; null = Werkzeugansicht. */
  befundliste: () => HTMLElement | null;
  /** Befundliste zeigen (mobil aufgeklappt) und zurückgeben; fehlt es, gilt `befundliste`. */
  zurListe?: () => HTMLElement | null;
}): Sprunglinks {
  const link = (text: string, ziel: string, springe: () => void): HTMLAnchorElement => {
    const a = document.createElement('a');
    a.className = 'sv-sprunglink';
    a.href = `#${ziel}`;
    a.textContent = text;
    a.addEventListener('click', (ev) => {
      ev.preventDefault();
      springe();
    });
    return a;
  };
  const zurListe = link('Zur Befundliste', 'befundliste', () => {
    if (o.befundliste() === null) return;
    (o.zurListe?.() ?? o.befundliste())?.focus();
  });
  const zurAnsicht = link('Zur 3D-Ansicht', 'view', () => o.leinwand.focus());
  const zuWerkzeugen = link('Zu den Werkzeugen', 'toolbar', o.werkzeuge);
  // Als erste Kinder von body, vor allem anderen (auch vor dem Link zurück zu den Projekten).
  document.body.prepend(zurListe, zurAnsicht, zuWerkzeugen);

  const aktualisiere = (): void => {
    const empfang = o.befundliste() !== null;
    zurListe.hidden = !empfang;
    zuWerkzeugen.hidden = empfang;
  };
  aktualisiere();
  return { aktualisiere };
}
