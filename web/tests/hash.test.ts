import { describe, expect, it } from 'vitest';
import { HASH_MAX_VERTICES, decodeBefund, decodeHash, encodeHash, poseAus } from '../src/hash';
import type { ViewState } from '../src/hash';
import type { Vec3 } from '../src/manifest';

const origin: [number, number, number] = [1469, 7107, 1];

describe('hash', () => {
  it('Roundtrip Kamera + Messung auf 1 mm', () => {
    const s = {
      cam: [1470.1234, 7100.5, 5.25] as [number, number, number],
      tgt: [1469, 7107, 1] as [number, number, number],
      m: [[[1469.001, 7107.002, 1.003], [1470, 7108, 2]]] as [[number, number, number], [number, number, number]][],
    };
    const back = decodeHash(encodeHash(s, origin), origin);
    expect(back.cam![0]).toBeCloseTo(1470.123, 3);
    expect(back.m![0]![0][2]).toBeCloseTo(1.003, 3);
  });
  it('leerer oder kaputter Hash → leerer Zustand statt Absturz', () => {
    expect(decodeHash('', origin)).toEqual({});
    expect(decodeHash('#cam=a,b', origin)).toEqual({});
  });
  it('Standpunkt-Link in fester Feldreihenfolge, ohne cam/tgt', () => {
    const h = encodeHash({ st: 'S017', yaw: 12.346, pitch: -3.2, fov: 60, cam: [1, 2, 3], tgt: [4, 5, 6] }, origin);
    expect(h).toBe('#st=S017&yaw=12.35&pitch=-3.20&fov=60.0');
  });
  it('Standpunkt + Messung hin und zurück', () => {
    const s = { st: 'S017', yaw: -170.5, pitch: 10, fov: 45.5, m: [[[1469.001, 7107.002, 1.003], [1470, 7108, 2]]] as [[number, number, number], [number, number, number]][] };
    const back = decodeHash(encodeHash(s, origin), origin);
    expect(back.st).toBe('S017');
    expect(back.yaw).toBeCloseTo(-170.5, 2);
    expect(back.fov).toBeCloseTo(45.5, 1);
    expect(back.m![0]![1]).toEqual([1470, 7108, 2]);
  });
  it('Winkel werden normiert und geklemmt', () => {
    const back = decodeHash('#st=S001&yaw=400&pitch=120&fov=500', origin);
    expect(back.yaw).toBeCloseTo(40, 9);
    expect(back.pitch).toBe(89);
    expect(back.fov).toBe(100);
  });
  it('st ohne Winkel ist erlaubt (Standardblick)', () => {
    expect(decodeHash('#st=S001', origin)).toEqual({ st: 'S001' });
  });
  it('unsichere ID oder kaputte Winkel → leerer Zustand', () => {
    expect(decodeHash('#st=%3Cimg%3E', origin)).toEqual({});
    expect(decodeHash('#st=S001&yaw=abc', origin)).toEqual({});
  });
  it('alte Links mit cam/tgt bleiben gültig', () => {
    const back = decodeHash('#cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000', origin);
    expect(back.cam).toEqual([1470, 7109, 4]);
    expect(back.st).toBeUndefined();
  });
});

describe('hash — Messarten (g=)', () => {
  const g = [
    { kind: 'polyline' as const, points: [[1469.001, 7107.002, 1.003], [1470, 7108, 2], [1471.5, 7108, 2.25]] as [number, number, number][] },
    { kind: 'area' as const, points: [[1470, 7100, 0], [1474, 7100, 0], [1474, 7102.5, 0], [1470, 7102.5, 0]] as [number, number, number][] },
    { kind: 'height' as const, points: [[1470, 7100, 0], [1470.3, 7100.4, 2.5]] as [number, number, number][] },
  ];
  it('kodiert ursprungsrelativ mit Artkürzel p/a/h', () => {
    expect(encodeHash({ st: 'S017', g: [g[2]!] }, origin)).toBe('#st=S017&g=h:1.000,-7.000,-1.000;1.300,-6.600,1.500');
  });
  it('Rundreise auf 1 mm, zusammen mit m= und st', () => {
    const m: [[number, number, number], [number, number, number]][] = [[[1469.001, 7107.002, 1.003], [1470, 7108, 2]]];
    const back = decodeHash(encodeHash({ st: 'S017', yaw: 10, m, g }, origin), origin);
    back.m![0]!.forEach((p, j) => p.forEach((v, k) => expect(v).toBeCloseTo(m[0]![j]![k]!, 3)));
    expect(back.g!.map((x) => x.kind)).toEqual(['polyline', 'area', 'height']);
    back.g!.forEach((x, i) => x.points.forEach((p, j) => p.forEach((v, k) => expect(v).toBeCloseTo(g[i]!.points[j]![k]!, 3))));
  });
  it('alter Link nur mit m= bleibt gültig und hat kein g', () => {
    const back = decodeHash('#m=1.000,2.000,3.000;4.000,5.000,6.000', origin);
    expect(back.m).toEqual([[[1470, 7109, 4], [1473, 7112, 7]]]);
    expect(back.g).toBeUndefined();
  });
  it('kaputtes g= → leerer Zustand (wie jeder andere kaputte Parameter)', () => {
    for (const bad of ['a:0,0,0;1,0,0', 'h:0,0,0;1,0,0;2,0,0', 'x:0,0,0;1,0,0', 'p:0,0,0;1,0', 'p:', 'p:0,0,0;1,0,0|']) {
      expect(decodeHash(`#g=${bad}`, origin)).toEqual({});
    }
  });
  it(`mehr als HASH_MAX_VERTICES Eckpunkte → abgelehnt`, () => {
    const many = Array.from({ length: HASH_MAX_VERTICES + 1 }, (_, i) => `${i}.000,0.000,0.000`).join(';');
    expect(decodeHash(`#g=p:${many}`, origin)).toEqual({});
  });
});

