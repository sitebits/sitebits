// Tafel am Westportal des synthetischen Testpakets — wird von anschlag.ts erst beim Treffer nachgeladen.
// Textterminal, eine Frage, eine kleine Pixelfigur mit Begleitmusik aus WebAudio (keine Dateien, keine fremden
// Quellen). Esc schließt aus jedem Zustand; der Fokus kehrt danach zurück.

interface Sprache {
  datei: string; kopf: string; text: string[]; ja: string; nein: string; gut: string; falsch: string; nachsatz: string;
  exe: string; tonAus: string; tonAn: string; ascii: string; pixel: string; zurueck: string; dialog: string; figur: string;
}
type Haltung = [[number, number], [number, number]];
interface Schritt { s: number; b: number; aL: string; aR: string; bL: string; bR: string; m: number }
interface Figur {
  GB: number; GH: number; KOPF: string[]; MUND_AUF: string; RUMPF: string[]; FARBE: Record<string, string>;
  ZEICHEN: Record<string, string>; ARM: Record<string, Haltung>; BEIN: Record<string, [number, number]>; FOLGE: Schritt[];
}
interface Musik { BPM: number; TAKTE: [number, number, number][][]; AKK: number[][]; BASS: number[] }
interface Inhalt { de: Sprache; en: Sprache; figur: Figur; musik: Musik }

