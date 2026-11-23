from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(str, Enum):
    interviewer = "interviewer"
    respondent = "respondent"
    third_party = "third_party"
    unknown = "unknown"


class Segment(Record):
    segment_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text_raw: str = Field(min_length=1)
    speaker_id: str | None = None
    speaker_role: Role = Role.unknown
    role_confidence: float = Field(default=0, ge=0, le=1)
    requires_review: bool = True
    overlapping_speakers: list[str] = Field(default_factory=list)
    alignment_confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms")
        return self


class Turn(Record):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker_id: str

    @model_validator(mode="after")
    def interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Invalid speaker interval")
        return self


class Transcript(Record):
    schema_version: Literal["1.0"] = "1.0"
    language: Literal["ru"] = "ru"
    source_sha256: str
    source_kind: Literal["audio", "synthetic_text"] = "audio"
    duration_seconds: float = Field(gt=0)
    segments: list[Segment]

    @model_validator(mode="after")
    def ids(self):
        keys = [s.segment_id for s in self.segments]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate segment IDs")
        if any(s.end_ms > self.duration_seconds * 1000 + 100 for s in self.segments):
            raise ValueError("Segment exceeds audio duration")
        return self


class SpeakerRole(Record):
    speaker_id: str
    role: Role
    confidence: float = Field(ge=0, le=1)
    source_segment_ids: list[str] = Field(min_length=1)
    reason: str


class RoleResult(Record):
    speakers: list[SpeakerRole]


class Mention(Record):
    segment_id: str
    text: str = Field(min_length=1)


class Person(Record):
    person_id: str
    name: str | None = None
    relationship_to_respondent: str | None = None
    identity_status: Literal["explicit", "inferred_from_context", "ambiguous"]
    mentions: list[Mention] = Field(min_length=1)


class DateInfo(Record):
    value: str
    precision: Literal["day", "month", "year", "period", "unknown"]
    approximate: bool


class Evidence(Record):
    segment_id: str
    quote: str = Field(min_length=1)


class Fact(Record):
    fact_id: str
    human_reviewed: bool = False
    subject_person_id: str | None
    predicate: str = Field(min_length=1)
    value: str = Field(min_length=1)
    date: DateInfo | None = None
    evidence: list[Evidence] = Field(min_length=1)
    evidence_type: Literal["reported", "inferred", "unclear"]
    certainty: Literal["explicit", "approximate", "ambiguous"]
    has_conflict: bool = False
    conflict_group_id: str | None = None
    requires_review: bool = False


class Relation(Record):
    subject_person_id: str
    object_person_id: str
    relationship: str
    evidence: list[Evidence] = Field(min_length=1)
    requires_review: bool = True


class Conflict(Record):
    conflict_group_id: str
    fact_ids: list[str] = Field(min_length=2)
    explanation: str


class FactSet(Record):
    schema_version: Literal["1.0"] = "1.0"
    people: list[Person]
    facts: list[Fact]
    relations: list[Relation] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class Settings(Record):
    whisper_model: str
    diarization_config: str
    diarization_backend: Literal["pyannote", "sherpa_onnx"] = "pyannote"
    llm_model: str
    device: Literal["cpu", "cuda"] = "cpu"
    compute_type: str = "int8"
    llm_gpu_layers: int = Field(default=0, ge=-1)
    llm_context: int = Field(default=8192, ge=2048)
    threads: int = Field(default=4, ge=1)
    min_speakers: int = Field(default=2, ge=2, le=3)
    max_speakers: int = Field(default=3, ge=2, le=3)
    role_threshold: float = Field(default=0.85, ge=0, le=1)
    chunk_chars: int = Field(default=5000, ge=500, le=20000)
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @model_validator(mode="after")
    def speakers(self):
        if self.min_speakers > self.max_speakers:
            raise ValueError("min_speakers exceeds max_speakers")
        return self
