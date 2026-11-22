"""Fixtures for the intake evals. The fixture-backed FakeLLM lives in scripts/sample_messages.py
so the demo replay and the evals use exactly the same fake."""

import pytest

from app.providers.llm import FakeLLM
from scripts.sample_messages import fixture_llm, load_messages

MESSAGES = load_messages()
BY_ID = {m["id"]: m for m in MESSAGES}


@pytest.fixture
def fake_llm() -> FakeLLM:
    return fixture_llm(MESSAGES)
