# Plan

Work one milestone at a time. Each ends with its acceptance check passing.

| M | Goal | Acceptance |
|---|---|---|
| M0 | Skeleton: repo layout, FastAPI health, DB models from DATA_MODEL, seed from `data/sample`, pytest, ruff | `pytest` green; `GET /health`; seed loads 3 orders, suppliers |
| M1 | Planner port with golden tests; materials netting | All 7 golden cases and netting pass unchanged |
| M2 | Intake pipeline with fake LLM: store message, extract promises, verify quote, `needs_clarification`, injection test, idempotency | EVALS extraction + safety cases pass using fixtures |
| M3 | Discord bot: channel per supplier, text intake, confirmation card, approve/reject buttons | Message in test channel creates one promise; repeat creates none |
| M4 | Voice: Sarvam STT + translation adapter (mockable); voice note to promise | Sample Ogg path works end to end with mock; real call behind env key |
| M5 | Web read-only: orders, ledger, plan chart, what-if, Brain graph | Matches prototypes' behaviour and golden numbers |
| M6 | Monitor + reminders ladder; Ask agent with citations | Overdue promise yields drafted reminder; Ask cites records |
| M7 | Demo hardening: seed script, replayable demo script, README run steps | 3-minute demo runs from a clean DB |

Phase 2 (only after M7): documents, risk events, cost of delay, reliability model, Monte Carlo, phone agent, email.
