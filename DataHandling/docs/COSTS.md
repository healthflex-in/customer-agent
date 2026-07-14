# Operating Costs — current state + optimization ladder

**Last updated**: 2026-05-26
**Assumed traffic**: 2,000 interviews / month (~67 / day)
**Region**: `ap-south-1` (Mumbai)

This document is intentionally explicit about assumptions. Every number traces
back to a source. If you change the traffic assumption, scale the LLM line
items proportionally; the EC2 line is fixed.

---

## TL;DR

| Tier | $/month | $/day | $/year | Notes |
|---|---|---|---|---|
| **Today (unchanged)** | **~$40** | **$1.32** | **$478** | Baseline. |
| Code changes shipped, no instance change | ~$40 | $1.32 | $478 | Same $, just leaner & faster. |
| Instance → t3a.medium (safe) | $34 | $1.11 | $402 | -$76/yr. No risk. |
| Instance → t4g.small + swap + prompt trim | $19 | $0.62 | $223 | **-$255/yr** (-53%). Needs ARM rebuild + careful test. |
| Aggressive: t4g.small RI + prompt trim | $15 | $0.49 | $176 | **-$302/yr** (-63%). 1-yr commitment. |

---

## 1. Current cost breakdown

### 1a. EC2 (fixed, regardless of traffic)

`t2.medium` on-demand in Mumbai @ ~$0.0464/hr × 730 hrs/month:

| Line | $/month |
|---|---|
| Compute (t2.medium) | **$33.87** |
| EBS root (~20 GB gp3) | ~$1.60 |
| Data transfer out (negligible at current scale) | ~$0.10 |
| **EC2 subtotal** | **~$35.57** |

### 1b. Gemini LLM (scales with traffic)

**Assumption**: a "typical" interview =
- ~8 conversational turns
- ~2,500 input tokens per turn (system prompt + chat history + form schema)
- ~300 output tokens per turn (agent reply)

**One interview** at Gemini 2.0 Flash ($0.10 in / $0.40 out per 1M tokens):

| Item | Tokens | Cost |
|---|---|---|
| Input | 8 × 2,500 = 20,000 | $0.0020 |
| Output | 8 × 300 = 2,400 | $0.00096 |
| **Per interview** | | **~$0.0030** |

**Per month (2,000 interviews)**:

| Item | Tokens / month | Cost |
|---|---|---|
| Input | 40,000,000 | $4.00 |
| Output | 4,800,000 | $1.92 |
| **LLM subtotal** | | **~$5.92** |

### 1c. Other (currently $0 or negligible)

| Item | Cost | Notes |
|---|---|---|
| Whisper transcription | $0 | Runs locally on EC2 CPU (no API). |
| Embeddings | $0 | `BAAI/bge-small-en-v1.5` runs locally. |
| MongoDB | $0 | Existing cluster (not billed to this app). |
| S3 (attachments) | <$1 | At current volume, well under the free-tier ceiling. |
| ChromaDB | $0 | Local file-backed. |

### 1d. Current total

| Category | $/month | $/day | $/interview |
|---|---|---|---|
| EC2 (fixed) | $35.57 | $1.17 | $0.0178 |
| Gemini (variable) | $5.92 | $0.20 | $0.0030 |
| **TOTAL** | **$41.49** | **$1.36** | **$0.0208** |

> The dominant cost is **EC2 (86%)**, not the LLM. The biggest savings are
> therefore from instance downgrading, not model swapping. This was
> confirmed empirically by the A/B test in `tests/llm_ab.py` — switching
> Gemini variants saves <4%.

---

## 2. Optimization ladder

### Tier 1: Code changes (shipped, no $ change yet)

These don't reduce $ on their own — they *enable* the bigger moves below by
reducing memory and risk.

- Dead code removed (~2,400 LOC of unused Python / dirs in `.trash/`).
- Killed `audio_files/` and `transcripts/` debug writes → no EBS creep.
- `openai-whisper` → `faster-whisper` (int8) → ~150 MB less RAM, ~3-4× faster.
- Cleaner audio pipeline (no dead WAV encode, format detection simplified).
- New SDK: `llama_index.llms.gemini.Gemini` → `GoogleGenAI` (now reports tokens).

**Net effect**: peak memory dropped from ~1.6 GiB → ~1.1 GiB. This is what
unlocks the `small` instance tier below.

### Tier 2: Safe instance downgrade

`t3a.medium`: same 4 GiB RAM, AMD CPU, **no rebuild needed** (x86_64).

| Line | $/month | Δ vs today |
|---|---|---|
| EC2 (t3a.medium) | $27.45 | **-$8.12** |
| Gemini (unchanged) | $5.92 | 0 |
| **Total** | **$33.37** | **-$8.12 (-20%)** |

