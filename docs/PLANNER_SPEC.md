# Planner spec (deterministic, pure functions)

Reference implementation: `reference/prototypes/plant-planner.html` (`cap`, `slotFor`, `sched`, `computeAll`). Expected output: `data/sample/golden_planner.json`. Port exactly; the golden tests must pass unchanged.

**Generalisation (D-42).** The owner can now define the route and machines, so the rules below hold per step in route order rather than for the five fixed steps. A step's capacity is the sum over its machine groups of (count − machines down) × rate, rounded after the shift multiplier. Downtime can target one machine group, or a whole step, in which case machines are taken from its groups in order. The second shift applies to steps flagged for it. Ready = last step's last day + dispatch. With the sample route (one group per step) this is exactly the prototype's behaviour.

## Inputs
`process.json`, `orders.json`, flags: `second_shift`, `new_request_ahead` (priority), `yarn_delay_days`, `extra_downtime[]`.

## Rules
- Days are integers (UTC date). Sample year 2027.
- **Capacity** of a step on day d = (machines - machines_down on d) x rate. Knitting x1.5 when second shift is on. Round to integer.
- **Dye slots**: fixed dates (06-28, 07-07, 07-16, 07-25). An order dyes in the first slot >= yarn_arrival + yarn_delay + 1. Dyeing takes 5 days. Knitting starts the day after dye finish (slot + 5).
- **Steps** in order: knit, link, board, pair, pack. Step i starts the day after step i-1 started. Each day it processes `min(free capacity, upstream cumulative output through previous day - already done, remaining)`.
- **Shared capacity**: orders are scheduled one at a time in priority order; capacity committed by earlier orders reduces free capacity for later ones.
- **Priority**: default booked orders first (SO-1182, SO-1190), then tentative (SO-1195). Flag `new_request_ahead` moves SO-1195 before SO-1190.
- **Ready date** = last packing day + 2 (dispatch). **Slack** = ship_by - ready (days; negative = late). Transit is 35 days after dispatch (shown, not used for slack).
- **Planned downtime**: 2 knitting machines 07-15 to 07-25.

## Process numbers
knit 12 x 350/day; link 6 x 800; board 2 x 2,500; pair 30 x 200; pack 13 x 500.

## Materials netting
need = pairs x qty_per_pair x (1 + waste), rounded up; buy = ceil(max(need - on_hand, 0) / MOQ) x MOQ. Golden values for SO-1182 are in the golden file (`materials_netting`).

## Using promises
A promise date for a material replaces the order's `yarn_arrival`. Realistic arrival = promised date + supplier `usual_slip_days` (rounded up) once reliability data exists; the golden tests use promised dates only.

## Tests
Parametrise over `golden_planner.json` cases: for each order assert dye_start, dye_finish, knit_start, knit_finish, pack_finish, ready, slack_days.
