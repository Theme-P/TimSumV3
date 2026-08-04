from datetime import datetime, timezone
import os
from typing import List, Optional

from pydantic_core import core_schema
from pydantic import BaseModel, Field, field_validator, ConfigDict
from bson import ObjectId


DEFAULT_NTC_MODEL = "ict-ollama/gemma4:31b-it-q4_K_M"
DEFAULT_GLM_FALLBACK_MODEL = "ict-ollama/glm4.7flashq4:latest"
DEFAULT_QWEN_FALLBACK_MODEL = "ict-ollama/qwen2.5:72b-instruct-q4_K_M"
DEFAULT_FALLBACK_MODEL_VALUE = DEFAULT_GLM_FALLBACK_MODEL
DEFAULT_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("NTC_FALLBACK_MODELS", DEFAULT_FALLBACK_MODEL_VALUE).split(",")
    if model.strip()
] or [DEFAULT_GLM_FALLBACK_MODEL]
LEGACY_PRIMARY_MODELS = {"gpt-4.1"}
LEGACY_FALLBACK_MODEL_ALIASES = {
    "qwen2.5:72b-instruct-q4_K_M": DEFAULT_QWEN_FALLBACK_MODEL,
    "seallms-v3-7b:latest": DEFAULT_GLM_FALLBACK_MODEL,
    "ict-ollama/seallms-v3-7b:latest": DEFAULT_GLM_FALLBACK_MODEL,
}
LEGACY_FALLBACK_MODELS_TO_DROP = {"scb10x/typhoon2.1-gemma3-12b"}


def _clean_env_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


def get_env_ntc_model() -> str:
    return _clean_env_value(os.getenv("NTC_MODEL")) or DEFAULT_NTC_MODEL


def normalize_primary_model(value: Optional[str]) -> str:
    model = (value or "").strip()
    if not model or model in LEGACY_PRIMARY_MODELS:
        return get_env_ntc_model()
    return model


def normalize_fallback_models(value) -> List[str]:
    if value is None:
        return list(DEFAULT_FALLBACK_MODELS)
    if isinstance(value, str):
        raw_models = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_models = [str(item).strip() for item in value if item and str(item).strip()]

    models: List[str] = []
    for model in raw_models:
        mapped = LEGACY_FALLBACK_MODEL_ALIASES.get(model, model)
        if mapped in LEGACY_FALLBACK_MODELS_TO_DROP:
            continue
        if mapped not in models:
            models.append(mapped)
    return models or list(DEFAULT_FALLBACK_MODELS)

class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type, _handler):
        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema, _handler):
        return {"type": "string"}

class LLMConfig(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str = "default_fallback"
    primary_model: str = DEFAULT_NTC_MODEL
    fallback_models: List[str] = Field(default_factory=lambda: list(DEFAULT_FALLBACK_MODELS))
    temperature: float = Field(0.3, ge=0.0, le=1.0)
    max_tokens: int = Field(4000, ge=100, le=16000)
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={ObjectId: str}
    )


class LLMConfigUpdate(BaseModel):
    primary_model: Optional[str] = None
    fallback_models: Optional[List[str]] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(None, ge=100, le=16000)

    @field_validator("primary_model")
    @classmethod
    def primary_model_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("primary_model must not be blank")
        return normalize_primary_model(value)

    @field_validator("fallback_models")
    @classmethod
    def clean_fallback_models(cls, value):
        if value is None:
            return value
        cleaned = normalize_fallback_models(value)
        if not cleaned:
            raise ValueError("fallback_models must contain at least one model")
        return cleaned


class LLMConfigTestRequest(BaseModel):
    system_prompt: str
    user_prompt: str
    primary_model: Optional[str] = None
    fallback_models: Optional[List[str]] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(None, ge=100, le=16000)


def get_default_llm_config() -> dict:
    return {
        "name": "default_fallback",
        "primary_model": get_env_ntc_model(),
        "fallback_models": list(DEFAULT_FALLBACK_MODELS),
        "temperature": 0.3,
        "max_tokens": 4000,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": "system",
    }