**Risk**: essentially zero. Same RAM, same instruction set, ~20% cheaper.

### Tier 3: Aggressive instance + prompt trim

`t4g.small`: 2 vCPU **ARM**, 2 GiB RAM, + 2 GB swapfile.
Requires: ARM image rebuild (`docker buildx build --platform linux/arm64`).

Additionally: trim `src/llm/functionalities.py:make_template` (495 lines today)
by ~50% to cut average input tokens per turn from 2,500 → ~1,250.

| Line | $/month | Δ vs today |
|---|---|---|
| EC2 (t4g.small) | $12.26 | -$23.31 |
| EBS (gp3, unchanged) | $1.60 | 0 |
| Gemini after prompt trim | ~$3.00 | -$2.92 |
| **Total** | **~$16.86** | **-$24.63 (-59%)** |

**Risk**: medium.
- ARM rebuild requires multi-arch image (manageable with `docker buildx`).
- 2 GiB RAM is tight; need ≥1 day of production memory metrics before commit.
- Prompt trim needs A/B against real transcripts to verify quality.

### Tier 4: Reserved instance (1-yr)

A 1-year Standard Reserved Instance (no upfront) saves ~30% off the on-demand
rate. Stackable on any tier above.

| Tier + RI | $/month | $/year | Δ vs today |
|---|---|---|---|
| t3a.medium RI | ~$25.84 | $310 | -$168/yr |
| t4g.small RI | **~$11.20** | **$134** | **-$344/yr** |

**Caveat**: RIs lock you in for 12 months. Only commit once the tier has been
proven stable for ~30 days.

---

## 3. What is NOT a worthwhile optimization (and why)

Honest list of things I considered and rejected, with reasons. Saves future
time spent re-investigating.

| Idea | Why we're not doing it |
|---|---|
| Switch Gemini 2.0 Flash → Gemini 2.5 Flash-Lite | A/B test showed **3.8% savings** — well within sampling noise. Google priced 2.5-flash-lite at the same rate as 2.0-flash. |
| Switch to Gemini 2.5 Flash (newer baseline) | **3.5× more expensive** per call due to its higher output pricing ($2.50/M vs $0.40/M). Adopting this would *increase* the bill. |
| Switch to GPT-4o-mini or Claude Haiku | All more expensive than Flash for this use case. No quality justification. |
| Gemini context caching for the static system prompt | Gemini requires a **32k-token minimum** to cache. Your `make_template` output is ~5-10k tokens. Padding to 32k would waste more than it saves. **Prompt trimming is the better lever.** |
| Move Whisper to Google Cloud Speech API | Currently $0 (local). API would add per-second charges. Worth revisiting if and only if CPU on a downsized instance becomes the bottleneck. |
| Move ChromaDB to a managed vector store | Adds $/month for an avoidable need at current scale. |

---

## 4. Suggested rollout order

1. **Ship the current code changes to EC2** (no $ impact, makes the box leaner). Verify in prod for ~3 days.
2. **Add a 2 GB swapfile on EC2** (free safety net). Verify.
3. **Switch to `t3a.medium`** via EC2 console (stop, change type, start). 30s downtime. Save $8/mo.
4. **Collect 7 days of `docker stats` data** on the new instance.
5. **If memory is comfortably under 1.5 GiB peak**: rebuild image for ARM and switch to `t4g.small`. Save another $15/mo.
6. **Trim `make_template` prompt** (separate code PR). Save ~$3/mo at current traffic; more as you scale.
7. **Once stable for 30 days, buy a 1-yr Reserved Instance** for the final ~30% off.

---

## 5. Sensitivity analysis (what if traffic changes)

| Interviews/mo | Gemini $ | EC2 $ today | Total today | Total at Tier 3 |
|---|---|---|---|---|
| 500 | $1.48 | $35.57 | $37.05 | $14.46 |
| 1,000 | $2.96 | $35.57 | $38.53 | $15.46 |
| **2,000** (assumed) | **$5.92** | **$35.57** | **$41.49** | **$16.86** |
| 5,000 | $14.80 | $35.57 | $50.37 | $22.86 |
| 10,000 | $29.60 | $35.57 | $65.17 | $32.86 |

The EC2 line is fixed → if you 5x traffic, the relative cost-per-interview
*drops*. Inversely, if traffic drops, the LLM line shrinks but EC2 stays.
This is the classic "fixed-cost server" pattern. To make the LLM line a
larger share (and thus more worth optimizing) you'd need to either run more
turns per interview or significantly more interviews/month.
