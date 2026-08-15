import { useCallback, useEffect, useState } from "react";
import { backend } from "../../api/backend";
import type { ConfigurationStatus, LlmHealth } from "../../api/contracts";
import { Notice } from "../../components/Feedback";
import { PageHeader } from "../../components/PageHeader";
import { credentialDisplayState } from "./credentialStatus";

export function SettingsPage() {
  const [configuration, setConfiguration] = useState<ConfigurationStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [routineModel, setRoutineModel] = useState("");
  const [judgmentModel, setJudgmentModel] = useState("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [health, setHealth] = useState<LlmHealth | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"save" | "remove" | "models" | "health" | "">("");

  const load = useCallback(async () => {
    const current = await backend.configuration.get();
    setConfiguration(current);
    setRoutineModel(current.llm_model);
    setJudgmentModel(current.llm_judgment_model);
    setAvailableModels(previous => Array.from(new Set([
      ...previous, current.llm_model, current.llm_judgment_model,
    ])).sort((left, right) => left.localeCompare(right)));
  }, []);

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
    if (!confirm("Remove the locally saved GenAI.mil API key? AI features will stop unless the environment fallback is set.")) return;
    setBusy("remove"); setNotice(""); setError(""); setHealth(null);
    try {
      await backend.configuration.removeCredential();
      await load();
      setNotice("The locally encrypted API key was removed.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  async function saveModels(event: React.FormEvent) {
    event.preventDefault();
    setBusy("models"); setNotice(""); setError(""); setHealth(null);
    try {
      const saved = await backend.configuration.saveModels({
        routine_model: routineModel, judgment_model: judgmentModel,
      });
      setRoutineModel(saved.routine_model);
      setJudgmentModel(saved.judgment_model);
      await load();
      setNotice("Model choices saved and applied. No restart is required.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  async function testHealth() {
    setBusy("health"); setNotice(""); setError(""); setHealth(null);
    try {
      const result = await backend.configuration.testHealth();
      setHealth(result);
      if (result.available_models?.length) setAvailableModels(result.available_models);
      if (!result.ok) setError(result.error || "GenAI.mil health check failed.");
    } catch (value) { setError((value as Error).message); }
    finally { setBusy(""); }
  }

  const internal = configuration?.llm_adapter === "internal";
  const credentialState = configuration ? credentialDisplayState(configuration) : null;
  const sourceLabel = configuration?.credential_error
    ? "Saved credential unavailable"
    : configuration?.api_key_source === "environment"
      ? "GENAI_API_KEY environment fallback"
      : configuration?.api_key_source === "encrypted_local" ? "Encrypted local key" : "Not configured";
  const modelOptions = Array.from(new Set([
    ...availableModels, routineModel, judgmentModel,
  ].filter(Boolean))).sort((left, right) => left.localeCompare(right));

  return <>
    <PageHeader eyebrow="Local AI connection" title="GenAI.mil settings" />
    <div className="settings-page">
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
      {error && <Notice error onDismiss={() => setError("")}>{error}</Notice>}
      <section className="panel settings-card">
        <header><div><small>OpenAI-compatible endpoint</small><h2>API connection</h2></div><span className={`connection-state ${configuration?.api_key_present ? "ready" : ""}`}>{configuration?.api_key_present ? "Configured" : "Key required"}</span></header>
        {!configuration ? <p className="loading">Loading configuration...</p> : <div className="settings-facts">
          <div><small>Endpoint</small><strong>{configuration.llm_endpoint || "Fake test adapter"}</strong></div>
          <div><small>Routine model</small><strong>{configuration.llm_model}</strong></div>
          <div><small>Judgment model</small><strong>{configuration.llm_judgment_model}</strong></div>
          <div><small>Credential</small><strong>{sourceLabel}</strong></div>
        </div>}
        <form className="credential-form" onSubmit={save}>
          <div className="credential-field">
            <label htmlFor="genai-api-key">GenAI.mil API key</label>
            <input id="genai-api-key" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={configuration?.api_key_present ? "Paste a replacement key" : "Paste API key"} disabled={!internal || Boolean(busy)} />
            {credentialState && <div className={`credential-saved-state ${credentialState.tone}`} role="status" aria-live="polite">
              <span className="credential-state-dot" aria-hidden="true" />
              <span><strong>{credentialState.label}</strong><small>{credentialState.detail}</small></span>
            </div>}
          </div>
          <div className="settings-actions">
            <button className="button primary" disabled={!internal || !apiKey.trim() || Boolean(busy)}>{busy === "save" ? "Encrypting..." : "Save encrypted key"}</button>
            <button type="button" className="button" onClick={remove} disabled={!internal || !configuration?.api_key_local_saved || Boolean(busy)}>{busy === "remove" ? "Removing..." : "Remove saved key"}</button>
          </div>
        </form>
        <p className="security-note">Saved keys are protected by Windows DPAPI for your current Windows account and stored outside OneDrive. The key saved here is used first; <code>GENAI_API_KEY</code> is only a fallback when no local key is saved.</p>
      </section>
      <section className="panel settings-card">
        <header><div><small>Workload routing</small><h2>Model selection</h2></div></header>
        <p>Choose separate models for routine processing and higher-judgment routing. Refresh API health to renew this pick list from GenAI.mil.</p>
        {configuration?.model_preference_error && <Notice error>Saved model choices could not be read, so the packaged defaults are active.</Notice>}
        <form className="model-form" onSubmit={saveModels}>
          <label>Routine model<select value={routineModel} onChange={event => setRoutineModel(event.target.value)} disabled={!internal || Boolean(busy)}>{modelOptions.map(model => <option key={model} value={model}>{model}</option>)}</select></label>
          <label>Judgment model<select value={judgmentModel} onChange={event => setJudgmentModel(event.target.value)} disabled={!internal || Boolean(busy)}>{modelOptions.map(model => <option key={model} value={model}>{model}</option>)}</select></label>
          <div className="settings-actions"><button className="button primary" disabled={!internal || !routineModel || !judgmentModel || Boolean(busy)}>{busy === "models" ? "Saving..." : "Save model choices"}</button></div>
        </form>
      </section>
      <section className="panel settings-card">
        <header><div><small>Live request</small><h2>API health</h2></div>{health && <span className={`connection-state ${health.ok ? "ready" : "failed"}`}>{health.ok ? "Healthy" : "Unavailable"}</span>}</header>
        <p>This refreshes the model pick list, then sends a small JSON-only request with each selected model. It does not send project data.</p>
        {health?.ok && <div className="health-result"><strong>Both selected models responded</strong><span>Routine: {health.routine?.model_id || routineModel}{health.routine?.latency_ms !== undefined ? ` - ${health.routine.latency_ms} ms` : ""}</span><span>Judgment: {health.judgment?.model_id || judgmentModel}{health.judgment?.latency_ms !== undefined ? ` - ${health.judgment.latency_ms} ms` : ""}</span></div>}
        <button className="button" onClick={testHealth} disabled={!internal || !configuration?.api_key_present || Boolean(busy)}>{busy === "health" ? "Refreshing & testing..." : "Refresh models & test API"}</button>
      </section>
    </div>
  </>;
}