// Die Messliste bleibt auch nach dem Neuladen in Anlegereihenfolge. m= trägt weiter die
// Strecken (alte Links/Viewer), g= die übrigen Arten; die Reihenfolge über beide steht in o=.
describe('hash — Anlegereihenfolge über m= und g= (o=)', () => {
  const d1: [[number, number, number], [number, number, number]] = [[1470, 7107, 1], [1471, 7107, 1]];
  const d2: [[number, number, number], [number, number, number]] = [[1470, 7108, 1], [1472, 7108, 1]];
  const poly = { kind: 'polyline' as const, points: [[1470, 7100, 0], [1471, 7100, 0]] as [number, number, number][] };
  const area = { kind: 'area' as const, points: [[1470, 7100, 0], [1474, 7100, 0], [1474, 7102.5, 0]] as [number, number, number][] };
  it('Rundreise mit verschachtelten Arten: Strecke, Polylinie, Strecke, Fläche', () => {
    const order = ['distance', 'polyline', 'distance', 'area'] as const;
    const h = encodeHash({ m: [d1, d2], g: [poly, area], order: [...order] }, origin);
    expect(h).toMatch(/&o=dpda$/);
    expect(decodeHash(encodeHash({ m: [d1], g: [poly], order: ['polyline', 'distance'] }, origin), origin).order).toEqual(['polyline', 'distance']);
    const back = decodeHash(h, origin);
    expect(back.order).toEqual(order);
    expect(back.m).toHaveLength(2);
    expect(back.g!.map((x) => x.kind)).toEqual(['polyline', 'area']);
  });
  it('o= nur, wenn beide Parameter belegt sind (sonst ist die Reihenfolge eindeutig)', () => {
    expect(encodeHash({ m: [d1], order: ['distance'] }, origin)).not.toMatch(/o=/);
    expect(encodeHash({ g: [poly], order: ['polyline'] }, origin)).not.toMatch(/o=/);
  });
  it('unpassendes oder kaputtes o= wird ignoriert, die Messungen bleiben (Strecken zuerst)', () => {
    const base = encodeHash({ m: [d1], g: [poly] }, origin);
    for (const bad of ['dd', 'd', 'p', 'dpp', 'dx', 'da', '']) {
      const back = decodeHash(`${base}&o=${bad}`, origin);
      expect(back.order).toBeUndefined();
      expect(back.m).toHaveLength(1);
      expect(back.g).toHaveLength(1);
    }
  });
});

// ly= trägt die sichtbaren Ebenen-IDs, kommagetrennt; nur eine Abweichung von der
// Voreinstellung wird überhaupt geschrieben (layers.ts, lyParam) — hash.ts selbst kennt diese Regel nicht,
// kodiert/dekodiert nur das Feld.
describe('hash — Ebenen (ly=)', () => {
  it('ly= Rundreise; fehlt → undefined; leer → []; ungültige IDs verworfen', () => {
    const o: Vec3 = [0, 0, 0];
    expect(decodeHash(encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ly: ['punkte', 'mesh'] }, o), o).ly).toEqual(['punkte', 'mesh']);
    expect(decodeHash('#cam=1,2,3', o).ly).toBeUndefined();
    expect(decodeHash('#ly=', o).ly).toEqual([]);
    expect(decodeHash('#ly=punkte,Böse<id>', o).ly).toEqual(['punkte']);
    expect(encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0] }, o)).not.toContain('ly=');
  });
});

