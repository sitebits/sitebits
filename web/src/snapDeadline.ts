// Frist des Kantenfangs: snapToEdge fragt ``expired`` nur an festen Stellen
// (vor jedem Kandidaten, nach der Linien-Regel). Die Schritte danach (Kantengerade, Fußpunkt, nächster
// Originalpunkt) laufen ungeprüft — ein dort erst nach Fristende fertiger Fang wird hier in
// ``{ point: null, why: 'budget' }`` umgewandelt. Damit gilt die Frist konstruktionsbedingt: ein Fang ist nur
// Ergebnis, wenn er vor Fristende vorlag. Reine Logik mit eingespeister Uhr (Vitest mit Fake-Zeit).
import { snapToEdge } from './edgeSnap';
import type { SnapInput, SnapResult } from './edgeSnap';

export function snapWithinDeadline(input: Omit<SnapInput, 'expired'>, now: () => number, deadline: number): SnapResult {
  const expired = (): boolean => now() > deadline;
  const r = snapToEdge({ ...input, expired });
  if (r.point && expired()) return { point: null, why: 'budget' };
  return r;
}
