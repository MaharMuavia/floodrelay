# Decision records

Short notes on choices that are not obvious from the code, and on the ones that
were forced by circumstance.

---

## 1. Urgency is arithmetic, not a model output

**Decision.** `services/scoring.py` computes urgency with a fixed weighted
formula. The model is asked only to explain the resulting number in words, and is
explicitly told in `prompts/triage.md` that the score is not its to change.

**Why.** A coordinator has to be able to disagree with a priority. If the number
comes from a model, disagreeing means arguing with a black box; if it comes from
a formula, they can see the five terms and check the one they doubt. It is also
the difference between a demo and something anyone would run: the same message
always produces the same number.

**Consequence.** Photo severity and rainfall feed the *explanation* and, for the
photo, one weighted term — but never the ranking directly.

---

## 2. Model output is grounded against the source text

**Decision.** `nodes/extract.py` re-checks every count and boolean the model
returns. A number must appear in the message (as digits, or as an English or
Roman Urdu number word); `pregnant: true` requires a word meaning pregnant.
Ungrounded values are dropped to `null`, with the reason recorded.

**Why.** Observed, not hypothetical. The local model returned `pregnant: 1` for a
message that never mentions pregnancy, and `0` for "not stated" — both inflate
the vulnerability term and can push a calm request above a genuine rescue. It
also classified a roof rescue as `other`, which would have prevented gate rule 1
from firing at all.

**Consequence.** A small, cheap, local model becomes usable here. It can be
wrong, but it cannot be wrong *and* believed. There is a keyword backstop that
forces `rescue` when a message describes people trapped or on a roof, and it
deliberately wins over the "this is an offer of help" rule — a false rescue costs
attention, a missed one costs more.

---

## 3. The forward pass is a Strands `Graph`; resume is not

**Decision.** `agent/forward_graph.py` builds a real `strands.multiagent.Graph`
for `extract -> geolocate -> dedupe -> triage -> match -> gate`. Every arrow is a
`GraphEdge`, the retry from `geolocate` back to `extract` is a genuine cycle with
a condition on it, and the dedupe/match handback is another. `gate` has no
outgoing edges, so a run that raises a `DecisionCard` terminates there.

`Pipeline.resume_after_decision` is **not** a graph, and that is the considered
part of this decision.

**Why the split.** Human-in-the-loop is inherently multi-invocation. A
coordinator may answer in ten seconds or in ten minutes, from a different
process, after a restart. Expressing "wait for a human" inside a graph run would
mean a node blocking while holding the pipeline lock, a worker thread and an open
store connection — and would put the one code path that can reach `roster.assign`
behind a second layer of orchestration. The halt is what makes the gate safe to
hold, so the halt is modelled as the graph ending rather than as the graph
waiting. Resume re-enters at `match` with the coordinator's answer in state.

**What this replaced.** Until this change the topology ran as explicit Python and
this section argued that Strands' multi-agent primitives could not be used,
because the local models available (`deepseek-r1:7b`, `phi3:mini`) advertise
completion only and Graph orchestrates *agents that call tools*. That reasoning
was sound and is now obsolete: with a tool-calling provider configured
(§18) there are real tool-calling agents to orchestrate, so the topology moved
onto the SDK.

**The check that it stayed honest.** `scripts/e2e.py --no-model` produces
byte-identical output before and after the move, and `test_forward_graph.py`
pins the visit order for every branch — the straight line, one retry, two
retries, a duplicate at either dedupe pass, and the conflict handback — against
the real `Graph`, plus a guard that fails if `gate` ever gains an outgoing edge.

**Cost, stated plainly.** A cyclic graph has no natural termination count, so
`set_max_node_executions(24)` is a backstop against a future edit breaking one
of the two loop conditions. The SDK marks an over-budget run `FAILED` and returns
quietly rather than raising, which would leave a request stuck in `processing`
with nothing to answer, so `run_forward` turns that into an exception. `Swarm` is
still not used: this pipeline is a fixed sequence with two conditional edges, and
handing that to a self-organising swarm would replace a topology a coordinator
can read with one they cannot.

---

## 4. DynamoDB items store a JSON body, not attribute-per-field

**Decision.** Each item carries its payload as a JSON string in `body`, with only
the attributes used for indexing (`gsi1pk`, `gsi1sk`, `status`) promoted to
top-level scalars.

**Why.** DynamoDB coerces floats to `Decimal`. Urgency scores round-tripping
through `Decimal` and back is exactly the kind of silent numeric drift this
project cannot afford, and the alternative — converting every float on the way in
and out — is a defect surface for no benefit at this scale.

**Consequence.** Filtering on non-indexed fields happens in Python. Fine for a
district's traffic (tens of requests), not for a national deployment.

