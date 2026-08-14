import { useCallback, useEffect, useState } from "react";
import { backend } from "../../api/backend";
import type { ConfigurationStatus, LlmHealth } from "../../api/contracts";
import { Notice } from "../../components/Feedback";
import { PageHeader } from "../../components/PageHeader";

export function SettingsPage() {
  const [configuration, setConfiguration] = useState<ConfigurationStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [health, setHealth] = useState<LlmHealth | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"save" | "remove" | "health" | "">("");
  const load = useCallback(async () => setConfiguration(await backend.configuration.get()), []);
  useEffect(() => { load().catch(value => setError(value.message)); }, [load]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy("save"); setNotice(""); setError(""); setHealth(null);
    try {
      await backend.configuration.saveCredential(apiKey);
      setApiKey("");
      await load();
      setNotice("API key encrypted for this Windows account. The key itself is never displayed by the app.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  async function remove() {
    if (!confirm("Remove the locally saved GenAI.mil API key? AI features will stop unless GENAI_API_KEY is set.")) return;
    setBusy("remove"); setNotice(""); setError(""); setHealth(null);
    try {
      await backend.configuration.removeCredential();
      await load();
      setNotice("The locally encrypted API key was removed.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  async function testHealth() {
    setBusy("health"); setNotice(""); setError(""); setHealth(null);
    try {
      const result = await backend.configuration.testHealth();
      setHealth(result);
      if (!result.ok) setError(result.error || "GenAI.mil health check failed.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  const internal = configuration?.llm_adapter === "internal";
  const sourceLabel = configuration?.credential_error
    ? "Saved credential unavailable"
    : configuration?.api_key_source === "environment"
      ? "GENAI_API_KEY environment override"
      : configuration?.api_key_source === "encrypted_local" ? "Encrypted local key" : "Not configured";

  return <>
    <PageHeader eyebrow="Local AI connection" title="GenAI.mil settings" />
    <div className="settings-page">
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
      {error && <Notice error onDismiss={() => setError("")}>{error}</Notice>}
      <section className="panel settings-card">
        <header><div><small>OpenAI-compatible endpoint</small><h2>API connection</h2></div><span className={`connection-state ${configuration?.api_key_present ? "ready" : ""}`}>{configuration?.api_key_present ? "Configured" : "Key required"}</span></header>
        {!configuration ? <p className="loading">Loading configuration…</p> : <div className="settings-facts">
          <div><small>Endpoint</small><strong>{configuration.llm_endpoint || "Fake test adapter"}</strong></div>
          <div><small>Routine model</small><strong>{configuration.llm_model}</strong></div>
          <div><small>Judgment model</small><strong>{configuration.llm_judgment_model}</strong></div>
          <div><small>Credential</small><strong>{sourceLabel}</strong></div>
        </div>}
        <form className="credential-form" onSubmit={save}>
          <label>GenAI.mil API key<input type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={configuration?.api_key_present ? "Paste a replacement key" : "Paste API key"} disabled={!internal || Boolean(busy)} /></label>
          <div className="settings-actions">
            <button className="button primary" disabled={!internal || !apiKey.trim() || Boolean(busy)}>{busy === "save" ? "Encrypting…" : "Save encrypted key"}</button>
            <button type="button" className="button" onClick={remove} disabled={!internal || !configuration?.api_key_local_saved || Boolean(busy)}>{busy === "remove" ? "Removing…" : "Remove saved key"}</button>
          </div>
        </form>
        <p className="security-note">Saved keys are protected by Windows DPAPI for your current Windows account and stored outside OneDrive. A <code>GENAI_API_KEY</code> environment variable takes priority and cannot be removed from this page.</p>
      </section>
      <section className="panel settings-card">
        <header><div><small>Live request</small><h2>API health</h2></div>{health && <span className={`connection-state ${health.ok ? "ready" : "failed"}`}>{health.ok ? "Healthy" : "Unavailable"}</span>}</header>
        <p>This sends a small JSON-only request to GenAI.mil using the routine model. It does not send project data.</p>
        {health?.ok && <div className="health-result"><strong>Connection succeeded</strong><span>{health.model_id}{health.latency_ms !== undefined ? ` · ${health.latency_ms} ms` : ""}</span></div>}
        <button className="button" onClick={testHealth} disabled={!internal || !configuration?.api_key_present || Boolean(busy)}>{busy === "health" ? "Testing…" : "Test API health"}</button>
      </section>
    </div>
  </>;
}
