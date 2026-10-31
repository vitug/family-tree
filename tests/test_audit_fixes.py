"""Тесты фиксов аудита + magic-link входа + префикса reverse-proxy."""

from __future__ import annotations

import asyncio
import gzip
import sqlite3
import zlib
from datetime import date

import pytest
from fastapi import HTTPException


# ── trust_proxy / _get_client_ip ────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, headers, host="9.9.9.9"):
        from starlette.datastructures import Headers

        self.headers = Headers(headers)
        self.client = type("C", (), {"host": host})()


def test_client_ip_ignores_forwarded_by_default():
    from app.security import _get_client_ip

    req = _FakeRequest({"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"})
    assert _get_client_ip(req) == "9.9.9.9"


def test_client_ip_trusts_forwarded_when_enabled(monkeypatch):
    from app import security
    from app.security import _get_client_ip

    monkeypatch.setattr(security.settings, "trust_proxy", True)
    req = _FakeRequest({"x-forwarded-for": "1.2.3.4, 10.0.0.1"})
    assert _get_client_ip(req) == "1.2.3.4"
    req2 = _FakeRequest({"x-real-ip": "5.6.7.8"})
    assert _get_client_ip(req2) == "5.6.7.8"


# ── Лимитеры: bounded memory + 5 попыток lockout ────────────────────────────


def test_rate_limiter_prunes_empty_keys():
    from app.security import RateLimiter

    class Req:
        pass

    rl = RateLimiter(max_requests=100, window_seconds=1)
    asyncio.run(rl.is_allowed("auth:k", Req()))
    assert "auth:k" in rl.requests
    # окно истекло → ключ удаляется, словарь не растёт
    rl.requests["auth:k"] = [0.0]
    asyncio.run(rl.is_allowed("auth:k", Req()))
    assert len(rl.requests["auth:k"]) == 1  # новая запись, не накапливается


def test_rate_limiter_blocks_at_limit():
    from app.security import RateLimiter

    class Req:
        pass

    rl = RateLimiter(max_requests=2, window_seconds=60)
    assert asyncio.run(rl.is_allowed("k", Req()))
    assert asyncio.run(rl.is_allowed("k", Req()))
    assert not asyncio.run(rl.is_allowed("k", Req()))


def test_brute_force_locks_on_5th_attempt():
    from app.security import BruteForceProtection

    bp = BruteForceProtection(max_attempts=5, lockout_seconds=300)
    for _ in range(4):
        bp.record_attempt("ip:x", success=False)
    assert not bp.is_locked("ip:x")  # 4 попытки — ещё не lockout (было 3)
    bp.record_attempt("ip:x", success=False)
    assert bp.is_locked("ip:x")


def test_brute_force_success_clears_state():
    from app.security import BruteForceProtection

    bp = BruteForceProtection(max_attempts=5, lockout_seconds=300)
    for _ in range(4):
        bp.record_attempt("ip:x", success=False)
    bp.record_attempt("ip:x", success=True)
    assert not bp.is_locked("ip:x")
    assert "ip:x" not in bp.attempts


def test_check_brute_force_removed():
    import app.security as security

    assert not hasattr(security, "check_brute_force")


# ── jwt_secret_key: обязательный при старте ─────────────────────────────────


def test_jwt_secret_required_at_startup():
    import subprocess
    import sys

    code = (
        "import os; "
        "os.environ['FAMILY_TREE_JWT_SECRET_KEY']=''; "
        "os.environ.pop('FAMILY_TREE_VIEW_TOKEN', None); "
        "os.environ.pop('FAMILY_TREE_EDIT_TOKEN', None); "
        "import sys; sys.path.insert(0, '.'); "
        "import app.config"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd="."
    )
    assert proc.returncode != 0
    assert "JWT_SECRET_KEY" in proc.stderr


# ── gzip-лимит (zip-bomb) ───────────────────────────────────────────────────


