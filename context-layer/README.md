# Assessment Test Recommender (context-layer)

A self-contained, semi-deterministic service that recommends **which objective tests to run** for a
patient — derived from their intake form — and surfaces them on the dashboard via a **"customer" icon**
(parallel to the existing clinician-agent "robot" icon). Runs on the **customer-agent EC2**.

See `ARCHITECTURE.md` for the full design, boundaries, and loopholes.

## Pipeline (per request)
```
customer-info.form_data
   → ConditionClassifier   (deterministic; category/keywords)
   → CandidateRetriever    (deterministic; tests_pool.json[objective+learned] resolved vs
                            objectiveAssessments  +  VALD from vald-exercise-details;
                            hard contraindication filter)
   → Ranker                (LLM Flash temp=0 if enabled: ranks + PROPOSES net-new tests;
                            else deterministic priority order)
   → Validator             (catalog/VALD ⊆ pool; proposals flagged needs_review)
   → pool.add_learned_test (LEARNING: persist proposals into tests_pool.json)
   → RecoWriter            → customer_reco_form_data  { objectiveAssessment: { tests: [...] } }
```
One LLM call only when intake/condition/pool changed (cached via `assessment_context.sourceFormHash`).
`USE_LLM_RANKER=false` (default) runs fully deterministic — zero LLM cost, no proposals.

### The growing pool (`data/tests_pool.json`)
Three test sources: (1) `objectiveAssessments` catalog, (2) VALD `vald-exercise-details`, (3)
**agent-learned** tests. When the LLM proposes a test the pool lacks (for a condition/muscle/joint),
it's appended to `tests_pool.json` (`status: proposed`) so it becomes a deterministic candidate next
time — the pool grows and LLM usage drops. A clinician promotes `proposed → approved`; proposals are
always flagged `needs_review` and never auto-added to the clinical catalog.
Run `python -m scripts.validate_pool` to check pool names vs the catalog and list pending reviews.

## Run locally
```bash
cd context-layer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in MONGO_URI (stance-dashboard cluster)
python -m scripts.validate_pool         # check data/tests_pool.json vs the live catalog
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload
```

## API
| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | Liveness + Mongo status |
| GET  | `/recommendations?userId=&appointmentId=[&reportId=][&refresh=false]` | Get recommended tests |
| POST | `/recommendations/refresh?userId=&appointmentId=` | Force regeneration |

Auth: send `X-API-Key: <RECOMMENDER_API_KEY>` (enforced only when the env var is set).

Response shape (drop-in for the dashboard's `mapAgentFormDataToAssessment`):
```json
{
  "status": "generated|cached|no_condition|no_tests",
  "condition": "knee_pain",
  "poolVersion": "TRR:...",
  "objectiveAssessment": { "tests": [ { "objectId": "...", "testName": "McMurray's Test",
    "exerciseId": "...", "unitName": "", "value": null, "left": null, "right": null,
    "dataType": "POSITIVE_NEGATIVE", "calculationType": "POSITIVE_NEGATIVE",
    "testDetail": "...", "_recoRank": 1, "_recoConfidence": 0.9, "_recoRationale": "..." } ] }
}
```

## Frontend "customer" icon (dashboard timeline — the only dashboard change)
**The click is the trigger** — generation is lazy: clicking the icon calls the endpoint, which runs
the pipeline and returns the tests, which the handler applies to the form. Nothing runs beforehand.
Mirror the robot icon in `timeline/page.tsx` Actions column:
```tsx
// next to <BotIcon .../>
<div onClick={async (e) => {
  e.stopPropagation();
  const r = await fetch(
    `${RECOMMENDER_URL}/recommendations?userId=${patientId}&appointmentId=${row.appointment._id}&reportId=${row._id}`,
    { headers: { 'X-API-Key': RECOMMENDER_KEY } },
  ).then(res => res.json());
  // reuse the existing mapper + NewReportForm agentReportData prop:
  setCustomerRecoDataMap(prev => ({ ...prev, [row._id]: mapAgentFormDataToAssessment(r) }));
}} title="Get patient-derived test recommendations">
  <UserIcon className="w-5 h-5" />
</div>
```
The recommender never writes `reports` / `AgentReport` / `clinician_agent_form_data` — applying is
always an explicit clinician action, and saving stays the dashboard's existing flow.

## Deploy (customer-agent EC2)
```bash
cd context-layer
docker compose -f deployment/docker-compose.yml up -d --build
curl -sf http://localhost:8002/health
```
Or paste the `assessment-recommender` service block into `healthflex-agent/docker-compose.yml`.

## Tests
```bash
python -m pytest tests/ -v          # deterministic pipeline, no DB required
```
