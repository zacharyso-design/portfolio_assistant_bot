import assert from "node:assert/strict";
import test from "node:test";

import { credentialDisplayState } from "./credentialStatus.ts";


test("shows when an encrypted API key is saved on this device", () => {
  assert.deepEqual(credentialDisplayState({
    credential_error: false,
    api_key_local_saved: true,
    api_key_source: "encrypted_local",
  }), {
    label: "API key saved on this device",
    detail: "Encrypted with Windows DPAPI. Paste a new key above to replace it.",
    tone: "saved",
  });
});


test("distinguishes an environment fallback from a locally saved key", () => {
  assert.deepEqual(credentialDisplayState({
    credential_error: false,
    api_key_local_saved: false,
    api_key_source: "environment",
  }), {
    label: "Environment fallback only",
    detail: "GENAI_API_KEY is active because no key is saved on this device.",
    tone: "fallback",
  });
});


test("shows when no API key is available", () => {
  assert.deepEqual(credentialDisplayState({
    credential_error: false,
    api_key_local_saved: false,
    api_key_source: "none",
  }), {
    label: "No API key saved",
    detail: "Paste a key above and choose Save encrypted key.",
    tone: "missing",
  });
});


test("gives an unavailable saved key precedence over other status", () => {
  assert.deepEqual(credentialDisplayState({
    credential_error: true,
    api_key_local_saved: true,
    api_key_source: "environment",
  }), {
    label: "Saved key unavailable",
    detail: "Remove the saved key and enter it again for this Windows account.",
    tone: "error",
  });
});
