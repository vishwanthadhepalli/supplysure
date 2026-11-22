# SupplySure starter pack

Hand this folder to your coding assistant (Claude Code in VS Code). Open the folder, then paste the Milestone 0 prompt from `docs/PROMPTS.md`.

```
CLAUDE.md                 rules the assistant reads every session
docs/PRD.md               what and why, users, scope, demo
docs/ARCHITECTURE.md      layers, harnesses, agents, data flow
docs/DATA_MODEL.md        Postgres tables (DDL) and graph model
docs/PLANNER_SPEC.md      exact planner rules + golden tests
docs/PLAN.md              milestones M0 to M7 with acceptance checks
docs/DECISIONS.md         decided and open choices
docs/EVALS.md             how we test extraction, planner, safety
docs/PROMPTS.md           ready-to-paste prompts per milestone
data/sample/              process, orders, suppliers, messages, golden planner output
reference/prototypes/     the HTML prototypes (visual and logic reference only)
```

Open choices (marked in DECISIONS.md): reasoning model, phone-call agent, hosting, web write access, Sarvam API details to verify.

## Run the web app locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.seed              # suppliers, orders, machines, materials
.venv/bin/python -m scripts.sample_messages   # optional: replay sample supplier messages into the ledger
.venv/bin/uvicorn app.main:app --reload       # then open http://127.0.0.1:8000
```

**SupplyGPT:** put your key in `.env` (`SARVAM_API_KEY=...`; `cp .env.example .env` first if needed) and restart. Then type or speak to SupplyGPT in English or an Indian language, for example "Two knitting machines will be down next Tuesday to Thursday". It proposes changes; nothing happens until you approve.

Views: SupplyGPT, Orders (workload chart, materials), Production plan (the route, the machines on each step, outside stages, a per-order schedule), Machines (orders per machine group, load, planned and probable downtime), Scenarios (what-if and the 7 tested cases), Ledger (promises with source quotes, activity log), Factory Brain (graph with traces). You can edit factory setup in the web app; supplier data is read-only there. After a schema change, delete `supplysure.db` and seed again.
