from typing import List, Optional
from pydantic import BaseModel, Field
from bson import ObjectId

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")

class LLMConfig(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str = "default_fallback"
    primary_model: str = "gpt-4.1"
    fallback_models: List[str] = ["qwen2.5:72b-instruct-q4_K_M", "scb10x/typhoon2.1-gemma3-12b"]
    temperature: float = 0.3
    max_tokens: int = 4000
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