---

## 5. GSI1 sorts on inverted urgency

`gsi1sk` is `f"{1.0 - urgency:.4f}#{id}"`. Zero-padded so lexicographic order
matches numeric order, and inverted so that the board's "most urgent first" query
is a plain forward scan rather than a descending one.

---

## 6. Geocoding results are cached permanently, including empty ones

**Decision.** `store/geocache_repo.py` never evicts. An empty candidate list is
cached as a real answer, distinct from a cache miss.

**Why.** Nominatim runs on donated infrastructure and asks for one request per
second. A place that resolved yesterday resolves the same today, and re-asking an
unresolvable location on every run is precisely the behaviour the usage policy
exists to prevent. It is also what makes the demo offline-safe.

**Verified trap.** Nominatim returns **HTTP 403** for a User-Agent containing a
placeholder domain such as `example.org`. The default is now `FloodRelay/0.1`,
and the geocoder returns an error naming this cause if it sees a 403.

---

## 7. Out-of-district geocodes are treated as low confidence

**Decision.** Results outside the configured `GEOCODE_VIEWBOX` get confidence
0.35 — below the gate floor — so they reach a human.

**Why.** Several Khyber Pakhtunkhwa villages share a name across districts: there
is a Mohib Banda in Nowshera and another in Mardan, about 40 km apart. Nominatim
ranks by prominence, not by where the coordinator is working, and returned the
Mardan one with a single confident match. A confidently wrong district is worse
than an ambiguous result, because nothing prompts anyone to question it.

---

## 8. Proximity from a shared place *name* is weak evidence in dedupe

**Decision.** When two requests were both geocoded from the same place name and
land on identical coordinates, that contributes 0.15 to the duplicate score, not
the 0.45 that genuine co-location earns. Headcount agreement was raised to 0.25
to carry the weight instead.

**Why.** Found by an end-to-end run, not by reasoning. A geocoded village name
resolves to one centroid, so two unrelated households in Pir Sabaq had identical
coordinates and were auto-merged — one genuine rescue call silently disappeared.
A false merge is the worst failure this system has: it does not surface anywhere,
because the request is simply gone.

**Consequence.** Coordinates that a caller sent themselves still count fully;
those are precise. Only geocoded-name coincidence is discounted.

---

## 9. One approval authorises exactly one dispatch

`DecisionCard` carries `consumed_at` / `consumed_by`, set the moment the gate
accepts it. A replayed card raises. This is beyond the brief, but an approval
that can be reused is not really an approval.

---

## 10. The pipeline is serialised behind one lock

Dedupe and contention detection both read the whole open request set. Two
concurrent runs race on it, and the failure mode is a missed duplicate or a
missed conflict — exactly the things this system exists to catch. Correctness
beats throughput at district scale.

---

## 11. Fixed a bug in the Strands Ollama provider at source, not by retrying

`strands/models/ollama.py` builds its metadata chunk as
`eval_count + prompt_eval_count` and `int(total_duration / 1e6)`, with no null
checks. Ollama returns all three fields as `null` on some responses, so the
provider raises `TypeError` and kills the whole agent invocation.

**The first attempt at this was wrong.** It assumed the nulls only appeared on
the model-load chunk, so `complete()` retried once on the theory that the second
attempt would find the model resident. That theory did not survive measurement:
the nulls recur on ordinary completions too, every retry hit the same failure,
and the retries at two nested levels multiplied. The visible symptom was a
**reproducible 657-709 second outlier** on one seed message across two
end-to-end runs, against 18-58 seconds for everything else.

`agent/ollama_compat.py` now subclasses the provider and coerces the missing
counts to zero. The same message completes in **47 seconds**. The retry in
`complete()` stays, but only for genuinely transient failures -- a fault that
recurs on every attempt turns a retry into nothing but doubled latency.

A `max_tokens` ceiling was added at the same time. Extraction needs ~150 tokens
and triage ~200; without a cap a small model can ramble for thousands, which at
~16 tok/s on CPU is minutes of wall clock for one request.

---

## 12. Amber means one thing only

`--signal` is used exclusively for things awaiting a human decision. Not for
warnings, not for connection trouble (that is grey, or red when offline), not for
hover. If amber is on screen, the coordinator has something to answer. It is the
strongest usability idea in the console and it only works if nothing else borrows
it.

---

## 13. PII redaction must catch inline signatures

The signature pattern originally required the dash to start a line. Real messages
do not cooperate. One seed message ends `"Koi boat bhejo jaldi. - Asif"` --
signed off mid-sentence, no newline -- and the name went straight through to the
model, the store and the audit log. It was caught by reading the stored text
after a live run, not by any test.

