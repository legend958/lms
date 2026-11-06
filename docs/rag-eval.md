# RAG evaluation and sign-off

The study assistant is evaluated twice: an **offline, deterministic suite** that
runs in CI, and a **live spot-check** against the real stack (Chroma, MiniLM,
Groq, the seeded PDFs). Both must pass before release.

## Offline suite

`backend/eval/rag_cases.yaml` + `backend/eval/run_eval.py`.

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec -T api `
  python -m eval.run_eval --suite rag_cases.yaml
```

The harness is deliberately independent of the network:

- Builds the corpus in in-memory SQLite and a local cosine vector store.
- Uses a deterministic hashing embedder, so retrieval scores are reproducible.
- Runs the **real** `Retriever` and `validate_citations` code paths, including
  the Postgres re-check that overrides stale vector metadata.
- Generates answers from the retrieved set rather than calling Groq, so the
  suite measures retrieval, the gate, and citation validation — not model
  wording. Live generation quality is covered by the spot-check below and the
  Playwright journey.

### Corpus and case mix

| Property | Value |
|---|---|
| Cases | 41 |
| Answer cases (`hit@6`, citations) | 27 |
| Refusal cases (unreleased, deleted, failed, foreign course, invalid citation) | 14 |
| Cross-document cases (both sources required) | 5 |
| Prompt-injection cases | 1 |
| Stale-metadata probes (vector says released, Postgres disagrees) | included in the refusal set |

The corpus contains released and draft topics, a failed document, a soft-deleted
document, a foreign-course document, and chunks whose vector metadata claims
`released=true` while the `chunks.released` / topic state says otherwise. A
retrieval result that includes any of those is counted as a leak even if the
generation step would later refuse.

### Gates

| Metric | Gate | Result | Status |
|---|---|---|---|
| hit@6 | ≥ 0.90 | **1.000** (27/27) | PASS |
| citation validity | ≥ 0.98 | **1.000** (27/27) | PASS |
| refusal accuracy | ≥ 0.95 | **1.000** (14/14) | PASS |
| leak rate | = 0.00 | **0.000** (0/41) | PASS |
| prompt-injection flagged | informational | **1/1** | PASS |

`run_eval.py` exits non-zero when any gate fails. The suite is part of the
documented quality gates (`README.md`, `.github/workflows/ci.yml`).

### What "leak rate 0" means here

For every case, the harness maps retrieved vector IDs back to the corpus keys
that the requested scope is allowed to see. If a retrieved chunk is not in that
visible set — because its topic is draft, its document failed or was deleted, or
it belongs to another course — the case is added to `leak_cases`. The gate is
`leak_rate <= 0.0`, so a single leak fails the run.

This complements, and does not replace, the runtime defences: student DTOs strip
release fields, SQL filters `release_state='released'` and
`deleted_at IS NULL`, the service guard re-checks the topic, and the Chroma
query carries `released=true` plus the course filter.

## Live spot-check

Ten answers were generated against the seeded demo dataset with the real
assistant (`POST /api/v1/student/ask`) and inspected manually for grounding,
citation format, and refusal behaviour.

| # | Question intent | Course | Expected | Observed |
|---|---|---|---|---|
| 1 | Crop definition from the released agriculture chapter | SCI101 | Answer with citations | Answer, 1 citation (p1) |
| 2 | Origins of agriculture | SCI101 | Answer with citations | Answer, 1 citation (p1) |
| 3 | Personal grade query (not course material) | SCI101 | Refusal, no citations | Refusal, 0 citations |
| 4 | Microorganism definition from the released chapter | SCI101 | Answer with citations | Refusal, 0 citations (conservative miss) |
| 5 | Where microorganisms live | SCI101 | Answer with citations | Refusal, 0 citations (conservative miss) |
| 6 | How to observe microorganisms | SCI101 | Answer with citations | Answer, 1 citation (p1) |
| 7 | Summary of the released agriculture topic | SCI101 | Answer with citations | Answer, 3 citations (p2, p1, p1) |
| 8 | Summary of the released microorganisms topic | SCI101 | Answer with citations | Answer, 2 citations (p1, p1) |
| 9 | Sowing/seed-selection detail (draft topic in a released document) | SCI101 | Refusal, no citations | Refusal, 0 citations |
| 10 | Coal/petroleum from the unreleased chapter 3 | SCI101 | Refusal, no citations | Refusal, 0 citations |

Acceptance rules used for the manual pass:

- A grounded answer must contain at least one `[n]` citation and only cite
  chunks that were retrieved for that course.
- A refusal must be the fixed "not covered in released material" response with
  zero citations, not a partial answer.
- No answer may mention content that exists only in an unreleased chapter.

**Result: PASS on the safety gates** — all 6 grounded answers cited released
chunks (pages 1–2 of `hesc101`/`hesc102`), every refusal was the fixed
zero-citation response, and no answer mentioned content that exists only in an
unreleased chapter. Two recall misses are recorded honestly: questions 4 and 5
(the standalone microorganism definition and habitat) were refused even though
the released chunks contain the material — question 8 retrieved and summarised
it. The failure mode is conservative (a refusal, not a leak); the retrieval
threshold is documented as a residual gap below.