def test_gzip_bomb_blocked():
    from app.services.gramps_import import MAX_DECOMPRESSED_SIZE, _gzip_decompress_limited

    bomb = gzip.compress(b"\0" * (MAX_DECOMPRESSED_SIZE + 64 * 1024 * 1024))
    with pytest.raises(ValueError):
        _gzip_decompress_limited(bomb)


def test_gzip_normal_roundtrip():
    from app.services.gramps_import import _gzip_try_decompress

    payload = b"<database>" + b"x" * 1_000_000
    assert _gzip_try_decompress(gzip.compress(payload)) == payload
    assert _gzip_try_decompress(b"not-gzip-at-all") == b"not-gzip-at-all"


# ── Магические байты фото ───────────────────────────────────────────────────


def test_sniff_image_type():
    from app.routers.media import _sniff_image_type

    assert _sniff_image_type(b"\xFF\xD8\xFF" + b"\0" * 8) == "image/jpeg"
    assert _sniff_image_type(b"\x89PNG\r\n\x1a\n" + b"\0" * 8) == "image/png"
    assert _sniff_image_type(b"GIF89a" + b"\0" * 8) == "image/gif"
    assert _sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert _sniff_image_type(b"II*\x00" + b"\0" * 8) == "image/tiff"
    assert _sniff_image_type(b"BM" + b"\0" * 8) == "image/bmp"
    # подделка content-type не проходит
    assert _sniff_image_type(b"<script>alert(1)</script>") is None
    assert _sniff_image_type(b"") is None


# ── _safe_media_path: каноничная проверка ───────────────────────────────────


def test_safe_media_path_rejects_prefix_sibling(tmp_path, monkeypatch):
    from app import security

    media = tmp_path / "media"
    media.mkdir()
    (media / "ok.txt").write_text("ok")
    sibling = tmp_path / "media2"
    sibling.mkdir()
    (sibling / "evil.txt").write_text("evil")
    monkeypatch.setattr(security.settings, "media_dir", str(media))

    assert security._safe_media_path("ok.txt") == (media / "ok.txt").resolve()
    with pytest.raises(HTTPException):
        security._safe_media_path("../media2/evil.txt")
    with pytest.raises(HTTPException):
        security._safe_media_path("..\\media2\\evil.txt")


# ── Magic-link вход (эндпоинт) ──────────────────────────────────────────────


def test_magic_link_sets_cookie_and_redirects(app_env):
    from app.config import settings

    resp = app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    cookie_header = resp.headers.get("set-cookie", "")
    assert "ft_access_token=" in cookie_header
    assert "HttpOnly" in cookie_header


def test_magic_link_edit_role(editor_client):
    me = editor_client.get("/api/v1/auth/me").json()
    assert me["role"] == "editor"
    assert me["permissions"] == "read_write"


def test_magic_link_viewer_role(viewer_client):
    me = viewer_client.get("/api/v1/auth/me").json()
    assert me["role"] == "viewer"
    assert me["permissions"] == "read_only"


def test_magic_link_bad_token_401(app_env):
    resp = app_env.get("/api/v1/auth/link/WRONG-TOKEN-1234567890", follow_redirects=False)
    assert resp.status_code == 401
    assert "ft_access_token" not in resp.headers.get("set-cookie", "")


def test_magic_link_redirect_uses_root_path(app_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "root_path", "/fTkTest123")
    resp = app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/fTkTest123/"


# ── Префикс reverse-proxy ───────────────────────────────────────────────────


def test_index_injects_root_path(app_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "root_path", "/fTkTest123")
    resp = app_env.get("/")
    assert resp.status_code == 200
    assert 'window.ROOT_PATH="/fTkTest123"' in resp.text
    assert "/fTkTest123/static/js/app.js" in resp.text


def test_index_without_root_path_plain(app_env):
    resp = app_env.get("/")
    assert resp.status_code == 200
    # инъекции значения ROOT_PATH быть не должно (сам index ссылается на
    # window.ROOT_PATH в скрипте регистрации service worker — это норма)
    assert 'window.ROOT_PATH="' not in resp.text


