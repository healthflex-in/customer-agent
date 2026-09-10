# Assessment Test Recommender — Context Layer

**Status:** Design / proposal (grounded in customer-agent, stance_dashboard API + frontend, clinician-agent)
**Owner:** Healthflex / Stance Health
**Last updated:** 2026-08-10

A separate, low-cost, **semi-deterministic** agent that, per patient/appointment, derives the
condition from the intake form and recommends **which objective tests to run** (plus prefillable
data). Its output is surfaced on the dashboard by a **new "customer" icon** sitting next to the
existing **robot icon**, mirroring that flow. It runs as a **new service on the customer-agent EC2**.

The agent is a single stateless worker; state is **persisted per-user/appointment context docs**,
and the active context is *switched* when a clinician opens a different patient.

> **System boundary (important).** This whole layer — pipeline, storage, and API — lives on the
> **customer-agent EC2**, NOT inside the stance dashboard codebase. The dashboard's *only* touchpoint
> is a thin FE "customer" icon that calls **our** REST API. No dashboard-API/backend changes, no
> business logic in the dashboard. Everything the recommender knows and produces is owned and served
> by us; the dashboard just renders what our endpoint returns and lets the clinician apply it.

---

## 1. How this fits the two AI sources that already exist

The dashboard assessments/timeline already has **two clinician-agent surfaces** per report row
(`stance-dashboard-frontend-work/.../timeline/page.tsx`, Actions column):

- **External-link icon** → opens `https://ai.stance.health/assessment/{patientId}/{appointmentId}`.
- **Robot icon (`BotIcon`)** → `fetchAgentReport(appointmentId, reportId)` runs GraphQL
  `GET_CLINICIAN_AGENT_FORM_DATA`, reads the **`clinician_agent_form_data`** collection (written by
  the **clinician-agent** service), maps it via `utils/agentFormDataMapper.ts`
  (`mapAgentFormDataToAssessment`) into `{ assessment: { objectiveAssessment: { tests: [...] }, ... } }`,
  and hands it to `NewReportForm` via the `agentReportData` prop. Clinician edits → saves → `Report`.

