"""SupplyGPT API: text and voice input, proposals, approve / reject, history."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import sarvam_api_key, today
from app.db import get_session
from app.models import AssistantInput, AssistantProposal
from app.providers.sarvam import (
    LANGUAGES,
    MAX_AUDIO_SECONDS,
    SarvamNotConfigured,
    SpeechAndLanguage,
    get_sarvam,
)
from app.supplygpt.service import (
    SupplyGPTError,
    apply_proposal,
    handle_text,
    handle_voice,
    history,
    reject_proposal,
    speech_for,
)

router = APIRouter(prefix="/api/supplygpt")
MAX_AUDIO_BYTES = 10 * 1024 * 1024
AUDIO_TYPES = (
    "audio/wav", "audio/x-wav", "audio/wave", "audio/webm", "audio/ogg", "audio/mpeg",
    "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/flac", "audio/opus",
)  # fmt: skip


def get_client() -> SpeechAndLanguage:
    try:
        return get_sarvam()
    except SarvamNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    language_code: str | None = None


def _proposal_json(p: AssistantProposal) -> dict:
    return {
        "id": p.id,
        "type": p.type,
        "status": p.status,
        "summary": p.summary,
        "quote": p.quote,
        "payload": json.loads(p.payload),
        "reason": p.reason,
        "result_ref": p.result_ref,
        "created_at": p.created_at.isoformat(),
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
    }


def _input_json(i: AssistantInput, props: list[AssistantProposal]) -> dict:
    return {
        "id": i.id,
        "created_at": i.created_at.isoformat(),
        "kind": i.kind,
        "language_code": i.language_code,
        "language": LANGUAGES.get(i.language_code or "", i.language_code),
        "original_text": i.original_text,
        "english_text": i.english_text,
        "reply": i.reply,
        "reply_local": i.reply_local,
        "proposals": [_proposal_json(p) for p in props],
    }


def _one(session: Session, row: AssistantInput) -> dict:
    return next(_input_json(i, p) for i, p in history(session, 200) if i.id == row.id)


@router.get("/status")
def status() -> dict:
    return {
        "configured": bool(sarvam_api_key()),
        "today": today().isoformat(),
        "languages": LANGUAGES,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
    }


@router.get("/history")
def get_history(limit: int = 50, session: Session = Depends(get_session)) -> dict:
    return {"inputs": [_input_json(i, p) for i, p in history(session, max(1, min(limit, 200)))]}


@router.post("/text", status_code=201)
def post_text(
    body: TextIn,
    session: Session = Depends(get_session),
    client: SpeechAndLanguage = Depends(get_client),
) -> dict:
    if body.language_code and body.language_code not in LANGUAGES:
        raise HTTPException(422, f"unsupported language {body.language_code}")
    try:
        row = handle_text(session, client, body.text, body.language_code)
    except SupplyGPTError as exc:
        raise HTTPException(502 if str(exc).startswith("Sarvam") else 422, str(exc)) from exc
    return _one(session, row)


@router.post("/voice", status_code=201)
async def post_voice(
    file: UploadFile = File(...),
    language_code: str | None = Form(None),
    session: Session = Depends(get_session),
    client: SpeechAndLanguage = Depends(get_client),
) -> dict:
    if language_code and language_code not in LANGUAGES:
        raise HTTPException(422, f"unsupported language {language_code}")
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in AUDIO_TYPES:
        raise HTTPException(415, f"Unsupported audio type {content_type or 'unknown'}.")
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "The recording is too large (10 MB max).")
    try:
        row = handle_voice(
            session, client, audio, file.filename or "voice.wav", content_type, language_code
        )
    except SupplyGPTError as exc:
        raise HTTPException(502 if str(exc).startswith("Sarvam") else 422, str(exc)) from exc
    return _one(session, row)


@router.get("/inputs/{input_id}/speech")
def get_speech(
    input_id: str,
    session: Session = Depends(get_session),
    client: SpeechAndLanguage = Depends(get_client),
) -> Response:
    """SupplyGPT's reply as speech (MP3), in the owner's language when Sarvam can speak it."""
    if session.get(AssistantInput, input_id) is None:
        raise HTTPException(404, f"unknown input {input_id}")
    try:
        audio, lang = speech_for(session, client, input_id)
    except SupplyGPTError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600", "Content-Language": lang},
    )


@router.post("/proposals/{proposal_id}/apply")
def post_apply(proposal_id: str, session: Session = Depends(get_session)) -> dict:
    return _proposal_json(apply_proposal(session, proposal_id))


@router.post("/proposals/{proposal_id}/reject")
def post_reject(proposal_id: str, session: Session = Depends(get_session)) -> dict:
    return _proposal_json(reject_proposal(session, proposal_id))
