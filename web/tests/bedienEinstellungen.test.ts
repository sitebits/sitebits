import { describe, expect, it } from 'vitest';
import { bewegungReduziert, drehenInSchritten, ladeEinstellungen, speichereEinstellungen, vorgabeEinstellungen } from '../src/bedienEinstellungen';

describe('bedienEinstellungen', () => {
  it('Vorgaben', () => {
    const e = vorgabeEinstellungen();
    expect(e.drehSchrittGrad).toBe(15);
    expect(e.sichtfeldGrad).toBeGreaterThanOrEqual(70);
    expect(e.sichtfeldGrad).toBeLessThanOrEqual(100);
    expect(e.zielen).toBe('halten');
    expect(e.vignette).toBe(true);
    expect(e.hinweiseImmer).toBe(false);
    expect(e.orbitLinks).toBe('drehen');
  });
  it('Linke Maustaste im Orbit: eigenes Feld ohne neues Schema, alte Speicherstände → drehen, Fremdwert → Vorgabe', () => {
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, orbitLinks: 'verschieben' })).orbitLinks).toBe('verschieben');
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, invertY: true })).orbitLinks).toBe('drehen');
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, orbitLinks: 'zoomen' })).orbitLinks).toBe('drehen');
    expect(ladeEinstellungen(speichereEinstellungen({ ...vorgabeEinstellungen(), orbitLinks: 'verschieben' }))).toEqual({ ...vorgabeEinstellungen(), orbitLinks: 'verschieben' });
  });
  it('Hinweise immer zeigen: eigenes Feld ohne neues Schema, Fremdwert → Vorgabe', () => {
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, hinweiseImmer: true })).hinweiseImmer).toBe(true);
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, hinweiseImmer: 'ja' })).hinweiseImmer).toBe(false);
    expect(ladeEinstellungen(JSON.stringify({ schema: 1, invertY: true })).hinweiseImmer).toBe(false);
    expect(ladeEinstellungen(speichereEinstellungen({ ...vorgabeEinstellungen(), hinweiseImmer: true }))).toEqual({ ...vorgabeEinstellungen(), hinweiseImmer: true });
  });
  it('klemmt und verwirft Fremdes', () => {
    const e = ladeEinstellungen(JSON.stringify({ schema: 1, sichtfeldGrad: 150, drehSchrittGrad: -3, tempo: 'schnell', zielen: 'irgendwie', fadenkreuzPx: 8 }));
    expect(e.sichtfeldGrad).toBe(100);
    expect(e.drehSchrittGrad).toBe(5);
    expect(e.tempo).toBe(vorgabeEinstellungen().tempo);
    expect(e.zielen).toBe('halten');
    expect(e.fadenkreuzPx).toBe(16);
    expect(ladeEinstellungen('nicht json')).toEqual(vorgabeEinstellungen());
    expect(ladeEinstellungen(null)).toEqual(vorgabeEinstellungen());
    expect(ladeEinstellungen(JSON.stringify({ schema: 2, invertY: true }))).toEqual(vorgabeEinstellungen());
    expect(ladeEinstellungen(speichereEinstellungen({ ...vorgabeEinstellungen(), invertY: true }))).toEqual({ ...vorgabeEinstellungen(), invertY: true });
  });
  it('verwirft nicht endliche Zahlen und falsche Typen', () => {
    const e = ladeEinstellungen('{"schema":1,"empfindlichkeit":null,"invertY":"ja","vignette":0,"bewegungReduzieren":"vielleicht","drehenInSchritten":"an","hoeheHalten":true}');
    expect(e.empfindlichkeit).toBe(1);
    expect(e.invertY).toBe(false);
    expect(e.vignette).toBe(true);
    expect(e.bewegungReduzieren).toBe('system');
    expect(e.drehenInSchritten).toBe('an');
    expect(e.hoeheHalten).toBe(true);
  });
  it('reduzierte Bewegung: System oder eigene Einstellung; Drehen in Schritten folgt bei auto', () => {
    const e = vorgabeEinstellungen();
    expect(bewegungReduziert(e, true)).toBe(true);
    expect(bewegungReduziert({ ...e, bewegungReduzieren: 'aus' }, true)).toBe(false);
    expect(bewegungReduziert({ ...e, bewegungReduzieren: 'an' }, false)).toBe(true);
    expect(drehenInSchritten(e, true)).toBe(true);
    expect(drehenInSchritten(e, false)).toBe(false);
    expect(drehenInSchritten({ ...e, drehenInSchritten: 'aus' }, true)).toBe(false);
    expect(drehenInSchritten({ ...e, drehenInSchritten: 'an' }, false)).toBe(true);
  });
});
