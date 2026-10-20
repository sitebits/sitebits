import { describe, expect, it } from 'vitest';
import { Abgleich, ABGLEICH_TEXT, kanonisch, SAMMELN_MS } from '../src/abgleich';
import type { AbgleichOps, AbgleichZiel } from '../src/abgleich';
import { KEINE_VERBINDUNG, PlattformFehler } from '../src/plattformApi';
import type { ServerEintrag } from '../src/plattformApi';
import { fakeUhr } from './fakePlattform';

type Ding = { k: string; wert: string };
type D = { wert: string };

const eintrag = (id: string, version: number, wert: string): ServerEintrag<D> => ({
  id, version, daten: { wert }, paketRevision: 1, erstelltVon: 1, erstelltAm: 0, geaendertVon: 1, geaendertVonName: 'A', geaendertAm: 0,
});

function aufbau() {
  let liste: Ding[] = [];
  const server = new Map<string, ServerEintrag<D>>();
  const aufrufe: string[] = [];
  const meldungen: string[] = [];
  const ergebnisse: string[] = [];
  const st: { fehler: PlattformFehler | null; tor: Promise<void> | null; verliere: boolean } = { fehler: null, tor: null, verliere: false };
  // Anfrage kommt an, die Antwort geht verloren (z. B. Zeitüberschreitung).
  const verloren = (): void => {
    if (st.verliere) {
      st.verliere = false;
      throw new PlattformFehler(0, KEINE_VERBINDUNG);
    }
  };
  const wirf = (): void => {
    if (st.fehler) {
      const f = st.fehler;
      st.fehler = null;
      throw f;
    }
  };
  const ops: AbgleichOps<D> = {
    anlegen: async (d, id) => {
      aufrufe.push(`POST ${id} ${d.wert}`);
      if (st.tor) await st.tor;
      wirf();
      const da = id === null ? undefined : server.get(id);
      if (da) throw new PlattformFehler(409, 'Diese ID ist schon vergeben.', da);
      const e = eintrag(id ?? `S${server.size + 1}`, 1, d.wert);
      server.set(e.id, e);
      verloren();
      return e;
    },
    aendern: async (sid, d, v) => {
      aufrufe.push(`PATCH ${sid} v${v} ${d.wert}`);
      wirf();
      const alt = server.get(sid);
      if (!alt) throw new PlattformFehler(404, 'nicht gefunden');
      if (alt.version !== v) throw new PlattformFehler(409, 'Konflikt', alt);
      const e = eintrag(sid, v + 1, d.wert);
      server.set(sid, e);
      verloren();
      return e;
    },
    loeschen: async (sid, v) => {
      aufrufe.push(`DELETE ${sid} v${v}`);
      wirf();
      const alt = server.get(sid);
      if (!alt) throw new PlattformFehler(404, 'nicht gefunden');
      if (alt.version !== v) throw new PlattformFehler(409, 'Konflikt', alt);
      server.delete(sid);
    },
  };
  const ziel: AbgleichZiel<Ding, D> = {
    liste: () => liste,
    schluessel: (x) => x.k,
    daten: (x) => ({ wert: x.wert }),
    clientId: (x) => x.k,
    uebernehmen: (lokal, aktuell) => {
      liste = liste.filter((x) => x.k !== lokal);
      if (aktuell) liste.push({ k: aktuell.id, wert: aktuell.daten.wert });
      return aktuell ? aktuell.id : null;
    },
    ergebnis: (lokal, fehler) => ergebnisse.push(`${lokal} ${fehler ?? 'bestätigt'}`),
  };
  const u = fakeUhr();
  const ab = new Abgleich(ziel, ops, (t) => meldungen.push(t), u.uhr);
  return { ab, server, aufrufe, meldungen, ergebnisse, u, st, setze: (l: Ding[]) => void (liste = l), liste: () => liste };
}

