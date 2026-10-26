import './spleenStart';
import { freieSpanne } from './freiraum';
import { createSplatLayer } from './splatLayer';
import type { SplatHandle } from './splatLayer';
import { createMeshLayer } from './meshLayer';
import type { MeshLayerHandle } from './meshLayer';
import { createTilesLayer } from './tilesLayer';
import type { TilesLayerHandle } from './tilesLayer';
import { TilesetFehler } from './tilesMath';
import { KachelPickSource } from './tilesPick';
import { datenSparen } from './meshMath';
import { erstelleSonne } from './sonne';
import { sonnenOrt } from './sonneOrt';
import { attachSonnePanel } from './sonnePanel';
import { attachKontrollpunktePanel } from './kontrollpunktePanel';
import { kpEntwurfSpeicher } from './kontrollpunktEntwurf';
// Einstieg: ?p=<paket-url> (Default aus VITE_STANDARDPAKET, sonst /data/beispiel/ — standardpaket.ts) → project.json (Schema 1/2) → Range-Probe → Viewer + UI.
import { setLazPerfPath } from '@giro3d/giro3d/sources/las/config.js';
import './styles/tokens.css';
import './styles/toolbar.css';
import './styles/panels.css';
import './styles/bedienung.css';
import './styles/begehen.css';
import './styles/fadenkreuz.css';
import './styles/befund.css';
import { loadManifest, resolveUrl } from './manifest';
import { probeRange } from './rangeProbe';
import { createViewer } from './viewer';
import { addFensterControls, addKlassenControls, addPanoramaControls, addPoiControls, buildUi } from './ui';
import { filterVonLink, filterZuLink } from './klassen';
import { attachPanorama } from './panorama';
import type { Panorama } from './panorama';
import { MeasureStore } from './measureStore';
import type { MeasureKind } from './measureStore';
import { attachMeasureTool } from './measure';
import { attachKoordinatenzeile } from './koordinatenzeile';
import { attachFensterEffekt } from './fensterEffekt';
import type { Koordinatenzeile } from './koordinatenzeile';
import { attachMeasureList } from './measureList';
import type { MeasureList } from './measureList';
import { createToolbar } from './toolbar';
import { anschlagMoeglich, oeffneAnschlag, trifftAnschlag } from './anschlag';
import { erkenneModus, gastFetch, starteDebug, zeigeGastHinweis, zeigeZurueck } from './platform';
import { debugAn, setzeDebug, viewerVersion } from './debug';
import { verbindePlattform } from './plattformViewer';
import { attachShortcuts, PANEL_AKTION } from './keymap';
import { BELEGUNG_SCHLUESSEL, kuerzelFuer, ladeBelegung, padBelegung, speichereBelegung } from './tastenbelegung';
import type { AktionId, Belegung } from './tastenbelegung';
import { bewegungReduziert, EINSTELLUNGEN_SCHLUESSEL, ladeEinstellungen, speichereEinstellungen } from './bedienEinstellungen';
import type { BedienEinstellungen } from './bedienEinstellungen';
import { createAnsage, createBedienung, createEinstellungen } from './bedienDialoge';
import { ansichtVon, ansichtWechselText, punktwolkeSatz, vollbildVerfuegbar } from './bedienhilfe';
import type { HilfeKontext } from './bedienhilfe';
import { fingerGesten, mausTasten } from './drehpunkt';
import { attachDrehpunktMarker } from './drehpunktMarker';
import { attachLeinwand } from './leinwand';
import { attachBegehen } from './begehen';
import type { Begehen } from './begehen';
import type { KeyAction, ToolId } from './keymap';
import { createPanelHost } from './panels';
import type { PanelHost } from './panels';
import { attachPanelMenu } from './panelMenu';
import { browserStorage, loadFotoLayout, saveFotoLayout } from './layoutStore';
import { attachFotoTeilung, synchronisiereFotoLeinwand } from './fotoTeilung';
import { attachFotoWerkzeug } from './fotoWerkzeug';
import { fotoTiefenquelle } from './fotoTiefe';
import { attachFotoSynchron } from './fotoSynchron';
import { attachClipBox } from './clipBox';
import { pointAllowed } from './clipBoxMath';
import { decodeBefund, decodeHash, encodeHash, poseAus } from './hash';
import type { OrderKind, ViewState } from './hash';
import { hashGeometrie } from './hashGeometrie';
import { loadStations, ohnePanoramen } from './stations';
import { loadImages } from './images';
import { verdecktDurchWolke } from './imageMath';
import type { ImageSet } from './images';
import { attachImageLayer } from './imageLayer';
import { attachPhotoPanel } from './photoPanel';
import { attachFotoGalerie } from './fotoGalerie';
import { attachThroughCamera } from './throughCamera';
import type { Station, StationSet } from './stations';
import { attachStationLayer } from './stationLayer';
import { attachMinimap } from './minimap';
import { markerPosition, neighbourInDirection } from './stationMath';
import { Vector3 } from 'three';
import { CompositePickSource, attachPickPreference, loadPickPreference } from './compositePick';
import { MeshPickSource } from './meshPick';
import { zoomStoppAnbinden } from './zoomStopp';
import { createRayPicker } from './rayPick';
import { scanFilterFor, scanFilterHint } from './scanFilter';
import type { ScanFilter } from './scanFilter';
import { PoiStore, RUECKMELDUNG } from './poiStore';
import { attachPois } from './pois';
import type { PoiPanel } from './pois';
import { createLayerRegistry, initialVisibility, layerForKey, lyParam, visibleBounds } from './layers';
import type { LayerRegistry } from './layers';
import { attachLayersPanel } from './layersPanel';
import { standardPaketPfad } from './standardpaket';
import { createRueckfrage } from './rueckfrage';
import { FORMULAR_OFFEN } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';
import { attachZielpunktSteuerung } from './zielpunktSteuerung';
import type { ZielpunktSteuerung } from './zielpunktSteuerung';
import { attachBefundSteuerung } from './befundSteuerung';
import type { BefundSteuerung } from './befundSteuerung';
import { entwurfSchluesselFuer } from './befundEntwurf';
import { attachEntwurfSteuerung } from './entwurfSteuerung';
import { attachHinweisAnzeige } from './hinweisAnzeige';
import { attachPadSteuerung } from './padSteuerung';
import type { MenueEintrag, PadSteuerung } from './padSteuerung';
import { hinweisSchluessel } from './hinweise';
import { attachEmpfang } from './empfang';
import { attachSprunglinks } from './sprunglinks';

/** Kamera-/Messänderungen werden gedrosselt in den URL-Hash geschrieben (kein replaceState pro Frame). */
const HASH_THROTTLE_MS = 300;
/** Ebene je Aktion: „Einzeltasten aus“ schaltet nur die Ebene „global“ ab, die Begehen-Tasten wirken weiter. */
/** Werkzeugwechsel im Begehen: Reihenfolge der Werkzeugleiste. */
const WERKZEUG_REIHENFOLGE: readonly ToolId[] = ['navigate', 'distance', 'polyline', 'area', 'height', 'poi'];
/** Werkzeug der Leiste → Messart (Navigieren und POI anlegen messen nicht). */
const KIND_OF: Record<ToolId, MeasureKind | null> = {
  navigate: null,
  distance: 'distance',
  polyline: 'polyline',
  area: 'area',
  height: 'height',
  poi: null,
};

// laz-perf.wasm selbst ausliefern statt vom CDN (siehe vite.config.ts, Plugin scanview-laz-perf-wasm).
setLazPerfPath(`${import.meta.env.BASE_URL}wasm`);

const msg = document.getElementById('msg')!;
// Eine Live-Region für alle Meldungen: sichtbar in #msg, zusätzlich für Hilfstechnik in #ansage.
const ansage = createAnsage(document.getElementById('ansage')!);
const say = (t: string): void => {
  msg.textContent = t;
  if (t) ansage.melde(t);
};
/** Vorübergehende Meldung; danach wieder die Grundmeldung (wie in measure.ts). */
const notify = (t: string): void => {
  msg.textContent = t;
  ansage.melde(t);
  window.setTimeout(() => {
    if (msg.textContent === t) msg.textContent = msg.dataset.base ?? '';
  }, 4000);
};

