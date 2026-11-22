"""SupplyGPT: voice/text input through Sarvam (faked), proposals, approval (D-49, D-50)."""

import pytest
from sqlalchemy import select

from app.models import ActionLog, AssistantProposal, Downtime, Machine, Promise
from app.providers.sarvam import FakeSarvam, Transcription
from app.supplygpt.api import get_client

TODAY = "2027-06-14"  # a Monday


def action(type_, quote, **fields):
    base = {k: None for k in (
        "name", "step_id", "machine_id", "unit", "count", "rate_per_machine_day",
        "machines_down", "from_text", "to_text", "duration_days", "planned", "reason",
        "supplier_id", "material_id", "quantity",
    )}  # fmt: skip
    return {**base, "type": type_, "quote": quote, **fields}


ADD_MACHINES = "We bought 4 new knitting machines that make 350 pairs a day each."
DOWNTIME = "Two knitting machines will be down next Tuesday to Thursday for needle servicing."
AMBIGUOUS = "Packing stations: one will be out on the 12th or 22nd."
SUPPLIER = "Ravi called. The cotton yarn will come on the 28th."
TAMIL = "இரண்டு பின்னல் இயந்திரங்கள் நாளை பழுதாகலாம்."
TAMIL_EN = "Two knitting machines may break down tomorrow."


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("SUPPLYSURE_TODAY", TODAY)
    return FakeSarvam(
        chats={
            "4 new knitting machines": {
                "reply": "You bought 4 knitting machines. I can add them.",
                "actions": [action("add_machine",
                                   "4 new knitting machines that make 350 pairs a day each",
                                   step_id="knit", name="New knitting machines", count=4,
                                   rate_per_machine_day=350)],
            },
            "needle servicing": {
                "reply": "Two knitting machines out next week.",
                "actions": [action("add_downtime", DOWNTIME, machine_id="machine-knit",
                                   machines_down=2, from_text="next Tuesday", to_text="Thursday",
                                   planned=True, reason="needle servicing")],
            },
            "12th or 22nd": {
                "reply": "A packing station will be out.",
                "actions": [action("add_downtime", AMBIGUOUS, machine_id="machine-pack",
                                   machines_down=1, from_text="the 12th or 22nd")],
            },
            "Ravi called": {
                "reply": "Ravi gave a new date for cotton yarn.",
                "actions": [action("supplier_update", "The cotton yarn will come on the 28th",
                                   supplier_id="sup-ravi", material_id="cotton_yarn_kg")],
            },
            TAMIL_EN: {
                "reply": "Two knitting machines may break down tomorrow.",
                "actions": [action("add_downtime", "Two knitting machines may break down tomorrow",
                                   machine_id="machine-knit", machines_down=2, from_text="tomorrow",
                                   planned=False, reason="possible breakdown")],
            },
        },
        translations={TAMIL: TAMIL_EN,
                      "Two knitting machines may break down tomorrow.": "TA: reply"},
        transcripts={b"voice-bytes": Transcription(TAMIL, "ta-IN", "req-1")},
    )  # fmt: skip


@pytest.fixture
def gpt(api, fake):
    from app.main import app

    app.dependency_overrides[get_client] = lambda: fake
    yield api
    app.dependency_overrides.pop(get_client, None)


