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

## 3. Strands `Graph` and `Swarm` are not used; the topology is explicit

**Decision.** `agent/graph.py` runs the brief's topology in plain Python: the
same nodes, the same conditional retry edge from `geolocate` back to `extract`,
the same dedupe/match handback, the same halting gate. Strands `Agent`, `@tool`
and the typed hook system are used throughout.

**Why.** The models available on this machine (`deepseek-r1:7b`, `phi3:mini`)
advertise `completion` only — neither supports tool calling. Strands' Graph and
Swarm orchestrate agents that call tools; with these models they would be
orchestrating nothing. Building them anyway would have produced an impressive
call graph that could not actually run.

**Cost, stated plainly.** This gives up model-driven tool use, which the brief
values. `agent/tools/` is written and registered as real `@tool` functions, so
setting `MODEL_PROVIDER=bedrock` or `anthropic` makes that path live.

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
