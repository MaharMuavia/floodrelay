# FloodRelay

A flood-relief coordination agent for a volunteer coordinator at a small local
relief organisation. Unstructured help requests arrive by web form, WhatsApp/SMS
webhook, or bulk paste. The agent extracts, geolocates, de-duplicates, scores
urgency, and matches each request to the nearest capable resource — on its own.

It stops and asks a human when a life-safety call, a doubtful location, or a
resource conflict needs judgement.

**The product is a console, not a chatbot.** There is no chat input. The
coordinator watches a board and answers decision cards.

---

## The one rule

**Nothing is dispatched to a responder without a human approving it.**

This is enforced in code, not in a prompt. `agent/hooks/human_gate.py` intercepts
every tool call; anything tagged dispatch-class raises `GateViolation` unless the
invocation carries a resolved `DecisionCard` whose chosen option is a dispatch
for that exact request and resource — and that card has not already been spent.

Two test files exist to keep it that way, and between them they assert that a
dispatch does **not** happen for: no card, an unresolved card, a "hold" answer, a
card for a different request, a card for a different resource, a replayed card,
and an unreachable datastore.

- `backend/tests/test_human_gate.py` covers the rule itself.
- `backend/tests/test_tool_agent.py` covers the rule **on the agent's own path**:
  a real `strands.Agent`, driven by a scripted model, decides on its own to call
  `roster_assign`, and the run is refused before the tool body is entered. That
  is the case that actually matters — a gate that only fires when Python calls it
  is not a gate on an autonomous agent.

---

## Status

### It works, and here is the shape of it

An unstructured message arrives. A **Strands `Graph`** runs
`extract → geolocate → dedupe → triage → match → gate`, with a real conditional
cycle back to `extract` when a location will not resolve and a real handback from
`match` to `dedupe` when two calls contend for one boat. With a tool-calling
provider configured, the model inside `extract`, `geolocate` and `triage`
**chooses and calls the `@tool` functions itself** — the geocoder, the rainfall
and river gauges, the national situation report — and every one of those calls
passes through the human gate, the audit log and the PII redactor.

The gate is where it stops. `rescue`/`medical`, a doubtful location, a resource
conflict, or an uncertain duplicate all end the graph run with a `DecisionCard`
and wait for a person. Nothing resumes until they answer.

What the model is never allowed to do is move the numbers. Urgency is computed by
a pure function before the model is asked anything; geolocation confidence is
computed from the candidate list the tool returned, not from the model's
description of it.

