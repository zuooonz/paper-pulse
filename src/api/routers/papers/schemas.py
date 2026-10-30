"""Pydantic request models for the papers API."""

from pydantic import BaseModel
from pydantic import Field
from typing import List
from typing import Optional, Literal
from pydantic import StrictBool
from pydantic import field_validator

class BatchDoiRequest(BaseModel):
    dois: List[str] = Field(max_length=100)
    translate_zh: bool = True

class BatchDoiResultItem(BaseModel):
    doi: str
    status: str
    paper_id: Optional[int] = None
    title: Optional[str] = None
    message: Optional[str] = None

class GenerateDraftRequest(BaseModel):
    profile: str = "default"
    themes: Optional[List[str]] = None
    model: Optional[str] = None
    force_regen: bool = False

class GenerateVideoRequest(BaseModel):
    resolution: str = "720P"           # 720P or 1080P
    ratio: str = "16:9"                # 16:9, 9:16, 1:1
    duration: int = 5                  # seconds (5 or 10)
    style: str = "professional"        # storyboard style: professional, popular_science, news

class BatchGenerateDraftRequest(BaseModel):
    paper_ids: List[int] = Field(max_length=100)
    profile: str = "default"
    themes: Optional[List[str]] = None
    model: Optional[str] = None
    force_regen: bool = False

class BatchReviewRequest(BaseModel):
    paper_ids: List[int] = Field(max_length=50)
    profile: str = "default"
    themes: Optional[List[str]] = None
    model: Optional[str] = None

class BatchGenerateWordSummaryRequest(BaseModel):
    paper_ids: List[int] = Field(max_length=100)
    profile: str = "report"
    model: Optional[str] = None

class MultimodalRelationRequest(BaseModel):
    method: str
    timing: str

class UpdatePaperRequest(BaseModel):
    title_zh: Optional[str] = None
    journal_name: Optional[str] = None
    research_fields: Optional[List[str]] = None
    paradigm: Optional[List[str]] = None
    subject_diseases: Optional[List[str]] = None
    research_topics: Optional[List[str]] = None
    neuroscience_subtopics: Optional[List[str]] = None
    study_design: Optional[str] = None
    paradigms: Optional[List[str]] = None
    uses_multimodal: Optional[bool] = None
    multimodal_methods: Optional[List[str]] = None
    multimodal_relations: Optional[List[MultimodalRelationRequest]] = None
    corresponding_region: Optional[str] = None
    first_authors: Optional[List[dict]] = None
    corresponding_authors: Optional[List[dict]] = None
    authors: Optional[List[dict]] = None
    equipment_brand: Optional[List[str]] = None
    channel_count: Optional[int] = None
    device_citation_text: Optional[str] = None
    citation_error_type: Optional[str] = None
    uses_target_software: Optional[StrictBool] = None
    accepted_date: Optional[str] = None
    notes: Optional[str] = None
    sample_size: Optional[int] = None
    tags: Optional[List[str]] = None
    entity_label: Optional[str] = None

class UpdateDoiRequest(BaseModel):
    new_doi: str

class DeleteReasonBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("删除原因至少填写 2 个字符")
        return value

class DeletePaperBody(DeleteReasonBody):
    pass

class BatchDeleteBody(DeleteReasonBody):
    paper_ids: List[int] = Field(max_length=200)

class BatchDownloadBody(BaseModel):
    paper_ids: List[int] = Field(max_length=200)

class ReviewRequest(BaseModel):
    decision: Literal["approve", "exclude", "reset"]
    note: str | None = Field(default=None, max_length=2000)
    entity_label: str | None = None
