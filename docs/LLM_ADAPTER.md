# LLM adapter

`FakeLlmAdapter` is deterministic and accepts only fictional test/demo evidence. It exercises the same citation validation and mutation path as production.

Outside the automated test harness, selecting the fake adapter also requires the explicit process-level opt-in `PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM=1`. Production configuration should always use `adapter = "internal"`.

`InternalHttpLlmAdapter` is the only production adapter. Its production defaults target `https://api.genai.mil/v1/chat/completions`, use `gemini-3.5-flash` for routine extraction/rewriting, and use `gemini-3.1-pro-preview` for multi-project routing and project-fit judgment. Configuration still controls the endpoint, models, authentication header/scheme, CA bundle, timeout, token limit, retry count, process-wide sliding-window rate limit, and evidence bound. It sends OpenAI-compatible JSON messages directly with `httpx` and contains no commercial provider SDK.

Controls:

- HTTPS and a stable configured host/port are mandatory.
- TLS verification is always enabled; an optional CA bundle replaces the trust path without disabling verification.
- Redirect following is disabled; an off-host redirect is explicitly rejected.
- A key entered in Settings is Windows-DPAPI encrypted under local application data and takes priority. `GENAI_API_KEY` remains a fallback when no local key is saved; neither status nor health responses contain the key.
- The Settings health action reads the same-host OpenAI-compatible `/v1/models` catalog with redirects disabled, then runs a small JSON-only probe for each selected model. Routine and judgment choices are written atomically under local application data, outside OneDrive, and apply immediately without a restart.
- Source text is delimited and labeled as untrusted evidence, with the JSON-only output contract repeated adjacent to the data.
- Requests use temperature `0.0`, explicit token bounds, and JSON Schema response format. An HTTP 400 caches a process-lifetime fallback to JSON Object mode.
- Successful content is accepted only from `choices[0].message.content`. The parser accepts a plain object, a fenced object, an object surrounded by prose, and invalid Windows-path backslashes without corrupting valid JSON escapes. Invalid output receives a corrective retry containing the bounded prior reply; there are never more than three attempts.
- Network failures, timeouts, HTTP 429, and 5xx responses use bounded 5/10-second backoff and honor `Retry-After` up to a 60-second safety ceiling so the worker cannot be stalled indefinitely by a hostile or mistaken header. Authentication and other permanent 4xx failures fail immediately. Every retry passes through the same thread-safe rate limiter.
- Optional multimodal requests use the supplied image MIME type in a base64 data URL, the routine model, and a 4,000-token response bound.
- Citation source/chunk pairs must exist in the exact supplied evidence package.
- The same bounded evidence list is used both for the model call and the citation allow-list; chunks are kept whole and a boundary chunk is never partially supplied. Oversized/non-fitting chunks are counted in source metadata and shown as a partial-evidence warning instead of being silently hidden.
- Source processing returns cited knowledge updates; validated updates become durable `knowledge_items` rather than being appended to prior summary prose.
- Before a direct-project source can create knowledge, the required `project_fit(selected_project, evidence, projects)` operation returns bounded confidence, a recommended project, a review decision, a reason, and citations restricted to the supplied evidence. This is a second LLM round trip for each first-pass direct upload and includes the full local project roster (IDs, names, and SNOW numbers) so the model can recommend a different destination. A review is required when selected-project confidence is below `0.45`, or when a different project is recommended with confidence of at least `0.65`; the source remains pending.
- The required `living_summary(project, knowledge)` adapter operation returns `{"sections": [...]}`. Every section has `section`, `text`, and one or more `knowledge_item_ids` drawn only from the supplied project knowledge. Unknown or wrong-project IDs reject the result.
- Living Summary generation always regenerates from non-flagged eligible knowledge. It never uses prior summary prose as evidence, and each successful run creates a version.
- Missing/foreign citations, malformed JSON, explicit uncertainty, or missing update fields block knowledge mutation and create review work.
- A substantive chat answer is rejected unless it contains at least one validated cited claim; an uncertainty string cannot excuse an uncited factual answer.
- Status/priority recommendations go to Review Queue; action closure requests always go to review.
- Logs and raised HTTP details do not include prompts, source bodies, keys, or model responses.

Open **Settings** and choose **Refresh models & test API**, or run `portfolio-assistant --config config.toml config-test --connect`. Both report a safe success/error without revealing the key or sending project data; the Settings action also refreshes the selectable model catalog and tests both chosen models.