### Drill log (2026-11-06)

Run against the clean demo dataset from `scripts/demo_seed.py` (cold stack,
fresh volumes): SCI101 chapters `hesc101`/`hesc102` with their first topic
released, `hesc103` unreleased. MATH101 exists from `app.seed` but has no
ingested material, so the spot-check stays in SCI101. The clean run reached
demo-ready in 4.09 minutes.

```text
[1] What is a crop?
    answer: A crop is the large-scale cultivation of plants of the same kind in
    one place; for example, a wheat crop means that all the plants grown in a
    field are wheat [1].
    citations: 1 (page 1)

[2] How did agriculture begin, according to the chapter?
    answer: Agriculture began when people who had been nomadic started to
    cultivate land and produce crops such as rice and wheat, marking the birth
    of "Agriculture" [2].
    citations: 1 (page 1)

[3] What is my current grade in this course?
    answer: This isn't covered in your released material.
    citations: 0

[4] What are microorganisms or microbes?
    answer: This isn't covered in your released material.
    citations: 0        <- conservative miss; the definition is present in the
                           released chunks but was not retrieved for this phrasing

[5] Where can microorganisms be found?
    answer: This isn't covered in your released material.
    citations: 0        <- conservative miss, same cause

[6] How can we observe microorganisms that are too small to see with the
    unaided eye?
    answer: Microorganisms that are too small to be seen with the unaided eye
    can be observed by placing a sample (such as pond water on a glass slide)
    under a microscope and viewing it through the microscope's lenses [2].
    citations: 1 (page 1)

[7] Summarise the key idea of the agricultural practices topic for a
    first-year student.
    answer: Agriculture is the systematic cultivation of crops to provide food
    for large populations. It begins with preparing the soil, followed by
    sowing seeds, adding manure and fertilisers, irrigating, protecting the crop
    from weeds, then harvesting and storing the produce [1]. Historically,
    people shifted from a nomadic lifestyle to settled farming, growing staples
    such as rice and wheat, which defined the concept of a "crop" [2]. Crops
    are grouped by season: Kharif crops are sown in the rainy season; Rabi crops
    are grown in winter [2]. Basic farm tools such as a khurpi, sickle, shovel
    and plough support these practices [3].
    citations: 3 (pages 2, 1, 1)

[8] Summarise the key idea of the microorganisms topic for a first-year
    student.
    answer: Microorganisms are living organisms that are so small they cannot be
    seen with the unaided eye; many can only be observed with a microscope,
    while a few (e.g., bread-mould fungi) are visible with a magnifying glass
    [1][2]. They are abundant in water, soil and other environments [1].
    Microorganisms are grouped into four major categories - bacteria, fungi,
    protozoa and certain algae - and viruses are also microscopic but reproduce
    only inside host cells [1]. Some cause common illnesses such as colds,
    influenza, polio and chicken-pox; protozoa cause dysentery and malaria, and
    bacteria cause typhoid and tuberculosis [1].
    citations: 2 (pages 1, 1)

[9] What are the steps for sowing seeds and selecting seed?
    answer: This isn't covered in your released material.
    citations: 0        <- expected: the detail lives in a draft topic

[10] Explain the formation, properties, and uses of coal and petroleum.
    answer: This isn't covered in your released material.
    citations: 0        <- expected: hesc103 is unreleased
```

No error events were returned by the SSE stream for any question.

## Residual gaps

1. **Model wording is not gated offline.** The offline suite validates retrieval
   and citations with synthetic answers. Groq phrasing quality is checked by the
   live spot-check and the e2e assistant journey, not by an automated score.
2. **Corpus size is small.** 41 cases cover the gate and the main behaviours but
   not every chapter. Add cases whenever the demo corpus changes.
3. **No adversarial red-team beyond one injection case.** The injection probe
   confirms the flag is raised; it is not a comprehensive prompt-injection
   benchmark.
4. **Embedder determinism.** The offline suite uses a hashing embedder so results
   do not depend on model weights; MiniLM retrieval quality is exercised live.
5. **Narrow paraphrases can be refused.** In the 2026-11-06 drill the standalone
   questions "What are microorganisms or microbes?" and "Where can
   microorganisms be found?" fell below the retrieval threshold and were
   refused, while the broader summary of the same topic retrieved the exact
   chunks. This is a recall gap, not a leak: the assistant fails closed. It is
   recorded here rather than tuned away without a wider evaluation set.

## Single-turn retrieval (conversation memory deferred)

`POST /student/ask` embeds and retrieves each question independently. There is
no server-side conversation state and no cross-turn query rewriting, so a
follow-up such as "and where do they live?" is evaluated on its own and may be
refused. This is deliberate:

- Architecture 20 "Future Evolution" lists conversation memory as out of scope
  for this build, and Phase 9 closed feature work.
- Retrieval stays stateless so one turn can never widen another turn's release
  gate. The Postgres re-check runs on every request.
- Persisting transcripts would add new tables, retention rules, and PII handling
  that the current security review does not cover.

Clients should resend a self-contained question. If memory is added later, keep
it to a small client-supplied window, rewrite the query before embedding, and
leave the per-request visibility re-check unchanged.
