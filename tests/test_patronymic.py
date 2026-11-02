"""Тесты автоопределения отчества по имени отца (patronymic.py + API-эндпоинт)."""
from __future__ import annotations

import pytest


# ── patronymic_from_father: правила русской патронимики ────────────────────


@pytest.mark.parametrize(
    "father,gender,expected",
    [
        ("Иван", "male", "Иванович"),
        ("Иван", "female", "Ивановна"),
        ("Дмитрий", "male", "Дмитриевич"),
        ("Николай", "female", "Николаевна"),
        ("Сергей", "male", "Сергеевич"),
        ("Михаил", "male", "Михайлович"),
        ("Михаил", "female", "Михайловна"),
        ("Никита", "male", "Никитич"),
        ("Никита", "female", "Никитична"),
        ("Илья", "male", "Ильич"),
        ("Илья", "female", "Ильинична"),
        ("Яков", "male", "Яковлевич"),
        ("Яков", "female", "Яковлевна"),
        ("Лев", "male", "Львович"),
        ("Павел", "male", "Павлович"),
        ("Гавриил", "female", "Гавриловна"),
        ("Пётр", "male", "Петрович"),
        ("Максим", "female", "Максимовна"),
        # регистр и пробелы нормализуются
        ("  ПЁТР ", "male", "Петрович"),
    ],
)
def test_patronymic_rules(father, gender, expected):
    from app.services.patronymic import patronymic_from_father

    assert patronymic_from_father(father, gender) == expected


@pytest.mark.parametrize("father,gender", [("", "male"), (None, "male"), ("Иван", None), ("Иван", "unknown")])
def test_patronymic_no_data(father, gender):
    from app.services.patronymic import patronymic_from_father

    assert patronymic_from_father(father, gender) is None


# ── API-эндпоинт GET /persons/{id}/patronymic-suggestion ───────────────────


def test_suggestion_requires_jwt(app_env):
    r = app_env.get("/api/v1/persons/1/patronymic-suggestion")
    assert r.status_code == 401


@pytest.fixture()
def sample_family(editor_client):
    """Отец Михаил + сын без отчества → (father_id, child_id)."""
    father = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Михаил", "last_name": "Тестов", "gender": "male"},
    ).json()
    child = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Пётр", "last_name": "Тестов", "gender": "male"},
    ).json()
    rel = editor_client.post(
        "/api/v1/relationships",
        json={
            "person_id": father["id"],
            "related_person_id": child["id"],
            "relation_type": "parent_of",
        },
    )
    assert rel.status_code == 201, rel.text
    return father["id"], child["id"]


def test_suggestion_from_father(editor_client, sample_family):
    """Отчество выводится по PARENT_OF-связи с отцом мужского пола."""
    father_id, child_id = sample_family
    r = editor_client.get(f"/api/v1/persons/{child_id}/patronymic-suggestion")
    assert r.status_code == 200
    data = r.json()
    assert data["patronymic"] == "Михайлович"
    assert data["father_id"] == father_id


def test_suggestion_daughter(editor_client):
    father = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Иван", "last_name": "Тестов", "gender": "male"},
    ).json()
    daughter = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Мария", "last_name": "Тестова", "gender": "female"},
    ).json()
    editor_client.post(
        "/api/v1/relationships",
        json={
            "person_id": father["id"],
            "related_person_id": daughter["id"],
            "relation_type": "parent_of",
        },
    )
    data = editor_client.get(f"/api/v1/persons/{daughter['id']}/patronymic-suggestion").json()
    assert data["patronymic"] == "Ивановна"


def test_suggestion_no_father(editor_client):
    person = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Сирота", "last_name": "Тестов", "gender": "male"},
    ).json()
    r = editor_client.get(f"/api/v1/persons/{person['id']}/patronymic-suggestion")
    assert r.status_code == 200
    data = r.json()
    assert data["patronymic"] is None
    assert data["father_name"] is None


def test_suggestion_404(editor_client):
    r = editor_client.get("/api/v1/persons/999999/patronymic-suggestion")
    assert r.status_code == 404