describe('hash — Koordinatenanzeige (ko=)', () => {
  it('nur an wird geschrieben; nur ko=1 schaltet ein', () => {
    const o: Vec3 = [0, 0, 0];
    expect(decodeHash(encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ko: true }, o), o).ko).toBe(true);
    expect(encodeHash({ cam: [1, 2, 3], tgt: [0, 0, 0], ko: false }, o)).not.toContain('ko=');
    expect(decodeHash('#cam=1,2,3', o).ko).toBeUndefined();
    expect(decodeHash('#cam=1,2,3&ko=0', o).ko).toBeUndefined();
  });
});

describe('Befundlink b/rev', () => {
  const o: Vec3 = [1000, 2000, 300];
  it('Rundreise mit Kamera und mit Standpunkt', () => {
    const s: ViewState = { cam: [1001, 2002, 301.5], tgt: [1000, 2000, 300], b: 'P-a1b2c3', rev: 4 };
    expect(decodeHash(encodeHash(s, o), o)).toEqual(s);
    const p: ViewState = { st: 'S012', yaw: 10.25, pitch: -3.5, fov: 80, b: 'P-x', rev: 1 };
    expect(decodeHash(encodeHash(p, o), o)).toEqual(p);
  });
  it('Rundreise mit Grenzwerten von rev und ohne rev', () => {
    for (const rev of [0, 2_147_483_647]) {
      const s: ViewState = { st: 'S001', b: 'P-1', rev };
      expect(decodeHash(encodeHash(s, o), o)).toEqual(s);
    }
    const ohne: ViewState = { st: 'S001', b: 'P-1' };
    expect(decodeHash(encodeHash(ohne, o), o)).toEqual(ohne);
  });
  it('b und rev stehen am Ende, nach Messungen und Ebenen', () => {
    const h = encodeHash({ cam: [1000, 2000, 300], tgt: [1000, 2000, 300], m: [[[1000, 2000, 300], [1001, 2000, 300]]], ly: ['a'], b: 'P-1', rev: 2 }, o);
    expect(h.endsWith('&ly=a&b=P-1&rev=2')).toBe(true);
  });
  it('alte Links ohne b/rev bleiben gültig und bekommen keine Felder dazu', () => {
    const d = decodeHash('#cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000', o);
    expect(d.cam).toEqual([1001, 2002, 303]);
    expect('b' in d).toBe(false);
    expect('rev' in d).toBe(false);
  });
  it('ungültiges b wird einzeln verworfen, die Ansicht bleibt', () => {
    for (const b of ['', 'x y', 'a'.repeat(65), '<script>', 'P%2F1', '../P']) {
      const d = decodeHash(`#cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000&b=${b}&rev=2`, o);
      expect(d.cam).toEqual([1001, 2002, 303]);
      expect(d.tgt).toEqual([1000, 2000, 300]);
      expect(d.b).toBeUndefined();
      expect(d.rev).toBeUndefined(); // rev ohne b ist bedeutungslos
    }
  });
  it('ungültiges b lässt auch Standpunkt, Messungen und Ebenen stehen', () => {
    const d = decodeHash('#st=S001&yaw=10.00&m=0.000,0.000,0.000;1.000,0.000,0.000&ly=a&b=x%20y', o);
    expect(d.st).toBe('S001');
    expect(d.yaw).toBe(10);
    expect(d.m).toHaveLength(1);
    expect(d.ly).toEqual(['a']);
    expect(d.b).toBeUndefined();
  });
  it('ungültiges rev wird einzeln verworfen, b bleibt', () => {
    for (const rev of ['-1', '1.5', 'abc', '', '99999999999', '1e3', '2147483648', ' 1', '0x1', '0001']) {
      const d = decodeHash(`#st=S001&b=P-1&rev=${rev}`, o);
      expect(d.st).toBe('S001');
      expect(d.b).toBe('P-1');
      expect(d.rev).toBeUndefined();
    }
  });
  it('b mit Sonderzeichen oder doppelt: nur b fällt weg bzw. das erste gilt', () => {
    for (const b of ['P-1#x', 'a%26rev%3D5', 'P%0A1', 'P+1']) {
      const d = decodeHash(`#st=S001&b=${b}&rev=2`, o);
      expect(d.st).toBe('S001');
      expect(d.b).toBeUndefined();
      expect(d.rev).toBeUndefined();
    }
    expect(decodeHash('#st=S001&b=P-1&b=../x', o).b).toBe('P-1');
  });
  it('rev ohne b wird ignoriert', () => {
    const d = decodeHash('#st=S001&rev=3', o);
    expect(d.st).toBe('S001');
    expect('rev' in d).toBe(false);
  });
  it('rev wird nur mit b geschrieben', () => {
    expect(encodeHash({ cam: [1000, 2000, 300], tgt: [1000, 2000, 300], rev: 3 }, o)).not.toContain('rev=');
  });
  it('ungültige b/rev werden nicht geschrieben', () => {
    expect(encodeHash({ st: 'S001', b: '../P', rev: 3 }, o)).toBe('#st=S001');
    for (const rev of [-1, 1.5, Number.NaN, 2_147_483_648]) {
      expect(encodeHash({ st: 'S001', b: 'P-1', rev }, o)).toBe('#st=S001&b=P-1');
    }
  });
});