**This project adds a THIRD, parallel source — the "customer" icon** — driven by the *patient's own
intake data* (customer-agent) rather than the clinician. It does **not** touch `AgentReport` /
`clinician_agent_form_data` (that is clinician-agent's territory) and never writes to `Report`.

```
Actions column per report row:
  [↗ external]   [🤖 robot = clinician-agent]   [👤 customer = THIS service]  ← new
```

---

## 2. Reuse: the catalog and the form shape already exist

| Existing asset | Role here |
|---|---|
| **`objectiveAssessments`** (`stance_dashboard/.../objective-assessment/model.ts`) | Master **test catalog** = deterministic pool. testName, testDetail, dataType, unitName, normativeRangeMin/Max, calculationType. GraphQL `getAllTests`/`searchTests`. |
| **`ObjectiveTest`** shape (`reports/records/objective/model.ts`) | The per-test object the form consumes. For a *recommendation* we fill catalog metadata (`objectId`, `testName`, `exerciseId`, `unitName`, `dataType`, `calculationType`, normative fields) and **leave measurements (`value/left/right/positiveNegative`) empty** — clinician measures. `exerciseId` is just our identifier for which test (copy from catalog). |
| **`mapAgentFormDataToAssessment`** (FE mapper) | Already maps `objectiveAssessment: { tests: [...] }` → the form. Our payload uses the same shape, so we reuse the mapper (or a thin clone). |

### Three candidate sources (semi-deterministic pool)
1. **`objectiveAssessments`** catalog (manual special tests / ROM / strength) — resolved by name.
2. **`vald-exercise-details`** catalog (VALD instrumented tests: ForceDeck/ForceFrame/Dynamo), already
   tagged by `bodyPart`/`category` — pulled by mapping the condition to a body part.
3. **Agent-learned tests** — net-new tests the LLM proposes when the pool lacks something the condition
   needs; persisted and reused (see "learning loop" below).

**The core new asset — a growing, file-backed pool.** `objectiveAssessments` has **no condition tags**,
so the condition→test mapping lives in a version-controlled **`data/tests_pool.json`** (the source of
truth). It is the deterministic pool AND the learning memory:

```json
{ "version": 1, "conditions": {
  "knee_pain": {
    "objectiveTests": ["McMurray's Test", "Lachman Test", "Knee Flexion ROM"],
    "priorityByTest": { "McMurray's Test": 9 },
    "contraindications": ["acute_fracture_suspected"],
    "learnedTests": [ { "testName": "Ober's Test", "muscle": "ITB", "joint": "knee",
                        "priority": 5, "addedBy": "agent", "status": "proposed" } ]
  } } }
```

### Learning loop (pool grows over time)
When the LLM proposes a net-new test for a condition/muscle/joint, `app/pool.py::add_learned_test`
**appends it to `tests_pool.json`** (status `proposed`, deduped, atomic write). Next request it is a
**deterministic** candidate — so coverage grows and the LLM is needed less over time. A clinician
promotes `proposed → approved`; proposed tests are always flagged `needs_review` and never
auto-added to the clinical `objectiveAssessments` catalog.

---

## 3. Architecture

```
Clinician clicks the 👤 customer icon on a report row (patientId, appointmentId, reportId)
            │  REST → our service on the customer-agent EC2 (the only cross-boundary call)
            ▼
┌──────────────────────────────────────────────────────────────┐
│  Recommender service — FastAPI on customer-agent EC2 (new port)│
│    POST /context/activate {userId, appointmentId}             │
│    GET  /recommendations  •  POST /recommendations/refresh     │
└───────────────┬────────────────────────────────────────────────┘
     ┌──────────┴───────────┐
     ▼                      ▼
┌───────────────┐   ┌──────────────────────────────────────────────┐
│ ContextManager│   │             Recommender Pipeline              │
│ activate/save/│   │ 1. ConditionClassifier                        │
│ switch/evict  │   │    customer-info.form_data → condition(s)     │
│ LRU + lock    │   │    deterministic (category/keywords)→LLM fallb│
│               │   │ 2. CandidateRetriever (DETERMINISTIC)         │
│               │   │    tests_pool.json(objective+learned) + VALD  │
│               │   │    hard contraindication filter HERE          │
│               │   │ 3. Ranker (LLM Flash, temp=0) — rank + PROPOSE│
│               │   │ 4. Validator (catalog⊆pool; proposals flagged)│
│               │   │ 4b. pool.add_learned_test  (LEARNING LOOP)    │
│               │   │ 5. RecoWriter → { objectiveAssessment:{tests}}│
│               │   └──────────────────────────────────────────────┘
       │
       ▼
┌──────────── Storage ─────────────────────────────────────────────┐
│ FILE (ours): data/tests_pool.json  ← pool + learned tests (grows) │
│ Mongo READ:  customer-info(form_data), objectiveAssessments,      │
│              vald-exercise-details, appointments                  │
│ Mongo WRITE: customer_reco_form_data (NEW), assessment_context    │
│ NEVER: reports, AgentReport, clinician_agent_form_data,           │
│        objectiveAssessments (catalog is clinician-owned)          │
└───────────────────────────────────────────────────────────────────┘
```

### Components
- **ContextManager** — persisted context keyed by `(userId, appointmentId)`; `activate` loads/creates
  + LRU-caches; switching patients = `activate(other)`, previous stays persisted. Optimistic lock
  (`version`), form-hash invalidation.
- **ConditionClassifier** — reads `customer-info.form_data` (same cluster). Deterministic first
  (reuse `customer-info.category` / keyword map), Gemini Flash fallback on low confidence, cached by
  `hash(form_data)`.
- **CandidateRetriever (deterministic)** — condition(s) → `tests_pool.json` (objective names resolved
  vs the `objectiveAssessments` catalog + agent-`learnedTests`) **plus** VALD tests from
  `vald-exercise-details` (body-part match). **Contraindication hard-filter before the LLM.**
  Multi-condition → union+dedupe.
- **Ranker (LLM)** — Flash, temp=0. Orders the pool AND may **propose** up to `MAX_PROPOSED_TESTS`
  net-new tests (with muscle/joint) when the pool lacks something. No LLM → deterministic priority order.
- **Validator** — catalog/VALD picks must be ⊆ the pool (no hallucinated catalog tests); proposals are
  allowed but capped and flagged `needs_review`.
- **Learning step** — freshly proposed tests are appended to `tests_pool.json` (status `proposed`) so
  they become deterministic candidates next time. The pool grows; LLM usage drops over time.
- **RecoWriter** — build `ObjectiveTest[]` (metadata, empty measurements, `_source`/`_isProposed` tags) and upsert
  `customer_reco_form_data` as `{ objectiveAssessment: { tests: [...] }, ...optional prefill }` keyed
  by `(appointmentId, reportId|userId)` — the exact shape the FE mapper consumes.

---

## 4. New collections

**`customer_reco_form_data`** (FE read target — mirrors `clinician_agent_form_data`)
```json
{ "userId": ObjectId, "appointmentId": ObjectId, "reportId": ObjectId,
  "objectiveAssessment": { "tests": [ /* ObjectiveTest, measurements blank */ ] },
  "condition": "knee_pain", "poolVersion": "TRR@2026-08-10",
  "updatedAt": ISODate }
```
**`assessment_context`** (thin agent sidecar: provenance + cache control)
```json
{ "userId": ObjectId, "appointmentId": ObjectId,
  "condition": {"label":"knee_pain","confidence":0.86,"source":"deterministic"},
  "sourceFormHash": "sha1(form_data)", "candidateTestNames": ["..."],
  "provenance": [ {"testName":"McMurray's Test","rank":1,"confidence":0.8,"rationale":"..."} ],
  "version": 3 }
```

---

## 5. Delivery / integration — REST from our EC2 (the layer stays ours)

The layer is self-contained on the customer-agent EC2, so the contract is **our REST API**, not a
dashboard GraphQL query:

- **FE 👤 customer icon → our REST endpoint** (Delivery B).
  `GET https://<customer-agent-ec2>:8002/recommendations?appointmentId=..&userId=..` returns the
  `{ objectiveAssessment: { tests: [...] }, ... }` payload; the FE reuses `mapAgentFormDataToAssessment`
  + `NewReportForm`'s `agentReportData` prop to apply it. The **only** dashboard change is adding the
  icon + one fetch call — no dashboard-API/backend work.
- Requires **CORS** (allow the dashboard origin) + **auth** on our endpoint (PHI over the wire — reuse
  the dashboard's token / an API key; see §9).
- **Storage stays under our control.** We already have `MONGO_URI` to the shared `stance-dashboard`
  cluster, so `customer_reco_form_data` + `assessment_context` can live there for convenience — but
  the dashboard never reads those collections directly; it only ever calls our API. (If we later
  prefer full isolation, the store can move to a customer-agent-owned DB with zero FE impact, since
  the REST contract is the boundary.)

> A GraphQL-inside-the-dashboard variant (add `GET_CUSTOMER_RECOMMENDATION_DATA` to their API) is
> possible later if the dashboard team wants it native, but it pulls logic/coupling into the dashboard
> and is explicitly **not** what we're doing here.

**The click IS the trigger.** Nothing runs beforehand — there is no pre-warm/background job. When the
clinician clicks the 👤 icon, the FE calls `GET /recommendations`, which lazily runs the pipeline
(classify → retrieve → rank → write) and returns the payload; the FE then populates the form from the
response. Repeat clicks are served from cache (unchanged intake/condition/pool) so only the first click
pays the work; the icon's `refresh` forces a regenerate.

```
click 👤 → GET /recommendations → [classify→retrieve→(rank)→write] → payload → form populated
           (2nd click, nothing changed → cached payload, instant)
```

---

## 6. Deployment (customer-agent EC2)

New FastAPI container beside `customer-agent-prod` (port 8000) in `healthflex-agent/docker-compose.yml`,
e.g. `assessment-recommender` on port **8002**; volume-mounted for `update-fast.sh` code-only deploys;
add `/health`. Same `MONGO_URI` (stance-dashboard) + Gemini creds as customer-agent.

---

## 7. Cost & determinism controls
- Deterministic-first (classify + pool + contraindications, no LLM).
- **One LLM ranking call per material change**, not per icon click — skip when `sourceFormHash` +
  condition + `poolVersion` unchanged; serve cached doc.
- temp=0 + cache key `(condition, formHash, poolVersion)` → repeatable output.
- Prompt-cache the static pool text; Gemini Flash single-shot; no per-user agent process.
- Allow-list constrained → cheap + safe.

---

## 8. Build plan (each phase independently verifiable)
0. **Contracts (mostly confirmed):** same cluster ✓; `exerciseId` = catalog identifier ✓;
   AgentReport owned by clinician-agent → we use our own collection ✓; deploy on customer-agent EC2 ✓.
   Delivery = REST from our EC2 (layer stays ours) ✓. Remaining: confirm reportId vs userId key for `customer_reco_form_data`.
1. **`tests_pool.json`** + seed (start with knee_pain) mapped to real catalog testNames + contraindications; deterministic CandidateRetriever + VALD retriever + `validate_pool`. → verify: condition → safe candidate list (unit tests vs real catalog).
2. **ConditionClassifier** from `customer-info.form_data` (+reuse category), LLM fallback, cache. → verify: labelled sample accuracy.
3. **ContextManager + `assessment_context`** (activate/save/switch, LRU, lock, form-hash invalidation). → verify: switch two patients repeatedly; edits persist; stale writes rejected.
4. **Ranker + Validator + RecoWriter** → `customer_reco_form_data` in FE-compatible shape. → verify: doc maps cleanly through `mapAgentFormDataToAssessment` into the form; measurements blank.
5. **FE 👤 customer icon** → calls our REST endpoint (CORS + auth). → verify: click loads recommended tests into `NewReportForm`; nothing auto-saves to `Report`.
6. **Feedback / eval / monitoring**: accept/reject per test (what survives into saved reports), cost/patient, cache hit rate, classification accuracy → tune rules.

---

## 9. Loopholes & risks
**Clinical / safety**
- Clinical decision support → liability. Clinician-in-the-loop by construction (icon suggests; clinician edits + saves). Never write `Report`.
- **Contraindications = deterministic hard filters** before the LLM. Highest-severity failure if missed.
- **Comorbidity** → union pools or the battery under-tests.
- **First vs follow-up**: emit the correct slot; drive off `isFirstAssessment`/appointment.

**Two-AI confusion (new, important)**
- Now three sources feed one form (external AI page, 🤖 clinician-agent, 👤 customer). Clinicians can
  be confused or double-apply. Keep surfaces **clearly labelled and separate**; do **not** auto-merge
  robot + customer data; last-applied wins in the form, and applying is always explicit.

**The core gap**
- Everything rides on the curated **`tests_pool.json`** — needs a clinician owner. Under-curation → poor output.

**Learning loop (new)**
- **Unreviewed drift**: agent-proposed tests are auto-appended (`status: proposed`). They must stay
  flagged `needs_review` and NEVER auto-promote to `approved` or into the clinical `objectiveAssessments`
  catalog — a clinician promotes. Otherwise the pool silently fills with unvetted tests.
- **Proposal quality**: proposals are only as good as the LLM; cap them (`MAX_PROPOSED_TESTS`), keep temp=0,
  and require a periodic clinician review queue (the `pending review` list from `validate_pool`).
- **Pool poisoning / dedupe**: dedupe is name-based and case-insensitive; near-duplicate names ("Ober test"
  vs "Ober's Test") can still accrete → periodic dedupe/curation pass.

**Data quality / integration**
- Garbage-in from intake → wrong condition → wrong tests. Gate on confidence; fall back to a broad safe set + "confirm condition".
- **userId naming trap**: `customer-info.userId` vs `reports.patient` vs `appointments`/`agentReport.patient`. Normalize; try ObjectId then string.
- **Catalog drift**: pool objective names can dangle if `objectiveAssessments` changes → `validate_pool` on a schedule.
- **VALD body-part mapping** is heuristic (`condition → bodyPart` tokens); a bad map over/under-pulls VALD tests → keep the map curated alongside the pool.
- **Payload-shape drift**: if clinician-agent's `formData`/mapper shape changes, ours must track it → contract test against `mapAgentFormDataToAssessment`.

**Consistency / concurrency**
- Stale context: intake edited after caching → invalidate via `sourceFormHash`.
- **Pool file writes** are atomic (`os.replace`) + thread-locked, but multiple *containers* writing the
  same mounted file could still race → single writer, or move learned tests to a Mongo collection if we scale out.
- Concurrent clinicians / agent regen while clinician edits → optimistic lock; regen must not clobber the in-form draft (applying is explicit, so FE state is safe; the collection is advisory).

**LLM behavior**
- Non-determinism vs "semi-deterministic": temp=0 + cache. Validate emitted tests ⊆ pool (no hallucinations). Cost creep: never rank per click → change-detection + cache.

**Ops / compliance**
- New EC2 container: resource contention with customer-agent; give it its own port + health check + limits.
- Delivery B needs CORS + auth (PHI over the wire). Docs hold PHI → encryption at rest + access control.
- No eval loop = flying blind → build Phase 6, not "later". Scope discipline: recommends *which tests*, does not diagnose or fill measured values.

---

## 10. Remaining open questions
1. Auth mechanism for our REST endpoint (reuse dashboard token vs shared API key) + allowed CORS origins.
2. Key `customer_reco_form_data` by `reportId` (like robot flow) or by `(userId, appointmentId)` before a report exists?
3. Condition taxonomy: reuse `customer-info.category` values verbatim for `tests_pool.json` condition keys?
4. Who curates `tests_pool.json` + reviews the agent's `proposed` learned tests (which clinician, which surface)?
5. Should the 👤 icon also prefill non-test data (e.g., chief complaint/duration from intake), or tests only for v1?
6. At scale (multiple containers), keep the learning pool as a mounted file or move `learnedTests` to a Mongo collection?
