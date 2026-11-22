# Architecture

## Principle
One main **Ask agent** plus fixed **workflows** (harnesses). Workflows are deterministic pipelines with one narrow LLM step each. Code owns numbers; models read and explain.

## Layers
| Layer | Choice |
|---|---|
| Experience | Discord (writes: intake, approvals), web app (reads: orders, ledger, graph, plan) |
| Channel | discord.py bot; webhooks to simulate suppliers in tests; Ogg/Opus voice attachments |
| Understanding | Sarvam STT + translation; document reader; LLM extractor (structured output) |
| Agent | Ask agent (read-only tools) + workflows H1 to H8 |
| Tools | `get_orders`, `get_promises`, `run_plan(whatif)`, `graph_query`, `draft_message` (read/draft only) |
| Memory | Postgres: ledger, graph, messages, action log; supplier reliability stats |
| Model | Adapter `providers/llm.py` (open, D-03); adapter `providers/sarvam.py` |
| Learning | Phase 2: slip statistics, Monte Carlo ship dates, insight cards |
| Governance | Autonomy level 1, approvals, injection defense, quote verification, action log |
| Operations | Logging, eval suite, replayable message fixtures |
| Infrastructure | FastAPI, Postgres/SQLite, hosting open (D-05) |

## Harnesses
- **H1 Intake**: message/voice in, idempotent store, transcribe, translate, extract promises, verify quotes, store promise or `needs_clarification`.
- **H2 Documents**: read invoice/packing list/dye slip, link to order and supplier.
- **H3 Planner**: deterministic schedule, slack, what-if (PLANNER_SPEC).
- **H4 Cost** (phase 2): cost of delay per order.
- **H5 Monitor**: daily scan, due-soon and overdue promises, reminder ladder drafts.
- **H6 Comms**: draft supplier messages in the supplier's language; owner approves; send; log.
- **H7 Risk events** (phase 2): weather, ports, strikes linked to suppliers.
- **H8 Ask**: answer with citations to records.
- **H9 Evals**: see EVALS.md.

## Intake flow
Discord message -> store raw (unique on discord_message_id) -> if audio: STT -> translate to English -> LLM extractor (structured: promises[], each with quote) -> quote substring check against transcript/text -> insert promise (status `open` or `needs_clarification`) -> link to order item via supplier + material -> recompute plan -> post confirmation card in Discord with quote and effect on slack.

## Injection defense
Extractor has no tools, receives source text inside delimiters, output schema is fixed; outputs that reference unknown orders or contain instructions are discarded. State changes happen only in code paths triggered by validated promise objects or owner approval.

## Web/Discord split
Discord writes (intake, approvals). Web reads through FastAPI JSON endpoints. Same DB.

## Suggested layout
```
app/
  main.py            FastAPI app
  config.py
  db.py, models.py
  planner/           pure functions, no I/O
  intake/            extractor, verifier, pipeline
  providers/         llm.py, sarvam.py (adapters + fakes)
  discord_bot/
  agents/            ask.py, monitor.py, comms.py
  web/               static html/js
tests/
data/sample/
```
