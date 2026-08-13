# Security and failure behavior

- Server configuration accepts only `127.0.0.1`; inbound Host values are restricted to loopback names on the configured port and browser Origin values must use HTTP, a loopback hostname, and that exact port.
- No analytics, telemetry, external fonts, CDNs, public assets, or commercial AI SDKs are present.
- Every resolved source path must remain beneath the configured OneDrive root. Filenames are basename-normalized and control/reserved characters are replaced. Windows device names are not rewritten directly, but every stored original receives a UUID prefix, so no saved basename can equal a reserved device name.
- Upload, attachment, extracted-text, retry, and evidence-package sizes are bounded by configuration.
- An ASGI request-size guard caps declared and streamed request bodies before unbounded multipart spooling; the file-level streaming check remains the authoritative source-size limit.
- State-changing browser requests require either the exact configured loopback Origin or the application's non-simple `X-Requested-With` header, preventing origin-less form submissions from mutating data.
- Capture uses a destination-local temporary file, verified hash, atomic rename, then database commit. Failure cleans up only that exact temporary file.
- SQL uses parameters for values. Dynamic query fragments come exclusively from code allowlists.
- HTML email is parsed as inert text; scripts, styles, iframes, objects, and embeds are discarded.
- Arbitrary originals use `Content-Disposition: attachment` and `application/octet-stream`; they are never rendered inline on the application origin.
- LLM redirects are disabled, host-checked, and TLS verification is mandatory. Keys are read only from the configured environment variable.
- Safe fixed error codes/messages are stored; model-supplied review text is not placed in source error fields. Source content, complete prompts/model responses, secrets, and attachments are not logged.
- AI cannot complete action items or projects and cannot mutate manual status/priority without an explicit reviewed user decision.
- Direct-project evidence is checked for project fit before its first memory commit. That endpoint call includes bounded source evidence plus the full local project roster (IDs, names, and SNOW numbers); low confidence or a better project match requires an explicit keep, move, or archive decision in Review Queue.
- Source removal preserves the original package and hashes in the managed Archive while excluding its chunks and derived records from active retrieval. Package moves are constrained beneath the configured OneDrive root and collision checked.
- Append-only triggers protect project updates, routing-rule history, and source lifecycle events from ordinary update/delete operations.
- Read connections are closed deterministically after each service/query context; write transactions also close after commit or rollback.

The acceptance suite covers hostile Host/Origin, traversal-shaped names, oversized capture cleanup, unsupported/scanned files, uncited model output, project-only retrieval, exact-once retry, attachment downloads, and manual closure protection.
