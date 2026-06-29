import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CampaignBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    product: str = Field(min_length=1, max_length=160)
    audience: str = Field(min_length=1, max_length=240)
    status: str = Field(default="drafting", max_length=40)
    due_date: date | None = None
    owner: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1)
    tone: str = Field(min_length=1, max_length=160)
    brief: str = Field(min_length=1)
    channels: list[str] = Field(default_factory=list)
    brand_inputs: list[str] = Field(default_factory=list)


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    product: str | None = Field(default=None, min_length=1, max_length=160)
    audience: str | None = Field(default=None, min_length=1, max_length=240)
    status: str | None = Field(default=None, max_length=40)
    due_date: date | None = None
    owner: str | None = Field(default=None, min_length=1, max_length=120)
    goal: str | None = Field(default=None, min_length=1)
    tone: str | None = Field(default=None, min_length=1, max_length=160)
    brief: str | None = Field(default=None, min_length=1)
    channels: list[str] | None = None
    brand_inputs: list[str] | None = None


class CampaignRead(CampaignBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
