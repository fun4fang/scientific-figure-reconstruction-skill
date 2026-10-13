# Optional zero-tool API Authoring

This advanced route is **optional** and is not needed for the default Codex
Host-Managed Skill. Its dedicated model has a different capability boundary:
no host tools/instruction discovery or Codex login. The existing offline
request/file/tool security tests remain valid.

Authoring needs a separately supplied API key in `UF_OPENAI_API_KEY` and an
explicit `--model` available to that API account for image inputs and strict
structured outputs. No default model or fallback exists. Current Codex login
cannot be used. API calls may incur separate charges. The prior gpt-5.6-luna
Codex result does not prove API availability/account billing eligibility.
No additional Python/Node dependency is required: this is a standard-library
HTTPS/TLS/JSON client. API/model compatibility is NOT_VERIFIED: no API requests
were made in security closure. Account/model/cost selection and live testing
need separate user authorization. Offline Quick Start needs no key.

After that authorization, supply the dedicated key privately to this process
and invoke (never commit or log the key):

```powershell
.\.venv\Scripts\ufigure.exe author --workspace . --image examples/workflow.png --out runs/api-demo --model YOUR_AUTHORIZED_API_MODEL --allow-remote
```

The fixed destination is `https://api.openai.com/v1/responses`: verified TLS,
no redirects, proxy/base-URL overrides, retries or weaker fallback. One initial
request and one explicit `--correct-once` request maximum per job. An attempted
request spends its slot even on failure; a new job is not permission for more
calls than the user authorized. No schema-format retry is performed.

The body contains only the explicit model, fixed reconstruction instructions,
public Proposal schema, metadata-stripped source pixels and fixed controls:
`tools=[]`, `tool_choice="none"`, `parallel_tool_calls=false`, `store=false`,
`stream=false`, 8192 max output tokens. There is no Agent loop/tool dispatcher,
shell/file/web/MCP tool implementation, remote file ID or server conversation
reference. Authentication goes in the HTTP header, never model content.

Correction sends that source and the two actual rendered images plus the
validated previous proposal as untrusted **user data**, never instructions.
Local paths, host documents and Codex auth are not sent. Image text cannot
grant tools or alter client read/destination scopes. Tool/unknown output items,
refusals, incomplete responses, bad JSON/schema/identities fail closed without
dispatch. Scientific geometry/relation/content validations remain mandatory;
valid JSON is not verified scientific truth.

Limits: existing input budgets, at most three images, 70 MiB serialized request,
4 MiB response, 180-second transport deadline within the CLI job deadline.
Attempts record digests/dimensions/model; raw responses, headers and keys are
not logged. Proposals/renders remain sensitive. `store=false` is a request
setting, not a zero-retention promise; check Provider account data policies.
All job directories should stay private. Installation/offline realization
does not call a model or upload figures.

Official references: [API key setup](https://developers.openai.com/api/docs/quickstart),
[image inputs](https://developers.openai.com/api/docs/guides/images-vision),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[pricing](https://developers.openai.com/api/docs/pricing).
