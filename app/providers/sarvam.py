"""Sarvam AI adapter: speech-to-text, translation, and chat completions with JSON output.

API shapes were checked against docs.sarvam.ai on 2026-11-22 (D-49):
- POST /speech-to-text      multipart: file, model (saaras:v4), mode, language_code (optional =
                            auto-detect). Sync REST takes up to 30 s of audio. Returns
                            {transcript, language_code, request_id}.
- POST /translate           JSON: input (<= 1000 chars for mayura:v1), source_language_code
                            ("auto" allowed with mayura:v1), target_language_code, model.
                            Returns {translated_text, source_language_code}.
- POST /text-to-speech      JSON: text (<= 2500 chars), language_code (11 languages), model
                            (bulbul:v3 default), speaker, output_audio_codec, speech_sample_rate,
                            enable_preprocessing. Returns {audios: [base64], request_id}.
- POST /v1/chat/completions JSON, OpenAI-shaped. model sarvam-105b; response_format
                            json_schema gives structured output in choices[0].message.content.
Auth header on all three: api-subscription-key.
Errors: {"error": {"message", "code", "request_id"}} (STT model errors use {"detail"}).

Everything goes through this module so it can be mocked (FakeSarvam) or swapped.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import sarvam_api_key

BASE_URL = "https://api.sarvam.ai"
MAX_TRANSLATE_CHARS = 1000  # mayura:v1 limit
MAX_AUDIO_SECONDS = 30  # sync REST limit
MAX_TTS_CHARS = 2500
# Languages text-to-speech can speak (a subset of the ones it can hear and translate).
TTS_LANGUAGES = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN",
                 "pa-IN", "ta-IN", "te-IN"}  # fmt: skip

# Languages offered in the UI (subset of what Sarvam supports).
LANGUAGES = {
    "unknown": "Detect automatically",
    "en-IN": "English",
    "ta-IN": "Tamil",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "gu-IN": "Gujarati",
    "bn-IN": "Bengali",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
}


class SarvamError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class SarvamNotConfigured(SarvamError):
    pass


class _BadAnswer(SarvamError):
    """The API answered, but not with usable JSON. Worth one retry."""


@dataclass(frozen=True)
class Transcription:
    transcript: str
    language_code: str | None
    request_id: str | None = None


@dataclass(frozen=True)
class Translation:
    text: str
    source_language_code: str | None


class SpeechAndLanguage(Protocol):
    def transcribe(
        self, audio: bytes, filename: str, content_type: str, language_code: str | None = None
    ) -> Transcription: ...

    def translate(self, text: str, source: str = "auto", target: str = "en-IN") -> Translation: ...

    def chat_json(self, system: str, user: str, schema: dict, name: str) -> dict: ...

    def chat_tools(
        self, system: str, user: str, tools: list[dict]
    ) -> tuple[str | None, list[ToolCall]]: ...

    def speak(self, text: str, language_code: str = "en-IN") -> bytes: ...


@dataclass(frozen=True)
class ToolCall:
    """A function call the model asked for. SupplySure never executes these; they are read as
    proposals and validated in code (D-56)."""

    name: str
    arguments: dict


def _chunks(text: str, limit: int = MAX_TRANSLATE_CHARS) -> list[str]:
    """Split on sentence ends so each chunk fits the translate limit."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:
            parts.append(sentence[:limit])
            sentence = sentence[limit:]
        if len(current) + len(sentence) + 1 > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


class SarvamClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        stt_model: str | None = None,
        llm_model: str | None = None,
        timeout: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self.stt_model = stt_model or os.environ.get("SARVAM_STT_MODEL", "saaras:v4")
        self.llm_model = llm_model or os.environ.get("SARVAM_LLM_MODEL", "sarvam-105b")
        self.tts_model = os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3")
        self.tts_speaker = os.environ.get("SARVAM_TTS_SPEAKER", "kavya")
        self._http = http or httpx.Client(
            base_url=base_url, timeout=timeout, headers={"api-subscription-key": api_key}
        )

    def _check(self, r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                raise SarvamError(err.get("message") or r.text, r.status_code, err.get("code"))
            detail = body.get("detail") if isinstance(body, dict) else None
            raise SarvamError(str(detail or r.text or r.reason_phrase), r.status_code)
        return body

    def transcribe(
        self, audio: bytes, filename: str, content_type: str, language_code: str | None = None
    ) -> Transcription:
        data = {"model": self.stt_model, "mode": "transcribe"}
        if language_code and language_code != "unknown":
            data["language_code"] = language_code
        try:
            r = self._http.post(
                "/speech-to-text", data=data, files={"file": (filename, audio, content_type)}
            )
        except httpx.HTTPError as exc:
            raise SarvamError(f"Could not reach Sarvam: {exc}") from exc
        body = self._check(r)
        return Transcription(
            transcript=(body.get("transcript") or "").strip(),
            language_code=body.get("language_code"),
            request_id=body.get("request_id"),
        )

    def translate(self, text: str, source: str = "auto", target: str = "en-IN") -> Translation:
        out, detected = [], None
        for chunk in _chunks(text):
            payload = {
                "input": chunk,
                "source_language_code": source or "auto",
                "target_language_code": target,
                "model": "mayura:v1",
            }
            try:
                r = self._http.post("/translate", json=payload)
            except httpx.HTTPError as exc:
                raise SarvamError(f"Could not reach Sarvam: {exc}") from exc
            body = self._check(r)
            out.append(body.get("translated_text") or "")
            detected = detected or body.get("source_language_code")
        return Translation(" ".join(p for p in out if p).strip(), detected)

    def chat_json(self, system: str, user: str, schema: dict, name: str, attempts: int = 2) -> dict:
        """Strict-JSON chat. Live sarvam-105b sometimes returns malformed or empty JSON, so the
        call is retried once before giving up (D-57)."""
        payload = {
            "model": self.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,  # extraction, not creativity
            "reasoning_effort": None,  # off: reasoning ate the answer in live tests (D-56)
            "max_tokens": 4096,  # reasoning tokens count against this
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        last: SarvamError | None = None
        for _ in range(max(1, attempts)):
            try:
                return self._chat_json_once(payload)
            except _BadAnswer as exc:  # only answer-shape problems are retried
                last = exc
        raise last  # type: ignore[misc]

    def _chat_json_once(self, payload: dict) -> dict:
        try:
            r = self._http.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise SarvamError(f"Could not reach Sarvam: {exc}") from exc
        body = self._check(r)
        try:
            choice = body["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise _BadAnswer("Unexpected chat response from Sarvam") from exc
        if not content:
            raise _BadAnswer(
                f"Sarvam returned no answer (finish_reason={choice.get('finish_reason')})"
            )
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.S)  # tolerate a fenced block around the JSON
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            raise _BadAnswer("Sarvam's answer was not valid JSON") from None

    def chat_tools(
        self, system: str, user: str, tools: list[dict]
    ) -> tuple[str | None, list[ToolCall]]:
        """One chat turn with function definitions. Returns the text reply and the calls."""
        payload = {
            "model": self.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "reasoning_effort": None,  # off (D-56)
            "max_tokens": 4096,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            r = self._http.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise SarvamError(f"Could not reach Sarvam: {exc}") from exc
        body = self._check(r)
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SarvamError("Unexpected chat response from Sarvam") from exc
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue  # malformed call: dropped, never guessed
            if fn.get("name") and isinstance(args, dict):
                calls.append(ToolCall(fn["name"], args))
        return (message.get("content") or None), calls


    def speak(self, text: str, language_code: str = "en-IN") -> bytes:
        """Text to MP3 speech. Long text is split at sentence ends; MP3 parts are joined."""
        if language_code not in TTS_LANGUAGES:
            raise SarvamError(f"Sarvam cannot speak {language_code}", 400)
        out = b""
        for chunk in _chunks(text.strip(), MAX_TTS_CHARS):
            payload = {
                "text": chunk,
                "language_code": language_code,
                "model": self.tts_model,
                "speaker": self.tts_speaker,
                "output_audio_codec": "mp3",
                "speech_sample_rate": 24000,
                "enable_preprocessing": True,  # reads numbers and dates naturally
            }
            try:
                r = self._http.post("/text-to-speech", json=payload)
            except httpx.HTTPError as exc:
                raise SarvamError(f"Could not reach Sarvam: {exc}") from exc
            body = self._check(r)
            audios = body.get("audios") or []
            if not audios:
                raise SarvamError("Sarvam returned no audio")
            try:
                out += base64.b64decode(audios[0])
            except (ValueError, TypeError) as exc:
                raise SarvamError("Sarvam returned audio that could not be decoded") from exc
        return out


def get_sarvam() -> SarvamClient:
    key = sarvam_api_key()
    if not key:
        raise SarvamNotConfigured("SARVAM_API_KEY is not set. Add it to .env and restart.")
    return SarvamClient(key)


@dataclass
class FakeSarvam:
    """Deterministic stand-in for tests and offline demos."""

    transcripts: dict[bytes, Transcription] = field(default_factory=dict)
    translations: dict[str, str] = field(default_factory=dict)
    chats: dict[str, dict] = field(default_factory=dict)  # keyed by a substring of the user text
    calls: list[tuple[str, object]] = field(default_factory=list)
    stt_model: str = "fake"
    llm_model: str = "fake"

    def transcribe(self, audio, filename, content_type, language_code=None):
        self.calls.append(("transcribe", language_code))
        if audio not in self.transcripts:
            raise SarvamError("fake: no transcript for this audio", 400)
        return self.transcripts[audio]

    def translate(self, text, source="auto", target="en-IN"):
        self.calls.append(("translate", text))
        return Translation(self.translations.get(text, text), source if source != "auto" else None)

    def chat_json(self, system, user, schema, name):
        self.calls.append(("chat", user))
        # Match on the owner's or supplier's message only, not on the factory snapshot.
        m = re.search(r"<(owner_message|supplier_message)>\n(.*?)\n</\1>", user, re.S)
        said = m.group(2) if m else user
        for key, value in self.chats.items():
            if key in said:
                return json.loads(json.dumps(value))
        return {"reply": "I did not find anything to change.", "actions": []}

    def speak(self, text, language_code="en-IN"):
        self.calls.append(("speak", (language_code, text)))
        if language_code not in TTS_LANGUAGES:
            raise SarvamError(f"Sarvam cannot speak {language_code}", 400)
        return b"ID3fake-mp3:" + text.encode("utf-8")

    def chat_tools(self, system, user, tools):
        """Same canned answers as chat_json, returned the way the tools API returns them."""
        raw = self.chat_json(system, user, {}, "tools")
        calls = [
            ToolCall(a["type"], {k: v for k, v in a.items() if k != "type"})
            for a in raw.get("actions", [])
        ]
        return raw.get("reply"), calls