The anchor is gone. Because a bare dash before a capitalised word is a weak
signal, a short stop-list protects the common non-names (`Please`, `Urgent`,
`Thanks`, `Send`): redacting "Please" as a person corrupts the message the
coordinator has to read, which is its own kind of failure. `test_redaction.py`
covers both directions -- 29 cases, roughly half of them asserting that
something is *not* redacted.

---

## 14. A failed run must still leave something answerable

**Found by looking at a running board.** Three requests sat marked
`needs_decision` with no decision card behind them, because the Ollama server had
died mid-run. The queue showed amber "Needs you" on all three; there was nothing
to answer and no way to move them forward. They were simply stuck.

The failure path set the status and stopped there. It now writes a
`processing_failed` card offering "Try again" or "Handle this one myself".

**Why this matters more than the bug.** The console's colour scheme rests on one
promise: if amber is on screen, there is something to answer (see #12). A status
that claims to need a human without giving them anything to do breaks that
promise silently, and the coordinator learns to distrust the colour. There is now
an invariant test: every `needs_decision` request must have an open card.

---

## 15. The triage explanation is grounded too, not just the extraction

**Decision.** An explanation that cites evidence the request does not have --
a photo when no photo was attached, rainfall when no weather was fetched -- is
rejected, and the deterministic sentence is used instead.

**Why.** Observed on a live run. For a text-only message the model wrote
*"with recent photographs showing them on the ground"*. No photograph existed.

The urgency number was unaffected, because it is arithmetic (#1). But the
sentence beside it is what a coordinator actually reads before committing the
only boat, and fabricated evidence there is worse than a plain description. The
same reasoning that grounds extraction (#2) applies to prose: the model may
phrase the answer, it may not invent the facts.

---

## 16. Mona Sans carries the interface

**Decision.** One variable family for headings, body and numerals, with IBM Plex
Mono retained only for data compared character by character.

**Why.** Mona Sans has a width axis as well as a weight axis. The urgency column
runs at 92% width so a stack of scores reads as one tight block the eye can scan
without the type getting smaller; headings sit at 96%; prose stays at normal
width. Replacing the previous two-family pairing also halves the font requests,
which is not nothing for a coordinator on bad wifi.

---

## 17. The queue must survive a narrow window

Below the 1280px breakpoint the queue column carried only `min-h-0` while the map
had `min-h-[320px]`, so in a stacked single-column grid the queue collapsed to
zero height and the map filled the screen. The primary interface simply vanished
on any laptop narrower than 1280px, and it was invisible in testing because the
three-column layout was what got looked at.

Each column now has an explicit minimum height below the breakpoint, and the
queue -- the thing the console is for -- gets the most room and stays first in
source order.

---

## 18. The model chooses the tools; Python still owns the numbers

**Decision.** With `MODEL_PROVIDER=bedrock` or `anthropic`, `extract`,
`geolocate` and `triage` run a real `strands.Agent` with `@tool` functions bound
to it, and the model decides which to call. `agent/tool_agent.py` is the only
place such an agent is built, and it always attaches the same three hooks:
`HumanGateHook`, `AuditLogHook`, `PIIRedactionHook`.

**Why this needed saying out loud.** Before it, every `@tool` function in this
repository was decorated as a tool and then called from Python around a
completion-only model. The decoration was true and the tool use was not, which
meant the human gate hook -- the single most important thing here -- had never
once fired on a tool call an agent actually chose to make.
`test_tool_agent.py` now drives a real `Agent` into `roster_assign` over a
scripted model and asserts the refusal happens *before* the tool body runs, with
no provider, no credentials and no network.

**What the model is not given.** Geolocation confidence is still computed by
`_point_from` from the candidate list the tool returned, never from the model's
prose about it — letting the model pick *which* place to look up is useful,
letting it pick how confident we are is not. Urgency is computed before the
model is asked anything at all, so nothing a tool returns can move a request up
the queue (#1). `roster_assign` is deliberately absent from the triage agent's
tool list: the gate would refuse it anyway, but an explanation has no business
holding a tool that dispatches a boat.

**Found while wiring it.** Raising out of a `BeforeToolCallEvent` callback stops
the tool, which is the job — but Strands wraps the exception in an
`EventLoopException` before the caller sees it. A caller matching on
`GateViolation` by type would therefore report the gate working correctly as a
crash, and show the coordinator a `processing_failed` card instead of the truth.
`caused_by_gate()` walks the cause chain, and the tests pin it.

**Cost.** Two providers now differ in more than latency, so `/healthz` and the
About screen report which one is live and whether tool-calling is active, rather
than leaving an operator to infer it from the config. The Ollama path remains
supported and documented, with `OLLAMA_TOOL_CALLING` for local models that do
support tools.
