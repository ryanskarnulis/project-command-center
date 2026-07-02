from __future__ import annotations


from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCDateTime


class ProfileRead(BaseModel):
    """An effective (merged) model profile, plus which fields were overridden."""

    name: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    response_mode: str
    system_prompt: str
    overridden_fields: list[str]


class ProfileUpdate(BaseModel):
    """Editable profile fields. Provider / response_mode / prompt are not editable here."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)


class PromptRead(BaseModel):
    name: str
    text: str


class PromptUpdate(BaseModel):
    text: str


class EvalCaseResult(BaseModel):
    name: str
    passed: bool
    reason: str


class EvalRunResult(BaseModel):
    suite: str
    passed: int
    total: int
    cases: list[EvalCaseResult]


class OllamaStatus(BaseModel):
    """Liveness of the local Ollama runtime, for the settings health panel."""

    reachable: bool
    host: str


class EvalRunRecord(BaseModel):
    """A persisted eval run (history). No per-case detail — that lives only in
    the in-memory ``EvalRunResult`` returned at run time."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    suite: str
    passed: int
    total: int
    created_at: UTCDateTime