export const TAFEL =
  '646577e9bc03f044d617e0e7dbcf683687dc804705d7656ff522bc68a06e22025832cf2a30c7f2c6ac284e605eea68bc949071657c797229' +
  'c52db6daef176a4cc048c15d58e5b97575e1645dda8bc0c86933e5408abdafa9ac236b5e48a47555fd8d1b2a595b52d668bfa6bc217200e3' +
  'b6699c0c0e5dab256bd730a80bc2684370baec811edbf8dc29c4d8d154666ae39977833efc29311636fb4d3d098d356dbcb05162c840058b' +
  '5e7455db2d3b7f810630dd26934091b08db3b5ce04166a4a1f3556fe45d7c1059309e260af7ffe906488233a04866970689c0096bb1a9f24' +
  '8894ab68948a1a647331d9855436ebb44d9bc96365a0ca23288a4b0147a4b05a174787565fd59ea2d16464f163c092d39461704147c182b9' +
  'd1a4d641b874a99226dddd09267df886da6d0e0786d20b32b4da7bd4b1a6ee40d6d74b5f8366a3a9978921072ddd5c4fa2b9dffb022cec8a' +
  'ffe3a7465ea25d3344151967a40e8ee8596241434efa63dde873c8ebe8aa8bdeebb33bceea92e15bb911abf3f5344cbba7c53b48382ed5b9' +
  'cdc4bdb98c1420d266fb0f0a32e805f12004fc48b0eb57a731b7c684da34a8086307352f7d25f2c01d607ae486ee4fb0c2d14611bf1bd289' +
  'cac4e6d235e073145099515ecdfcf01ddc47f569cf17519d3a337e5a7cee99ff97661c48d109c62f968818c10818080c628b30b9c7b1d285' +
  '00e41b672c5d26bee6596745057fe89a9965955396473223e2c3cfb16af7f06a7b6cdfef9292003441d20689e38fb6ddcc924150a4a15acd' +
  '75733f447f06f92c3b32b3ec72a9bbf4ad213933e7cd97dbc8772ba28923b0a0c47119f6142abdfa9720bd50a9bddd51f3c50855a4120859' +
  'e9c01b70406333e4b8372dde1b20b3b144b888ddea88f5c500f28198ca9897231e4e2c127a0dd4ab013b173101abbe0c9d367cf3a086e081' +
  '5830c5085355dddeec416039b96fd26266e26dc4835f8706ce5fdbf52090a021a67878ea89702ff6de9fcb0d2f9fe66170d66521837127f1' +
  '5ba8a3122b209aa7b6f867a792f6617a48366a89e6d8d299e14db51c50e01ea1804f7c9360e6e148a4c40a0a04f757f678e0af6f2c90d599' +
  '1c91919f386f7609be47cfbe5b8f882ad928433498ba1198bf482cb25cc7d533f7dfd9a4df9fb1c6e399ce70617b1efda8e89abd9d8e6c79' +
  'd816470b31196db6d2510acccba2d148046e04bcd716372480e5f14979110b704dc70bd8de4e89307e43dec227c1da9f294fd2d2c9a8feff' +
  '1dcf227bbf06cade952d1d7588b611eadbdd39f4a3e808e87ba4ffae831894bf55d9e0f45afaf206d514f0d1e90dbd79602caa38efa3588a' +
  '36094b8a4a10e83f8ad9747e71fd077c634efd019c39a57ade97801f284660d44d82fb31f58eb3831313ed003288333adc3e99cd2d6e09c2' +
  '687a7ac19dfa5423b478722145feb2ad62bc4edc11bb2e76293e5cef8842aaceaca84fe5089ea008cb3487d91333420b964b784106af392b' +
  'b2732c618f5a4e09b02440949bdd94015f243daf771cc6cb65902964bf2069719f659cfb6b15bb0a4175e3065b542cc54cbe8eaeaa958861' +
  '2ae2cf5282e96d000cbe12082dc244a036d4a460e14b234a6fb7670c1a122ba1c740fb2772fa967354ef2c95c66643f714430a67da271132' +
  '40d4989b0c1c6ef49845abb6c1a583a6b477d01e9c11bacd22098fe020e90a1b5c38ba41a4406514c2174250ab0ddd71630bd5c0df8caf4c' +
  'a5116a60601d67f5bfbe540d10de2ba0f326ebeb7393a70851e723d3180f91de0bd5c60e7cf57cdc30b83ac12404d8f90dd4a1161a8a7cb7' +
  'a62851d654ffe5d92ff1d439308eb4128c44c8a9f2ec5d83912c2f85845bf52903e189da9a491f976c80bbff97a5c4883d3083608725769a' +
  'd3928a82e621865df8675cc5d4eb5bc328ed56c829b44f2afa5b40cb62114e8c74066caa8bab2231ecd076c1517d5f9e10f4c84354297a1c' +
  'daeb9d7956b141e8104cc0be0093618dae7a0e2523492e9aa253c5e8a2836e90378d4a1a43b4edc39cd0f4a895bdbe6f72b9701ef5282089' +
  '9f1d97b9db1643c2d2569b25238cb64bed578d9cb404c194f925dbd80b4b581bbbf2906c7bc0e5a01bf5ad9ad2932c42dc735b32dfa64b34' +
  'ae494bb01f4b4776b26f9dd721410315cdf021c72e6a3fafd29e701fc0c6ab2fefd1602ec195d704489430af6986d37f2a071cbd7c7efed9' +
  'aa5bff88e725100090ea004003508c7415527d3917dc8bf3716f971119586ef25516c6c63e25e1eeb1b2f1288be9d5e36a529931c3e1347d' +
  '15036261f319891d8f2b056c4b70c26bc691cc7e8d74556cee5f87b56b350441c4984242d2718177fa34f84de2a43d4cadc3e5013dcabbc2' +
  '3521bde742993ce6ff011142fa8d611ea2aea7fd54983c73d6dee4fcc2415f2050cc31444ac128b714f17a5d788427f530bf2375196f342e' +
  '84f018d9b9127bcfe99dd5c59acf362d1592b0d16db5379682d8db18448ff8910063483aa927467c75b3a8b2f56bb6f238bce9f7a3d8ee19' +
  '3a7e056fb4d99d6d48d45ebd33e35ed0c8268e505fbb5f389dbbe11626643f78cd1f85610db94811118aaf7935e21aba1d1a12fb72c3d036' +
  '3071fa0eda96fcebc3c7669d8b51dbcd123f0e51a95a59796f24550fad41a67dacf825edcc2a0f56c8530ba07b0e347331cd3723dc5656da' +
  'c955333defc68ba2a001d7a9a040f3478e9e9d4774a756ecc105d2a4fc8e2dec5aea4b175556bbec45fd9f23316f256694ae242df52f88b5' +
  '1c8124a6145cbd397673e2e27740de809a228545492e45e407b54aef7f9a51125de1a9ff6961ebbaf2b39eb43eaa36d8d5434c23eac7d1d0' +
  'cbfb33c7630a162fc76f5bc193081854b7e3e279e0175644846baaaf769dbd6d3274d1796033aa6e8606ae254b99ea24fb2a41ef27d23669' +
  'add30ca7d74b6cf61476ea765e7fefe5bee04f86932b0d9c3fce24948b0b74e808a4b54fafb8d9ce5b6bd7e2c42caa7edaa93bb3b6869c22' +
  '3978f6c147680feb4901258ed0ab7a0eae6988fcfb7b77f83b7c724cb67ade9dac872fe1371fe2697438d929378bed39f3f7ac25887e840f' +
  'f23c6bbe202ddf9469440d6ab1344bfd1a2b7a6128b9e0e5b9cfa5132825166a0b788f26f5df5d433e744f2bf25670a588fe9451cf36afd9' +
  '3dddb56cddfe43ca2491085ab80642bdc428a6c66d13475e13807a78a8682ab86e2e8f8323899906010a89d6f77c2b8e03b24b0557c3f6ae' +
  '64525ebfb40b8bf45a13377b22001e5ebf95aab6e1bbc6f382fac9127d7c8b1276bbe4260192f28bafdde2f3dd9024c2c0086d411197ee42' +
  '427aa1b3cfae310d4cc30e21007a056e6a5deb6c10edf005e5f051467de8d22acea096553622c260d990191721ff41c1a3bfe2c9c09fb5fb' +
  '0c1bd8aca05eb0efcc36acb754c2e0aad92241a09fda8e4cfff5e78e2cde919d501c3a9e71111990e56ba8de514af17064e94c2a7cad433e' +
  'e9b8dfa144e28570dbfa272b3588ea70aaac6fc299575a775e9509d53d441152fe0a5b15ed842521aa79246d3f5d997c23023896d098aed9' +
  '6ab6c8ef64e45de5cb540708276965d96d57d20768b77880f348afff54612b2bb4b324af085042f75b42373342ef596001769a73c753e512' +
  '614d1be4c3119492bf996b43b67a4de99b715ceb5b6e8a60eae31493cbb1c18d0cb450a1c6466c9c1b85fdc104a691b90ce045674796456b' +
  '4dc64a48dc403857eeb8c359d7edd9bf1db0b0fbfde0ee4af35d476df05c86f7044415452ddbe9c4f9523bb5855c0f5d3360ea54e2cfd1fa' +
  '64efa5ccc5386b8a1bb9547dcce728e190af6e76dbe0cb8039bc068e366c15c185eec90d4009b9fbb5183dec4f8507e8684ae95c80a4f0ed' +
  'b79ebc306c5a4ff66b81d84fc5b01d91b4bb9fd7fd97a578c2057cd6dd07ac892d4bdc52b851521e7b9e77914f7e374d1741346b94f77671' +
  '60ef4e91bbd23d3de0616ae8828546375232577149ae829c1430b0091e3e96f6ad8d8aacd8df6c2d6f9510d080c26cc10f40b562fcf37a63' +
  '089703719b852eba40e96c71f22dca6881e37b047d27d0eb5dd3200186c4fb75fd4c75880fc1f7f1f194a82a404c71167061824fbdd9f47b' +
  'ee2a1e7fff28c32e4b476994aa4155d6edaa73f1f2a694e66b2bc851addbe702e5320659b966edf177438c247698f854321856c8a54dbdd9' +
  'd7697f6b34a8e920b7fe6d7b825c37245272f3631d677e0513fb7de018eff06f5bc3df88662370766af317c96e095fc7fb336acd0a6154b6' +
  '5ed27cdc17cf6d22a954cfcec95448fa30fae82ffd6ea751e6f0f6aaba241a7c2703959cfd948175f241ed036c08448d67c98d9ba2200c04' +
  '967b393e539ae32e1dd1a387bb1a6ba4b4b8f40e5c51a40685780a4fabe7c21e1ca6530b0d304d6129b16de5d97ccfa807c9912875053253' +
  '3c90b5c0e9deef809eca7d7d603ae8119b1d4396ae5f21fd56eac0fe39657b45b69346296fa54ad1052451792a1bb8290c76bd982f5d4ead' +
  '3381efd7052bfc66023650080004464795d21a996d8815fa16360f96df3cca2726077b50d7104427424ef190956868d083e267fe78c70f62' +
  '479f8e4c08cfd3815ff4f7d9ef5395133903da97f9c0ec1f3496266ecc7008c2924734721b9be2d700dcfc75a6eb8b312074f524a1e5ae44' +
  '4b8185c58a794a6abf76e33a2e64ca3504b471ae1104ccf0fc48673e954e5f1be03367870a9352e663c95d80f04090e989dbb4393fb2535e' +
  '8c25b9cefd623bbac3d264dba9e42991c68bf46d19fc30ec1cdf904f3d4d14d44abe8fd53e1b3022928584c4290ce0c2545fc55ec2fb9ff6' +
  '68f1c1a6b4cb12a03cfc998979a8f511bc2450f5d4718d3eae8f40f85786429131e05abcb64530ede74744a70c4871bde095757ef89b35e8' +
  '65fe35e56e1da3e80a31da882e515686af4286092740fff9609eac57b0fa2fe290cb0152cffcc6032c6167d13bd4158fc9c21c703c532c0c' +
  '3b5fdec2c970f19e02d8bf80348241c4539bc15480e7d9c0ff38fbdc29443922e5aea4ad213431dae680829466482bcb5244cb3ffe9d791b' +
  '5692d1ad074a86e149e1c18a710846e2557ac95fbeddd4737f45b83a927090496331c9032f2088f78c4a87d65775d555f2bab7d45c11176a' +
  'cd093de6d043333bfcd10e866733a6d0b27746035af6ad55c7302a5b7fe5e3e3ab1cbef26927f06d3bcba791a5c5526453a11072e42db8c4' +
  '0f7a02388e3b291dc3be71a030210ecc589005ec98c05ff1f35e62e07b829cf34d4ae71da4bd74ec88d2287bd2dfa793ae75cf59cbaa731b' +
  '1de27223765141647405aef3a04aa3dce4ff450fb91d528af02dce56c59031647194a090e2427d3d365cf97f6cdc5bfd9a00284589efcd8d' +
  '48a41f56034e62aca5c63cd1b15b94cec974365b32d66a12bc39034ba558d70813f926040fb612c29bde74dc58bebdd908af75c64ffd1267' +
  '938c27dad727774029afe137ca2f86e84aa19435946d253e637330267ce5f535e9ad403fe849c320c5c3a9b47491fe7a8150d21fc071b7ee' +
  'dac91edbeb11e7227e523d1213a060eff0c5ef98dce9266dc72dffcbb73f29ed43e296570f42a9f3235116ea5cd7eeed0c9337169b5a7f71' +
  '2b55b7ce888858cc8a2f3551885c8fd926db0826cca55b42140a89451994cef3bc18888f337aaed9c3718080cc5ff2e52e82717e308eba85' +
  '79224d1e5f75ca3811cdcf3b63';