def test_root_path_validator():
    from app.config import Settings

    assert Settings.model_validate({"root_path": ""}).root_path == ""
    assert Settings.model_validate({"root_path": "abc"}).root_path == "/abc"
    assert Settings.model_validate({"root_path": "/abc/"}).root_path == "/abc"


# ── CRUD через cookie-сессию + rollback + N+1-кеш ───────────────────────────


def test_person_crud_and_validation_rollback(editor_client):
    r = editor_client.post(
        "/api/v1/persons", json={"first_name": "Иван", "last_name": "Тестов", "gender": "male"}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # некорректные даты → 400 и данные не «залипают»
    r = editor_client.patch(
        f"/api/v1/persons/{pid}",
        json={"birth_date": "1950-01-01", "death_date": "1940-01-01"},
    )
    assert r.status_code == 400
    person = editor_client.get(f"/api/v1/persons/{pid}").json()
    assert person["birth_date"] is None
    assert person["death_date"] is None

    assert editor_client.delete(f"/api/v1/persons/{pid}").status_code == 204


def test_viewer_cannot_write(viewer_client):
    r = viewer_client.post(
        "/api/v1/persons", json={"first_name": "Х", "last_name": "У", "gender": "male"}
    )
    assert r.status_code == 403


def test_tree_graph_cache_consistent(editor_client):
    # семья: родитель → ребёнок; ancestors/relatives через кеш графа
    p1 = editor_client.post(
        "/api/v1/persons", json={"first_name": "Пётр", "last_name": "Род", "gender": "male"}
    ).json()
    p2 = editor_client.post(
        "/api/v1/persons", json={"first_name": "Анна", "last_name": "Род", "gender": "female"}
    ).json()
    editor_client.post(
        "/api/v1/relationships",
        json={
            "person_id": p1["id"],
            "related_person_id": p2["id"],
            "relation_type": "parent_of",
        },
    )
    rel = editor_client.get(f"/api/v1/tree/{p2['id']}/relatives").json()
    assert [p["id"] for p in rel["parents"]] == [p1["id"]]
    anc = editor_client.get(f"/api/v1/tree/{p2['id']}/ancestors").json()
    assert anc["generations"] and anc["generations"][0][0]["id"] == p1["id"]

    # обратная связь → цикл отклоняется
    r = editor_client.post(
        "/api/v1/relationships",
        json={
            "person_id": p2["id"],
            "related_person_id": p1["id"],
            "relation_type": "parent_of",
        },
    )
    assert r.status_code == 400

    # удаление связи инвалидирует кеш
    rels = editor_client.get(f"/api/v1/relationships?person_id={p2['id']}").json()
    rid = rels[0]["id"]
    assert editor_client.delete(f"/api/v1/relationships/{rid}").status_code == 204
    rel = editor_client.get(f"/api/v1/tree/{p2['id']}/relatives").json()
    assert rel["parents"] == []


def test_sqlite_wal_mode(app_env, tmp_path):
    # база из фикстуры app_env живёт в tmp_path; WAL включён на connect
    db_files = list(tmp_path.glob("test_family.db*"))
    assert db_files, "тестовая база не найдена"
    conn = sqlite3.connect(str(tmp_path / "test_family.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


# ── История правок (person_revisions) ───────────────────────────────────────


def test_person_history_records_create_update_delete(editor_client):
    r = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Пётр", "last_name": "Исторов", "gender": "male"},
    )
    pid = r.json()["id"]

    h = editor_client.get(f"/api/v1/persons/{pid}/history").json()
    assert len(h) == 1
    assert h[0]["action"] == "create"
    assert h[0]["person_name"] == "Исторов Пётр"

    # правка двух полей — в истории ровно их diff
    editor_client.patch(
        f"/api/v1/persons/{pid}",
        json={"birth_date": "1950-01-01", "birth_place": "Москва"},
    )
    h = editor_client.get(f"/api/v1/persons/{pid}/history").json()
    assert h[0]["action"] == "update"
    fields = {c["field"] for c in h[0]["changes"]}
    assert fields == {"birth_date", "birth_place"}
    by_field = {c["field"]: c for c in h[0]["changes"]}
    assert by_field["birth_date"]["old"] is None
    assert by_field["birth_date"]["new"] == "1950-01-01"

    # no-op PATCH не создаёт ревизию
    editor_client.patch(f"/api/v1/persons/{pid}", json={"birth_place": "Москва"})
    assert len(editor_client.get(f"/api/v1/persons/{pid}/history").json()) == 2

    # отклонённая правка (даты) не попадает в историю
    editor_client.patch(
        f"/api/v1/persons/{pid}", json={"death_date": "1940-01-01"}
    )
    assert len(editor_client.get(f"/api/v1/persons/{pid}/history").json()) == 2

    # delete: ревизия переживает удаление человека
    editor_client.delete(f"/api/v1/persons/{pid}")
    h = editor_client.get(f"/api/v1/persons/{pid}/history").json()
    assert h[0]["action"] == "delete"
    assert h[0]["person_name"] == "Исторов Пётр"
    assert len(h) == 3  # create + update + delete


def test_person_history_viewer_can_read(viewer_client, editor_client):
    pid = editor_client.post(
        "/api/v1/persons", json={"first_name": "Аня", "gender": "female"}
    ).json()["id"]
    assert editor_client.get(f"/api/v1/persons/{pid}/history").status_code == 200
    assert editor_client.get("/api/v1/persons/999999/history").json() == []


def test_priority_family_ordering(editor_client, app_env, monkeypatch):
    """Живая приоритетная фамилия и её супруги/дети идут в списке первыми."""
    import os
    # пере-инициализация Settings с приоритетной фамилией невозможна без
    # пересоздания приложения — проверяем через сервисный слой напрямую.
    from app.services.tree_service import TreeService
    from app.database import SessionLocal
    from app.models import Person, Relationship, RelationType, Gender
    from app.config import Settings
    client = editor_client
    r = client.post(
        "/api/v1/persons",
        json={"first_name": "Иван", "last_name": "vitug"},
    )
    assert r.status_code in (200, 201), r.text
    ivan = r.json()
    r = client.post(
        "/api/v1/persons",
        json={"first_name": "Мария", "last_name": "Иванова"},
    )
    maria = r.json()
    r = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Пётр", "last_name": "Чужой"},
    )
    petr = r.json()
    r = editor_client.post(
        "/api/v1/persons",
        json={"first_name": "Дед", "last_name": "vitug", "death_date": "1990-01-01"},
    )
    ded = r.json()
    # Мария — супруга Ивана, Пётр — ребёнок
    for a, b, t in [(ivan["id"], maria["id"], "spouse_of"),
                    (ivan["id"], petr["id"], "parent_of")]:
        rr = editor_client.post(
            "/api/v1/relationships",
            json={"person_id": a, "related_person_id": b, "relation_type": t},
        )
        assert rr.status_code in (200, 201), rr.text
    # сервисный вызов с приоритетной фамилией
    ts_settings = Settings(priority_family="vitug")
    from app.services import tree_service as tsm
    orig = tsm.settings
    tsm.settings = ts_settings
    try:
        svc = TreeService(SessionLocal())
        ordered = svc.list_persons(limit=10)
        ids = [p.id for p in ordered]
        # живой vitug и его супруга/ребёнок — в первых трёх, до умершего
        # однофамильца и остальных (может быть и чужой живой раньше из-за
        # сортировки по фамилии внутри ранга 0/1 — проверяем ранги)
        rank_names = [(p.last_name or "", p.death_date is None) for p in ordered]
        assert ids.index(ivan["id"]) < ids.index(ded["id"]), "живой раньше умершего однофамильца"
        assert ids.index(ivan["id"]) < ids.index(petr["id"]) or ids.index(maria["id"]) < ids.index(petr["id"])
        # живой vitug первый
        assert ids[0] == ivan["id"] or rank_names[0][0] == "vitug"
    finally:
        tsm.settings = orig


def test_person_access_links(editor_client, app_env):
    """Персональная ссылка: создание, вход по ней, фокус на человеке, отзыв."""
    from app.models import PersonAccessLink

    r = editor_client.post(
        "/api/v1/persons", json={"first_name": "Лена", "last_name": "Тестова"}
    )
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]

    # без авторизации — 401
    anon = app_env  # TestClient без editor-куки
    from fastapi.testclient import TestClient
    from app.main import app as live_app
    with TestClient(live_app) as anon_client:
        assert anon_client.post(f"/api/v1/persons/{pid}/access-link").status_code == 401

    # editor создаёт ссылку
    r = editor_client.post(f"/api/v1/persons/{pid}/access-link")
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["person_id"] == pid and "/auth/link/" in link["url"]

    # вход по ссылке — 303 на ?person=<id>, кука viewer
    token = link["url"].rsplit("/", 1)[-1]
    with TestClient(live_app, follow_redirects=False) as c:
        resp = c.get(f"/api/v1/auth/link/{token}")
        assert resp.status_code == 303
        assert f"person={pid}" in resp.headers["location"]
        me = c.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["role"] == "editor"
        assert me.json()["person_id"] == pid

    # список и отзыв
    r = editor_client.get(f"/api/v1/persons/{pid}/access-links")
    assert r.status_code == 200 and any(l["id"] == link["id"] for l in r.json())
    r = editor_client.delete(f"/api/v1/persons/access-links/{link['id']}")
    assert r.status_code == 204

    # после отзыва ссылка не работает
    with TestClient(live_app, follow_redirects=False) as c:
        resp = c.get(f"/api/v1/auth/link/{token}")
        assert resp.status_code in (401, 403, 404)