describe('Abgleich', () => {
  it('anlegen, ändern, löschen → POST, PATCH mit Version, DELETE mit Version', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '2' }]);
    await t.ab.lauf();
    t.setze([]);
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'PATCH a v1 2', 'DELETE a v2']);
    expect(t.ab.offen()).toBe(0);
    expect(t.meldungen).toEqual([]);
  });

  it('Ergebnis je Eintrag: bestätigt, abgelehnt mit dem Grund des Servers; Störungen melden kein Ergebnis', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    expect(t.ergebnisse).toEqual(['a bestätigt']);
    t.setze([{ k: 'a', wert: '1' }, { k: 'b', wert: '2' }]);
    t.st.fehler = new PlattformFehler(422, 'Höchstens 100 eigene Rückmeldungen je Projekt.');
    await t.ab.lauf();
    expect(t.ergebnisse).toEqual(['a bestätigt', 'b Höchstens 100 eigene Rückmeldungen je Projekt.']);
    expect(t.meldungen).toEqual(['Nicht gespeichert: Höchstens 100 eigene Rückmeldungen je Projekt.']);
    // Offline: kein Ergebnis, der Eintrag bleibt offen und kommt später.
    t.setze([{ k: 'a', wert: '1' }, { k: 'c', wert: '3' }]);
    t.st.fehler = new PlattformFehler(0, KEINE_VERBINDUNG);
    await t.ab.lauf();
    expect(t.ergebnisse).toHaveLength(2);
    t.u.alle();
    await t.ab.lauf();
    expect(t.ergebnisse.at(-1)).toBe('c bestätigt');
    // Antwort verloren, Wiederholung trifft auf den angekommenen Stand (409): gilt als bestätigt.
    t.setze([{ k: 'a', wert: '1' }, { k: 'c', wert: '3' }, { k: 'd', wert: '4' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.u.alle();
    await t.ab.lauf();
    expect(t.ergebnisse.at(-1)).toBe('d bestätigt');
  });

  it('unverändert → keine Anfrage; nur lokale Einträge erst nach einer Änderung', async () => {
    const t = aufbau();
    const x = { k: 'link', wert: '1' };
    t.setze([x]);
    t.ab.nurLokalLassen('link', x);
    await t.ab.lauf();
    expect(t.aufrufe).toEqual([]);
    expect(t.ab.offen()).toBe(0);
    t.setze([{ k: 'link', wert: '2' }]);
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST link 2']);
  });

  it('geaendert() sammelt mehrere Änderungen zu einem Lauf', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.ab.geaendert();
    t.ab.geaendert();
    t.ab.geaendert();
    expect(t.u.timer.map((x) => x.ms)).toEqual([SAMMELN_MS]);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1']);
  });

  it('offline: meldet, versucht erneut, nichts geht verloren', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.st.fehler = new PlattformFehler(0, KEINE_VERBINDUNG);
    await t.ab.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline]);
    expect(t.ab.offen()).toBe(1);
    expect(t.u.timer.map((x) => x.ms)).toEqual([2000]);
    t.st.fehler = new PlattformFehler(503, 'Wartung');
    t.u.alle();
    await t.ab.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline]); // nicht doppelt gemeldet
    expect(t.u.timer.map((x) => x.ms)).toEqual([5000]); // Wartezeit wächst
    t.u.alle();
    await t.ab.lauf();
    expect(t.server.get('a')?.daten.wert).toBe('1');
    expect(t.ab.offen()).toBe(0);
    expect(t.meldungen.at(-1)).toBe(ABGLEICH_TEXT.wieder);
  });

  it('401 → Hinweis auf neue Anmeldung, Änderung bleibt offen', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.st.fehler = new PlattformFehler(401, 'nicht angemeldet');
    await t.ab.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.sitzung]);
    expect(t.ab.offen()).toBe(1);
  });

  it('409: übernimmt Serverstand und meldet', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.server.set('a', eintrag('a', 2, 'fremd'));
    t.setze([{ k: 'a', wert: 'meins' }]);
    await t.ab.lauf();
    expect(t.aufrufe.at(-1)).toBe('PATCH a v1 meins');
    expect(t.liste()).toEqual([{ k: 'a', wert: 'fremd' }]);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
    expect(t.ab.version('a')).toBe(2);
    t.setze([{ k: 'a', wert: 'neu' }]);
    await t.ab.lauf();
    expect(t.aufrufe.at(-1)).toBe('PATCH a v2 neu');
  });

  it('404 beim Ändern → Fassung des Nutzers wird neu angelegt', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.server.delete('a');
    t.setze([{ k: 'a', wert: '2' }]);
    await t.ab.lauf();
    expect(t.aufrufe.slice(1)).toEqual(['PATCH a v1 2', 'POST a 2']);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.neuAngelegt]);
  });

  it('422: meldet und wiederholt denselben Inhalt nicht', async () => {
    const t = aufbau();
    t.st.fehler = new PlattformFehler(422, 'Titel fehlt.');
    t.setze([{ k: 'a', wert: '' }]);
    await t.ab.lauf();
    expect(t.meldungen).toEqual(['Nicht gespeichert: Titel fehlt.']);
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a ']);
    expect(t.ab.offen()).toBe(0);
    t.setze([{ k: 'a', wert: 'x' }]);
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a ', 'POST a x']);
  });

  it('409 beim Anlegen mit gleichem Inhalt gilt als gespeichert (wiederholter POST)', async () => {
    const t = aufbau();
    t.server.set('a', eintrag('a', 1, '1'));
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    expect(t.meldungen).toEqual([]);
    expect(t.ab.offen()).toBe(0);
    expect(t.ab.version('a')).toBe(1);
    t.setze([{ k: 'a', wert: '2' }]);
    await t.ab.lauf();
    expect(t.aufrufe.at(-1)).toBe('PATCH a v1 2');
  });

  it('409 beim Ändern mit gleichem Inhalt gilt als gespeichert (Antwort verloren)', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.server.set('a', eintrag('a', 2, '2')); // der erste PATCH kam an, die Antwort nicht
    t.setze([{ k: 'a', wert: '2' }]);
    await t.ab.lauf();
    expect(t.meldungen).toEqual([]);
    expect(t.liste()).toEqual([{ k: 'a', wert: '2' }]);
    expect(t.ab.version('a')).toBe(2);
    expect(t.ab.offen()).toBe(0);
  });

  it('POST kam an, Antwort verloren, danach geändert → Wiederholung bestätigt den alten Stand und schickt den neuen', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '2' }]);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'POST a 2', 'PATCH a v1 2']);
    expect(t.server.get('a')?.daten.wert).toBe('2');
    expect(t.liste()).toEqual([{ k: 'a', wert: '2' }]);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.ab.offen()).toBe(0);
  });

  it('PATCH kam an, Antwort verloren, danach geändert → kein Konflikt, neuer Stand landet auf dem Server', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '2' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '3' }]);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'PATCH a v1 2', 'PATCH a v1 3', 'PATCH a v2 3']);
    expect(t.server.get('a')?.daten.wert).toBe('3');
    expect(t.liste()).toEqual([{ k: 'a', wert: '3' }]);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.ab.version('a')).toBe(3);
  });

  it('A→B→A nach verlorener Antwort: Server endet bei A', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '2' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '1' }]);
    expect(t.ab.offen()).toBe(1); // B kann am Server angekommen sein
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'PATCH a v1 2', 'PATCH a v1 1', 'PATCH a v2 1']);
    expect(t.server.get('a')?.daten.wert).toBe('1');
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.ab.offen()).toBe(0);
  });

  it('POST kam an (Antwort verloren), dann lokal gelöscht → DELETE per Client-ID, keine Waise', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.setze([]);
    expect(t.ab.offen()).toBe(1);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'DELETE a v1']);
    expect(t.server.has('a')).toBe(false);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.ab.offen()).toBe(0);
  });

  it('POST kam nie an, dann lokal gelöscht → DELETE-Versuch, 404 still', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.st.fehler = new PlattformFehler(0, KEINE_VERBINDUNG);
    await t.ab.lauf();
    t.setze([]);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe).toEqual(['POST a 1', 'DELETE a v1']);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.ab.offen()).toBe(0);
  });

  it('PATCH kam an (Antwort verloren), dann gelöscht → DELETE mit der neuen Version, kein Konflikt', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.setze([{ k: 'a', wert: '2' }]);
    t.st.verliere = true;
    await t.ab.lauf();
    t.setze([]);
    t.u.alle();
    await t.ab.lauf();
    expect(t.aufrufe.slice(2)).toEqual(['DELETE a v1', 'DELETE a v2']);
    expect(t.server.has('a')).toBe(false);
    expect(t.liste()).toEqual([]);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
  });

  it('409 beim Löschen: Stand des anderen wird wieder angezeigt', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    await t.ab.lauf();
    t.server.set('a', eintrag('a', 2, 'fremd'));
    t.setze([]);
    await t.ab.lauf();
    expect(t.aufrufe.at(-1)).toBe('DELETE a v1');
    expect(t.liste()).toEqual([{ k: 'a', wert: 'fremd' }]);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
    expect(t.ab.version('a')).toBe(2);
    expect(t.server.has('a')).toBe(true);
  });

  it('Änderung während eines laufenden Abgleichs → zweiter Durchgang im selben Lauf', async () => {
    const t = aufbau();
    let auf: () => void = () => undefined;
    t.st.tor = new Promise<void>((r) => (auf = r));
    t.setze([{ k: 'a', wert: '1' }]);
    const p1 = t.ab.lauf();
    await Promise.resolve();
    t.setze([{ k: 'a', wert: '2' }]);
    const p2 = t.ab.lauf();
    expect(p2).toBe(p1);
    t.st.tor = null;
    auf();
    await p1;
    expect(t.aufrufe).toEqual(['POST a 1', 'PATCH a v1 2']);
    expect(t.ab.offen()).toBe(0);
  });

  it('sofort() überspringt das Sammelfenster', async () => {
    const t = aufbau();
    t.setze([{ k: 'a', wert: '1' }]);
    t.ab.geaendert();
    await t.ab.sofort();
    expect(t.u.timer).toEqual([]);
    expect(t.aufrufe).toEqual(['POST a 1']);
  });

  it('abgelehnteAnzahl zählt vom Server abgelehnte Inhalte', async () => {
    const t = aufbau();
    t.st.fehler = new PlattformFehler(422, 'Titel fehlt.');
    t.setze([{ k: 'a', wert: '' }]);
    await t.ab.lauf();
    expect([t.ab.offen(), t.ab.abgelehnteAnzahl()]).toEqual([0, 1]);
  });

  it('kanonisch sortiert Schlüssel und lässt undefined weg', () => {
    expect(kanonisch({ b: 1, a: { d: 1, c: 2 }, x: undefined })).toBe('{"a":{"c":2,"d":1},"b":1}');
  });
});

it('Prüfung entfernt Bindung und übernimmt neue Version ohne Rückschreiben', async () => {
  const t=aufbau(); t.setze([{k:'P1',wert:'vorschlag'}]);
  t.ab.kenne('P1','P1',1,t.liste()[0]!);
  t.ab.geprueft('P1',eintrag('P1',2,'angenommen'));
  await t.ab.sofort(); expect(t.aufrufe).toEqual([]); expect(t.ab.version('P1')).toBe(2);
  t.ab.geprueft('P1',null); await t.ab.sofort();
  expect(t.liste()).toEqual([]); expect(t.ab.offen()).toBe(0); expect(t.aufrufe).toEqual([]);
});
