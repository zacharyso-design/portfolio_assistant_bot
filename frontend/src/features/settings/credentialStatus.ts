export type CredentialConfiguration = {
  credential_error: boolean;
  api_key_local_saved: boolean;
  api_key_source: string;
};

export type CredentialDisplayState = {
  label: string;
  detail: string;
  tone: "saved" | "fallback" | "missing" | "error";
};

export function credentialDisplayState(
  configuration: CredentialConfiguration,
): CredentialDisplayState {
  if (configuration.credential_error) {
    return {
      label: "Saved key unavailable",
      detail: "Remove the saved key and enter it again for this Windows account.",
      tone: "error",
    };
  }

  if (configuration.api_key_local_saved) {
    return {
      label: "API key saved on this device",
      detail: "Encrypted with Windows DPAPI. Paste a new key above to replace it.",
      tone: "saved",
    };
  }

  if (configuration.api_key_source === "environment") {
    return {
      label: "Environment fallback only",
      detail: "GENAI_API_KEY is active because no key is saved on this device.",
      tone: "fallback",
    };
  }

  return {
    label: "No API key saved",
    detail: "Paste a key above and choose Save encrypted key.",
    tone: "missing",
  };
}
