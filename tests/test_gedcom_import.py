"""Тесты GEDCOM-импорта и сохранения external_id (Gramps/GEDCOM)."""
from __future__ import annotations

import io

GEDCOM_SAMPLE = """0 HEAD
1 SOUR test
2 NAME Тест
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Иван Петрович /Тестов/
1 SEX M
1 BIRT
2 DATE 12 JAN 1948
2 PLAC Москва
1 DEAT
2 DATE 2001
0 @I2@ INDI
1 NAME Мария Николаевна /Тестова/
1 SEX F
1 BIRT
2 DATE ABT 1950
0 @I3@ INDI
1 NAME Пётр Иванович /Тестов/
1 SEX M
1 BIRT
2 DATE 1975
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""


def _upload(editor_client, text, replace="false"):
    return editor_client.post(
        f"/api/v1/import/gedcom?replace={replace}",
        files={"file": ("family.ged", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )


def test_gedcom_import_people_and_external_ids(editor_client):
    r = _upload(editor_client, GEDCOM_SAMPLE)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["people"] == 3
    assert data["families"] == 1
    assert data["relationships"] == 3  # супруги + 2 родителя→ребёнок

    people = {p["first_name"]: p for p in editor_client.get("/api/v1/persons").json()}
    assert people["Иван"]["external_id"] == "@I1@"
    assert people["Иван"]["external_source"] == "gedcom"
    assert people["Иван"]["middle_name"] == "Петрович"
    assert people["Иван"]["last_name"] == "Тестов"
    assert people["Иван"]["birth_date"] == "1948-01-12"
    assert people["Мария"]["birth_date"] == "1950-01-01"  # ABT → год
    assert people["Мария"]["gender"] == "female"


def test_gedcom_reimport_updates_no_duplicates(editor_client):
    _upload(editor_client, GEDCOM_SAMPLE)
    people_before = editor_client.get("/api/v1/persons").json()
    count_before = len(people_before)

    # тот же файл с изменённой датой — люди обновляются, не дублируются
    changed = GEDCOM_SAMPLE.replace("12 JAN 1948", "15 FEB 1948")
    r = _upload(editor_client, changed)
    assert r.status_code == 200
    data = r.json()
    assert data["people"] == 0
    assert data["updated_people"] == 3
    assert data["relationships"] == 0  # связи уже есть, не дублируются

    people_after = editor_client.get("/api/v1/persons").json()
    assert len(people_after) == count_before
    ivan = next(p for p in people_after if p["first_name"] == "Иван")
    assert ivan["birth_date"] == "1948-02-15"


def test_gedcom_requires_editor(app_env):
    r = app_env.post(
        "/api/v1/import/gedcom",
        files={"file": ("x.ged", io.BytesIO(b"0 HEAD"), "text/plain")},
    )
    assert r.status_code == 401


def test_gedcom_rejects_garbage(editor_client):
    r = _upload(editor_client, "не гедком вообще")
    assert r.status_code == 400


def test_gedcom_replace_clears(editor_client):
    _upload(editor_client, GEDCOM_SAMPLE)
    r = _upload(editor_client, GEDCOM_SAMPLE, replace="true")
    assert r.status_code == 200
    assert r.json()["people"] == 3  # созданы заново в чистой базе
    assert len(editor_client.get("/api/v1/persons").json()) == 3
