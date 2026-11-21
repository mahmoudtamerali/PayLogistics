"""Request/response shapes and input validation for visit notes (T54)."""
import re
import unicodedata
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config

# Identifiers: letters, digits, underscore, hyphen. No dots, slashes or spaces,
# so an id can never change the meaning of a key or a path.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
NOTE_ID_PATTERN = r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$"
_ID_RE = re.compile(ID_PATTERN)
_NOTE_ID_RE = re.compile(NOTE_ID_PATTERN)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")


def valid_id(value: Optional[str]) -> bool:
    return bool(value) and _ID_RE.fullmatch(value) is not None


def valid_note_id(value: Optional[str]) -> bool:
    return bool(value) and _NOTE_ID_RE.fullmatch(value) is not None


def valid_request_id(value: Optional[str]) -> bool:
    return bool(value) and _REQUEST_ID_RE.fullmatch(value) is not None


class NoteIn(BaseModel):
    """Body of POST /patients/{patient_id}/notes. Unknown fields are rejected,
    so a caller cannot set author_id, created_at or note_id."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visit_date: date
    text: str = Field(min_length=1, max_length=config.MAX_NOTE_CHARS)

    @field_validator("text")
    @classmethod
    def no_control_characters(cls, v: str) -> str:
        # Allow newline and tab; reject other control/format characters
        # (NUL, escapes, bidi overrides) that break logs and displays.
        for ch in v:
            if ch in "\n\t":
                continue
            if unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
                raise ValueError("control characters are not allowed")
        return v


class NoteOut(BaseModel):
    note_id: str
    patient_id: str
    author_id: str
    visit_date: str
    text: str
    created_at: str


class NoteList(BaseModel):
    patient_id: str
    notes: list[NoteOut]