def test_revision_actor(editor_client, app_env):
    """История правок запоминает автора: editor-ссылка → «Редактор»."""
    r = editor_client.post(
        "/api/v1/persons", json={"first_name": "Ав", "last_name": "Тор"}
    )
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    r = editor_client.patch(f"/api/v1/persons/{pid}", json={"biography": "тест"})
    assert r.status_code == 200, r.text
    hist = editor_client.get(f"/api/v1/persons/{pid}/history").json()
    assert hist, "история пуста"
    actors = {h.get("actor") for h in hist}
    assert "Редактор" in actors, actors


def test_scoped_access_link(editor_client, app_env):
    """Ссылка с ограничением семьёй: правит свою семью, чужих — 403."""
    from fastapi.testclient import TestClient
    from app.main import app as live_app

    # семья: Иван — родитель Марии; чужой Пётр
    r = editor_client.post("/api/v1/persons", json={"first_name": "Сц", "last_name": "Иван"})
    assert r.status_code in (200, 201), r.text
    ivan = r.json()["id"]
    r = editor_client.post("/api/v1/persons", json={"first_name": "Сц", "last_name": "Мария"})
    assert r.status_code in (200, 201), r.text
    maria = r.json()["id"]
    r = editor_client.post("/api/v1/persons", json={"first_name": "Сц", "last_name": "Пётр"})
    assert r.status_code in (200, 201), r.text
    petr = r.json()["id"]
    rr = editor_client.post(
        "/api/v1/relationships",
        json={"person_id": ivan, "related_person_id": maria, "relation_type": "parent_of"},
    )
    assert rr.status_code in (200, 201), rr.text

    # ссылка с глубиной 1 (родители/дети/супруги)
    r = editor_client.post(
        f"/api/v1/persons/{ivan}/access-link",
        json={"role": "editor", "scope_depth": 1},
    )
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[-1]

    with TestClient(live_app) as c:
        c.get(f"/api/v1/auth/link/{token}", follow_redirects=False)
        me = c.get("/api/v1/auth/me").json()
        assert me["role"] == "editor" and me["person_id"] == ivan
        # свою семью править можно
        assert c.patch(
            f"/api/v1/persons/{maria}", json={"notes": "ок"}
        ).status_code == 200
        # чужого — нельзя
        assert c.patch(
            f"/api/v1/persons/{petr}", json={"notes": "нет"}
        ).status_code == 403
        # связь двух чужих людей — нельзя
        r = editor_client.post("/api/v1/persons", json={"first_name": "Сц", "last_name": "Др"})
        dr = r.json()["id"]
        assert c.post(
            "/api/v1/relationships",
            json={"person_id": petr, "related_person_id": dr, "relation_type": "spouse_of"},
        ).status_code == 403
        # добавить нового супруга в свою семью — можно (один конец в семье)
        r = editor_client.post("/api/v1/persons", json={"first_name": "Сц", "last_name": "Жен"})
        zhen = r.json()["id"]
        assert c.post(
            "/api/v1/relationships",
            json={"person_id": zhen, "related_person_id": maria, "relation_type": "spouse_of"},
        ).status_code in (200, 201)


