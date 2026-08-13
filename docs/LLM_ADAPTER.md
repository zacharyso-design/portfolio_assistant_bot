# LLM adapter

`FakeLlmAdapter` is deterministic and accepts only fictional test/demo evidence. It exercises the same citation validation and mutation path as production.

Outside the automated test harness, selecting the fake adapter also requires the explicit process-level opt-in `PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM=1`. Production configuration should always use `adapter = "internal"`.

`InternalHttpLlmAdapter` is the only production adapter. Configuration controls the HTTPS base URL, chat path, model, authentication header/scheme, key environment variable, CA bundle, timeout, and evidence bound. It sends OpenAI-compatible JSON messages but contains no commercial provider SDK or provider-specific destination.

Controls:

- HTTPS and a stable configured host/port are mandatory.
- TLS verification is always enabled; an optional CA bundle replaces the trust path without disabling verification.
- Redirect following is disabled; an off-host redirect is explicitly rejected.
- Source text is labeled as untrusted evidence and all responses must be JSON objects.
- Citation source/chunk pairs must exist in the exact supplied evidence package.
- The same bounded evidence list is used both for the model call and the citation allow-list; chunks are kept whole and a boundary chunk is never partially supplied. Oversized/non-fitting chunks are counted in source metadata and shown as a partial-evidence warning instead of being silently hidden.
- Source processing returns cited knowledge updates; validated updates become durable `knowledge_items` rather than being appended to prior summary prose.
- The required `living_summary(project, knowledge)` adapter operation returns `{"sections": [...]}`. Every section has `section`, `text`, and one or more `knowledge_item_ids` drawn only from the supplied project knowledge. Unknown or wrong-project IDs reject the result.
- Living Summary generation always regenerates from non-flagged eligible knowledge. It never uses prior summary prose as evidence, and each successful run creates a version.
- Missing/foreign citations, malformed JSON, explicit uncertainty, or missing update fields block knowledge mutation and create review work.
- A substantive chat answer is rejected unless it contains at least one validated cited claim; an uncertainty string cannot excuse an uncited factual answer.
- Status/priority recommendations go to Review Queue; action closure requests always go to review.
- Logs and raised HTTP details do not include prompts, source bodies, keys, or model responses.

Run `portfolio-assistant --config config.toml config-test --connect` after production endpoint values are supplied. It reports a safe success/error and model ID without revealing the key.
