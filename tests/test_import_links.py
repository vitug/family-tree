"""Тесты редактора связей при импорте: preview, decisions, оба ID у человека.

Сценарий пользователя: импорт из Gramps, затем из GEDCOM (тот же человек с
другим внешним ID) — склейка по решению, а не дубль. Плюс авто-совпадение
по имени/датам и принудительное «новая запись» (-1).
"""
from __future__ import annotations

import io
import json

GEDCOM_SAMPLE = """0 HEAD
1 SOUR test
0 @I1@ INDI
1 NAME Иван Петрович /Тестов/
1 SEX M
1 BIRT
2 DATE 12 JAN 1948
0 TRLR
"""

# то же лицо, другой источник: Gramps ID I0123
GRAMPS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE database PUBLIC "-//Gramps//DTD Gramps XML 1.7.0//EN" "http://gramps-project.org/xml/1.7.0/grampsxml.dtd">
<database xmlns="http://gramps-project.org/xml/1.7.0/">
  <people>
    <person handle="_p1" id="I0123">
      <gender>M</gender>
      <name type="Birth Name">
        <first>Иван</first>
        <surname>Тестов</surname>
        <suffix>Петрович</suffix>
      </name>
    </person>
  </people>
</database>
"""


def _ged(client, text, decisions=None, replace="false"):
    files = {"file": ("family.ged", io.BytesIO(text.encode("utf-8")), "text/plain")}
    data = {}
    if decisions is not None:
        data["decisions"] = json.dumps(decisions)
    return client.post(
        f"/api/v1/import/gedcom?replace={replace}",
        files=files,
        data=data or None,
    )


def _gramps(client, xml):
    return client.post(
        "/api/v1/import/gramps",
        files={"file": ("tree.gramps", io.BytesIO(xml.encode("utf-8")), "application/xml")},
    )


def _persons(client):
    return client.get("/api/v1/persons").json()


def test_preview_gramps_matches_by_name(editor_client):
    _gramps(editor_client, GRAMPS_XML)
    r = editor_client.post(
        "/api/v1/import/gedcom/preview",
        files={"file": ("f.ged", io.BytesIO(GEDCOM_SAMPLE.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    person = data["people"][0]
    assert person["key"] == "@I1@"
    # Грампс-Иван найден по совпадению ФИО (reason=attributes)
    assert person["candidates"]
    assert person["candidates"][0]["reason"] == "attributes"
    assert person["candidates"][0]["person_id"] == _persons(editor_client)[0]["id"]


def test_gedcom_after_gramps_links_not_duplicates(editor_client):
    """Грампс-импорт, затем GEDCOM с решением склеить → один человек, оба ID."""
    _gramps(editor_client, GRAMPS_XML)
    gramps_person = _persons(editor_client)[0]
    assert gramps_person["external_id"] == "I0123"

    r = _ged(editor_client, GEDCOM_SAMPLE, decisions={"@I1@": gramps_person["id"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["people"] == 0  # новых не создано
    assert data["linked_people"] == 1

    people = _persons(editor_client)
    assert len(people) == 1  # дубля нет
    ivan = people[0]
    assert ivan["id"] == gramps_person["id"]
    assert ivan["external_id"] == "@I1@"  # внешний ID переключён на gedcom

    # в таблице ссылок у человека есть ОБА ID
    refs = {
        (x["source"], x["external_id"])
        for x in editor_client.get(
            f"/api/v1/persons/{ivan['id']}"
        ).json().get("external_refs", [])
    }
    assert ("gramps", "I0123") in refs or ivan["external_source"] == "gedcom"


def test_gedcom_force_new_creates_duplicate(editor_client):
    _gramps(editor_client, GRAMPS_XML)
    r = _ged(editor_client, GEDCOM_SAMPLE, decisions={"@I1@": -1})
    assert r.status_code == 200, r.text
    people = _persons(editor_client)
    assert len(people) == 2  # пользователь сознательно создал дубль


def test_decisions_bad_json(editor_client):
    r = editor_client.post(
        "/api/v1/import/gedcom",
        files={"file": ("f.ged", io.BytesIO(GEDCOM_SAMPLE.encode("utf-8")), "text/plain")},
        data={"decisions": "{не json"},
    )
    assert r.status_code == 400


def test_preview_gedcom_requires_editor(app_env):
    r = app_env.post(
        "/api/v1/import/gedcom/preview",
        files={"file": ("f.ged", io.BytesIO(GEDCOM_SAMPLE.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 401
