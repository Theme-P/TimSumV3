from fastapi import APIRouter, Depends, HTTPException, Body, Request
from typing import List
import logging

from ..models.meeting_template import MeetingTemplate, MeetingTemplateUpdate
from ..services.mongo import MongoService
from ..core.auth import get_current_admin
from ..models.user import UserData

logger = logging.getLogger(__name__)

router = APIRouter()

def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service

@router.get("", response_model=List[MeetingTemplate])
def list_meeting_templates(
    db: MongoService = Depends(get_mongo_service),
    admin_user: UserData = Depends(get_current_admin)
):
    """
    Get all meeting templates (Admin only).
    """
    templates = db.get_all_meeting_templates()
    return templates

@router.put("/{meeting_type_id}", response_model=dict)
def update_meeting_template(
    meeting_type_id: int,
    update_data: MeetingTemplateUpdate,
    db: MongoService = Depends(get_mongo_service),
    admin_user: UserData = Depends(get_current_admin)
):
    """
    Update a meeting template by its ID (Admin only).
    """
    update_dict = update_data.model_dump(exclude_unset=True)
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_dict["updated_by"] = getattr(admin_user, "email", "admin")
    success = db.update_meeting_template(meeting_type_id, update_dict)
    if not success:
        raise HTTPException(status_code=404, detail="Meeting template not found")

    return {"message": "Meeting template updated successfully"}

@router.post("/test", response_model=dict)
def test_meeting_template(
    payload: dict = Body(..., description="Payload containing system_prompt, user_prompt, temperature, etc."),
    admin_user: UserData = Depends(get_current_admin)
):
    """
    Test a prompt template (Admin only).
    Calls the LLM directly to preview the output.
    """
    from ..services.summarizer import _call_llm_with_fallback
    
    system_prompt = payload.get("system_prompt", "")
    user_prompt = payload.get("user_prompt", "")
    temperature = payload.get("temperature", 0.4)
    max_tokens = payload.get("max_tokens", 4000)
    
    if not system_prompt or not user_prompt:
        raise HTTPException(status_code=400, detail="Missing prompts")
        
    try:
        # Avoid circular import by getting db lazily or assuming summarizer handles it
        result = _call_llm_with_fallback(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=60
        )
        return {"result": result}
    except Exception as e:
        logger.error(f"Error testing template: {e}")
        raise HTTPException(status_code=500, detail=str(e))
