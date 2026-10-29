"""Маскировка личной информации в заметках для пользователей с правами просмотра (viewer)."""
from __future__ import annotations

import re

_PHONE_RE = re.compile(r"\+?[78][\s\-()]?\d{3}[\s\-()]?\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}|\+\d[\d\s\-()]{9,}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#_domofon/access codes: sequences like #1495# or "домофон: 0000"
_CODE_RE = re.compile(r"#\d{3,}#|\b(?:домофон|код)\s*[:—-]?\s*\d{3,}\b", re.IGNORECASE)
# адреса: улица/проспект/пр-т + номер, кв.
_ADDR_RE = re.compile(
    r"\b(?:ул\.?|улица|просп\.?|проспект|пр-т|пер\.?|переулок|д\.?|дом)\s*[\wа-яё]+"
    r"(?:\s*[,/]\s*(?:кв\.?|корп\.?|к\.?|стр\.?|подъезд)\s*\d+\w*)*",
    re.IGNORECASE,
)

_PHONE_SUB = "[телефон скрыт]"
_EMAIL_SUB = "[e-mail скрыт]"
_CODE_SUB = "[код скрыт]"
_ADDR_SUB = "[адрес скрыт]"


def mask_notes(notes: str | None) -> str | None:
    """Маскирует телефоны, e-mail, коды доступа и адреса в тексте заметок."""
    if not notes:
        return notes
    text = notes
    text = _EMAIL_RE.sub(_EMAIL_SUB, text)
    text = _PHONE_RE.sub(_PHONE_SUB, text)
    text = _CODE_RE.sub(_CODE_SUB, text)
    text = _ADDR_RE.sub(_ADDR_SUB, text)
    return text


def viewer_allowed_ids(db, user: dict) -> set[int]:
    """id людей, по которым viewer'у (по персональной ссылке) разрешены полные заметки."""
    from app.models import NoteAccessRequest

    if user.get("role") != "viewer" or user.get("person_id") is None:
        return set()
    rows = (
        db.query(NoteAccessRequest.person_id)
        .filter(
            NoteAccessRequest.requester_person_id == user["person_id"],
            NoteAccessRequest.status == "approved",
        )
        .all()
    )
    return {r[0] for r in rows}


def mask_person_read(person, allowed_ids: set[int] | None = None) -> None:
    """Мутирует PersonRead-подобный объект: маскирует поле notes (in place).

    allowed_ids — id людей, по которым этому viewer'у разрешён полный доступ.
    """
    if person is None:
        return
    pid = getattr(person, "id", None)
    if allowed_ids and pid in allowed_ids:
        return
    if getattr(person, "notes", None):
        person.notes = mask_notes(person.notes)


def mask_tree_node(node, allowed_ids: set[int] | None = None) -> None:
    """Рекурсивно маскирует notes в TreeNode (person + предки/потомки)."""
    if node is None:
        return
    mask_person_read(getattr(node, "person", None), allowed_ids)
    for child in getattr(node, "children", None) or []:
        mask_tree_node(child, allowed_ids)
    for parent in getattr(node, "parents", None) or []:
        mask_tree_node(parent, allowed_ids)


def mask_member_list(members, allowed_ids: set[int] | None = None) -> None:
    for m in members or []:
        mask_person_read(m, allowed_ids)


def mask_revisions(revisions) -> None:
    """История правок: маскирует notes и diff-значения поля notes."""
    for r in revisions or []:
        val = getattr(r, "notes", None)
        if val:
            setattr(r, "notes", mask_notes(val))
        changes = getattr(r, "changes", None)
        if isinstance(changes, list):
            for ch in changes:
                if isinstance(ch, dict) and ch.get("field") == "notes":
                    for key in ("old", "new"):
                        if ch.get(key):
                            ch[key] = mask_notes(ch[key])
        for field_name in ("data", "snapshot"):
            d = getattr(r, field_name, None)
            if isinstance(d, dict) and d.get("notes"):
                d["notes"] = mask_notes(d["notes"])
