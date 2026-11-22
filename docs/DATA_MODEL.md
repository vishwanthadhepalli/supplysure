# Data model

Postgres DDL (SQLite-compatible with minor type swaps for tests). The graph is derived from these tables (nodes = rows, edges = foreign keys and `graph_edges`).

```sql
CREATE TABLE suppliers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- yarn, dye, trim, print, freight, other
  language TEXT NOT NULL DEFAULT 'en',
  discord_channel_id TEXT,
  usual_slip_days NUMERIC(5,2) NOT NULL DEFAULT 0,
  single_source BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE materials (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  unit TEXT NOT NULL,
  moq NUMERIC(12,2) NOT NULL DEFAULT 1,
  on_hand NUMERIC(12,2) NOT NULL DEFAULT 0,
  supplier_id TEXT REFERENCES suppliers(id)
);

-- Production route: in-house steps in `sequence` order (D-43). Edited in the web app.
CREATE TABLE process_steps (
  id TEXT PRIMARY KEY,                 -- knit, link, board, pair, pack, ...
  name TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  unit TEXT NOT NULL DEFAULT 'machines', -- machines, lines, people, stations
  second_shift BOOLEAN NOT NULL DEFAULT FALSE
);

-- A group of identical machines (or people, lines) doing one step. A step can have several.
CREATE TABLE machines (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  step TEXT NOT NULL REFERENCES process_steps(id),
  count INTEGER NOT NULL,
  rate_per_machine_day INTEGER NOT NULL,
  notes TEXT
);

CREATE TABLE downtime (
  id TEXT PRIMARY KEY,
  step TEXT NOT NULL,
  machine_id TEXT REFERENCES machines(id),  -- null: step-wide
  from_date DATE NOT NULL,
  to_date DATE NOT NULL,
  machines_down INTEGER NOT NULL,
  planned BOOLEAN NOT NULL DEFAULT TRUE,    -- false: probable breakdown
  reason TEXT
);

-- Outside stages and shift rules. Single row (id = 1), seeded from process.json.
CREATE TABLE production_settings (
  id INTEGER PRIMARY KEY,
  dye_slots TEXT NOT NULL,             -- JSON list of ISO dates
  dye_days INTEGER NOT NULL,
  second_shift_multiplier NUMERIC(4,2) NOT NULL,
  dispatch_days INTEGER NOT NULL,
  transit_days INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  buyer TEXT NOT NULL,
  pairs INTEGER NOT NULL,
  ship_by DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'booked',   -- booked, tentative, done
  grams_per_pair NUMERIC(6,2) NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  yarn_arrival DATE                        -- planned yarn arrival; an open yarn promise overrides it (D-35)
);

CREATE TABLE order_materials (
  order_id TEXT REFERENCES orders(id),
  material_id TEXT REFERENCES materials(id),
  qty_per_pair NUMERIC(12,6) NOT NULL,
  waste_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
  expected_arrival DATE,
  PRIMARY KEY (order_id, material_id)
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,                -- discord, email, web
  source_message_id TEXT NOT NULL,
  supplier_id TEXT REFERENCES suppliers(id),
  received_at TIMESTAMP NOT NULL,
  kind TEXT NOT NULL,                  -- text, voice, document
  raw_text TEXT,
  transcript TEXT,
  transcript_lang TEXT,
  english_text TEXT,
  attachment_url TEXT,
  UNIQUE (source, source_message_id)
);

CREATE TABLE promises (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(id),
  supplier_id TEXT NOT NULL REFERENCES suppliers(id),
  order_id TEXT REFERENCES orders(id),
  material_id TEXT REFERENCES materials(id),
  quantity NUMERIC(12,2),
  promised_date DATE,
  status TEXT NOT NULL DEFAULT 'open', -- open, needs_clarification, kept, slipped, cancelled
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5,
  quote TEXT NOT NULL,
  quote_verified BOOLEAN NOT NULL DEFAULT FALSE,
  clarification_question TEXT,
  created_at TIMESTAMP NOT NULL,
  resolved_at TIMESTAMP
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  message_id TEXT REFERENCES messages(id),
  supplier_id TEXT REFERENCES suppliers(id),
  order_id TEXT REFERENCES orders(id),
  doc_type TEXT NOT NULL,              -- invoice, packing_list, dye_slip, bill_of_lading
  extracted_json TEXT,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE risk_events (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                  -- weather, port, strike, other
  description TEXT NOT NULL,
  starts_on DATE,
  ends_on DATE,
  supplier_id TEXT REFERENCES suppliers(id)
);

CREATE TABLE graph_edges (
  src_type TEXT NOT NULL,
  src_id TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  rel TEXT NOT NULL,                   -- supplies, needs, runs_on, ships_to, affects
  PRIMARY KEY (src_type, src_id, dst_type, dst_id, rel)
);

CREATE TABLE reminders (
  id TEXT PRIMARY KEY,
  promise_id TEXT NOT NULL REFERENCES promises(id),
  step INTEGER NOT NULL,               -- 1 message, 2 follow-up, 3 call, 4 alert owner
  draft_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'drafted', -- drafted, approved, sent, skipped
  created_at TIMESTAMP NOT NULL,
  sent_at TIMESTAMP
);

CREATE TABLE action_log (
  id TEXT PRIMARY KEY,
  at TIMESTAMP NOT NULL,
  actor TEXT NOT NULL,                 -- system, owner, agent
  action TEXT NOT NULL,
  payload TEXT
);

CREATE TABLE predictions (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  made_at TIMESTAMP NOT NULL,
  predicted_ready DATE NOT NULL,
  actual_ready DATE
);
```

```sql
-- SupplyGPT (D-50): what the owner said, and the changes proposed from it.
CREATE TABLE assistant_inputs (
  id TEXT PRIMARY KEY,
  created_at TIMESTAMP NOT NULL,
  kind TEXT NOT NULL,                  -- text, voice
  language_code TEXT,
  original_text TEXT NOT NULL,         -- typed text or transcript
  english_text TEXT NOT NULL,          -- what the model read
  reply TEXT,
  reply_local TEXT
);

CREATE TABLE assistant_proposals (
  id TEXT PRIMARY KEY,
  input_id TEXT NOT NULL REFERENCES assistant_inputs(id),
  type TEXT NOT NULL,                  -- add_machine, update_machine, add_downtime, add_step, supplier_update
  status TEXT NOT NULL DEFAULT 'pending', -- pending, needs_clarification, invalid, applying, applied, rejected
  summary TEXT NOT NULL,
  quote TEXT NOT NULL,                 -- verbatim, verified
  payload TEXT NOT NULL,               -- JSON, validated
  reason TEXT,
  result_ref TEXT,
  created_at TIMESTAMP NOT NULL,
  resolved_at TIMESTAMP
);
```

## Graph node types
order, supplier, material, machine (group), step (process step), document, risk_event (risk events and downtime), customer. Edges: supplier supplies material, material needed by order, machine works on step, step next step, last step makes order, downtime affects machine, order for customer. Traversals: downstream (what does this supplier affect), upstream (what does this order depend on), single-source (material with one supplier and `single_source`).
