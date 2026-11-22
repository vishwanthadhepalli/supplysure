# Prompts (paste one per session)

## Milestone 0: Skeleton
Read CLAUDE.md, docs/PLAN.md, docs/ARCHITECTURE.md and docs/DATA_MODEL.md. Do Milestone M0 only.
1. Create the layout in ARCHITECTURE.md (empty modules are fine).
2. Implement DB models and a schema-creation function from DATA_MODEL.md (SQLite for dev/tests, Postgres via DATABASE_URL).
3. Add `GET /health`.
4. Write `scripts/seed.py` that loads `data/sample/{process,orders,suppliers}.json` into the DB.
5. Add pytest tests: health works, seed creates 3 orders and the suppliers, schema creates.
6. Add requirements.txt, ruff config, and run `pytest -q` and `ruff check .`.
Stop when the M0 acceptance check in PLAN.md passes. Summarise what you did and any decision you made, and add it to docs/DECISIONS.md.

## Milestone 1: Planner
Read CLAUDE.md, docs/PLANNER_SPEC.md and `data/sample/golden_planner.json`. Do Milestone M1 only.
1. Implement `app/planner/` as pure functions (no I/O): capacity, dye slot, schedule per order with shared capacity, ready date and slack, materials netting.
2. Use `reference/prototypes/plant-planner.html` as the logic reference.
3. Write `tests/test_planner_golden.py` parametrised over every case in the golden file, asserting all fields, plus the materials netting.
4. Do not change the golden file. If a test fails, fix the planner, not the expectation; if you believe the golden file is wrong, stop and explain.
Done when all golden tests pass and ruff is clean.

## Milestone 2: Intake with fake LLM
Read CLAUDE.md, docs/ARCHITECTURE.md (intake flow, injection defense), docs/EVALS.md and `data/sample/messages.json`. Do M2 only.
Implement the intake pipeline with a `FakeLLM` that returns each fixture's expected extraction, the quote verifier (substring check), idempotent storage on (source, source_message_id), `needs_clarification` for ambiguous dates, and relative-date resolution in code. Tests must cover: normal promise, ambiguous date, hostile instruction, duplicate message, quote mismatch rejected.

## Later milestones
Use the M-row in PLAN.md as the task; start the prompt with "Read CLAUDE.md and the relevant docs. Do Milestone Mx only. Stop when its acceptance check passes."
