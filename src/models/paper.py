from pydantic import BaseModel, ConfigDict, Field, field_validator


class Paper(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    venue: str = Field(min_length=1)
    year: int = Field(ge=1900, le=2200)
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    citation_count: int | None = Field(default=None, ge=0)
    topics: list[str] = Field(default_factory=list)
    source: str = "dblp"

    @field_validator("title", "venue")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