describe('decodeBefund', () => {
  it('liest b und rev auch bei kaputter Pose, die den übrigen Zustand verwirft', () => {
    for (const h of ['#cam=kaputt&tgt=0,0,0&b=P-1&rev=2', '#cam=0,0,0&tgt=0,0&b=P-1&rev=2', '#m=0,0,0&b=P-1&rev=2', '#g=x:1&b=P-1&rev=2', '#st=%3Cx%3E&b=P-1&rev=2']) {
      expect(decodeHash(h, origin)).toEqual({});
      expect(decodeBefund(h)).toEqual({ b: 'P-1', rev: 2 });
    }
  });
  it('dieselben Regeln wie im vollständigen Zustand', () => {
    expect(decodeBefund('')).toEqual({});
    expect(decodeBefund('#b=%3Cx%3E&rev=2')).toEqual({});
    expect(decodeBefund('#rev=2')).toEqual({});
    expect(decodeBefund('#b=P-1&rev=-1')).toEqual({ b: 'P-1' });
    expect(decodeBefund('#cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000&b=P-1&rev=7')).toEqual(
      { b: decodeHash('#cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000&b=P-1&rev=7', origin).b, rev: 7 },
    );
  });
});

describe('Eintrags-Anker e (Befund-Faden)', () => {
  it('nur zusammen mit b und nur als Schlüssel aus 16 Hex-Zeichen', () => {
    expect(decodeBefund('#b=P-1&e=0123456789abcdef')).toEqual({ b: 'P-1', e: '0123456789abcdef' });
    expect(decodeBefund('#b=P-1&rev=3&e=0123456789abcdef')).toEqual({ b: 'P-1', rev: 3, e: '0123456789abcdef' });
    expect(decodeBefund('#e=0123456789abcdef')).toEqual({});
    expect(decodeBefund('#b=P-1&e=12')).toEqual({ b: 'P-1' });
    expect(decodeBefund('#b=P-1&e=0123456789ABCDEF')).toEqual({ b: 'P-1' });
  });
  it('wird nie geschrieben: die Adresszeile trägt den Anker nicht weiter', () => {
    expect(encodeHash({ b: 'P-1', e: '0123456789abcdef' }, origin)).toBe('#b=P-1');
  });
});

describe('poseAus (nur die Pose, z. B. für den lokalen Entwurf)', () => {
  const voll: ViewState = {
    cam: [1470, 7100, 5],
    tgt: [1469, 7107, 1],
    m: [[[1469, 7107, 1], [1470, 7107, 1]]],
    g: [],
    order: ['distance'],
    st: 'S012',
    yaw: 12.5,
    pitch: -3,
    fov: 80,
    ly: ['wolke'],
    b: 'P-1',
    rev: 3,
  };
  it('behält Standpunkt, Winkel, Kamera und Ziel; Messungen, Ebenen und Befundangaben fallen weg', () => {
    expect(poseAus(voll)).toEqual({ cam: [1470, 7100, 5], tgt: [1469, 7107, 1], st: 'S012', yaw: 12.5, pitch: -3, fov: 80 });
  });
  it('fehlende Teile bleiben weg (keine undefined-Felder)', () => {
    expect(Object.keys(poseAus({ cam: [1, 2, 3], tgt: [0, 0, 0], m: [] }))).toEqual(['cam', 'tgt']);
    expect(poseAus({})).toEqual({});
  });
  it('übersteht Kodieren und Lesen der Adresszeile', () => {
    const h = encodeHash(poseAus(voll), origin);
    expect(h).not.toMatch(/[&#](m|g|o|ly|b|rev)=/);
    const zurueck = decodeHash(h, origin);
    expect(poseAus(zurueck)).toEqual(poseAus(decodeHash(encodeHash(voll, origin), origin)));
  });
});
