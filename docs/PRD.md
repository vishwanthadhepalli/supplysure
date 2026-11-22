# SupplySure PRD

## Problem
Small manufacturers cannot see past their direct suppliers. Where a part is, what is late and what will run short lives in WhatsApp-style chats, voice notes, emails, spreadsheets and phone calls. The owner finds out about a slip when it is already too late to fix.

## User
Owner or production/purchase manager of a small manufacturer. Example: Kovai Knits (socks, Coimbatore), 3 live orders, 12 knitting machines, suppliers who speak Tamil and send voice notes.

## Value
- Inputs: supplier messages and voice notes (any language), documents (invoices, packing lists, dye-house slips), the owner's process and orders.
- Outputs: promise ledger with evidence, realistic ship date per order, shortage forecast, drafted reminders, Factory Brain graph, answers to questions.
- Outcome: see slips early, act while there is slack, never lose a promise made on a call.

## Core features (MVP)
1. **Intake**: Discord channel per supplier; text and voice notes; voice is transcribed and translated (Sarvam).
2. **Promise ledger**: structured promises (who, what, quantity, date, confidence, source quote), verified by quote match, with `needs_clarification` for ambiguous dates.
3. **Planner**: deterministic multi-order scheduling over shared machines and yarn; slack, ready date, second shift and what-if (see PLANNER_SPEC).
4. **Factory Brain**: graph of orders, suppliers, materials, machines, documents, risk events; upstream/downstream questions; single-source detection.
5. **Monitor and reminders**: ladder (message, follow-up, call, alert owner) drafted for approval.
6. **Ask agent**: answers with citations to ledger/planner records.
7. **Web app (read-only for demo)**: Orders, Promise ledger, Brain graph, Plan chart.

## Phase 2 (after MVP)
Outbound logistics and documents, external risk events (weather, ports, strikes), cost of delay, supplier reliability model, Monte Carlo ship-date probabilities, phone-call agent, email intake.

## Non-goals
Reading private WhatsApp chats, ERP replacement, autonomous sending without approval, accounting.

## Success metrics
Extraction: promise recall and precision on the labelled messages; zero hallucinated quotes; zero state changes from hostile text. Planner: matches `golden_planner.json` exactly. Demo: Discord voice note appears as a ledger entry and moves an order's slack on the web view within seconds.

## 3-minute demo script
1. (20s) Problem and the owner's three orders.
2. (50s) Supplier sends a Tamil voice note in Discord; bot transcribes, shows English, adds promise "cotton bales reach on 22nd, yarn a few days late", quote shown.
3. (50s) Web view: SO-1182 slack drops to the realistic date; planner flags SO-1195 at -6 days.
4. (30s) What-if: second shift or reorder priority; slack numbers from golden file.
5. (20s) Bot drafts a reminder; owner approves; ledger logs it.
6. (10s) Close: Factory Brain graph, single-source warning.
