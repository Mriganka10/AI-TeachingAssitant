from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    generate_video: bool = False
    video_min_minutes: int | None = Field(default=None, ge=1)
    video_max_minutes: int | None = Field(default=None, ge=1)
    collections: list[str] = Field(default_factory=list)
    instructions: str = ""

    @model_validator(mode="after")
    def validate_video_duration_range(self):
        if (self.video_min_minutes is None) != (self.video_max_minutes is None):
            raise ValueError("video_min_minutes and video_max_minutes must be provided together")
        if (
            self.video_min_minutes is not None
            and self.video_max_minutes is not None
            and self.video_min_minutes > self.video_max_minutes
        ):
            raise ValueError("video_min_minutes cannot exceed video_max_minutes")
        return self


class ResearchRequest(BaseModel):
    """Review one uploaded manuscript and, optionally, return a revised version of it."""

    manuscript_document_id: str = Field(min_length=1, max_length=64)
    discipline: str = Field(default="", max_length=200)
    paper_type: Literal[
        "auto", "empirical", "review", "theoretical", "methods", "case_study", "other"
    ] = "auto"
    target_venue: str = Field(default="", max_length=300)
    author_name: str = Field(default="", max_length=200)
    generate_revision: bool = True
    use_web_search: bool = False
    # Other uploaded papers used to position the manuscript in its literature.
    collections: list[str] = Field(default_factory=lambda: ["research_papers"])
    instructions: str = Field(default="", max_length=4000)
