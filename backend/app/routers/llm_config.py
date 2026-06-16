from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..core.auth import get_current_admin
from ..models.llm_config import (
    LLMConfig,
    LLMConfigTestRequest,
    LLMConfigUpdate,
    get_default_llm_config,
)
from ..models.user import UserData
from ..services.mongo import MongoService

router = APIRouter()


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


@router.get("", response_model=List[LLMConfig])
def list_llm_configs(
    db: MongoService = Depends(get_mongo_service),
    _admin_user: UserData = Depends(get_current_admin),
):
    """List LLM runtime configs."""
    configs = db.get_all_llm_configs()
    if configs:
        return configs

    default_config = get_default_llm_config()
    db.upsert_llm_config(default_config["name"], default_config)
    saved = db.get_llm_config(default_config["name"])
    return [saved] if saved else [default_config]


@router.post("/test", response_model=dict)
def test_llm_config(
    payload: LLMConfigTestRequest = Body(...),
    db: MongoService = Depends(get_mongo_service),
    _admin_user: UserData = Depends(get_current_admin),
):
    """Test prompts using the current or supplied LLM runtime config."""
    if not payload.system_prompt.strip() or not payload.user_prompt.strip():
        raise HTTPException(status_code=400, detail="Missing prompts")

    from ..services.summarizer import _call_llm_with_fallback

    base_config = db.get_llm_config("default_fallback") or get_default_llm_config()
    override_config = {
        **base_config,
        "primary_model": payload.primary_model or base_config.get("primary_model"),
        "fallback_models": payload.fallback_models or base_config.get("fallback_models", []),
        "temperature": payload.temperature if payload.temperature is not None else base_config.get("temperature", 0.3),
        "max_tokens": payload.max_tokens if payload.max_tokens is not None else base_config.get("max_tokens", 4000),
    }

    result = _call_llm_with_fallback(
        system_prompt=payload.system_prompt,
        user_prompt=payload.user_prompt,
        temperature=override_config["temperature"],
        max_tokens=override_config["max_tokens"],
        timeout=60,
        mongo_service=db,
        override_config=override_config,
    )
    if not result:
        raise HTTPException(status_code=502, detail="All configured LLM providers failed")

    return {
        "result": result,
        "config": {
            "primary_model": override_config["primary_model"],
            "fallback_models": override_config["fallback_models"],
            "temperature": override_config["temperature"],
            "max_tokens": override_config["max_tokens"],
        },
    }


@router.get("/{name}", response_model=LLMConfig)
def get_llm_config(
    name: str,
    db: MongoService = Depends(get_mongo_service),
    _admin_user: UserData = Depends(get_current_admin),
):
    """Get a single LLM runtime config by name."""
    config = db.get_llm_config(name)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    return config


@router.put("/{name}", response_model=LLMConfig)
def update_llm_config(
    name: str,
    update_data: LLMConfigUpdate,
    db: MongoService = Depends(get_mongo_service),
    admin_user: UserData = Depends(get_current_admin),
):
    """Create or update an LLM runtime config."""
    update_dict = update_data.model_dump(exclude_unset=True)
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    existing = db.get_llm_config(name) or get_default_llm_config()
    existing.pop("_id", None)
    merged = {
        **existing,
        **update_dict,
        "name": name,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": getattr(admin_user, "email", "admin"),
    }
    db.upsert_llm_config(name, merged)

    saved = db.get_llm_config(name)
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save LLM config")
    return saved
