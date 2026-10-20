import { describe, expect, it } from 'vitest';
import { creationOptions, requestOptions } from '../src/portal/webauthn';

// Festes Beispiel-JSON, wie es py_webauthns options_to_json ausgibt (base64url-Strings).
const CREATION_JSON = {
  rp: { name: 'scanview', id: 'x.test' },
  user: { id: 'AAEC', name: 't@b.de', displayName: 'T' },
  challenge: 'AAEC',
  pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
  timeout: 300000,
  excludeCredentials: [],
  authenticatorSelection: { residentKey: 'preferred', requireResidentKey: false, userVerification: 'required' },
  attestation: 'none',
};

describe('webauthn — creationOptions', () => {
  it('wandelt challenge und user.id in ArrayBuffer', () => {
    const opts = creationOptions(CREATION_JSON);
    expect(opts.publicKey?.challenge).toBeInstanceOf(ArrayBuffer);
    expect(opts.publicKey?.user.id).toBeInstanceOf(ArrayBuffer);
    expect(new Uint8Array(opts.publicKey!.user.id as ArrayBuffer)).toEqual(new Uint8Array([0, 1, 2]));
    expect(new Uint8Array(opts.publicKey!.challenge as ArrayBuffer)).toEqual(new Uint8Array([0, 1, 2]));
  });

  it('wandelt excludeCredentials[].id in ArrayBuffer, wenn vorhanden', () => {
    const json = { ...CREATION_JSON, excludeCredentials: [{ id: 'AQI', type: 'public-key' }] };
    const opts = creationOptions(json);
    const ausschluss = opts.publicKey?.excludeCredentials;
    expect(ausschluss?.[0]?.id).toBeInstanceOf(ArrayBuffer);
    expect(new Uint8Array(ausschluss![0]!.id as ArrayBuffer)).toEqual(new Uint8Array([1, 2]));
  });

  it('akzeptiert die Optionen auch als JSON-String', () => {
    const opts = creationOptions(JSON.stringify(CREATION_JSON));
    expect(opts.publicKey?.challenge).toBeInstanceOf(ArrayBuffer);
  });
});

describe('webauthn — requestOptions', () => {
  const REQUEST_JSON_OHNE_CREDENTIALS = {
    challenge: 'AAEC',
    timeout: 300000,
    rpId: 'x.test',
    userVerification: 'required',
  };
  const REQUEST_JSON = {
    ...REQUEST_JSON_OHNE_CREDENTIALS,
    allowCredentials: [{ id: 'AQI', type: 'public-key', transports: ['internal'] }],
  };

  it('wandelt challenge und allowCredentials[].id in ArrayBuffer', () => {
    const opts = requestOptions(REQUEST_JSON);
    expect(opts.publicKey?.challenge).toBeInstanceOf(ArrayBuffer);
    const erlaubt = opts.publicKey?.allowCredentials;
    expect(erlaubt?.[0]?.id).toBeInstanceOf(ArrayBuffer);
    expect(new Uint8Array(erlaubt![0]!.id as ArrayBuffer)).toEqual(new Uint8Array([1, 2]));
  });

  it('kommt ohne allowCredentials aus', () => {
    const opts = requestOptions(REQUEST_JSON_OHNE_CREDENTIALS);
    expect(opts.publicKey?.challenge).toBeInstanceOf(ArrayBuffer);
    expect(opts.publicKey?.allowCredentials).toBeUndefined();
  });
});

describe('webauthn — credentialToJson', () => {
  it('serialisiert eine Registrierungsantwort (attestationObject)', async () => {
    const { credentialToJson } = await import('../src/portal/webauthn');
    const fake = {
      id: 'AQI',
      rawId: new Uint8Array([1, 2]).buffer,
      type: 'public-key',
      response: {
        clientDataJSON: new Uint8Array([3]).buffer,
        attestationObject: new Uint8Array([4]).buffer,
        getTransports: () => ['internal'],
      },
    } as unknown as PublicKeyCredential;
    const json = credentialToJson(fake);
    expect(json.id).toBe('AQI');
    expect(json.type).toBe('public-key');
    expect((json.response as Record<string, unknown>).attestationObject).toBe('BA');
    expect((json.response as Record<string, unknown>).transports).toEqual(['internal']);
  });

  it('serialisiert eine Anmeldeantwort (signature)', async () => {
    const { credentialToJson } = await import('../src/portal/webauthn');
    const fake = {
      id: 'AQI',
      rawId: new Uint8Array([1, 2]).buffer,
      type: 'public-key',
      response: {
        clientDataJSON: new Uint8Array([3]).buffer,
        authenticatorData: new Uint8Array([5]).buffer,
        signature: new Uint8Array([6]).buffer,
        userHandle: null,
      },
    } as unknown as PublicKeyCredential;
    const json = credentialToJson(fake);
    expect((json.response as Record<string, unknown>).signature).toBe('Bg');
    expect((json.response as Record<string, unknown>).userHandle).toBeNull();
  });
});

describe('webauthn — passkeyMoeglich', () => {
  it('liefert false ohne PublicKeyCredential im globalen Scope (Node-Testumgebung)', async () => {
    const { passkeyMoeglich } = await import('../src/portal/webauthn');
    expect(passkeyMoeglich()).toBe(false);
  });
});