def _say(gpt, text, **kw):
    r = gpt.post("/api/supplygpt/text", json={"text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _plan(api):
    return {o["id"]: o for o in api.get("/api/plan").json()["orders"]}


def test_add_machines_by_text_then_approve(gpt, seeded_session):
    before = _plan(gpt)["SO-1195"]["ready"]
    out = _say(gpt, ADD_MACHINES)
    assert out["language_code"] == "en-IN" and out["kind"] == "text"
    (p,) = out["proposals"]
    assert p["status"] == "pending"
    assert "+1,400 a day" in p["summary"]
    assert p["quote"] in ADD_MACHINES
    # Nothing changes until approval.
    assert (
        seeded_session.scalar(select(Machine).where(Machine.name == "New knitting machines"))
        is None
    )

    applied = gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply").json()
    assert applied["status"] == "applied" and applied["result_ref"]
    assert _plan(gpt)["SO-1195"]["ready"] < before
    assert gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply").status_code == 409
    actions = [a.action for a in seeded_session.scalars(select(ActionLog))]
    assert {"supplygpt_input", "machine_added", "supplygpt_applied"} <= set(actions)


def test_downtime_dates_are_resolved_in_code(gpt, seeded_session):
    (p,) = _say(gpt, DOWNTIME)["proposals"]
    assert p["status"] == "pending"
    # Monday Jun 14: "next Tuesday" = Jun 15, "to Thursday" counts from there = Jun 17.
    assert p["payload"]["from_date"] == "2027-06-15"
    assert p["payload"]["to_date"] == "2027-06-17"
    assert p["payload"]["machines_down"] == 2  # "Two" written as a word is accepted
    gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply")
    rows = seeded_session.scalars(
        select(Downtime).where(Downtime.reason == "needle servicing")
    ).all()
    assert len(rows) == 1 and rows[0].machine_id == "machine-knit"


def test_ambiguous_date_becomes_a_question(gpt):
    (p,) = _say(gpt, AMBIGUOUS)["proposals"]
    assert p["status"] == "needs_clarification"
    assert "Which one?" in p["reason"]
    assert gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply").status_code == 409
    assert gpt.post(f"/api/supplygpt/proposals/{p['id']}/reject").json()["status"] == "rejected"


def test_numbers_and_quotes_must_come_from_the_owner(gpt, fake):
    fake.chats["bought some"] = {
        "reply": "ok",
        "actions": [
            action("add_machine", "bought some knitting machines", step_id="knit", count=6,
                   rate_per_machine_day=350),
            action("add_step", "Please add a pressing step", name="Pressing"),
            action("add_machine", "bought some knitting machines", step_id="weaving"),
            action("delete_everything", "bought some"),  # not an allowed action: dropped
        ],
    }  # fmt: skip
    out = _say(gpt, "We bought some knitting machines at 350 a day.")
    statuses = [(p["type"], p["status"]) for p in out["proposals"]]
    assert statuses == [
        ("add_machine", "invalid"),  # 6 is not in the quote
        ("add_step", "invalid"),  # quote not in what was said
        ("add_machine", "needs_clarification"),  # unknown step
    ]
    assert "not in your words" in out["proposals"][0]["reason"]


def test_tamil_text_is_translated_and_answered_in_tamil(gpt, fake):
    out = _say(gpt, TAMIL, language_code="ta-IN")
    assert out["language_code"] == "ta-IN"
    assert out["english_text"] == TAMIL_EN
    assert out["reply_local"] == "TA: reply"
    (p,) = out["proposals"]
    assert p["payload"]["from_date"] == "2027-06-15"  # tomorrow
    assert p["payload"]["planned"] is False
    assert ("translate", TAMIL) in fake.calls


def test_tamil_script_is_detected_without_a_language_hint(gpt, fake):
    out = _say(gpt, TAMIL)
    assert out["english_text"] == TAMIL_EN


def test_voice_note_is_transcribed_then_handled(gpt, fake):
    r = gpt.post(
        "/api/supplygpt/voice",
        files={"file": ("note.wav", b"voice-bytes", "audio/wav")},
        data={"language_code": "unknown"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["kind"] == "voice" and out["language_code"] == "ta-IN"
    assert out["original_text"] == TAMIL and out["english_text"] == TAMIL_EN
    assert ("transcribe", "unknown") in fake.calls
    bad = gpt.post("/api/supplygpt/voice", files={"file": ("x.txt", b"hi", "text/plain")})
    assert bad.status_code == 415


def test_supplier_update_goes_through_the_ledger(gpt, seeded_session):
    (p,) = _say(gpt, SUPPLIER)["proposals"]
    assert "promised Jun 28" in p["summary"]
    applied = gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply").json()
    promise = seeded_session.get(Promise, applied["result_ref"])
    assert promise.quote == "The cotton yarn will come on the 28th"
    assert promise.quote_verified and promise.promised_date.isoformat() == "2027-06-28"
    assert promise.order_id == "SO-1182"
    so_1182 = _plan(gpt)["SO-1182"]
    assert so_1182["yarn"]["source"] == "promise"
    assert so_1182["dye"]["start"] == "2027-07-07"  # Jun 28 + 1 misses the Jun 28 dye slot
    ledger = gpt.get("/api/promises").json()["promises"]
    assert any(x["message"]["source"] == "supplygpt" for x in ledger)


def test_history_and_status(gpt):
    _say(gpt, ADD_MACHINES)
    hist = gpt.get("/api/supplygpt/history").json()["inputs"]
    assert hist[0]["original_text"] == ADD_MACHINES and hist[0]["proposals"]
    st = gpt.get("/api/supplygpt/status").json()
    assert st["today"] == TODAY and st["max_audio_seconds"] == 30


def test_empty_input_and_missing_key(api, monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    r = api.post("/api/supplygpt/text", json={"text": "hello"})
    assert r.status_code == 503
    assert "SARVAM_API_KEY" in r.json()["detail"]
    assert api.get("/api/supplygpt/status").json()["configured"] is False


def test_apply_failure_keeps_the_proposal_pending(gpt, seeded_session):
    (p,) = _say(gpt, ADD_MACHINES)["proposals"]
    seeded_session.execute(
        AssistantProposal.__table__.update()
        .where(AssistantProposal.id == p["id"])
        .values(payload='{"name": "X", "step": "gone", "count": 4, "rate_per_machine_day": 350}')
    )
    seeded_session.commit()
    r = gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply")
    assert r.status_code == 422
    assert seeded_session.get(AssistantProposal, p["id"]).status == "pending"


def test_original_words_are_sent_alongside_the_translation(gpt, fake):
    _say(gpt, TAMIL, language_code="ta-IN")
    chat_user = next(arg for kind, arg in fake.calls if kind == "chat")
    assert f"<owner_message>\n{TAMIL_EN}\n</owner_message>" in chat_user
    assert f"<owner_original>\n{TAMIL}\n</owner_original>" in chat_user
    english_only = _say(gpt, ADD_MACHINES)
    last_chat = [arg for kind, arg in fake.calls if kind == "chat"][-1]
    assert "<owner_original>" not in last_chat and english_only["proposals"]


def test_new_machine_group_never_reuses_an_existing_name(gpt, fake):
    fake.chats["4 new knitting machines"]["actions"][0]["name"] = "Knitting machines"
    (p,) = _say(gpt, ADD_MACHINES)["proposals"]
    assert p["payload"]["name"] == "Knitting machines (new)"


def test_placeholder_strings_from_the_model_count_as_missing(gpt, fake):
    # Live sarvam-105b sent "quantity": "null" as a string; that must not drop the proposal.
    fake.chats["Ravi called"]["actions"][0]["quantity"] = "null"
    (p,) = _say(gpt, SUPPLIER)["proposals"]
    assert p["status"] == "pending" and p["payload"]["quantity"] is None


def test_voice_reply_speaks_the_cards_in_the_owners_language(gpt, fake):
    out = gpt.post(
        "/api/supplygpt/voice", files={"file": ("note.wav", b"voice-bytes", "audio/wav")}
    ).json()
    r = gpt.get(f"/api/supplygpt/inputs/{out['id']}/speech")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-language"] == "ta-IN"
    spoken_lang, spoken_text = next(arg for kind, arg in fake.calls if kind == "speak")
    assert spoken_lang == "ta-IN"
    # The English script was translated before speaking, and it includes the pending change.
    english = next(
        arg for kind, arg in fake.calls[::-1] if kind == "translate" and "approval" in arg
    )
    assert "Waiting for your approval." in english
    assert (
        "Knitting machines: 2 machines will not be working on 15 June. Possible breakdown."
        in english
    )
    # Cached: asking again does not call Sarvam again.
    n = len([c for c in fake.calls if c[0] == "speak"])
    assert gpt.get(f"/api/supplygpt/inputs/{out['id']}/speech").content == r.content
    assert len([c for c in fake.calls if c[0] == "speak"]) == n


def test_questions_are_spoken_and_english_is_default(gpt, fake):
    out = _say(gpt, AMBIGUOUS)
    gpt.get(f"/api/supplygpt/inputs/{out['id']}/speech")
    lang, text = [arg for kind, arg in fake.calls if kind == "speak"][-1]
    assert lang == "en-IN"
    assert "Which one?" in text  # the clarification question is read out
    assert "approval" not in text  # nothing is waiting for approval
    assert gpt.get("/api/supplygpt/inputs/nope/speech").status_code == 404


def test_language_sarvam_cannot_speak_falls_back_to_english(gpt, fake, seeded_session):
    from app.models import AssistantInput

    out = _say(gpt, ADD_MACHINES)
    seeded_session.get(AssistantInput, out["id"]).language_code = "ur-IN"
    seeded_session.commit()
    r = gpt.get(f"/api/supplygpt/inputs/{out['id']}/speech")
    assert r.status_code == 200 and r.headers["content-language"] == "en-IN"


def test_spoken_reply_is_short_and_only_says_the_cards():
    from app.models import AssistantInput, AssistantProposal
    from app.supplygpt.service import spoken_reply

    row = AssistantInput(reply="I cannot do that. " * 20)
    pending = AssistantProposal(
        status="pending",
        summary="Add 4 × New knitting machines to Knitting (+1,400 a day)",
        quote="q",
        payload="{}",
        type="add_machine",
    )
    text = spoken_reply(row, [pending])
    assert "cannot" not in text  # the model's long reply is not read when there is a card
    assert "Add 4 New knitting machines to Knitting, +1,400 a day." in text
    assert spoken_reply(row, []).count("cannot") == 2  # no card: first two sentences only


def test_translation_that_loses_a_number_is_spoken_in_english(gpt, fake):
    from app.providers.sarvam import Translation

    out = gpt.post(
        "/api/supplygpt/voice", files={"file": ("note.wav", b"voice-bytes", "audio/wav")}
    ).json()
    real = fake.translate

    def lossy(text, source="auto", target="en-IN"):
        if source == "en-IN":  # the spoken script going to Tamil: drop every digit
            return Translation("".join(ch for ch in text if not ch.isdigit()), "en-IN")
        return real(text, source, target)

    fake.translate = lossy
    r = gpt.get(f"/api/supplygpt/inputs/{out['id']}/speech")
    assert r.headers["content-language"] == "en-IN"
    lang, text = [arg for kind, arg in fake.calls if kind == "speak"][-1]
    assert lang == "en-IN" and "15 June" in text
