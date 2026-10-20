// Die 3D-Ansicht als bedienbares Element: per Tab erreichbar, mit Name, Rolle und einer Kurzbeschreibung
// der Tasten (verstecktes Element, per aria-describedby verknüpft). Der Fokus allein startet nichts.
// In der Empfängeransicht ist sie ein Bild mit beschreibendem Namen (Standpunkt, Befund); ihr Inhalt steht als
// Text in der Befundliste. Fokussierbar bleibt sie, damit die Pfeiltasten dort drehen.
import type { AktionId } from './tastenbelegung';

export interface Leinwand {
  aktualisiere(): void;
  /** Werkzeug (bedienbar) oder Empfang (Bild mit Namen `name`). */
  setzeModus(m: 'werkzeug' | 'empfang', name?: string): void;
}

export function attachLeinwand(canvas: HTMLElement, o: { name: () => string; kuerzel: (a: AktionId) => string }): Leinwand {
  const hilfe = document.createElement('div');
  hilfe.id = 'leinwand-hilfe';
  hilfe.className = 'sv-nur-sr';
  document.body.append(hilfe);

  const empfangHilfe = document.createElement('div');
  empfangHilfe.id = 'leinwand-empfang-hilfe';
  empfangHilfe.className = 'sv-nur-sr';
  empfangHilfe.textContent = 'Inhalt als Text in der Befundliste. Pfeiltasten drehen die Ansicht.';
  document.body.append(empfangHilfe);

  canvas.tabIndex = 0;
  let modus: 'werkzeug' | 'empfang' = 'werkzeug';
  let empfangName = '';

  const aktualisiere = (): void => {
    if (modus === 'empfang') {
      canvas.setAttribute('role', 'img');
      canvas.removeAttribute('aria-roledescription');
      canvas.setAttribute('aria-describedby', empfangHilfe.id);
      canvas.setAttribute('aria-label', empfangName);
      return;
    }
    canvas.setAttribute('role', 'application');
    canvas.setAttribute('aria-roledescription', '3D-Ansicht');
    canvas.setAttribute('aria-describedby', hilfe.id);
    canvas.setAttribute('aria-label', o.name());
    const taste = (a: AktionId): string => o.kuerzel(a) || 'nicht belegt';
    hilfe.textContent =
      `Pfeiltasten im Begehen: umschauen, im Panorama mit Werkzeug: in Schritten drehen. ${taste('begehen')}: Begehen starten. ` +
      `${taste('begehen.setzen')}: am Fadenkreuz setzen (im Begehen und im Panorama mit Werkzeug). ` +
      `${taste('begehen.befund')}: Befund setzen (im Begehen und im Panorama mit Werkzeug). ${taste('teilen')}: Befundlink teilen. ${taste('tastenhilfe')}: Bedienung (Maus, Finger, Tasten). ` +
      'Esc beendet jeden Zustand, Tab verlässt die 3D-Ansicht.';
  };
  aktualisiere();
  return {
    aktualisiere,
    setzeModus: (m, name) => {
      modus = m;
      if (name !== undefined) empfangName = name;
      aktualisiere();
    },
  };
}