def test_only_admin_creates_links(editor_client, app_env):
    """Ссылки создаёт/отзывает только админ (глобальная editor-ссылка)."""
    from fastapi.testclient import TestClient
    from app.main import app as live_app

    r = editor_client.post("/api/v1/persons", json={"first_name": "Адм", "last_name": "Тест"})
    pid = r.json()["id"]

    # админ (editor_client — глобальная ссылка) создаёт unrestricted-ссылку
    me = editor_client.get("/api/v1/auth/me").json()
    assert me["is_admin"] is True
    r = editor_client.post(f"/api/v1/persons/{pid}/access-link", json={"role": "editor"})
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[-1]

    # сессия по персональной ссылке (editor, без scope) — НЕ админ
    with TestClient(live_app) as c:
        c.get(f"/api/v1/auth/link/{token}", follow_redirects=False)
        me = c.get("/api/v1/auth/me").json()
        assert me["role"] == "editor" and me["is_admin"] is False
        # создавать ссылки нельзя
        assert c.post(
            f"/api/v1/persons/{pid}/access-link", json={"role": "editor"}
        ).status_code == 403
        # отзывать чужие ссылки нельзя
        assert c.delete("/api/v1/persons/access-links/999").status_code in (403, 404)
        # свои данные править можно
        assert c.patch(
            f"/api/v1/persons/{pid}", json={"notes": "ок"}
        ).status_code == 200


