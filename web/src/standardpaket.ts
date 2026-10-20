// Standardpaket des Einzelmodus (Viewer ohne ?p= und ohne Plattform-Server). Der Name kommt
// beim Bau aus VITE_STANDARDPAKET — die private Demo-/Entwicklungsumgebung setzt dort ihr
// Referenzpaket; ohne Angabe (z. B. im öffentlichen Quelltext) gilt der neutrale Name „beispiel“.
// Nur eine gültige projectId wird übernommen, damit der Wert keinen anderen Pfad ansteuert.
const PROJEKT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function standardPaketPfad(wert: string | undefined): string {
  const id = (wert ?? '').trim();
  return `/data/${PROJEKT_ID_RE.test(id) ? id : 'beispiel'}/`;
}
