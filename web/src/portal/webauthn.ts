// Übersetzt zwischen py_webauthns JSON-Optionen (base64url-Strings, `options_to_json`) und den
// ArrayBuffer-Feldern, die die Browser-WebAuthn-API (`navigator.credentials.create/get`)
// verlangt — und zurück (`credentialToJson`). Reines Modul: keine DOM-/Netzwerk-Zugriffe, damit es
// sich ohne Browser per Vitest prüfen lässt. `passkeyMoeglich()` ist die einzige Ausnahme, die
// bewusst defensiv auf `window`/`navigator` prüft, statt sie vorauszusetzen (fehlen unter Node).
import { fromB64url, toB64url } from './b64url';

type JsonObjekt = Record<string, unknown>;

function alsObjekt(json: unknown): JsonObjekt {
  // py_webauthns `options_to_json` liefert einen JSON-String; der Server könnte ihn aber auch schon
  // geparst weiterreichen — beides tolerieren.
  const o: unknown = typeof json === 'string' ? JSON.parse(json) : json;
  if (typeof o !== 'object' || o === null) throw new Error('Ungültige WebAuthn-Optionen vom Server.');
  return o as JsonObjekt;
}

function b64Descriptor(c: unknown): PublicKeyCredentialDescriptor {
  const co = c as JsonObjekt;
  return { ...(co as unknown as PublicKeyCredentialDescriptor), id: fromB64url(co.id as string) };
}

export function creationOptions(json: unknown): CredentialCreationOptions {
  const o = alsObjekt(json);
  const user = o.user as JsonObjekt;
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...(o as unknown as PublicKeyCredentialCreationOptions),
    challenge: fromB64url(o.challenge as string),
    user: {
      ...(user as unknown as PublicKeyCredentialUserEntity),
      id: fromB64url(user.id as string),
    },
  };
  if (Array.isArray(o.excludeCredentials)) {
    publicKey.excludeCredentials = o.excludeCredentials.map(b64Descriptor);
  }
  return { publicKey };
}

export function requestOptions(json: unknown): CredentialRequestOptions {
  const o = alsObjekt(json);
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...(o as unknown as PublicKeyCredentialRequestOptions),
    challenge: fromB64url(o.challenge as string),
  };
  if (Array.isArray(o.allowCredentials)) {
    publicKey.allowCredentials = o.allowCredentials.map(b64Descriptor);
  }
  return { publicKey };
}

export function credentialToJson(c: PublicKeyCredential): Record<string, unknown> {
  const basis = { id: c.id, rawId: toB64url(c.rawId), type: c.type };
  const antwort = c.response;
  // Registrierung (navigator.credentials.create) liefert AuthenticatorAttestationResponse
  // (attestationObject), Anmeldung (…get) AuthenticatorAssertionResponse (signature) — per
  // Duck-Typing unterscheiden statt `instanceof`, das im Test ohne echten Browser nicht griffe.
  if ('attestationObject' in antwort) {
    const r = antwort as AuthenticatorAttestationResponse;
    return {
      ...basis,
      response: {
        clientDataJSON: toB64url(r.clientDataJSON),
        attestationObject: toB64url(r.attestationObject),
        transports: typeof r.getTransports === 'function' ? r.getTransports() : [],
      },
    };
  }
  const r = antwort as AuthenticatorAssertionResponse;
  return {
    ...basis,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      authenticatorData: toB64url(r.authenticatorData),
      signature: toB64url(r.signature),
      userHandle: r.userHandle ? toB64url(r.userHandle) : null,
    },
  };
}

export function passkeyMoeglich(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.PublicKeyCredential !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    typeof navigator.credentials !== 'undefined'
  );
}