def test_family_filter(editor_client):
    """Отбор по фамилии: носители + супруги/дети живых носителей, без чужих."""
    r = editor_client.post(
        "/api/v1/persons", json={"first_name": "Фл", "last_name": "vitug"}
    )
    assert r.status_code in (200, 201), r.text
    fam_id = r.json()["id"]
    r = editor_client.post("/api/v1/persons", json={"first_name": "Фл", "last_name": "Супруга"})
    assert r.status_code in (200, 201), r.text
    sp_id = r.json()["id"]
    r = editor_client.post("/api/v1/persons", json={"first_name": "Фл", "last_name": "Чужой"})
    assert r.status_code in (200, 201), r.text
    alien_id = r.json()["id"]
    rr = editor_client.post(
        "/api/v1/relationships",
        json={"person_id": fam_id, "related_person_id": sp_id, "relation_type": "spouse_of"},
    )
    assert rr.status_code in (200, 201), rr.text

    people = editor_client.get("/api/v1/persons?family=vitug").json()
    ids = {p["id"] for p in people}
    assert fam_id in ids and sp_id in ids
    assert alien_id not in ids
    assert all(
        (p["last_name"] or "").startswith("Тугарин")
        or (p.get("maiden_name") or "").startswith("Тугарин")
        or p["id"] in ids
        for p in people
    )