async function start(): Promise<void> {
  // Viewer-Naht zur Plattform (platform.ts): einmalig erkennen, ob ein Plattform-Server
  // dahinter läuft. Ohne Paket dorthin weiterleiten statt ihn im Einzelmodus zu erraten.
  const modus = await erkenneModus();
  if (modus.art === 'weiterleiten') { location.replace(modus.ziel); return; }
  // Debug-Overlay (Leistungsanzeige) in Einzel- und Plattformmodus, wenn debugAn(); der Schalter im
  // Einstellungsdialog baut es ab und wieder auf.
  let leistungAn = debugAn();
  let leistungAbbau = starteDebug(document.body, leistungAn);
  if (modus.art === 'plattform') zeigeZurueck(document.body);
  // Nur-Lese-Link (F28): bereinigtes Manifest vom Server, Kennzeichnung unten.
  const gast = modus.art === 'einzeln' ? modus.gast : undefined;
  if (gast) zeigeGastHinweis(document.body, gast);
  const pkg = new URLSearchParams(location.search).get('p') ?? (modus.art === 'einzeln' ? standardPaketPfad(import.meta.env.VITE_STANDARDPAKET) : '');
  const base = new URL(pkg, location.href).toString();
  say('Lade Projekt …');
  // Fehlerpfade (kein Paket, HTML statt JSON, Server weg, ungültiges Manifest) mit klarer Meldung (I3).
  const p = await loadManifest(base, gast ? gastFetch() : undefined);
  const cloud = p.cloud;
  let cloudSize = 0;
  if (cloud) {
    const probe = await probeRange(resolveUrl(base, cloud.url));
    if (!probe.ok) { say(`Punktwolke kann nicht gestreamt werden: ${probe.reason}`); return; }
    cloudSize = probe.size;
  }
  const bounds = visibleBounds(p, () => true);
  if (!bounds) { say('Paket enthält keine darstellbare räumliche Ebene.'); return; }
  const startLayers = decodeHash(location.hash, p.originWorld).ly ?? p.initialView.layers;
  const v = await createViewer(document.getElementById('view') as HTMLDivElement, p, cloud, base, cloudSize);
  if (cloud && v.cloud && initialVisibility(p.layers, startLayers)[cloud.id] === false) {
    v.cloud.visible = false; v.cloud.frozen = true;
  }
  const meshHandles: MeshLayerHandle[] = [];
  const meshMessages: string[] = [];
  let meshReady = false;
  const splatPending = new Set<string>();
  const splatLoading = (id: string, active: boolean): void => {
    if (active) splatPending.add(id); else splatPending.delete(id);
    if (meshReady) say([msg.dataset.base ?? '', splatPending.size ? 'Lade Sichtschicht …' : ''].filter(Boolean).join(' · '));
  };
  const meshNotify = (text: string): void => { if (meshReady) notify(text); else meshMessages.push(text); };
  const failedMeshes = new Set<string>();
  // Alle Vorschauen parallel; jede hat ein eigenes Zeitlimit (meshLayer.PREVIEW_TIMEOUT_MS), der Start
  // wartet also höchstens so lange wie die langsamste Vorschau, nie unbegrenzt.
  const geladen = await Promise.allSettled(p.meshes.map((mesh) => createMeshLayer({ instance: v.instance, layer: mesh,
    originWorld: p.originWorld, pkgBase: base, wasmBase: `${import.meta.env.BASE_URL}wasm/`, notify: meshNotify })));
  geladen.forEach((r, i) => {
    const mesh = p.meshes[i]!;
    if (r.status === 'fulfilled') meshHandles.push(r.value);
    else { failedMeshes.add(mesh.id); meshNotify(`Mesh ${mesh.title} nicht ladbar`); }
  });
  // Gekachelte Meshes (3D Tiles): Ebene mit Blattabruf zum Messen (tilesLayer.ts, tilesPick.ts). Ein Vertragsfehler
  // (CRS, Lage, Hierarchie) sperrt nur diese Ebene und sagt warum.
  const tilesHandles: TilesLayerHandle[] = [];
  const tilesGeladen = await Promise.allSettled(p.tiles.map((layer) => createTilesLayer({ instance: v.instance, layer, frameWkt: p.crs.wkt,
    pkgBase: base, wasmBase: `${import.meta.env.BASE_URL}wasm/`, notify: meshNotify })));
  tilesGeladen.forEach((r, i) => {
    const layer = p.tiles[i]!;
    if (r.status === 'fulfilled') tilesHandles.push(r.value);
    else {
      failedMeshes.add(layer.id);
      meshNotify(`Gekachelte Ebene ${layer.title} nicht ladbar${r.reason instanceof TilesetFehler ? `: ${r.reason.message}` : ''}`);
    }
  });
  const splatHandles: SplatHandle[] = [];
  const splatsLoaded = await Promise.allSettled(p.splats.map((layer) => createSplatLayer({
    instance: v.instance, layer, originWorld: p.originWorld, pkgBase: base, notify: meshNotify,
    loading: (active) => splatLoading(layer.id, active),
  })));
  splatsLoaded.forEach((result, i) => {
    if (result.status === 'fulfilled') splatHandles.push(result.value);
    else {
      const id = p.splats[i]!.id;
      splatHandles.push({ id, disabled: true, setVisible: () => undefined,
        setOpacity: () => undefined, dispose: () => undefined });
      meshNotify('Sichtschicht nicht ladbar – Grafikspeicher oder Sicherheitsrichtlinie prüfen.');
    }
  });
  window.addEventListener('pagehide', (ev) => { if (!ev.persisted) splatHandles.forEach((h) => h.dispose()); });
  v.instance.addEventListener('before-camera-update', () => {
    const cam = v.instance.view.camera; const c = cam.position;
    const sicht = { fovGrad: 'fov' in cam ? Number(cam.fov) : 0, sparsam: datenSparen(navigator as Parameters<typeof datenSparen>[0]) };
    for (const h of meshHandles) h.update([c.x, c.y, c.z], sicht);
  });
  window.addEventListener('pagehide', (ev) => { if (!ev.persisted) { meshHandles.forEach((h) => h.dispose()); tilesHandles.forEach((h) => h.dispose()); } });
  // Live-Sonne (S2): nur mit Mesh-Ebenen (Punktwolken/Sichtschichten sind unbeleuchtet und werfen keine Schatten).
  const sonne = meshHandles.length || tilesHandles.length ? erstelleSonne({ instance: v.instance, ort: sonnenOrt(p.originWorld, p.crs.wkt), meshes: [...meshHandles, ...tilesHandles] }) : null;
  // CRS-Hinweis (I4) bleibt stehen, statt wie die Ladeanzeige gelöscht zu werden.
  msg.dataset.base = v.crsHint ?? '';
  say(msg.dataset.base);
  meshReady = true;
  if (splatPending.size) say([msg.dataset.base ?? '', 'Lade Sichtschicht …'].filter(Boolean).join(' · '));
  if (meshMessages.length) notify(meshMessages.join(' · '));

  // Deep-Link: Kamera/Standpunkt + Messungen aus dem URL-Hash übernehmen, relativ zu originWorld
  // (st hat Vorrang vor cam/tgt — siehe Verdrahtung nach attachPanorama unten).
  const store = new MeasureStore();
  const initial = decodeHash(location.hash, p.originWorld);
  let vorschlaegeSichtbar = initial.kv !== false;
  // Befund aus dem Link getrennt gelesen: eine kaputte Pose verwirft initial ganz, den Befund nicht. Jetzt, bevor
  // das erste gedrosselte Schreiben die Adresszeile ersetzt.
  const befundImHash = decodeBefund(location.hash);
  // Klassen-/Echofilter aus dem Link (F30) sofort, damit die ersten Knoten schon gefiltert dekodiert werden;
  // nur, was das Paket hergibt (filterVonLink).
  if (cloud && (initial.kx || initial.ec)) v.setPunktfilter(filterVonLink(initial, cloud));
  // Reihenfolge wie beim Anlegen (o=); ohne o= erst die Strecken (m=), dann g=.
  const hashM = [...(initial.m ?? [])];
  const hashG = [...(initial.g ?? [])];
  const order: OrderKind[] = initial.order ?? [...hashM.map((): OrderKind => 'distance'), ...hashG.map((x) => x.kind)];
  let dropped = 0;
  for (const k of order) {
    if (k === 'distance') {
      const [a, b] = hashM.shift()!;
      store.add(a, b);
      continue;
    }
    const x = hashG.shift()!;
    try {
      store.addGeometry(x.kind, x.points);
    } catch {
      dropped++;
    }
  }
  // (plattformViewer.ts): im Plattformmodus Messungen/POIs des Projekts vom Server — vor dem Anhängen
  // des Messwerkzeugs, damit sync() sie gleich mitzeichnet; das Laden ist kein rückgängig zu machender Schritt.
  const plattform =
    modus.art === 'plattform' ? await verbindePlattform({ projectId: p.projectId, store, notify, nutzerId: modus.nutzerId }) : null;
  // Das Laden selbst ist kein rückgängig zu machender Schritt.
  store.resetHistory();

  // currentPano wird erst nach attachPanorama gesetzt; ein Drag während des noch laufenden
  // loadStations darf scheduleHashUpdate aber schon jetzt anhängen können, ohne auf ein noch nicht
  // angelegtes pano zuzugreifen (TDZ-Fehler) — deshalb hier deklarieren, dort zuweisen.
  let currentPano: Panorama | null = null;
  let hashTimer: ReturnType<typeof setTimeout> | undefined;
  // Strecken weiter in m= (alte Links), übrige Arten in g=, Anlegereihenfolge in o= (hash.ts).
  // Plattformmodus: nur nicht am Server gebundene Messungen (hashGeometrie.ts, Final-Fix C/D).
  const geometryState = (): Pick<ViewState, 'm' | 'g' | 'order'> => hashGeometrie(store.list(), plattform?.messungImLink);
  // Befundlink (b/rev) in der Adresszeile: nur solange die Empfängeransicht einen Befund zeigt, liefert
  // `befund.imLink` dessen ID und Revision. In der Werkzeugansicht ist die Adresszeile kein Befundlink —
  // ein mitgebrachtes b/rev fällt beim ersten gedrosselten Schreiben weg, damit Neuladen mit Auswahl
  // keine Empfängeransicht öffnet. Geteilt wird ein Befund über den erzeugten Teilen-Link.
  const befund: { imLink: () => Pick<ViewState, 'b' | 'rev'> } = { imLink: () => ({}) };
  // Koordinatenanzeige am Zeiger (F23) entsteht nach dem Messwerkzeug; ko=1 nur, solange sie an ist.
  let koZeile: Koordinatenzeile | null = null;
  const koParam = (): Pick<ViewState, 'ko'> => (koZeile?.an() ?? initial.ko) ? { ko: true } : {};
  // Aktuelle Ansicht ohne Befund — für die Adresszeile und den Teilen-Link (befundSteuerung.ts).
  const ansicht = (): ViewState => {
    const cam = v.instance.view.camera.position;
    const tgt = v.controls.target;
    const ps = currentPano?.state ?? null;
    // reg existiert erst nach attachStationLayer (unten) — vorher (z. B. ein Kameradrag während des
    // noch laufenden loadStations) bleibt ein aus dem Deep-Link übernommenes ly= unverändert stehen,
    // statt durch replaceState mit einem undefinierten Feld verworfen zu werden.
    const ly = reg ? lyParam(p.layers, Object.fromEntries(reg.entries().map((e) => [e.id, e.visible]))) : initial.ly;
    return ps
      ? { st: ps.station, yaw: ps.yaw, pitch: ps.pitch, fov: ps.fov, ...geometryState(), ly, ...koParam(), ...(vorschlaegeSichtbar ? {} : {kv: false}), ...filterZuLink(v.punktfilter()) }
      : { cam: [cam.x, cam.y, cam.z], tgt: [tgt.x, tgt.y, tgt.z], ...geometryState(), ly, ...koParam(), ...(vorschlaegeSichtbar ? {} : {kv: false}), ...filterZuLink(v.punktfilter()) };
  };
  const scheduleHashUpdate = (): void => {
    if (hashTimer !== undefined) return;
    hashTimer = setTimeout(() => {
      hashTimer = undefined;
      history.replaceState(null, '', encodeHash({ ...ansicht(), ...befund.imLink() }, p.originWorld));
    }, HASH_THROTTLE_MS);
  };

  // Schnittbox vor dem Messwerkzeug anhängen, damit dessen Pick-Filter von Anfang an
  // den aktuellen Boxzustand abfragen kann (per Getter — die Reihenfolge selbst wäre wegen des
  // Getter-Indirekts egal, siehe measure.ts).
  const clip = attachClipBox(v, bounds, (planes) => { meshHandles.forEach((h) => h.setClippingPlanes(planes)); tilesHandles.forEach((h) => h.setClippingPlanes(planes)); });

  // Werkzeug erst jetzt anhängen: sync() im Inneren zeichnet die eben aus dem Hash geladenen
  // Messungen sofort, ohne dass ein Kamera-Change-Listener vorher schon einen (leeren) Hash
  // zurückschreibt.
  // Panorama wird erst nach dem Laden der Standpunkte angehängt; bis dahin misst das Werkzeug im Orbit.
  let panoRef: Panorama | null = null;
  // Eine gemeinsame Strahlsuche für Messen und POIs (D49: getHierarchy nur einmal). Scanfilter: im Panorama
  // nur Punkte des Scans der aktuellen Station; die Standpunkte kommen erst
  // unten, deshalb per Getter.
  let stationById = new Map<string, Station>();
  const currentScanFilter = (): ScanFilter | null => {
    const id = panoRef?.state?.station;
    return scanFilterFor(cloud?.pointSourceIsScan, id === undefined ? null : (stationById.get(id) ?? { pointSourceId: null }));
  };
  // Ebenen-Register: erst nach attachStationLayer angelegt (unten), hier per Getter in Reichweite —
  // vorher (Punktwolke immer sichtbar) und danach dieselbe Prüfung wie ein Kürzeldruck (reg.isVisible).
  let reg: LayerRegistry | null = null;
  const cloudVisible = (): boolean => (reg ? reg.isVisible(cloud?.id ?? '') : true);
  const cloudPicker = createRayPicker(v, () => clip.getState(), currentScanFilter, cloudVisible);
  const meshPicker = new MeshPickSource({ camera: () => v.instance.view.camera,
    rect: () => v.instance.domElement.getBoundingClientRect(), layers: () => meshHandles, clip: () => clip.getState() });
  const tilesPicker = new KachelPickSource({ camera: () => v.instance.view.camera,
    rect: () => v.instance.domElement.getBoundingClientRect(), layers: () => tilesHandles, clip: () => clip.getState() });
  // Zoom-Stopp vor Mesh-Oberflächen (Mausrad, zwei Finger, Knopf „Näher“): sonst führt der Zoom zum Drehpunkt
  // in der Modellmitte durch die Fläche.
  const zoomStopp = meshHandles.length || tilesHandles.length ? zoomStoppAnbinden({ controls: v.controls, camera: () => v.instance.view.camera,
    // Im Panorama gibt es keinen Orbit-Zoom, und das Mesh wird dort weder gemessen noch getroffen.
    meshes: () => (panoRef?.active ? [] : [...meshHandles, ...tilesHandles].filter((h) => h.visible).flatMap((h) => h.occluderMeshes)),
    erlaubt: (p) => pointAllowed(clip.getState(), [p.x, p.y, p.z]) }) : null;
  for (const h of [...meshHandles, ...tilesHandles]) h.onChange(() => zoomStopp?.messen());
  let pickPreference = loadPickPreference();
  const picker = new CompositePickSource({ pointcloud: cloudPicker, cloudLayerId: cloud?.id ?? '', meshes: tilesHandles.length ? [meshPicker, tilesPicker] : [meshPicker],
    preference: () => pickPreference, panorama: () => panoRef?.active ?? false });
  // Ziffer der primären Punktwolke unter den Ebenen-Tasten (layerForKey) — für den Hinweis, wenn sie beim
  // Messen/POI-Anlegen ausgeblendet ist.
  const cloudKey = (() => {
    for (let n = 1; n <= 9; n++) if (layerForKey(p.layers, n) === cloud?.id) return n;
    return null;
  })();
  const hiddenCloudMessage = `Punktwolke ausgeblendet – zum Messen einblenden${cloudKey ? ` (Taste ${cloudKey})` : ''}`;
  // Werkzeugleiste = einziger Besitzer des aktiven Werkzeugs; die Messliste wird erst nach dem
  // Werkzeug angelegt, deshalb hier deklarieren (Muster wie currentPano).
  let list: MeasureList | null = null;
  // attachPois läuft erst nach dem Laden der Standpunkte; ein Leistenwechsel davor landet über
  // setCreateMode(toolbar.tool() === 'poi') direkt nach attachPois.
  let currentPois: PoiPanel | null = null;
  // Panel-Host: erst nach attachMeasureList angelegt (siehe unten), hier schon in
  // Reichweite, da die Werkzeugleiste ihren Listenknopf ebenfalls über panels.toggle() führt.
  let panels: PanelHost | null = null;
  // Begehen braucht das Panorama und wird deshalb erst danach angehängt; Werkzeugleiste und Klick-Handler
  // davor greifen per Getter darauf zu (Muster wie currentPano).
  let begehen: Begehen | null = null;
  // Zielpunkt-Steuerung (Fadenkreuz, Setzen, Gamepad): erst nach dem Begehen angelegt, davor per Getter.
  let zp: ZielpunktSteuerung | null = null;
  // Befund (Auswahl, Teilen, Link öffnen): erst nach den POIs angelegt, der Knopf „Teilen“ greift per Getter zu.
  let befundSt: BefundSteuerung | null = null;
  // Controller: erst nach dem Verteiler angelegt; Vibration beim Setzen davor per Getter.
  let padSt: PadSteuerung | null = null;
  // Tastenhinweise: nach den POIs angelegt; Zählen davor (Tastenhilfe, Fadenkreuz) per Getter.
  let hinweise: ReturnType<typeof attachHinweisAnzeige> | null = null;
  const austrittsTreffer = (): [number, number, number] | null => zp?.austrittsTreffer() ?? null;
  const unterPointerLock = (): boolean => begehen?.gesperrt() ?? false;
  // Rückfrage „vorn/hinten“ (Fadenkreuz und Mausklick). Solange sie offen ist, steht die Kamera: die
  // zweite Suche im gewählten Tiefenfenster braucht denselben Strahl wie die erste. Hat sie sich seit der
  // Eingabe doch bewegt, wird abgebrochen.
  const rueckfrage = createRueckfrage(document.body, notify);
  const kameraPose = (): number[] => {
    const c = v.instance.view.camera;
    return [c.position.x, c.position.y, c.position.z, c.quaternion.x, c.quaternion.y, c.quaternion.z, c.quaternion.w];
  };
  // Die Pose der Eingabe vergleicht zielpunktSetzen vor der zweiten Suche (ansichtUnveraendert).
  const frageStill = async (b: Rueckfrage, ort: { x: number; y: number }): Promise<Wahl | null> => {
    const orbitAn = v.controls.enabled;
    v.controls.enabled = false;
    try {
      return await rueckfrage.frage(b, ort);
    } finally {
      // Nur zurückgeben, was hier gesperrt wurde: das Begehen hält die Steuerung selbst gesperrt, ein inzwischen
      // betretenes Panorama ebenfalls (es gibt sie beim Verlassen selbst frei). panoRef statt pano: frageStill
      // kann laufen, bevor das Panorama angehängt ist.
      if (orbitAn && !(begehen?.aktiv ?? false) && !(panoRef?.active ?? false)) v.controls.enabled = true;
    }
  };
  // Tastenbelegung (browserweit in localStorage gespeichert). Kein Zugriff (Privatmodus, gesperrt) → Vorgabe.
  const safeGet = (k: string): string | null => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  };
  const safeSet = (k: string, wert: string): void => {
    try {
      localStorage.setItem(k, wert);
    } catch {
      /* Speicher gesperrt: die Einstellung gilt dann nur für diese Sitzung */
    }
  };
  let belegung = ladeBelegung(safeGet(BELEGUNG_SCHLUESSEL));
  let einstellungen = ladeEinstellungen(safeGet(EINSTELLUNGEN_SCHLUESSEL));
  // Maustasten im Orbit aus der Einstellung (links dreht oder verschiebt); gilt sofort, auch nach Änderung im Dialog.
  v.controls.mouseButtons = mausTasten(einstellungen.orbitLinks);
  v.controls.touches = fingerGesten(einstellungen.orbitLinks);
  // Beschriftung eines Kürzels: leer, wenn keins belegt ist oder „Einzeltasten aus“ es abschaltet — das
  // betrifft nur die Ebene „global“ (Strg-/Alt-Akkorde und die Begehen-Tasten bleiben aktiv und werden angezeigt).
  const kuerzel = (a: AktionId): string => kuerzelFuer(belegung, a);
  const leinwand = attachLeinwand(v.instance.domElement, { name: () => `3D-Ansicht ${p.title}`, kuerzel });
  // Dialog „Bedienung“: Maus und Finger der gerade aktiven Ansicht, darunter die Tasten. Begehen, Panorama und
  // Empfang entstehen erst weiter unten — per Getter.
  const vollbildMoeglich = vollbildVerfuegbar(document, document.documentElement);
  const eigenesFenster = (): boolean =>
    window.matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true;
  // Vollbild (Desktop, Android, iPad; ältere iPads nur mit Präfix). Das iPhone kennt es nicht: dort erklärt der Knopf
  // „Zum Home-Bildschirm“ (Web-App ohne Adresszeile); im eigenen Fenster entfällt er.
  type WebkitDok = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => void };
  type WebkitEl = HTMLElement & { webkitRequestFullscreen?: () => void };
  const vollbildElement = (): Element | null => document.fullscreenElement ?? (document as WebkitDok).webkitFullscreenElement ?? null;
  const wechsleVollbild = (): void => {
    const dok = document as WebkitDok;
    const el = document.documentElement as WebkitEl;
    const fehler = (): void => notify('Vollbild nicht möglich');
    try {
      if (vollbildElement()) {
        if (typeof document.exitFullscreen === 'function') document.exitFullscreen().catch(fehler);
        else dok.webkitExitFullscreen?.();
      } else if (typeof el.requestFullscreen === 'function') el.requestFullscreen().catch(fehler);
      else el.webkitRequestFullscreen?.();
    } catch {
      fehler();
    }
  };
  let empfangAktiv = (): boolean => false;
  const hilfeKontext = (): HilfeKontext => ({
    ansicht: ansichtVon({ begehen: begehen?.aktiv ?? false, panorama: panoRef?.active ?? false }),
    empfang: empfangAktiv(),
    orbitLinks: einstellungen.orbitLinks,
    vollbild: vollbildMoeglich,
    eigenesFenster: eigenesFenster(),
  });
  const tastenhilfeDialog = createBedienung({ belegung: () => belegung, kuerzel, kontext: hilfeKontext });
  const einstellungenDialog = createEinstellungen({
    belegung: () => belegung,
    setzeBelegung: (b: Belegung): void => {
      belegung = b;
      safeSet(BELEGUNG_SCHLUESSEL, speichereBelegung(b));
      aktualisiereKuerzel();
    },
    einstellungen: () => einstellungen,
    setzeEinstellungen: (e: BedienEinstellungen): void => {
      einstellungen = e;
      safeSet(EINSTELLUNGEN_SCHLUESSEL, speichereEinstellungen(e));
      zp?.groesse(e.fadenkreuzPx);
      v.controls.mouseButtons = mausTasten(e.orbitLinks);
      v.controls.touches = fingerGesten(e.orbitLinks);
      hinweise?.aktualisiere();
    },
    hinweiseZuruecksetzen: () => hinweise?.zuruecksetzen(),
    leistung: {
      an: () => leistungAn,
      setze: (an: boolean): void => {
        setzeDebug(an);
        leistungAbbau();
        leistungAn = an;
        leistungAbbau = starteDebug(document.body, an);
      },
    },
  });
  // Modale Dialoge im Begehen: vorher Pointer-Lock frei und gehaltene Tasten los; solange der Dialog offen
  // ist, steht das Begehen (begehen.ts). begehen per Getter: es wird erst weiter unten angehängt.
  const tastenhilfe = { oeffne: (): void => { begehen?.zeigerFreigeben(); tastenhilfeDialog.oeffne(); hinweise?.zaehle('tastenhilfe'); } };
  const einstellungenOeffnen = (): void => {
    begehen?.zeigerFreigeben();
    einstellungenDialog.oeffne();
  };
  const toolbar = createToolbar(
    document.getElementById('toolbar')!,
    () => tool.finish(),
    () => tool.cancelDrawing(),
    () => panels?.toggle('messungen'),
    () => tastenhilfe.oeffne(),
    einstellungenOeffnen,
    () => {
      if (!begehen) return;
      if (begehen.aktiv) begehen.beenden(austrittsTreffer());
      else begehen.starten();
    },
    () => befundSt?.teile(),
    // iPhone: keine Vollbild-API — der Knopf erklärt den Weg über den Home-Bildschirm (dort ohne Adresszeile).
    vollbildMoeglich ? wechsleVollbild
      : eigenesFenster() ? undefined
        : () => notify('Vollbild auf dem iPhone: im Browser „Teilen → Zum Home-Bildschirm“, von dort startet siteBits ohne Adresszeile'),
  );
  const zeigeVollbild = (): void => toolbar.setVollbild(vollbildElement() !== null);
  document.addEventListener('fullscreenchange', zeigeVollbild);
  document.addEventListener('webkitfullscreenchange', zeigeVollbild);
  // Schalter „Messwerte zeigen“ der Empfängeransicht (sie entsteht erst weiter unten — per Variable).
  let messwerteGeaendert = (): void => undefined;
  const tool = attachMeasureTool({
    v,
    store,
    sourceLayers: p.layers,
    vorschlaegeSichtbar: () => vorschlaegeSichtbar,
    onChange: () => {
      scheduleHashUpdate();
      list?.refresh();
      messwerteGeaendert();
      plattform?.messungenGeaendert();
    },
    source: picker,
    inPanorama: () => panoRef?.active ?? false,
    setLookLocked: (on) => panoRef?.setLookLocked(on),
    onDrawingChange: (d) => toolbar.setDrawing(d),
    poiMode: () => toolbar.tool() === 'poi',
    snapEdges: () => toolbar.snapEdges(),
    hiddenCloudMessage,
    unterPointerLock,
    frage: frageStill,
    rueckfrageOffen: () => rueckfrage.offen(),
    pose: kameraPose,
  });
  koZeile = attachKoordinatenzeile({
    canvas: v.instance.domElement,
    frame: p.frame,
    source: picker,
    inPanorama: () => panoRef?.active ?? false,
    gesperrt: () => unterPointerLock() || tool.isDragging(),
    melde: (t) => ansage.melde(t),
    onToggle: scheduleHashUpdate,
    anfangs: initial.ko ?? false,
  });
  toolbar.onSnapChange((on) => notify(on ? 'Kanten einrasten an' : 'Kanten einrasten aus'));
  list = attachMeasureList({
    root: document.getElementById('measure-panel')!,
    pruefen: plattform?.messungPruefen,
    store,
    tool,
    manifest: p,
    sourceLayers: p.layers,
    vorschlaegeSichtbar: () => vorschlaegeSichtbar,
    onChange: () => {
      scheduleHashUpdate();
      list?.refresh();
      messwerteGeaendert();
      plattform?.messungenGeaendert();
    },
    onToggle: (open) => toolbar.setListOpen(open),
    notify,
    gemeinsam: () => plattform?.messungenGemeinsam() ?? false,
  });
  toolbar.onChange((t) => {
    meshHandles.forEach((h) => h.setMeasuring(t !== 'navigate' && !(panoRef?.active ?? false)));
    tool.setKind(KIND_OF[t]);
    currentPois?.setCreateMode(t === 'poi');
  });
  if (dropped > 0) {
    notify(`${dropped} Messung(en) aus dem Link verworfen (ungültige Geometrie)`);
    // Link sofort bereinigen: sonst trüge er die verworfenen Messungen bis zur nächsten Änderung.
    scheduleHashUpdate();
  }
  // Panel-System: Inhalte werden in Rahmen umgehängt; hidden bleibt die Wahrheit (L3).
  const guiHost = document.createElement('div');
  // Ebenen-Panel: leerer Container vorab registriert (feste Panel-Reihenfolge/Layout-Persistenz),
  // attachLayersPanel füllt ihn erst nach attachStationLayer/createLayerRegistry unten (Registry braucht
  // stationLayer/stations, die erst dann existieren).
  const layersHost = document.createElement('div');
  // Foto-Fenster (orientierte Fotos): nur mit Fotos-Ebene; gefüllt nach dem Laden von images.json.
  const fotoHost = document.createElement('div');
  // Fotogalerie (Gruppen mit Vorschaubildern) — ersetzt die Foto-Zeilen im Ebenenmenü.
  const galerieHost = document.createElement('div');
  const sonneHost = document.createElement('div');
  const kpHost = document.createElement('div');
  const panelSt = (dock: 'left' | 'right', group: number, open: boolean) => ({
    dock, group, open, collapsed: false, float: { x: 240, y: 120, w: 340, h: 320 },
  });
  panels = createPanelHost({
    root: document.getElementById('panels')!,
    projectId: p.projectId,
    storage: browserStorage(),
    panels: [
      { id: 'ansicht', title: 'Ansicht', key: kuerzel(PANEL_AKTION.ansicht), defaults: panelSt('left', 0, true), content: guiHost },
      { id: 'ebenen', title: 'Ebenen', key: kuerzel(PANEL_AKTION.ebenen), defaults: panelSt('left', 1, true), content: layersHost },
      { id: 'messungen', title: 'Messungen', key: kuerzel('messliste'), defaults: panelSt('right', 0, false), content: document.getElementById('measure-panel')! },
      { id: 'pois', title: 'POIs', key: kuerzel(PANEL_AKTION.pois), defaults: panelSt('right', 0, false), content: document.getElementById('poi-panel')! },
      { id: 'karte', title: 'Minikarte', key: kuerzel(PANEL_AKTION.karte), defaults: panelSt('right', 1, true), content: document.getElementById('minimap')! },
      ...(p.images ? [
        { id: 'fotos', title: 'Fotos', defaults: panelSt('left', 2, false), content: galerieHost },
        { id: 'foto', title: 'Foto', defaults: panelSt('right', 2, false), content: fotoHost },
      ] : []),
      ...(sonne ? [{ id: 'sonne', title: 'Sonne', defaults: panelSt('right', 3, false), content: sonneHost }] : []),
      { id: 'kontrollpunkte', title: 'Kontrollpunkte', defaults: panelSt('right', 4, false), content: kpHost },
    ],
  });
  if (sonne) attachSonnePanel(sonneHost, sonne);
  // Kontrollpunkte am Modell picken (J3b): eigener Pickmodus für genau einen Klick. Panorama und Begehen entstehen
  // erst weiter unten — per Getter.
  const kontrollpunkte = attachKontrollpunktePanel({
    root: kpHost, frame: p.frame, projectId: p.projectId, sourceLayers: p.layers, version: viewerVersion(),
    leinwand: v.instance.domElement, picker, inPanorama: () => panoRef?.active ?? false, snapEdges: () => toolbar.snapEdges(),
    fliegeZu: (ziel) => {
      begehen?.beenden(null);
      if (panoRef?.active) panoRef.leave();
      v.frameAt(ziel, 5);
    },
    vorPicken: () => { if (toolbar.tool() !== 'navigate') toolbar.setTool('navigate'); },
    hiddenCloudMessage, notify, unterPointerLock, frage: frageStill, rueckfrageOffen: () => rueckfrage.offen(), pose: kameraPose,
    entwurf: kpEntwurfSpeicher(() => window.localStorage),
  });
  // Ein anderes Werkzeug nimmt die Klicks: Pickmodus beenden.
  toolbar.onChange((t) => { if (t !== 'navigate') kontrollpunkte.abbrechen(); });
  const panelMenu = attachPanelMenu(document.getElementById('panel-menu')!, panels);
  // Gemeinsam für Kürzel (run()), lil-gui-Knopf (addPoiControls) und panels.onChange unten:
  // bring-forward eines verdeckten Reiters feuert kein onChange (activateTab), deshalb hier zusätzlich
  // direkt nach panels.toggle() prüfen statt sich allein auf onChange zu verlassen.
  const focusPoiSearch = (): void => {
    document.querySelector<HTMLInputElement>('#poi-panel input[type=search]')?.focus();
  };
  const togglePois = (): void => {
    panels?.toggle('pois');
    if (panels?.isOpen('pois')) focusPoiSearch();
  };
  panels.onChange((id, open) => {
    if (id === 'messungen') {
      toolbar.setListOpen(open);
      if (open) list?.refresh();
    }
    // pois.togglePanel() setzt #poi-panel.hidden direkt (nicht über panels.open()) — der Fokus, den es
    // selbst synchron zu setzen versucht, greift ins Leere, solange der Rahmen noch im Stash steckt
    // (L3-Beobachter läuft erst als Microtask). Hier, nach dem render() in applyOpenChange, sitzt der
    // Rahmen schon im Baum — Fokus greift zuverlässig (Kürzelpfad und lil-gui-Knopf decken das
    // Nach-vorn-Holen ohne onChange separat über togglePois() ab).
    if (id === 'pois' && open) focusPoiSearch();
  });
  toolbar.setListOpen(panels.isOpen('messungen'));

  const ui = buildUi(v, cloud, clip, guiHost, kuerzel);
  const gui = ui.gui;
  if (cloud) addKlassenControls(gui, v, cloud, scheduleHashUpdate);
  // Fenster-Effekt (F32): nur Darstellung. Solange er läuft, wird nicht gemessen — Bild und Pick-Strahl
  // würden um die Kopfposition auseinanderlaufen.
  let fensterSchalter: ((an: boolean) => void) | null = null;
  const fenster = attachFensterEffekt({
    instance: v.instance,
    drehpunkt: () => (panoRef?.active ? null : v.controls.target),
    wasmBasis: `${import.meta.env.BASE_URL}wasm/`,
    melde: (t) => ansage.melde(t),
    onAnAus: (an) => {
      fensterSchalter?.(an);
      if (an && toolbar.tool() !== 'navigate') toolbar.setTool('navigate');
    },
  });
  fensterSchalter = addFensterControls(gui, fenster);
  toolbar.onChange((t) => {
    if (t !== 'navigate' && fenster.an()) {
      toolbar.setTool('navigate');
      ansage.melde('Zum Messen erst den Fenster-Effekt ausschalten');
    }
  });
  // Beschriftungen aus der Belegung (Werkzeugleiste, lil-gui); nach einer Änderung der Belegung erneut aufrufen.
  const panelAktion: Record<string, AktionId> = {
    ansicht: PANEL_AKTION.ansicht, ebenen: PANEL_AKTION.ebenen, messungen: 'messliste', pois: PANEL_AKTION.pois, karte: PANEL_AKTION.karte,
  };
  const aktualisiereKuerzel = (): void => {
    toolbar.setKuerzel(kuerzel);
    ui.aktualisiereKuerzel();
    panelMenu.setKuerzel((id) => (panelAktion[id] ? kuerzel(panelAktion[id]!) : ''));
    leinwand.aktualisiere();
    hinweise?.aktualisiere();
  };
  aktualisiereKuerzel();
  v.controls.addEventListener('change', scheduleHashUpdate);

  // Standpunkte. Ein Ladefehler lässt die Punktwolke bedienbar: Hinweis statt Abbruch.
  let stationSet: StationSet = { schemaVersion: 2, stations: [] };
  try {
    stationSet = await loadStations(base, p);
    // Server-Tor (F20): ohne Freigabe für diese Rolle keine Panoramabilder anfragen.
    if (plattform && !plattform.panoramenSichtbar) stationSet = ohnePanoramen(stationSet);
  } catch (e) {
    msg.dataset.base = [msg.dataset.base, e instanceof Error ? e.message : String(e)].filter(Boolean).join(' · ');
    say(msg.dataset.base);
  }
  const stations = stationSet.stations;
  stationById = new Map(stations.map((s) => [s.id, s]));
  const layer = attachStationLayer(v, stations);

  // Orientierte Fotos. 404 = für diese Rolle nicht freigegeben (Server-Tor): Ebene ausblenden, kein Fehler.
  let imageSet: ImageSet | null = null;
  if (p.images) {
    try {
      imageSet = await loadImages(base, p.images);
    } catch (e) {
      msg.dataset.base = [msg.dataset.base, e instanceof Error ? e.message : String(e)].filter(Boolean).join(' · ');
      say(msg.dataset.base);
    }
    if (!imageSet) {
      const fotosId = p.images.id;
      p.layers = p.layers.filter((l) => l.id !== fotosId);
      p.images = null;
      fotoHost.textContent = 'Fotos sind für dieses Konto nicht freigegeben.';
      galerieHost.textContent = 'Fotos sind für dieses Konto nicht freigegeben.';
    }
  }
  const fotoWerte = loadFotoLayout(browserStorage(), p.projectId);
  const fotoSpeichern = (): void => saveFotoLayout(browserStorage(), p.projectId, fotoWerte);
  const imgLayer = imageSet ? attachImageLayer(v.instance, imageSet, { tiefeM: fotoWerte.tiefe }) : null;

  // Ebenen-Register: erst jetzt, da es den Stationslayer/die Stationsliste braucht
  // (Standpunkte-Ebene: Sichtbarkeit über stationLayer.setVisible, „Einrahmen" über die Stationspositionen).
  reg = createLayerRegistry({
    project: p,
    viewer: v,
    stationLayer: layer,
    initial: initialVisibility(p.layers, initial.ly ?? p.initialView.layers),
    stations,
    images: imgLayer,
    meshes: [...meshHandles, ...tilesHandles, ...splatHandles, ...[...failedMeshes].map((id) => ({ id, disabled: true, setVisible() {}, setOpacity() {} }))],
  });
  attachLayersPanel(layersHost, reg, plattform?.rechte.alleAendern ? { an: vorschlaegeSichtbar, aendern: an => { vorschlaegeSichtbar = an; currentPois?.refresh(); tool.sync(); list?.refresh(); scheduleHashUpdate(); } } : undefined);
  // Unter der Ebenenliste statt in der oberen Leiste (Andre 07.10.: nahm dort zu viel Platz ein).
  if (p.meshes.length || p.tiles.length) attachPickPreference(layersHost, pickPreference, (value) => { pickPreference = value; });
  if (!p.initialViewDeclared) v.frameAll(visibleBounds(p, reg.isVisible));
  reg.onChange(scheduleHashUpdate);

  let activeStation: string | null = null;
  const systemReduziert = window.matchMedia('(prefers-reduced-motion: reduce)');
  const pano = attachPanorama(v, base, stations, layer, notify, { reduziert: () => bewegungReduziert(einstellungen, systemReduziert.matches),
    onOverlay: (alpha) => { meshHandles.forEach((h) => h.setPanoramaOverlay(alpha)); tilesHandles.forEach((h) => h.setPanoramaOverlay(alpha)); } });
  panoRef = pano;
  currentPano = pano;
  const drehpunkt = attachDrehpunktMarker(v, { reduziert: () => bewegungReduziert(einstellungen, systemReduziert.matches), melde: notify });

  // Zeitpunkt des letzten Sprungs per Klick auf eine Marke: Der erste Klick eines Doppelklicks springt
  // schon allein, wenn er eine Marke trifft — der Doppelklick darf danach nicht gleich weiterspringen.
  let letzterSprung = 0;
  const goToStation = (id: string): void => {
    const s = stations.find((x) => x.id === id);
    if (!s) return;
    // Erst das Begehen beenden: das Panorama sichert beim Betreten das Sichtfeld — das des Begehens wäre
    // danach dauerhaft eingestellt; ohne Panorama zöge die Begehen-Schleife die Kamera sofort zurück.
    begehen?.beenden(null);
    // Blick durch die Kamera zuerst verlassen: pano.enter sichert Sichtfeld und Steuerung — sonst die
    // des Fotos, und das spätere durch.leave() gäbe die Steuerung mitten im Panorama frei.
    if (durch.active) durch.leave();
    if (pano.enter(id)) {
      activeStation = id;
      // Die Schnittbox beschneidet weiter die Messpunkte, nicht das Panorama.
      if (clip.getState().enabled) notify('Schnittbox aktiv – Messpunkte nur innerhalb der Box');
      return;
    }
    // Kein Panorama an diesem Standpunkt (graue Station, z. B. gemischte Pakete): ein laufendes Panorama
    // verlassen (I4) — sonst bliebe es mit gesperrter Steuerung stehen, während Minikarte, Marker und
    // Kamera schon zur grauen Station wechseln. leave() setzt über onChange activeStation zurück, deshalb
    // erst danach setzen.
    if (pano.active) pano.leave();
    activeStation = id;
    layer.setActive(id);
    minimap.setActive(id);
    v.frameAt(markerPosition(s), 8);
  };
  const minimap = attachMinimap(document.getElementById('minimap')!, base, p, bounds, stations, goToStation);
  let panoWasActive = false;
  const scanHint = document.getElementById('scan-hint')!;
  // Dauerhinweis lässt sich wegtippen; er kommt erst wieder, wenn sich die Art der Filterung ändert (auf dem Handy
  // lag er über dem Panel, Rückmeldung 17.10.).
  let scanHintWeg = '';
  // Mobil: Höhe des unteren Panel-Blatts als --sv-panel-unten (Hinweise liegen darüber statt darauf, 17.10.).
  const panelHoehe = (): void => {
    const dock = document.querySelector<HTMLElement>("#panels[data-mobile='true']:not([data-quer='true']) .sv-dock[data-dock='bottom']");
    // Oberkante des höchsten Rahmens bis zum unteren Fensterrand.
    // Ausgeblendete Teile (Blatt tritt hinter das Befundformular zurück) zählen nicht.
    // Ohne Rahmen zählt das leere Dock nicht (es trägt seine Höhe als Inline-Stil).
    const sichtbar = dock ? [...dock.querySelectorAll<HTMLElement>('.sv-frame')].map((e) => e.getBoundingClientRect()).filter((r) => r.height > 0) : [];
    const oben = sichtbar.length ? Math.min(...sichtbar.map((r) => r.top)) : innerHeight;
    document.body.style.setProperty('--sv-panel-unten', `${Math.max(0, innerHeight - oben)}px`);
  };
  new ResizeObserver(panelHoehe).observe(document.getElementById('panels')!);
  new MutationObserver(panelHoehe).observe(document.getElementById('panels')!, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-mobile', 'hidden', 'style'] });
  window.addEventListener('resize', panelHoehe);
  panelHoehe();
  // Desktop/Tablet: Meldungszeile unter der Werkzeugleiste, Scan-Hinweis unten — je in die breiteste freie Lücke
  // zwischen Panels und Leisten (neben der Leiste war auf schmalen Fenstern kein Platz, die Meldung lief unter sie).
  // Auf dem Handy und in der Empfängeransicht gelten die festen Zonen aus dem CSS: Inline-Werte weg.
  const msgEl = document.getElementById('msg')!;
  const toolbarEl = document.getElementById('toolbar')!;
  const freieLage = (): void => {
    const css = document.body.classList.contains('sv-handy') || document.body.classList.contains('sv-empfang');
    for (const e of [msgEl, scanHint]) if (css) for (const k of ['top', 'left', 'bottom', 'maxWidth', 'transform'] as const) e.style[k] = '';
    if (css) return;
    const sichtbar = (e: Element): DOMRect | null => (e instanceof HTMLElement && !e.hidden && e.checkVisibility() ? e.getBoundingClientRect() : null);
    const hindernisse = [...document.querySelectorAll('.sv-frame, #panel-menu, .sv-zurueck, .sv-pano-aus, #poi-form, .sv-teilen, .sv-entwurf, .sv-koordinaten')]
      .map(sichtbar).filter((r): r is DOMRect => r !== null);
    const breite = document.documentElement.clientWidth;
    const leiste = toolbarEl.getBoundingClientRect();
    const oben = leiste.bottom + 8;
    const m = freieSpanne(breite, oben, oben + Math.max(msgEl.offsetHeight, 32), [...hindernisse, leiste]);
    Object.assign(msgEl.style, { top: `${oben}px`, left: `${m.left}px`, maxWidth: `${Math.max(0, m.right - m.left)}px` });
    // Zweimal: schmaler umgebrochen wird der Hinweis höher und kann in ein weiteres Hindernis ragen.
    for (let i = 0; i < 2; i++) {
      const s = freieSpanne(breite, scanHint.getBoundingClientRect().top - 8, innerHeight, hindernisse);
      Object.assign(scanHint.style, { left: `${(s.left + s.right) / 2}px`, maxWidth: `${Math.max(0, s.right - s.left)}px` });
    }
    // Die Tastenhinweise weichen dem Scan-Hinweis (hinweisAnzeige.ts) — nach dem Verschieben neu legen.
    window.dispatchEvent(new Event('sv-hinweise-lage'));
  };
  new ResizeObserver(freieLage).observe(toolbarEl);
  new ResizeObserver(freieLage).observe(document.getElementById('panels')!);
  new MutationObserver(freieLage).observe(document.getElementById('panels')!, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-mobile', 'hidden', 'style'] });
  new MutationObserver(freieLage).observe(msgEl, { childList: true, characterData: true, subtree: true });
  new MutationObserver(freieLage).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  // Befundformular, Befundlink, Entwurf, Panorama-Ausgang (teils später angelegt): ein- und ausgeblendet per hidden.
  new MutationObserver(freieLage).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('resize', freieLage);
  pano.onChange(() => requestAnimationFrame(freieLage));
  freieLage();
  scanHint.title = 'Tippen zum Ausblenden';
  scanHint.addEventListener('click', () => { scanHintWeg = scanHint.dataset.mode ?? ''; scanHint.hidden = true; });
  // Immer sichtbarer Ausgang aus dem Panorama: auf Touch-Geräten gibt es kein Esc, und der Eintrag im Panel
  // „Ansicht“ lag auf dem iPhone unter Hinweisen und Panels (Rückmeldung 15.10.).
  const panoAus = document.createElement('button');
  panoAus.type = 'button';
  panoAus.className = 'sv-pano-aus';
  panoAus.textContent = '× Panorama verlassen';
  panoAus.hidden = true;
  panoAus.addEventListener('click', () => pano.leave());
  document.body.append(panoAus);
  pano.onChange(() => { panoAus.hidden = !pano.active; });
  pano.onChange(() => {
    // Eine offene Rückfrage gehört zur Ansicht vor dem Panorama: abbrechen (die Steuerung bleibt beim Panorama).
    if (pano.active) rueckfrage.abbreche();
    // Verlassen beendet auch die Standpunktauswahl — Minikarte, 3D-Marker und stations.active() einig.
    if (panoWasActive && !pano.active) activeStation = null;
    panoWasActive = pano.active;
    minimap.setActive(pano.state?.station ?? activeStation, pano.state?.yaw ?? null);
    // Dauerhinweis, solange im Panorama nicht auf den eigenen Scan gefiltert werden kann.
    const f = currentScanFilter();
    const hint = f ? scanFilterHint(f.mode) : null;
    if (hint) scanHint.textContent = hint;
    scanHint.hidden = hint === null || scanHintWeg === (f?.mode ?? '');
    scanHint.dataset.mode = f?.mode ?? '';
    scheduleHashUpdate();
  });
  addPanoramaControls(gui, pano);

  // „Blick durch die Kamera“ und Foto-Fenster. Panorama und Begehen schließen sich gegenseitig aus.
  const durch = attachThroughCamera(v, base, {
    reduziert: () => bewegungReduziert(einstellungen, systemReduziert.matches),
    notify,
    vorher: () => {
      begehen?.beenden(null);
      if (pano.active) pano.leave();
    },
  });
  const fotoTeilung = imageSet && panels ? attachFotoTeilung({ panels, view: document.getElementById('view')!, werte: fotoWerte, speichern: fotoSpeichern,
    groesseAendern: () => synchronisiereFotoLeinwand(v),
  }) : null;
  const fotoSynchron = imageSet ? attachFotoSynchron(v, picker) : null;
  const fotoPanel = imageSet ? attachPhotoPanel(fotoHost, { pkgBase: base, set: imageSet, durch,
    schweben: () => fotoTeilung?.umschalten() ?? false, strahl: (b, x, y) => fotoSynchron?.zeige(b, x, y), strahlAus: () => fotoSynchron?.aus(), auswahlAus: () => imgLayer?.setTreffer(null) }) : null;
  fotoPanel?.onSelect((id) => imgLayer?.setActive(id));
  const zeigeFoto = (id: string): void => {
    imgLayer?.setActive(id);
    fotoTeilung?.oeffnen();
    fotoPanel?.show(id);
  };
  const fotoWerkzeug = imageSet ? attachFotoWerkzeug({ v, picker, set: imageSet,
    tiefe: fotoTiefenquelle(v, [...meshHandles, ...tilesHandles], cloudVisible).tiefeAsync,
    vorbereiten: () => { begehen?.beenden(null); if (durch.active) durch.leave(); if (pano.active) pano.leave(); toolbar.setTool('navigate'); fotoSynchron?.aus(); },
    messungen: () => store.list(), box: () => clip.getState(), melde: notify,
    ergebnis: (treffer, punkte, rand) => {
      imgLayer?.setTreffer(new Set(treffer.map((t) => t.bild.id)));
      fotoPanel?.auswahl(treffer, punkte, rand);
      if (treffer[0]) zeigeFoto(treffer[0].bild.id);
      else { fotoTeilung?.oeffnen(); fotoPanel?.show(null); }
    },
  }) : null;
  toolbar.onChange(() => fotoWerkzeug?.abbrechen());
  panels?.onChange((id, offen) => { if (id === 'foto' && !offen) fotoSynchron?.aus(); });
  if (imageSet && p.images) {
    attachFotoGalerie(galerieHost, { pkgBase: base, set: imageSet, reg, ebene: p.images.id, zeige: zeigeFoto,
      tiefe: fotoWerte.tiefe, rahmen: (meter) => { fotoWerte.tiefe = meter; imgLayer?.setTiefe(meter); fotoSpeichern(); },
      suchen: () => fotoWerkzeug?.starten() });
  }

  // Begehen: nach dem Panorama (sperrt sich dort), vor POIs und Verteiler.
  const vignetteEl = document.createElement('div');
  vignetteEl.className = 'sv-vignette';
  vignetteEl.setAttribute('aria-hidden', 'true');
  document.getElementById('view')!.after(vignetteEl);
  const walk = attachBegehen({
    v,
    pano,
    gesperrt: () => (durch.active ? 'Begehen ist beim Blick durch die Kamera nicht verfügbar' : null),
    einstellungen: () => einstellungen,
    systemReduziert: () => systemReduziert.matches,
    melde: say,
    kuerzel,
    vignetteEl,
    // Setzen am Fadenkreuz (zielpunktSteuerung.ts); zp wird gleich darunter synchron angelegt.
    setzen: (s) => zp!.setzen(s),
    rueckfrage,
    formularOffen: () => currentPois?.formularOffen() ?? false,
    formularFokussieren: () => currentPois?.fokussiereFormular(),
    endeZusatz: () => punktwolkeSatz(einstellungen.orbitLinks),
  });
  begehen = walk;
  const zeigeBegehen = (): void => toolbar.setBegehen(walk.aktiv, !pano.active && !durch.active);
  durch.onChange(() => zeigeBegehen());
  walk.onChange(zeigeBegehen);
  pano.onChange(zeigeBegehen);
  zeigeBegehen();
  // Ansichtswechsel (Orbit ↔ Panorama ↔ Begehen) mit einem kurzen Satz in der Live-Region ansagen — erst nach dem
  // Start: die Pose aus dem Link ist kein Wechsel. Das Begehen sagt Start und Ende selbst an (endeZusatz).
  let ansichtJetzt = ansichtVon({ begehen: walk.aktiv, panorama: pano.active });
  let ansichtAnsagen = false;
  const ansichtGewechselt = (): void => {
    const neu = ansichtVon({ begehen: walk.aktiv, panorama: pano.active });
    const satz = ansichtWechselText(ansichtJetzt, neu, einstellungen.orbitLinks);
    ansichtJetzt = neu;
    if (satz && ansichtAnsagen) ansage.melde(satz);
  };
  walk.onChange(ansichtGewechselt);
  pano.onChange(ansichtGewechselt);

  // Zielpunkt: Setzen am Fadenkreuz, Fadenkreuz mit Vorschau und Live-Wert, Ansage, Gamepad-Abbildung.
  const zielpunkt = attachZielpunktSteuerung({
    v,
    pano,
    picker,
    tool,
    toolbar,
    begehen: walk,
    rueckfrage,
    frage: frageStill,
    pose: kameraPose,
    pois: () => currentPois,
    einstellungen: () => einstellungen,
    kuerzel,
    say,
    ansage,
    cloudVisible,
    hiddenCloudMessage,
    hinweis: { gesetzt: (befund) => { hinweise?.zaehle(befund ? 'befund' : 'setzen'); padSt?.vibriere(); } },
  });
  zp = zielpunkt;

  // POIs — vor dem Deep-Link-Sprung, nach Minikarte/onChange.
  const pois = await attachPois({
    v,
    pkgBase: base,
    m: p,
    store: new PoiStore(p.projectId),
    picker,
    pano,
    stations,
    notify,
    hiddenCloudMessage,
    persistenz: plattform?.poiPersistenz,
    vorschlaegeSichtbar: () => vorschlaegeSichtbar,
    kategorieFest: plattform?.rechte.nurRueckmeldung ? RUECKMELDUNG : undefined,
    kategorien: plattform?.kategorien,
    unterPointerLock,
    vorSprung: () => walk.beenden(null),
    frage: frageStill,
    rueckfrageOffen: () => rueckfrage.offen(),
    pose: kameraPose,
    begehenAktiv: () => walk.aktiv,
    kennzeichen: plattform?.faeden ? (lokal) => plattform.faeden?.staende.kurz(plattform.poiPersistenz.meta?.(lokal)?.serverId) ?? null : undefined,
  });
  currentPois = pois;
  // Befund-Fäden: Stände (Kennzeichen, Filter) laden; jede Änderung zeichnet Marker und Befundliste neu.
  if (plattform?.faeden) {
    plattform.faeden.staende.onAenderung(() => pois.refresh());
    void plattform.faeden.staende.laden().then((fehler) => {
      if (fehler !== null) notify(`Stand der Befunde nicht geladen (${fehler}).`);
    });
  }
  // Empfängeransicht (Befundlink) und Sprunglinks; wendePoseAn steht weiter unten (erst beim Klick gebraucht).
  const empfang = attachEmpfang({
    pois, persistenz: plattform?.poiPersistenz, pano, v, leinwand, toolbar, begehen: walk, einstellungen: () => einstellungen, ansicht,
    wendePoseAn: (s) => wendePoseAn(s), melde: (t) => ansage.melde(t, { vorrang: true }), onAenderung: scheduleHashUpdate, rechte: plattform?.rechte ?? null, hinweis: notify,
    speicher: { lies: safeGet, schreib: safeSet }, messungen: () => store.list().length,
    zoomGrenze: () => zoomStopp?.grenze() ?? 0,
    vorschlaegeSichtbar: () => vorschlaegeSichtbar,
    faeden: plattform?.faeden ? { ...plattform.faeden, nutzerId: plattform.nutzerId } : null,
  });
  messwerteGeaendert = () => empfang.messwerteGeaendert();
  const sprung = attachSprunglinks({ leinwand: v.instance.domElement, werkzeuge: () => toolbar.fokussiere(), befundliste: () => (empfang.aktiv ? empfang.liste : null), zurListe: () => empfang.zeigeListe() });
  empfang.onChange(() => { hinweise?.aktualisiere(); sprung.aktualisiere(); });
  empfangAktiv = () => empfang.aktiv;
  // Zähler je Konto (Plattform mit Konto-ID), sonst je Browser: er enthält nur Hinweis-Zähler, keine Inhalte.
  hinweise = attachHinweisAnzeige({
    schluessel: hinweisSchluessel(modus.art === 'plattform' ? modus.nutzerId : null), lies: safeGet, schreib: safeSet,
    begehen: walk, pano, toolbar, pois, panels, panoramaZiel: () => zielpunkt.panoramaZiel(), empfang: () => empfang.aktiv, einstellungen: () => einstellungen,
    systemReduziert: () => systemReduziert.matches, kuerzel,
  });
  // Zeile zu Maus/Finger: eine Ausführung = eine Orbit-Geste, die die Ansicht bewegt hat (Ziehen, Rad, Zwei Finger;
  // MapControls start → change → end), oder ein gesetzter Drehpunkt (Doppelklick). Gezählt wird die Zeile, die
  // gerade angezeigt wird (hinweisAnzeige.ts wählt sie per Medienabfrage) — sonst verschwände sie auf
  // Hybrid-Geräten (Touch-Laptop, iPad mit Maus) nie.
  const nurTouch = window.matchMedia('(hover: none) and (pointer: coarse)');
  const zeigerArt = (): 'maus' | 'finger' => (nurTouch.matches ? 'finger' : 'maus');
  let orbitBewegt = false;
  v.controls.addEventListener('start', () => { orbitBewegt = false; });
  v.controls.addEventListener('change', () => { orbitBewegt = true; });
  v.controls.addEventListener('end', () => { if (orbitBewegt) hinweise?.zaehle(zeigerArt()); });
  v.onDrehpunktGesetzt(() => hinweise?.zaehle(zeigerArt()));
  // Auswahl → Seitentitel, nach dem Speichern Auswahl und Fokus auf die 3D-Ansicht (befundSteuerung.ts).
  const befundSteuerung = attachBefundSteuerung({
    pois, persistenz: plattform?.poiPersistenz, leinwand: v.instance.domElement, manifest: p, melde: say, hinweis: notify,
    onAenderung: scheduleHashUpdate, ansicht, ort: location,
    teilenKnopf: { setTeilen: (an) => toolbar.setTeilen(an), fokussiere: () => toolbar.fokussiereTeilen() },
    begehen: () => begehen,
    geteilt: () => hinweise?.zaehle('teilen'),
  });
  befundSt = befundSteuerung;
  befund.imLink = () => (empfang.aktiv ? befundSteuerung.imLink() : {});
  plattform?.verdrahte({
    pois,
    pano,
    messungenNeuZeichnen: () => {
      tool.sync();
      messwerteGeaendert();
      list?.refresh();
      scheduleHashUpdate();
    },
  });
  // Wurde die Leiste schon vor dem Ende von attachPois gewechselt, gilt ihr Stand.
  pois.setCreateMode(toolbar.tool() === 'poi');
  // POI gespeichert oder Formular abgebrochen → Leiste zurück auf Navigieren.
  pois.onModeChange((on) => {
    if (!on && toolbar.tool() === 'poi') toolbar.setTool('navigate');
  });
  // Derselbe Weg wie Kürzel/Werkzeugleiste (panels.toggle + Fokus), nicht mehr direkt togglePanel().
  addPoiControls(gui, togglePois);

  // Deep-Link: Standpunkt vor Kamera. Erst jetzt anwenden: pano.enter()
  // löst emit() synchron aus — vor der Minikarten-/onChange-Verdrahtung oben würde der Aufruf ins
  // Leere laufen und Minikarten-Pin sowie stations.active() blieben bis zur nächsten Pano-Änderung leer.
  // Pose anwenden (Link, fortgesetzter Entwurf); true = angewandt.
  const wendePoseAn = (s: ViewState): boolean => {
    if (durch.active) durch.leave();
    // Fehlende Winkel sind undefined → enter() nimmt den Standardblick.
    if (s.st && pano.enter(s.st, { yaw: s.yaw, pitch: s.pitch, fov: s.fov })) {
      activeStation = s.st;
      return true;
    }
    if (!s.cam || !s.tgt) return false;
    if (pano.active) pano.leave();
    v.instance.view.camera.position.set(...s.cam);
    v.controls.target.set(...s.tgt);
    v.controls.update();
    v.instance.notifyChange(v.instance.view.camera);
    return true;
  };
  const poseAngewandt = wendePoseAn(initial);
  // Lokaler Befundentwurf (je Konto und Projekt; Plattform ohne Konto-ID: keiner) — Angebot vor dem Befundlink.
  const bestaetigt = plattform?.rechte.poisAnlegen ? plattform.poiPersistenz.bestaetigt : undefined;
  void attachEntwurfSteuerung({
    v, pois, leinwand: v.instance.domElement, melde: say, hinweis: notify, persistenz: plattform?.poiPersistenz,
    nach: document.getElementById('view'), nachBefundlink: befundImHash.b !== undefined,
    schluessel: entwurfSchluesselFuer(modus, p.projectId),
    serverBestaetigt: bestaetigt ? (id) => bestaetigt(id) : null,
    ansicht: () => encodeHash(poseAus(ansicht()), p.originWorld).slice(1),
    wendeAnsichtAn: (a) => {
      walk.beenden(null);
      wendePoseAn(decodeHash(`#${a}`, p.originWorld));
    },
  }).biete();
  // Befundlink: Befund auswählen (ohne angewandte Pose springt die Ansicht zu ihm) und die Empfängeransicht öffnen.
  if (befundSteuerung.oeffneAusLink(befundImHash, { poseAngewandt }) === 'gefunden') empfang.starte(pois.ausgewaehlt(), befundImHash.e);
  ansichtAnsagen = true;

  // Klick auf einen Standpunkt-Marker — nur, wenn kein Werkzeug die Klicks braucht und die Maus
  // nicht gezogen wurde (Kameradrehung).
  let down: { x: number; y: number } | null = null;
  const anschlag = anschlagMoeglich(p.projectId);
  v.instance.domElement.addEventListener('pointerdown', (ev) => {
    down = { x: ev.clientX, y: ev.clientY };
  });
  v.instance.domElement.addEventListener('click', (ev) => {
    const moved = down ? Math.hypot(ev.clientX - down.x, ev.clientY - down.y) : 0;
    down = null;
    if (moved > 4 || toolbar.tool() !== 'navigate' || kontrollpunkte.picktBereit() || unterPointerLock() || tool.hitsHandle(ev.clientX, ev.clientY)) return;
    const id = layer.hitTest(ev.clientX, ev.clientY);
    if (id) {
      letzterSprung = performance.now();
      return goToStation(id);
    }
    // Pyramiden hinter einer Wand nicht anklickbar: Wolkentiefe an der Spitze gegen ihren Abstand.
    const foto = imgLayer?.hitTest(ev.clientX, ev.clientY, (p) =>
      p.tiefeM === undefined || !verdecktDurchWolke(p.tiefeM, picker.vorschauAtScreen(p.x, p.y).tiefeM));
    if (foto) return zeigeFoto(foto);
    if (anschlag) void (async () => {
      // Voller Treffer; liegt kein Originalpunkt genau auf dem Strahl, genügt die vorderste Fläche entlang des Strahls.
      const r = await picker.pickAtScreen(ev.clientX, ev.clientY);
      let punkt = r.point;
      const tiefe = r.info.frontDepthM;
      if (!punkt && tiefe !== undefined) {
        const rect = v.instance.domElement.getBoundingClientRect(); const cam = v.instance.view.camera;
        cam.updateMatrixWorld();
        const d = new Vector3(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1, 0.5)
          .unproject(cam).sub(cam.position).normalize();
        punkt = [cam.position.x + d.x * tiefe, cam.position.y + d.y * tiefe, cam.position.z + d.z * tiefe];
      }
      if (trifftAnschlag(punkt)) await oeffneAnschlag();
    })();
  });
  // Doppelklick im Panorama: zum Nachbarn in Klickrichtung (≤ 15°), wie man es aus Rundgängen kennt.
  // Im Orbit gehört der Doppelklick dem Drehpunkt (viewer.ts; dort nur mit aktiver Steuerung).
  v.instance.domElement.addEventListener('dblclick', (ev) => {
    const von = pano.state?.station;
    if (!von || toolbar.tool() !== 'navigate' || unterPointerLock() || performance.now() - letzterSprung < 600) return;
    const r = v.instance.domElement.getBoundingClientRect();
    const cam = v.instance.view.camera;
    cam.updateMatrixWorld();
    const p = new Vector3(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1, 0.5)
      .unproject(cam).sub(cam.position);
    const ziel = neighbourInDirection(stations, von, [p.x, p.y, p.z]);
    if (ziel) goToStation(ziel.id);
    else notify('Kein Standpunkt in dieser Richtung');
  });

  // Ein Verteiler für alle Kürzel (keymap.ts), Esc-Vorrang Ziehen → Zeichnung → Begehen → Panorama.
  const run = (a: KeyAction): void => {
    hinweise?.beobachte(a);
    // Im Begehen und im Panorama-Fadenkreuz verwirft ein Werkzeugwechsel per Taste kein offenes Befund-Formular.
    if ((a.type === 'tool' || a.type === 'werkzeug-wechsel') && (walk.aktiv || zielpunkt.panoramaZiel()) && pois.formularOffen()) {
      say(FORMULAR_OFFEN);
      return;
    }
    switch (a.type) {
      case 'tool':
        return toolbar.setTool(a.tool === toolbar.tool() ? 'navigate' : a.tool);
      case 'finish':
        tool.finish();
        return;
      case 'cancel-drag':
        tool.cancelDrag();
        return;
      case 'cancel-drawing':
        tool.cancelDrawing();
        return;
      case 'leave-panorama':
        return durch.active ? durch.leave() : pano.leave();
      case 'undo':
        return tool.undo();
      case 'delete-selection':
        return tool.deleteSelection();
      case 'toggle-list':
        // Über panels.toggle(), damit ein nur nach hinten gelegter Reiter nach vorn kommt
        // statt geschlossen zu werden (list.toggle() bleibt als eigene API für main.ts-fremde Aufrufer).
        return panels?.toggle('messungen');
      case 'toggle-snap':
        return toolbar.setSnapEdges(!toolbar.snapEdges());
      case 'toggle-panel':
        // Wie pois.togglePanel() den Suchfokus setzen, nur eben auch beim Nach-vorn-Holen (togglePois).
        if (a.panel === 'pois') togglePois();
        else panels?.toggle(a.panel);
        return;
      case 'toggle-layer': {
        const id = layerForKey(p.layers, a.index);
        if (id && reg) reg.setVisible(id, !reg.isVisible(id));
        return;
      }
      case 'alles-zeigen':
        if (v.controls.enabled) v.frameAll(visibleBounds(p, reg!.isVisible));
        return;
      case 'schnittbox':
        clip.setMode(a.modus);
        return;
      case 'standpunkt': {
        // Reihenfolge wie in der Minikarte; ohne aktiven Standpunkt: erster bzw. letzter.
        if (stations.length === 0) return;
        const i = activeStation ? stations.findIndex((x) => x.id === activeStation) : -1;
        const j = i < 0 ? (a.richtung === 1 ? 0 : stations.length - 1) : (i + a.richtung + stations.length) % stations.length;
        goToStation(stations[j]!.id);
        return;
      }
      case 'begehen-start':
        walk.starten();
        return;
      case 'begehen-ende':
        walk.esc(austrittsTreffer());
        return;
      case 'halten':
        if (walk.aktiv) walk.halte(a.aktion, true);
        else zielpunkt.panoHalte(a.aktion, true);
        return;
      case 'werkzeug-wechsel': {
        // Reihenfolge der Werkzeugleiste, zyklisch.
        const i = WERKZEUG_REIHENFOLGE.indexOf(toolbar.tool());
        const n = WERKZEUG_REIHENFOLGE.length;
        toolbar.setTool(WERKZEUG_REIHENFOLGE[(i + a.richtung + n) % n]!);
        return;
      }
      // Abfragetaste: frische Vorschau, sofort ansagen (ohne die Ruhezeit abzuwarten).
      case 'ansage':
        zielpunkt.ansageJetzt();
        return;
      case 'tastenhilfe':
        tastenhilfe.oeffne();
        return;
      // Befund am Fadenkreuz (F, Gamepad Y): im Begehen und im Panorama-Fadenkreuz.
      case 'befund':
        void zielpunkt.befundSetzen();
        return;
      case 'teilen':
        befundSteuerung.teile();
        return;
      case 'koordinaten':
        koZeile?.setzeAn(!koZeile.an());
        return;
      case 'koordinate-kopieren':
        void koZeile?.kopiere();
        return;
      case 'drehen-schritt':
        empfang.drehe(a.richtung);
        return;
    }
  };
  // Controller in allen Ansichten (padSteuerung.ts); erst jetzt, da der Verteiler steht. Im Begehen liest dessen
  // Bildschleife über `quelle`.
  const formularOffen = (): boolean => pois.formularOffen();
  padSt = attachPadSteuerung({
    v, pano, begehen: walk, rueckfrage,
    einstellungen: () => einstellungen,
    systemReduziert: () => systemReduziert.matches,
    belegung: () => padBelegung(belegung),
    say,
    hinweis: notify,
    werkzeug: () => toolbar.tool(),
    zeichnung: () => tool.isDrawing(),
    ziehen: () => tool.isDragging(),
    formularOffen,
    empfang: () => empfang.aktiv,
    durchKamera: () => durch.active,
    aktion: {
      werkzeug: (richtung) => run({ type: 'werkzeug-wechsel', richtung }),
      standpunkt: (richtung) => run({ type: 'standpunkt', richtung }),
      fang: () => {
        run({ type: 'toggle-snap' });
        say(toolbar.snapEdges() ? 'Fang ein' : 'Fang aus');
      },
      allesZeigen: () => run({ type: 'alles-zeigen' }),
      begehenStarten: () => run({ type: 'begehen-start' }),
      befund: () => run({ type: 'befund' }),
      mitteSetzen: () => void zielpunkt.mitteSetzen(),
      panoSetzen: (gedrueckt) => zielpunkt.panoHalte('begehen.setzen', gedrueckt),
      abbrechenZiehen: () => run({ type: 'cancel-drag' }),
      abbrechenZeichnung: () => run({ type: 'cancel-drawing' }),
      begehenBeenden: () => walk.beenden(austrittsTreffer()),
      panoramaVerlassen: () => run({ type: 'leave-panorama' }),
    },
    // Das Menü bündelt nur, was es auch per Taste oder Knopf gibt.
    menue: () => [
      walk.aktiv ? { text: 'Begehen beenden', tue: () => walk.beenden(austrittsTreffer()) }
        : !pano.active && !empfang.aktiv ? { text: 'Begehen starten', tue: () => run({ type: 'begehen-start' }) } : null,
      pano.active ? { text: 'Panorama verlassen', tue: () => run({ type: 'leave-panorama' }) } : null,
      !walk.aktiv && !pano.active ? { text: 'Alles zeigen', tue: () => run({ type: 'alles-zeigen' }) } : null,
      { text: 'Karte ein/aus', tue: () => run({ type: 'toggle-panel', panel: 'karte' }) },
      empfang.aktiv ? null : { text: 'Ebenen ein/aus', tue: () => run({ type: 'toggle-panel', panel: 'ebenen' }) },
      empfang.aktiv ? null : { text: 'Messliste ein/aus', tue: () => run({ type: 'toggle-list' }) },
      { text: 'Bedienung (Hilfe)', tue: () => run({ type: 'tastenhilfe' }) },
      empfang.aktiv ? null : { text: 'Einstellungen: Tastatur, Controller und Bewegung', tue: einstellungenOeffnen },
    ].filter((e): e is MenueEintrag => e !== null),
    benutztAenderung: (an) => {
      hinweise?.padAktiv(an);
      zielpunkt.padKreuz(an);
    },
  });
  const padQuelle = padSt;
  walk.setzeEingabeQuelle((dt) => padQuelle.quelle(dt));
  attachShortcuts({
    context: () => ({
      drawing: tool.isDrawing(),
      dragging: tool.isDragging(),
      // Blick durch die Kamera verhält sich für Esc wie ein Panorama (verlassen).
      panorama: pano.active || durch.active,
      selection: tool.hasSelection(),
      begehen: walk.aktiv,
      leinwandFokus: document.activeElement === v.instance.domElement || walk.gesperrt(),
      panoramaZiel: zielpunkt.panoramaZiel(),
      zeigerGesperrt: walk.zeigerGesperrt(),
      schnittbox: clip.enabled,
      empfang: empfang.aktiv, markieren: toolbar.tool() !== 'navigate',
    }),
    run,
    loslassen: (a) => (walk.aktiv ? walk.halte(a, false) : zielpunkt.panoHalte(a, false)),
    belegung: () => belegung,
  });

  const fps = { frames: 0, samples: [] as number[] };
  let last = performance.now();
  v.instance.addEventListener('after-render', () => {
    const now = performance.now();
    fps.samples.push(now - last);
    last = now;
    fps.frames++;
    if (fps.samples.length > 2000) fps.samples.shift();
  });
  (window as unknown as { __scanview: unknown }).__scanview = {
    viewer: v,
    get meshes() { return meshHandles.map((h) => ({ id: h.id, stage: h.stage, failure: h.failure, textur: h.textur, bytes: h.bytes,
      worldBox: h.worldBox, visible: h.visible, clippingPlaneCounts: h.clippingPlaneCounts,
      flaeche: h.flaeche, draht: h.draht })); },
    // Gekachelte Meshes: Zustand und Statistik für E2E und Leistungsmessung (Kacheln, Dreiecke, LRU, Blattspeicher).
    get tiles() { return tilesHandles.map((h) => ({ id: h.id, visible: h.visible, opacity: h.opacity, failure: h.failure, worldBox: h.worldBox,
      budget: h.budget, clippingPlaneCounts: h.clippingPlaneCounts, statistik: h.statistik(), blattUris: h.tileset.blaetter.map((b) => b.uri), kacheln: h.tileset.kachelzahl })); },
    get meshShadingMs() { return meshHandles.flatMap((h) => h.shadingMs); },
    // Live-Sonne: Zustand (an, zeit, stand) und setzen() — derselbe Weg wie das Panel (E2E, später Ansichtszustand/MCP).
    sonne: sonne ? { get an() { return sonne.an; }, get zeit() { return sonne.zeit.toISOString(); }, get stand() { return sonne.stand; },
      get ort() { return sonne.ort; }, get bahn() { return sonne.bahn; }, get spielt() { return sonne.spielt; },
      setzen: sonne.setzen.bind(sonne), zustand: () => sonne.zustand() } : null,
    zoomStopp,
    // Kontrollpunkte (J3b): Liste laden, wählen, picken, exportieren — derselbe Weg wie das Panel (E2E).
    kontrollpunkte,
    fps,
    manifest: p,
    measure: { store, picks: tool.picks, tool, debug: tool.debug },
    toolbar,
    // Gemeinsame Strahlsuche (Messen, POIs) — E2E-Haken für die Klickreihenfolge der POIs.
    picker,
    clip,
    // Test-Haken: Belegung zur Laufzeit ersetzen und Beschriftungen neu setzen (E2E).
    belegungSetzen: (roh: string): void => {
      belegung = ladeBelegung(roh);
      aktualisiereKuerzel();
    },
    stations: { list: stations, layer, minimap, active: () => activeStation, goTo: goToStation },
    fotos: { set: imageSet, layer: imgLayer, panel: fotoPanel, durch },
    pano,
    pois,
    panels: panels!,
    layers: reg!,
    begehen: walk,
    pad: padSt,
    drehpunkt,
    befund: befundSteuerung,
    empfang,
    koordinaten: koZeile,
  };
}

start().catch((e: unknown) => say(e instanceof Error ? e.message : String(e)));