**Verification** (real output in [Verification](#verification) below): 367 tests,
`ruff` and `mypy --strict` clean, and `scripts/e2e.py` replaying seed messages
through the same code path the live routes use with `unapproved dispatches: 0`.

### Working and verified

| Area | State |
|---|---|
| Strands `Graph` for the forward pass | Working — six nodes, a conditional retry cycle, the dedupe/match handback, a terminal gate; 11 topology tests |
| Model-driven tool calling | Working — with `bedrock`/`anthropic` the model chooses and calls the `@tool` functions; the gate fires on that path, proven by 6 tests over a real `Agent` |
| Extraction (English, Urdu, Roman Urdu) | Working against a live local model |
| Grounding of model output | Working — counts and booleans are checked back against the message |
| Deterministic urgency scoring | Working, 20 unit tests |
| Geocoding with permanent cache + 1 req/s limit | Working against live Nominatim |
| District disambiguation | Working — out-of-district matches are flagged for a human |
| Dedupe | Working, 17 unit tests |
| Resource contention detection | Working, raised a real `resource_conflict` card in an end-to-end run |
| Gate rules and decision cards | Working |
| AgentCore Runtime contract | `POST /invocations` + `GET /ping` served and exercised under `MODEL_PROVIDER=bedrock`; 13 tests. Image not built — see the runbook |
| WhatsApp webhook signature | Working — HMAC-SHA256 over the raw body, fails closed when unconfigured; 9 tests |
| Human gate + audit + PII redaction hooks | Working — registered on every tool-calling agent, and verified firing there |
| REST API + SSE stream | Working, 43 smoke tests |
| Next.js console | Builds and runs; queue, map, activity feed, decision dock |
| PII redaction | Verified end to end — names and numbers become `PERSON_1` / `CALLER_1`, inline sign-offs included; 29 tests |
| Satellite layers (NASA GIBS) | Working against the live service — 10 layers, no API key; flood extent, true colour and IMERG rainfall drawn on the map with the published colour key |
| River discharge (GloFAS via Open-Meteo) | Working against the live service — real discharge on the Kabul river at Nowshera |
| NDMA daily situation report | Working against the live PDF — report number, date, province and district damage figures parsed; 21 tests |
| ReliefWeb situation context | **Repaired.** The v1 endpoint this shipped against was decommissioned and had been silently dead; now v2 with a keyless RSS fallback |
| GDACS global flood alerts | Working against the live feed — worldwide flood alerts by severity, with this country called out; keyless, 15-minute cache; 17 tests |

### Real data, and what it is not allowed to do

The forty help requests are synthetic and always will be. Everything around them
is live: NASA GIBS satellite imagery, GloFAS river discharge, NDMA's daily
national situation report, ReliefWeb headlines, and GDACS worldwide flood
alerts.

GDACS is the one source that answers a question local data cannot: whether this
district's flood is tracked internationally, and how it ranks. At the time of
writing it shows Pakistan Green (3 deaths, 60 displaced) against Nepal Red
(955 deaths) — a comparison that shapes what outside help is plausible.

None of it touches the urgency formula and none of it can authorise a dispatch.
That is not a convention — `test_scoring_imports_no_context_source` parses
`scoring.py` and fails if any context module appears in its imports, and
`test_compute_urgency_accepts_no_context_arguments` pins the signature.

Two limits are stated on screen rather than buried here:

- **Grey on a flood layer is "Insufficient Data", not dry ground.** Measured
  over Nowshera, grey covers 54–64% of a MODIS flood tile and every water class
  together covers under 0.3%. Read the wrong way round, the layer says the
  opposite of what is true, so the published colour key is rendered beside it.
- **250 m pixels describe areas, never households.** No satellite value is shown
  on an individual request, for the same reason photo severity is switched off.
- **A layer GIBS lists is not a layer GIBS serves here.** On 2026-09-03 six of
  the ten curated layers returned 404 over Nowshera while their capabilities
  entries all named that day. Each layer is therefore probed with one real tile
  when the manifest is built; the picker marks the ones with nothing to show and
  the map says so, rather than drawing an empty overlay that reads as "no
  flood".

`pdfplumber` is an optional extra (`uv sync --extra ndma`); without it the NDMA
tool reports that it is missing rather than failing obscurely. When NDMA
eventually changes the sitrep layout, the console will say which report it could
not parse and link the PDF — it will not fall back to an older report and it
will not show a zero.

### Not built, or built differently than the brief specified

- **Strands `Swarm` is not used.** `Graph` is (see above). A swarm is for agents
  that self-organise; this pipeline is a fixed sequence with two conditional
  edges, and handing it to a swarm would replace a topology a coordinator can
  read with one they cannot. See `docs/decisions.md` §3.
- **The default `.env.example` now names a hosted provider, and this machine has
  neither.** There are no AWS credentials here and no Anthropic key, so the
  tool-calling path has been exercised against a scripted model in
  `test_tool_agent.py` rather than against a live one. Set `ANTHROPIC_API_KEY`
  and the same code runs against a real model with no other change; `/healthz`
  reports which provider is live and whether tool-calling is active.
- **Ollama remains supported but is no longer the headline.** The local models
  available here (`deepseek-r1:7b`, `phi3:mini`) advertise `completion` only, so
  under Ollama the tools are called from Python around the model.
  `OLLAMA_TOOL_CALLING=true` turns the model-driven path on for a local model
  that does support tools (qwen2.5, llama3.1, mistral-nemo).
- **Photo severity is switched off.** No local model has vision. `score_photo`
  returns `available: false` with a reason and contributes *nothing* to the
  urgency score rather than inventing a number. `/healthz`, the About page and
  the request detail screen all say so.
- **The WhatsApp webhook's payload shape is unverified.** The *signature* is
  verified — HMAC-SHA256 over the raw body, and the route refuses everything when
  no `WEBHOOK_SECRET` is configured. What has never run against a real WhatsApp
  Business account is `normalise_payload`, which is written from the documented
  format rather than an observed one. Delivery receipts are not handled.
- **Default store is in-process memory.** DynamoDB single-table access is
  implemented and `DDB_ENDPOINT` switches to DynamoDB Local; the in-memory
  backend is what the tests and default config use. `/healthz` reports which.
- **Not deployed, but the runtime contract is met and was exercised.** Reading
  the AgentCore HTTP protocol contract against this repo found three things the
  original image would have failed on: no `POST /invocations`, no `GET /ping`,
  and no ARM64 platform pin. All three are fixed and pinned by 13 offline tests,
  and the app was run under the deployment environment
  (`MODEL_PROVIDER=bedrock`, `DEMO_MODE=false`) serving both paths — output in
  [`infra/agentcore/README.md`](infra/agentcore/README.md). OTel was confirmed
  activating when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

  What did **not** happen: the image was never built, pushed, or launched.
  Docker Desktop is installed but its Linux engine would not start here (the
  `docker-desktop` WSL distro stays `Stopped`), and there are no AWS
  credentials. The runbook marks every step ✅ executed, ⚠️ verified another way,
  or ❌ not executed. No deploy is claimed.
- **The architecture diagram is SVG, not PNG.** `docs/architecture.svg` is
  hand-drawn vector; the mermaid source in `docs/architecture.md` is kept as the
  machine-readable description. Rasterising to PNG needs a headless-Chromium
  download this machine's link could not justify.
- **No authentication and no multi-tenancy**, by design.

---

## Demo data

`backend/seed/requests.jsonl` holds **40 synthetic messages**, written by hand to
resemble published flood reporting from Nowshera district. They deliberately
include clean messages, Roman Urdu, messages with no location, two near-duplicate
reports of one household, two rescue calls that contend for the single boat, one
coordinates-only message, and one donation offer that is not a request at all.

**No real person, phone number, or address appears anywhere in this repository.**
The console shows a banner saying so whenever `DEMO_MODE` is on.

---

## Running it

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+
- A model provider (see below)

### Backend

```bash
cd backend && uv sync --extra dev --extra ollama
```

Create `backend/.env` from `.env.example`.

**For the model to call its own tools** — the configuration this is built
around — use a hosted provider:

```bash
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
DDB_ENDPOINT=memory
```

or `MODEL_PROVIDER=bedrock` with an AWS session. `/healthz` will report
`"tool_calling": "active"`, and the activity feed will show the agent choosing
tools rather than the pipeline calling them.

**Offline fallback.** No key and no AWS session still works, with the tools
called from Python around a completion-only local model:

```bash
MODEL_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11435
OLLAMA_MODEL_HEAVY=phi3:mini
OLLAMA_MODEL_LIGHT=phi3:mini
OLLAMA_TOOL_CALLING=false
DDB_ENDPOINT=memory
```

Set `OLLAMA_TOOL_CALLING=true` if the local model does support tools (qwen2.5,
llama3.1, mistral-nemo) and it takes the same model-driven path as the hosted
providers.

Then:

```bash
cd backend && uv run uvicorn floodrelay.main:app --port 8080
```

### Console

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000.

### Ollama note

If `ollama list` shows nothing despite models being on disk, check
`OLLAMA_MODELS`. The Ollama desktop app may launch its server with a models path
that omits `\.ollama\models`, which makes it report `total blobs: 0`. Running
`ollama serve` with `OLLAMA_MODELS` set correctly fixes it.

---

## Verification

The suite needs **no model provider, no credentials and no network** — the
tool-calling path is exercised against a scripted model, so `pytest` proves the
gate fires on the agent's own path without anything configured.

```bash
cd backend && uv run ruff check . && uv run mypy src && uv run pytest -q
```

Last run on this machine, verbatim:

```
All checks passed!                          # ruff
Success: no issues found in 69 source files # mypy
367 passed                                  # pytest
```

`mypy` is clean in exactly the install documented above. `pdfplumber` is an
optional extra, so it is listed under `ignore_missing_imports` — without that,
`uv run mypy src` fails for anyone who did not add `--extra ndma`.

```bash
cd frontend && npx tsc --noEmit && npm run build
```

Both clean; five routes build (`/`, `/about`, `/audit`, `/requests/[id]`,
`/_not-found`).

### End to end

```bash
cd backend && uv run python ../scripts/e2e.py             # curated subset
cd backend && uv run python ../scripts/e2e.py --full      # all 40 messages
cd backend && uv run python ../scripts/e2e.py --no-model  # deterministic, offline
```

It replays seed messages through the same `PipelineService` the live routes use
— there is no separate demo branch — and asserts that decision cards were raised
and that **nothing was dispatched without an approved card**.

Last `--no-model` run, which is the one that needs nothing configured:

```
replayed in 0.0s
decision cards raised: 6 ['low_confidence_location', 'possible_duplicate',
                          'low_confidence_location', 'low_confidence_location',
                          'low_confidence_location', 'low_confidence_location']
requests: 6
  needs_decision  6

unapproved dispatches: 0
open decisions awaiting a human: 6
PASSED: decisions raised, and nothing was dispatched without approval.
```

This output is **byte-identical** (bar the random request ids) before and after
the move to a Strands `Graph` — that is how the refactor was checked, rather than
asserted.

An earlier run with the local model on, for the per-message cost:

```
replayed in 244.2s
decision cards raised: 5 ['low_confidence_location', 'resource_conflict',
                          'resource_conflict', 'low_confidence_location',
                          'possible_duplicate']
unapproved dispatches: 0
PASSED: decisions raised, and nothing was dispatched without approval.
```

Per-message latency on this machine's CPU models runs 20-47s, with vague
messages costing more because they trigger the geolocate retry loop. Any hosted
provider removes that cost entirely.

---

## How urgency is computed

Urgency is **not** an LLM output. `services/scoring.py` is a pure function:

```
urgency = 0.40 * kind_weight        rescue 1.0, medical 0.9, shelter 0.6, food_water 0.4
        + 0.25 * vulnerability      children, elderly, disabled, pregnant — capped
        + 0.20 * photo_severity     0 when there is no photo, or no vision model
        + 0.10 * water_level_signal keyword signal, English and Roman Urdu
        + 0.05 * recency            decays to zero over six hours
```

The console shows this breakdown on the request detail screen. The same message
always produces the same number.

---

## Grounding: why a small model is safe here

The model reads language; it does not get the last word on facts. Every count and
boolean it returns is checked back against the message before being accepted — a
number must actually appear in the text, and `pregnant: true` must correspond to
a word meaning pregnant. Anything ungrounded drops to `null` and the reason is
recorded in the extraction confidence and the audit log.

This is load-bearing. During development the local model returned
`pregnant: 1` for a message that never mentioned pregnancy, and classified a
roof rescue as `other` — which would have stopped gate rule 1 from firing. Both
are caught by grounding, and both have tests.

---

## Architecture

See [docs/architecture.md](docs/architecture.md) and
[docs/decisions.md](docs/decisions.md).

---

## Licence

MIT — see [LICENSE](LICENSE).

Map data © OpenStreetMap contributors, used under the
[ODbL](https://www.openstreetmap.org/copyright). Geocoding by Nominatim,
rainfall by Open-Meteo, situation reports by ReliefWeb.
