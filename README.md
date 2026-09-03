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

`backend/tests/test_human_gate.py` exists to keep it that way. Thirteen of its
cases assert that a dispatch does **not** happen: no card, an unresolved card, a
"hold" answer, a card for a different request, a replayed card, and an
unreachable datastore all raise. It is the most important test in the repository.

---

## Status: what is real, and what is not

This section is deliberately blunt. Nothing below is presented as working when
it is not.

### Working and verified

| Area | State |
|---|---|
| Extraction (English, Urdu, Roman Urdu) | Working against a live local model |
| Grounding of model output | Working — counts and booleans are checked back against the message |
| Deterministic urgency scoring | Working, 20 unit tests |
| Geocoding with permanent cache + 1 req/s limit | Working against live Nominatim |
| District disambiguation | Working — out-of-district matches are flagged for a human |
| Dedupe | Working, 17 unit tests |
| Resource contention detection | Working, raised a real `resource_conflict` card in an end-to-end run |
| Gate rules and decision cards | Working |
| Human gate + audit + PII redaction hooks | Working |
| REST API + SSE stream | Working, 28 smoke tests |
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

- **Model provider is Ollama, not Bedrock/Nova.** This machine had no AWS
  credentials and a ~50 KB/s link, which makes a Bedrock setup impractical and a
  4.7 GB model pull a 26-hour proposition. `get_model()` supports
  `bedrock | anthropic | ollama`; switching is one environment variable.
- **Strands `Graph` and `Swarm` are not used.** The local models available
  (`deepseek-r1:7b`, `phi3:mini`) advertise `completion` only — neither supports
  tool calling, and Strands' multi-agent primitives orchestrate agents that call
  tools. `agent/graph.py` implements the same topology explicitly: same nodes,
  the same conditional retry edge back to `extract`, the same dedupe/match
  handback, the same halting gate. Strands `Agent`, `@tool`, and the typed hook
  system **are** used. See `docs/decisions.md`.
- **Photo severity is switched off.** No local model has vision. `score_photo`
  returns `available: false` with a reason and contributes *nothing* to the
  urgency score rather than inventing a number. `/healthz`, the About page and
  the request detail screen all say so.
- **The WhatsApp webhook is untested.** `POST /intake/webhook` accepts the shape
  a Twilio/WhatsApp Business callback posts, but has never been run against the
  real provider. There is no signature verification.
- **Default store is in-process memory.** DynamoDB single-table access is
  implemented and `DDB_ENDPOINT` switches to DynamoDB Local; the in-memory
  backend is what the tests and default config use. `/healthz` reports which.
- **Not deployed.** No AgentCore Runtime deployment, no public URL, no
  CloudWatch. OTel wiring exists and activates when `OTEL_EXPORTER_OTLP_ENDPOINT`
  is set. `infra/agentcore/` holds a Dockerfile, a runtime config and the IAM
  notes — written, but **never executed**: no Docker daemon and no AWS
  credentials on the build machine, so the image has not even been built. A
  starting point, not a proven deployment.
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

Create `backend/.env` from `.env.example`. For the local setup this build was
verified with:

```bash
MODEL_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11435
OLLAMA_MODEL_HEAVY=phi3:mini
OLLAMA_MODEL_LIGHT=phi3:mini
DDB_ENDPOINT=memory
```

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

```bash
cd backend && uv run ruff check . && uv run mypy src && uv run pytest -q
```

Last run on this machine:

```
All checks passed!                          # ruff
Success: no issues found in 61 source files # mypy
236 passed                                  # pytest
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```

Both clean; five routes build.

### End to end

```bash
python scripts/e2e.py            # curated subset
python scripts/e2e.py --full     # all 40 messages (slow on CPU models)
```

It replays seed messages through the same `PipelineService` the live routes use
— there is no separate demo branch — and asserts that decision cards were raised
and that **nothing was dispatched without an approved card**. Last run:

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