/** Hex → Bytes, XOR mit xorshift32-Strom, UTF-8 → JSON. Nur gegen Mitlesen im Quelltext, kein Schutz. */
export function entpacke(hex: string, start = 0x2f6b1d93): unknown {
  let s = start >>> 0;
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16) ^ (s & 0xff);
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, stil: string, text = ''): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag); e.style.cssText = stil; if (text) e.textContent = text; return e;
};
/** Sichtbarkeit: `hidden` allein verliert gegen ein Inline-`display`, daher beides setzen. */
const zeig = (e: HTMLElement, an: boolean): void => { e.hidden = !an; e.style.display = an ? (e.dataset.anzeige ?? '') : 'none'; };
const PHOSPHOR = '#8dff9a', DUNKEL = '#3f8a4a', NACHT = '#0a0c11';
const KNOPF = `font:inherit;font-size:20px;padding:2px 12px;cursor:pointer;border:0;color:${NACHT};background:${PHOSPHOR}`;
const KNOPF_LEER = `font:inherit;font-size:20px;padding:2px 12px;cursor:pointer;border:0;color:${PHOSPHOR};background:transparent;outline:1px solid ${DUNKEL}`;

export function zeigeTafel(): Promise<void> {
  const inhalt = entpacke(TAFEL) as Inhalt;
  const deutsch = (navigator.languages ?? [navigator.language ?? 'en']).some((l) => /^de\b/i.test(l));
  const t = deutsch ? inhalt.de : inhalt.en;
  const reduziert = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const vorher = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  const huelle = el('div', 'position:fixed;inset:0;z-index:2000;display:flex;align-items:center;justify-content:center;' +
    'padding:16px;box-sizing:border-box;background:rgba(0,0,0,.6);font-family:ui-monospace,"DejaVu Sans Mono",monospace');
  const monitor = el('div', `position:relative;width:min(46rem,100%);max-height:100%;overflow:auto;box-sizing:border-box;` +
    `background:rgba(8,16,10,.96);border:2px solid ${DUNKEL};box-shadow:0 0 48px rgba(141,255,154,.12);padding:16px 18px 20px;color:${PHOSPHOR}`);
  monitor.setAttribute('role', 'dialog'); monitor.setAttribute('aria-modal', 'true'); monitor.setAttribute('aria-label', t.dialog);
  const raster = el('div', 'position:absolute;inset:0;pointer-events:none;' +
    'background:repeating-linear-gradient(to bottom,transparent 0 2px,rgba(0,0,0,.22) 2px 3px)');
  const kopf = el('div', `display:flex;justify-content:space-between;gap:12px;font-size:11px;letter-spacing:.1em;color:${DUNKEL};margin-bottom:10px`);
  const datei = el('span', '', t.datei); kopf.append(datei, el('span', '', t.kopf));
  const ausgabe = el('pre', `margin:0;white-space:pre-wrap;font:inherit;font-size:clamp(13px,3.6vw,18px);line-height:1.3;` +
    `text-shadow:0 0 6px rgba(141,255,154,.45);min-height:6em;cursor:pointer`);
  const knoepfe = el('div', 'display:flex;gap:14px;flex-wrap:wrap;margin-top:14px'); knoepfe.dataset.anzeige = 'flex'; zeig(knoepfe, false);
  const ja = el('button', KNOPF, t.ja), nein = el('button', KNOPF_LEER, t.nein);
  ja.type = nein.type = 'button'; knoepfe.append(ja, nein);
  const buehne = el('div', 'display:grid;justify-items:center;gap:10px'); buehne.dataset.anzeige = 'grid'; zeig(buehne, false);
  const bild = el('canvas', 'image-rendering:pixelated;max-width:60vw;filter:drop-shadow(0 0 6px rgba(141,255,154,.35))');
  bild.setAttribute('role', 'img'); bild.setAttribute('aria-label', t.figur);
  const ascii = el('pre', 'margin:0;font:inherit;font-size:clamp(6px,2vw,10px);line-height:1;overflow-x:auto;max-width:100%'); zeig(ascii, false);
  const unterzeile = el('p', `margin:0;text-align:center;font-size:12px;color:${DUNKEL}`);
  const leiste = el('div', 'display:flex;gap:12px;flex-wrap:wrap;justify-content:center');
  const ton = el('button', KNOPF_LEER, t.tonAus), ansicht = el('button', KNOPF_LEER, t.ascii), zurueck = el('button', KNOPF_LEER, t.zurueck);
  ton.type = ansicht.type = zurueck.type = 'button'; leiste.append(ton, ansicht, zurueck);
  buehne.append(bild, ascii, unterzeile, leiste);
  monitor.append(raster, kopf, ausgabe, knoepfe, buehne); huelle.append(monitor);
  document.body.append(huelle);

  // ---- Text tippt sich, Klick oder Taste spult vor ----
  const text = t.text.join('\n');
  let tippLauf = 0;
  const fertig = (): void => { ausgabe.textContent = text; zeig(knoepfe, true); ja.focus(); };
  const tippe = (): void => {
    const lauf = ++tippLauf; let i = 0;
    const schritt = (): void => {
      if (lauf !== tippLauf) return;
      i = Math.min(text.length, i + (text[i] === '\n' ? 1 : 2));
      ausgabe.textContent = text.slice(0, i) + '█';
      if (i < text.length) setTimeout(schritt, text[i - 1] === '\n' ? 160 : 24); else fertig();
    };
    if (reduziert) fertig(); else schritt();
  };
  const vorspulen = (): void => { if (knoepfe.hidden && !ausgabe.hidden) { tippLauf++; fertig(); } };

  // ---- Figur ----
  const f = inhalt.figur; const PX = 7;
  bild.width = f.GB * PX; bild.height = f.GH * PX;
  const zeichner = bild.getContext('2d');
  const pose = (p: Schritt): string[][] => {
    const g = Array.from({ length: f.GH }, () => Array<string>(f.GB).fill(''));
    const px = (x: number, y: number, c: string): void => { if (x >= 0 && x < f.GB && y >= 0 && y < f.GH) g[y]![x] = c; };
    const sprite = (zeilen: string[], x0: number, y0: number): void => zeilen.forEach((z, r) => [...z].forEach((c, k) => {
      if (c !== '.') px(x0 + k, y0 + r, c === 'E' || c === 'M' ? '' : c); }));
    const linie = (x0: number, y0: number, x1: number, y1: number, c: string): void => {
      const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) || 1, steil = Math.abs(y1 - y0) >= Math.abs(x1 - x0);
      for (let i = 0; i <= n; i++) {
        const x = Math.round(x0 + (x1 - x0) * i / n), y = Math.round(y0 + (y1 - y0) * i / n);
        px(x, y, c); if (steil) px(x + 1, y, c); else px(x, y + 1, c);
      }
    };
    const { s, b } = p, boden = 31;
    for (const [hx, art, links] of [[9, p.bL, true], [13, p.bR, false]] as const) {
      const [kdx, fdx] = f.BEIN[art]!, hy = 18 + b, ky = 24 + b;
      const knieX = hx + s + (links ? kdx : -kdx), fussX = hx + (links ? fdx : -fdx);
      linie(hx + s, hy, knieX, ky, 'P'); linie(knieX, ky, fussX, boden, 'P');
      for (let k = 0; k < 3; k++) px(fussX + (links ? 1 - k : k), boden + 1, 'B');
    }
    for (let r = 18; r <= 22; r++) {
      const y = r + b, w = Math.floor((r - 18) / 2);
      for (let x = 7 - w + s; x <= 16 + w + s; x++) if (!(r >= 21 && (x === 11 + s || x === 12 + s))) px(x, y, 'C');
    }
    sprite(f.RUMPF, 6 + s, 10 + b);
    sprite(p.m ? f.KOPF.map((z, i) => (i === 7 ? f.MUND_AUF : z)) : f.KOPF, 6 + s, b);
    for (const [sx, art, spiegel] of [[6, p.aL, 1], [17, p.aR, -1]] as const) {
      const [[ex, ey], [hx, hy]] = f.ARM[art]!, x0 = sx + s, y0 = 11 + b;
      const ellX = x0 + ex * spiegel, ellY = y0 + ey, handX = x0 + hx * spiegel, handY = y0 + hy;
      linie(x0, y0, ellX, ellY, 'A'); linie(ellX, ellY, handX, handY, 'A'); px(handX, handY, 'S'); px(handX + 1, handY, 'S');
    }
    return g;
  };
  let k = 0, takt = 0, alsAscii = false;
  const zeige = (): void => {
    const g = pose(f.FOLGE[k]!);
    if (alsAscii) { ascii.textContent = g.map((z) => z.map((c) => f.ZEICHEN[c] ?? '  ').join('')).join('\n'); return; }
    if (!zeichner) return;
    zeichner.clearRect(0, 0, bild.width, bild.height);
    g.forEach((z, y) => z.forEach((c, x) => { if (c) { zeichner.fillStyle = f.FARBE[c] ?? PHOSPHOR; zeichner.fillRect(x * PX, y * PX, PX, PX); } }));
  };
  const tanzStopp = (): void => clearInterval(takt);
  const tanzStart = (bpm: number): void => {
    k = 0; zeige(); tanzStopp();
    if (!reduziert) takt = window.setInterval(() => { k = (k + 1) % f.FOLGE.length; zeige(); }, 60000 / bpm / 2);
  };

  // ---- Musik ----
  const m = inhalt.musik;
  const melodie = new Map<number, [number, number]>();
  for (let w = 0; w < 2; w++) m.TAKTE.forEach((tk, i) => tk.forEach(([st, n, l]) => melodie.set((w * 4 + i) * 16 + st, [n, l])));
  let ac: AudioContext | null = null, haupt: GainNode | null = null, plan = 0, schrittNr = 0, planer = 0, an = true;
  const hz = (n: number): number => 440 * Math.pow(2, (n - 69) / 12);
  const s16 = 60 / m.BPM / 4, AUFTAKT = 16;
  const tonAus = (typ: OscillatorType, freq: number, zeit: number, dauer: number, laut: number): void => {
    if (!ac || !haupt) return;
    const o = ac.createOscillator(), g = ac.createGain(); o.type = typ; o.frequency.value = freq;
    g.gain.setValueAtTime(0, zeit); g.gain.linearRampToValueAtTime(laut, zeit + 0.005);
    g.gain.setValueAtTime(laut, zeit + dauer * 0.7); g.gain.exponentialRampToValueAtTime(0.0008, zeit + dauer);
    o.connect(g).connect(haupt); o.start(zeit); o.stop(zeit + dauer + 0.02);
  };
  const rauschen = (zeit: number, dauer: number, laut: number, hoch: boolean): void => {
    if (!ac || !haupt) return;
    const n = Math.floor(ac.sampleRate * dauer), puffer = ac.createBuffer(1, n, ac.sampleRate), d = puffer.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    const q = ac.createBufferSource(), filter = ac.createBiquadFilter(), g = ac.createGain();
    q.buffer = puffer; filter.type = hoch ? 'highpass' : 'bandpass'; filter.frequency.value = hoch ? 7000 : 1800;
    g.gain.setValueAtTime(laut, zeit); g.gain.exponentialRampToValueAtTime(0.0008, zeit + dauer);
    q.connect(filter).connect(g).connect(haupt); q.start(zeit);
  };
  const schlag = (zeit: number): void => {
    if (!ac || !haupt) return;
    const o = ac.createOscillator(), g = ac.createGain(); o.type = 'triangle';
    o.frequency.setValueAtTime(150, zeit); o.frequency.exponentialRampToValueAtTime(45, zeit + 0.12);
    g.gain.setValueAtTime(0.5, zeit); g.gain.exponentialRampToValueAtTime(0.001, zeit + 0.16);
    o.connect(g).connect(haupt); o.start(zeit); o.stop(zeit + 0.18);
  };
  const sechzehntel = (i: number, zeit: number): void => {
    if (i < AUFTAKT) { if (i < 8 ? i % 4 === 0 : true) rauschen(zeit, 0.09, 0.08 + 0.022 * i, false); if (i === 0) schlag(zeit); return; }
    const z = (i - AUFTAKT) % 128, a = z % 16, h = Math.floor(z / 8) % 4;
    if (a % 4 === 0) schlag(zeit);
    if (a === 4 || a === 12) rauschen(zeit, 0.12, 0.28, false);
    rauschen(zeit, 0.03, a % 4 === 2 ? 0.08 : 0.025, true);
    if (a % 2 === 0) tonAus('triangle', hz(m.BASS[h]! + (a % 4 === 2 ? 12 : 0)), zeit, s16 * 1.8, 0.3);
    if (a % 4 === 2) for (const n of m.AKK[h]!) tonAus('square', hz(n + 12), zeit, s16 * 1.1, 0.022);
    const ton1 = melodie.get(z); if (ton1) tonAus('square', hz(ton1[0]), zeit, s16 * ton1[1] * 0.92, 0.075);
  };
  const musikStart = (): void => {
    try {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      ac = ac ?? new Ctor(); void ac.resume();
      haupt?.disconnect(); haupt = ac.createGain(); haupt.gain.value = an ? 0.7 : 0; haupt.connect(ac.destination);
      plan = ac.currentTime + 0.05; schrittNr = 0; clearInterval(planer);
      planer = window.setInterval(() => { while (ac && plan < ac.currentTime + 0.2) { sechzehntel(schrittNr++, plan); plan += s16; } }, 50);
    } catch { /* ohne Ton tanzt die Figur trotzdem */ }
  };
  const musikStopp = (): void => { clearInterval(planer); haupt?.disconnect(); haupt = null; void ac?.close().catch(() => undefined); ac = null; };

  const zeigeBuehne = (zeile: string): void => {
    zeig(ausgabe, false); zeig(knoepfe, false); zeig(buehne, true);
    datei.textContent = t.exe; unterzeile.textContent = `${zeile} ${t.nachsatz}`;
    musikStart(); tanzStart(m.BPM); zurueck.focus();
  };

  return new Promise<void>((fertigGeschlossen) => {
    const schliesse = (): void => {
      musikStopp(); tanzStopp(); tippLauf++; huelle.remove();
      document.removeEventListener('keydown', taste, true);
      vorher?.focus(); fertigGeschlossen();
    };
    // Tasten gehören der Tafel (Erfassungsphase): Esc schließt, Tab bleibt im Dialog, sonst wird vorgespult.
    const taste = (ev: KeyboardEvent): void => {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); schliesse(); return; }
      if (ev.key === 'Tab') {
        const ziele = [...monitor.querySelectorAll('button')].filter((b) => b.offsetParent !== null);
        if (ziele.length) {
          const i = ziele.indexOf(document.activeElement as HTMLButtonElement);
          ev.preventDefault(); ziele[(i + (ev.shiftKey ? -1 : 1) + ziele.length) % ziele.length]!.focus();
        }
        ev.stopPropagation(); return;
      }
      ev.stopPropagation();
      if (!knoepfe.hidden || !buehne.hidden) return;
      ev.preventDefault(); vorspulen();
    };
    document.addEventListener('keydown', taste, true);
    ausgabe.addEventListener('click', vorspulen);
    huelle.addEventListener('click', (ev) => { if (ev.target === huelle) schliesse(); });
    ja.addEventListener('click', () => zeigeBuehne(t.gut));
    nein.addEventListener('click', () => zeigeBuehne(t.falsch));
    zurueck.addEventListener('click', schliesse);
    ton.addEventListener('click', () => { an = !an; if (haupt) haupt.gain.value = an ? 0.7 : 0; ton.textContent = an ? t.tonAus : t.tonAn; });
    ansicht.addEventListener('click', () => {
      alsAscii = !alsAscii; ansicht.textContent = alsAscii ? t.pixel : t.ascii; zeig(bild, !alsAscii); zeig(ascii, alsAscii); zeige();
    });
    monitor.tabIndex = -1; monitor.focus();
    tippe();
  });
}
