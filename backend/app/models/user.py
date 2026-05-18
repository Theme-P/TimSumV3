from typing import Optional
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, SecretStr

class User(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    username: str
    email: str
    password: SecretStr
    role: str
    salt: Optional[str] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )

class UserData(BaseModel):
    id: ObjectId = Field(alias="_id")
    username: str
    email: str
    role: str

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )

class Quota(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    user_id: ObjectId
    value1: float = Field(ge=0, le=100, description="Value 1 must be between 0 and 100")
    value2: float = Field(ge=0, le=100, description="Value 2 must be between 0 and 100")
    value3: float = Field(ge=0, le=100, description="Value 3 must be between 0 and 100")
    value4: float = Field(ge=0, le=100, description="Value 4 must be between 0 and 100")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )
