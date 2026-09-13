from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class UploadSessionCreate(BaseModel):
    expense_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class RunCreate(BaseModel):
    upload_id: str = Field(min_length=1, max_length=100)
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")


class RunDelete(BaseModel):
    confirm_run_id: str = Field(min_length=1, max_length=100)


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=1000)

    @field_validator("decided_by")
    @classmethod
    def strip_actor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decided_by cannot be blank")
        return value.strip()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    actor: str = Field(default="user", min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=100)


class APIKeyUpdate(BaseModel):
    api_key: str = Field(min_length=20, max_length=500)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("sk-"):
            raise ValueError("DeepSeek API Key must start with sk-")
        return value


class MessageResponse(BaseModel):
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
