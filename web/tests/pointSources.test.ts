import { expect, it } from 'vitest';
import { MeasureStore } from '../src/measureStore';
import { measurementsCsv } from '../src/measureCsv';
import { MessungSync, messungDaten } from '../src/serverMessungen';
import { PoiStore, parsePoiFile } from '../src/poiStore';
import { pruefeEntwurf } from '../src/befundEntwurf';
import { sourceLabel, measurementNotes, sourceReusable, anzeigeSprache } from '../src/pointSources';
import type { PointSource } from '../src/pointSources';
const mesh: PointSource = {source:'mesh',layerId:'modell'};
it('Quellen überstehen Eckpunktänderung, Einfügen, Entfernen, Undo, Server und JSON', () => {
 const store=new MeasureStore(); const m=store.addGeometry('polyline',[[0,0,0],[1,0,0],[2,0,0]],[mesh,null,mesh]);
 store.moveVertex(m.id,1,[1,1,0],{source:'pointcloud',layerId:'punkte'});
 store.insertVertex(m.id,2,[1.5,1,0],mesh); store.removeVertex(m.id,0);
 expect(store.get(m.id)!.pointSources).toEqual([{source:'pointcloud',layerId:'punkte'},mesh,mesh]);
 store.undo(); expect(store.get(m.id)!.pointSources).toHaveLength(4);
 expect(messungDaten(store.get(m.id)!).pointSources).toHaveLength(4);
 expect(JSON.stringify(store.toJSON({projectId:'test',revision:1}))).toContain('pointSources');
 store.moveVertex(m.id,1,[1,2,0]); expect(store.get(m.id)!.pointSources![1]).toBeNull();
});
it('falsche Anzahl/Quelle wird abgewiesen; alte Daten bleiben unbekannt', () => {
 const s=new MeasureStore(); expect(()=>s.addGeometry('distance',[[0,0,0],[1,0,0]],[mesh])).toThrow();
 expect(s.add([0,0,0],[1,0,0]).pointSources).toBeUndefined(); expect(sourceLabel(null,[])).toBe('unbekannt');
});
it('POI-Quelle übersteht Export und Import', () => {
 const s=new PoiStore('test'); s.add({title:'Quelle',category:'',note:'',position:[1,2,3],station:null,positionSource:mesh});
 const file=s.toFile(1); expect(parsePoiFile(file,'test','package')[0]!.positionSource).toEqual(mesh);
});
it('CSV enthält Quelle sowie Modellhinweise, Titel bleiben eine sichere Zelle', () => {
 const s=new MeasureStore(); const m=s.addGeometry('distance',[[0,0,0],[1,0,0]],[mesh,null]);
 const layers=[{id:'modell',title:'=Modell;"Test"',provenance:{scaleVerified:false,interpolated:true}}];
 expect(measurementNotes(m,layers)).toContain('Modelloberfläche (teils interpoliert)');
 const csv=measurementsCsv(s.list(),layers);
 expect(csv).toContain(';Quelle'); expect(csv).toContain('Modellmaß'); expect(csv).toContain('P2: unbekannt');
 expect(csv).toContain('"P1: Mesh: =Modell;""Test""');
});

it('Neuladen und Link-Zuordnung übernehmen Quellen aus dem Serverstand', async () => {
 const s=new MeasureStore(); s.add([0,0,0],[1,0,0]);
 const fetchFn=(async () => new Response(JSON.stringify({messungen:[{id:'M-server',version:1,daten:{kind:'distance',points:[[0,0,0],[1,0,0]],pointSources:[mesh,null]}}]}),{status:200})) as typeof fetch;
 const sync=new MessungSync({store:s,projectId:'test',schreibbar:true,melde:()=>undefined,fetchFn});
 await sync.laden(); expect(s.list()).toHaveLength(1); expect(s.list()[0]!.pointSources).toEqual([mesh,null]);
});
it('Befundentwurf behält Herkunft; ungültige Herkunft wird verworfen', () => {
 const e={schema:1,schluessel:'lokal|test',zeit:1,punkt:[1,2,3],station:null,ansicht:'',poiId:null,formular:{title:'',category:'',note:''},positionSource:mesh};
 expect(pruefeEntwurf(e,'lokal|test')!.positionSource).toEqual(mesh);
 expect(pruefeEntwurf({...e,positionSource:{source:'guess',layerId:'m'}},'lokal|test')).toBeNull();
});

it('Panorama übernimmt keine gespeicherten Mesh-Eckpunkte als Fangpunkte', () => {
 expect(sourceReusable(mesh,true)).toBe(false);
 expect(sourceReusable({source:'pointcloud',layerId:'punkte'},true)).toBe(true);
 expect(sourceReusable(null,true)).toBe(true);
 expect(sourceReusable(mesh,false)).toBe(true);
});

it('Modellmaß aus mehreren Ebenen ist eine Meldung mit allen Quellen, auf Englisch nach Browsersprache', () => {
 const m={pointSources:[mesh,{source:'mesh',layerId:'b'} as PointSource,mesh]};
 const layers=[{id:'modell',title:'A',provenance:{scaleVerified:false,interpolated:false,scaleSource:'unit:file'}},
   {id:'b',title:'B',provenance:{scaleVerified:false,interpolated:true,scaleSource:'unit:cli'}}];
 expect(measurementNotes(m,layers)).toEqual(['Modellmaß — Maßstab nicht belegt (Quelle: unit:file, unit:cli)','Modelloberfläche (teils interpoliert)']);
 expect(measurementNotes(m,layers,'en')).toEqual(['Model units — scale not verified (source: unit:file, unit:cli)','Model surface (partly interpolated)']);
 expect(anzeigeSprache(['de-DE','en'])).toBe('de'); expect(anzeigeSprache(['en-US','de'])).toBe('de'); expect(anzeigeSprache(['en-GB'])).toBe('en');
});
