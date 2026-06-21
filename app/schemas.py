from typing import Literal

from pydantic import BaseModel, Field


class OTPRequest(BaseModel):
    email: str


class OTPVerify(BaseModel):
    email: str
    otp: str = Field(min_length=4, max_length=12)


class TeachingRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    course: str = "General"
    audience: str = "University students"
    duration_minutes: int = Field(default=60, ge=15, le=300)
    difficulty: Literal["introductory", "intermediate", "advanced"] = "intermediate"
    use_web_search: bool = True
    collections: list[str] = Field(default_factory=list)
    instructions: str = ""


class ResearchRequest(BaseModel):
    research_topic: str = Field(min_length=3, max_length=1000)
    discipline: str = "General"
    use_web_search: bool = True
    collections: list[str] = Field(default_factory=lambda: ["research_papers"])
    instructions: str = ""
