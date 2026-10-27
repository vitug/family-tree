from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import Gender, RelationType


class PersonBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    maiden_name: str | None = Field(None, max_length=100)
    gender: Gender = Gender.UNKNOWN
    birth_date: date | None = None
    death_date: date | None = None
    birth_place: str | None = Field(None, max_length=255)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> PersonBase:
        if self.birth_date and self.death_date and self.death_date < self.birth_date:
            raise ValueError("Дата смерти не может быть раньше даты рождения")
        return self


class PersonCreate(PersonBase):
    pass


class PersonUpdate(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    maiden_name: str | None = Field(None, max_length=100)
    gender: Gender | None = None
    birth_date: date | None = None
    death_date: date | None = None
    birth_place: str | None = Field(None, max_length=255)
    notes: str | None = None


class PersonRead(PersonBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    photo_url: str | None = None
    created_at: datetime
    updated_at: datetime


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int | None
    url: str
    original_src: str | None = None
    mime_type: str | None = None
    description: str | None = None
    is_primary: bool = False


class RelationshipCreate(BaseModel):
    person_id: int
    related_person_id: int
    relation_type: RelationType
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None

    @field_validator("related_person_id")
    @classmethod
    def not_self(cls, v: int, info) -> int:
        person_id = info.data.get("person_id")
        if person_id is not None and v == person_id:
            raise ValueError("Нельзя создать связь человека с самим собой")
        return v


class RelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    related_person_id: int
    relation_type: RelationType
    start_date: date | None
    end_date: date | None
    notes: str | None
    created_at: datetime


class FamilyMember(BaseModel):
    """Узел семейного дерева для API-ответа."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    first_name: str
    last_name: str | None
    gender: Gender
    birth_date: date | None
    death_date: date | None
    photo_url: str | None = None
    relation_to_root: str | None = None
    generation: int = 0


class TreeNode(BaseModel):
    person: FamilyMember
    parents: list[TreeNode] = Field(default_factory=list)
    children: list[TreeNode] = Field(default_factory=list)
    spouses: list[FamilyMember] = Field(default_factory=list)


class TreeResponse(BaseModel):
    root_id: int
    depth_up: int
    depth_down: int
    nodes_count: int
    tree: TreeNode


class AncestorsResponse(BaseModel):
    person_id: int
    generations: list[list[FamilyMember]]


class DescendantsResponse(BaseModel):
    person_id: int
    generations: list[list[FamilyMember]]


class RelativesResponse(BaseModel):
    person_id: int
    parents: list[FamilyMember]
    children: list[FamilyMember]
    spouses: list[FamilyMember]
    siblings: list[FamilyMember]


class PathResponse(BaseModel):
    from_id: int
    to_id: int
    path: list[FamilyMember]
    steps: list[str]


class PersonRevisionRead(BaseModel):
    """Ревизия из истории правок человека."""

    id: int
    person_id: int
    person_name: str | None
    action: str  # create | update | delete
    changes: list[dict] | None  # [{field, old, new}]
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _parse_changes_json(cls, data):
        # changes в БД — TEXT с JSON
        if hasattr(data, "changes") and isinstance(data.changes, str):
            import json as _json

            try:
                data.changes = _json.loads(data.changes)
            except ValueError:
                data.changes = None
        return data
