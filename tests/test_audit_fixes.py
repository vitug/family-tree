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
    assert resp.headers["location"] == "/fTkTest123"


# ── Префикс reverse-proxy ───────────────────────────────────────────────────


def test_index_injects_root_path(app_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "root_path", "/fTkTest123")
    resp = app_env.get("/")
    assert resp.status_code == 200
    assert 'window.ROOT_PATH="/fTkTest123"' in resp.text
    assert '"/fTkTest123/static/js/app.js"' in resp.text


def test_index_without_root_path_plain(app_env):
    resp = app_env.get("/")
    assert resp.status_code == 200
    assert "window.ROOT_PATH" not in resp.text


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
    assert viewer_client.get(f"/api/v1/persons/{pid}/history").status_code == 200
    assert viewer_client.get("/api/v1/persons/999999/history").json() == []