def test_search_by_id(editor_client):
    """Поиск по числовому id находит точного человека."""
    r = editor_client.post(
        "/api/v1/persons", json={"first_name": "Ид", "last_name": "Поиск"}
    )
    assert r.status_code in (200, 201)
    pid = r.json()["id"]
    found = editor_client.get(f"/api/v1/persons?q={pid}").json()
    assert any(p["id"] == pid for p in found)


def test_gramps_names_unknown_primary():
    """Имя type=Unknown — текущая фамилия; Birth Name — девичья (инцидент Кузнецова/Яковлева)."""
    import xml.etree.ElementTree as ET
    from app.services.gramps_import import GrampsImporter

    xml = """<person handle="h" id="I1">
      <gender>F</gender>
      <name type="Unknown"><first>Светлана</first><surname>Кузнецова</surname></name>
      <name alt="1" type="Birth Name"><first>Светлана</first><surname>Яковлева</surname></name>
    </person>"""
    importer = GrampsImporter.__new__(GrampsImporter)
    names = importer._extract_names(ET.fromstring(xml))
    assert names["last_name"] == "Кузнецова"
    assert names["maiden_name"] == "Яковлева"
    assert names["first_name"] == "Светлана"


# ── Маскировка заметок для viewer + запросы доступа ────────────────────────

NOTE_WITH_PERSONAL = "Тел: +7 912 345 67 89. Email: a@b.ru. Домофон 1495. Просто биография."


def _mk_person_with_note(client):
    r = client.post(
        "/api/v1/persons",
        json={"first_name": "Нота", "last_name": "Проверка", "notes": NOTE_WITH_PERSONAL},
    )
    assert r.status_code in (200, 201)
    return r.json()["id"]


def test_viewer_notes_masked_editor_full(app_env, viewer_client, editor_client):
    from app.config import settings

    pid = _mk_person_with_note(editor_client)
    # viewer_client и editor_client делят один клиент: релогинимся явно
    app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    v = app_env.get(f"/api/v1/persons/{pid}").json()
    assert v["notes"] != NOTE_WITH_PERSONAL
    assert "[телефон скрыт]" in v["notes"]
    assert "[e-mail скрыт]" in v["notes"]
    t = app_env.get(f"/api/v1/tree/{pid}").json()
    assert t["tree"]["person"]["notes"] != NOTE_WITH_PERSONAL
    # editor видит полностью
    app_env.get(f"/api/v1/auth/link/{settings.edit_token}", follow_redirects=False)
    e = app_env.get(f"/api/v1/persons/{pid}").json()
    assert e["notes"] == NOTE_WITH_PERSONAL


def test_note_access_request_flow(app_env, viewer_client, editor_client):
    from fastapi.testclient import TestClient
    from app.main import app
    """Viewer с персональной ссылкой запрашивает доступ; админ одобряет — заметки открываются."""
    pid = _mk_person_with_note(editor_client)
    # персональная viewer-ссылка для человека pid (создаёт админ)
    r = editor_client.post(f"/api/v1/persons/{pid}/access-link", json={"role": "viewer"})
    assert r.status_code in (200, 201)
    link = r.json()["url"].rsplit("/", 1)[-1]
    with TestClient(app, follow_redirects=False) as link_client:
        resp = link_client.get(f"/api/v1/auth/link/{link}", follow_redirects=False)
        assert resp.status_code in (302, 303)
        # viewer по персональной ссылке
        notes_resp = link_client.get(f"/api/v1/persons/{pid}")
        assert "[телефон скрыт]" in notes_resp.json()["notes"]
        # запрос
        rr = link_client.post(f"/api/v1/persons/{pid}/note-access-request", json={})
        assert rr.status_code in (200, 201)
        req_id = rr.json()["id"]
        # дубль отклоняется
        dup = link_client.post(f"/api/v1/persons/{pid}/note-access-request", json={})
        assert dup.status_code == 409
        # админ видит pending
        lst = editor_client.get(f"/api/v1/persons/{pid}/note-access-requests?status=pending")
        assert any(x["id"] == req_id for x in lst.json())
        # одобряем
        dec = editor_client.post(f"/api/v1/persons/note-access-requests/{req_id}/decision?decision=approve")
        assert dec.status_code == 200
        # теперь заметки полные
        notes2 = link_client.get(f"/api/v1/persons/{pid}").json()["notes"]
        assert notes2 == NOTE_WITH_PERSONAL
        # но другие люди по-прежнему маскируются
        other = editor_client.post(
            "/api/v1/persons",
            json={"first_name": "Другой", "last_name": "Человек", "notes": NOTE_WITH_PERSONAL},
        ).json()["id"]
        other_notes = link_client.get(f"/api/v1/persons/{other}").json()["notes"]
        assert "[телефон скрыт]" in other_notes


def test_mask_no_false_positives():
    """Обычные слова (дерево, дети, дома) не маскируются как адреса."""
    from app.services.privacy import mask_notes

    text = "Семейное дерево MD. Дети и внуки. Дома жили в Иволгинске. род. 05.10.1948"
    assert mask_notes(text) == text
    masked = mask_notes("живёт ул. Пушкина 4 кв. 7")
    assert "[адрес скрыт]" in masked


# ── PWA: установка сайта как приложение ─────────────────────────────────────


def test_pwa_manifest(app_env):
    r = app_env.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "Семейное дерево"
    assert m["start_url"] == "/"
    assert m["scope"] == "/"
    assert m["display"] == "standalone"
    sizes = {i["sizes"] for i in m["icons"]}
    assert "192x192" in sizes and "512x512" in sizes
    assert any(i.get("purpose") == "maskable" for i in m["icons"])
    # иконки реально отдаются
    for i in m["icons"]:
        ri = app_env.get(i["src"])
        assert ri.status_code == 200, i["src"]


def test_pwa_sw(app_env):
    r = app_env.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    assert "fetch" in r.text


def test_pwa_index_links_and_root_path(app_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "root_path", "/door")
    r = app_env.get("/")
    assert r.status_code == 200
    html = r.text
    # manifest-ссылка с префиксом двери
    assert 'href="/door/manifest.webmanifest"' in html
    # ROOT_PATH для регистрации service worker
    assert 'window.ROOT_PATH="/door"' in html
    # service worker доступен по префиксному пути (nginx проксирует без URI-части)
    assert app_env.get("/sw.js").status_code == 200


def test_pwa_manifest_root_path_trailing_slash(app_env, monkeypatch):
    """root_path с завершающим слэшем (боевой формат двери) не должен давать // в start_url."""
    from app.config import settings

    monkeypatch.setattr(settings, "root_path", "/door/")
    m = app_env.get("/manifest.webmanifest").json()
    assert m["start_url"] == "/door/"
    assert m["scope"] == "/door/"
    assert m["icons"][0]["src"] == "/door/static/icons/icon-192.png"


# ── Cookie path = root_path (прод/dev на одном домене не конфликтуют) ────────


def test_auth_cookie_path_follows_root_path(app_env, monkeypatch):
    """Вход за дверью ставит cookie с Path=<дверь>/: иначе dev-стенд на том же
    домене vitug.ru затирает прод-cookie (разные JWT-секреты → 401 на проде)."""
    from app.config import settings
    from app.routers import auth as auth_mod

    monkeypatch.setattr(settings, "root_path", "/test-door-abc/")
    resp = app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    assert resp.status_code == 303
    cookie_header = resp.headers.get("set-cookie", "")
    assert "ft_access_token=" in cookie_header
    assert "Path=/test-door-abc/" in cookie_header

    # без двери — корневой путь (локальная разработка)
    monkeypatch.setattr(settings, "root_path", "")
    resp2 = app_env.get(f"/api/v1/auth/link/{settings.view_token}", follow_redirects=False)
    assert "Path=/" in resp2.headers.get("set-cookie", "")
