"""Offline tests for plans/quota, auth hardening, API and bot helpers (no network, no real APIs)."""
import os, sys, asyncio, datetime, tempfile, importlib
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    # isolate storage: run in a temp copy of config paths
    os.environ["GOOGLE_CLIENT_ID"] = "test-client"
    os.environ["ADMIN_KEY"] = "admin-secret-key"
    os.environ["DEMO_LOGIN"] = "1"
    os.environ["JWT_SECRET"] = "x" * 40
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    import backend.config as cfg
    tmp = tmp_path_factory.mktemp("storage")
    cfg.STORAGE_DIR = tmp; cfg.DB_FILE = tmp / "t.db"
    cfg.DATABASE_URL = f"sqlite+aiosqlite:///{cfg.DB_FILE}"; cfg.SYNC_DATABASE_URL = f"sqlite:///{cfg.DB_FILE}"
    cfg.TEMP_DIR = tmp / "temp"; cfg.OUTPUT_DIR = tmp / "outputs"
    cfg.TEMP_DIR.mkdir(); cfg.OUTPUT_DIR.mkdir()
    for m in [m for m in list(sys.modules) if m.startswith("backend") and m != "backend.config"]:
        del sys.modules[m]
    return cfg

@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient
    import backend.app as app_mod
    app_mod.OUTPUT_DIR = env.OUTPUT_DIR
    with TestClient(app_mod.app) as c:
        yield c, app_mod

def login(c):
    r = c.post("/api/auth/demo-login", json={"name": "T"}); assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}

def test_config_and_plans(client):
    c, _ = client
    cfg = c.get("/api/config").json()
    assert cfg["unlimited"] is False and len(cfg["plans"]) == 4
    assert {p["key"]: p["price"] for p in cfg["plans"]} == {"free": 0, "weekly": 29900, "monthly": 99900, "vip": 199900}
    assert c.get("/api/health").json()["status"] == "ok"
    assert "Sitemap" in c.get("/robots.txt").text

def test_guest_cannot_dub_and_history_private(client):
    c, _ = client
    r = c.post("/api/dub", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 401
    assert c.get("/api/history").json() == {"history": []}

def test_url_validation(client):
    c, _ = client
    h = login(c)
    for bad in ["http://evil.com/x", "file:///etc/passwd", "javascript:alert(1)", "https://youtube.com.evil.com/watch?v=abcdefg"]:
        assert c.post("/api/dub", json={"url": bad}, headers=h).status_code == 400, bad

def test_quota_free_then_paid(client, monkeypatch):
    c, app_mod = client
    h = login(c)
    async def fake_exec(**kw):
        out = app_mod.OUTPUT_DIR / f"{kw['job_id']}.mp4"; out.write_bytes(b"0" * 2000)
        from backend.db import get_db_context
        from backend.models import DubJob
        from backend.pipeline.quota import deduct_job_credits
        async with get_db_context() as db:
            await deduct_job_credits(db, kw["user"].id, 60)
            db.add(DubJob(id=kw["job_id"], user_id=kw["user"].id, youtube_url=kw["source"], status="completed",
                          video_duration=60, output_path=str(out), video_url=f"/outputs/{kw['job_id']}.mp4"))
            await db.commit()
        return {"title": "T", "duration": 60, "thumbnail": "", "video_url": f"/outputs/{kw['job_id']}.mp4",
                "out_file": str(out), "segments": [], "elapsed": 1.0}
    monkeypatch.setattr(app_mod, "execute_dubbing", fake_exec)
    monkeypatch.setattr(app_mod, "rate_limit", lambda *a, **k: None)
    me = c.get("/api/user/me", headers=h).json()
    assert me["usage"]["videos_remaining"] == 1 and me["usage"]["plan"] == "free"
    r = c.post("/api/dub", json={"url": "https://youtu.be/AAAAAAAAAAA"}, headers=h); assert r.status_code == 200, r.text
    import time
    for _ in range(50):
        p = c.get(f"/api/progress/{r.json()['job_id']}").json()
        if p["status"] == "completed": break
        time.sleep(0.1)
    assert p["status"] == "completed" and "user_id" not in p
    # free limit used up -> 402
    r2 = c.post("/api/dub", json={"url": "https://youtu.be/BBBBBBBBBBB"}, headers=h)
    assert r2.status_code == 402 and "limit" in r2.json()["detail"].lower()
    # admin activation requires key
    uid = me["user"]["id"]
    assert c.post("/api/admin/activate", json={"plan": "weekly", "user_id": uid}).status_code == 403
    assert c.post("/api/admin/activate", json={"plan": "weekly", "user_id": uid}, headers={"X-Admin-Key": "wrong"}).status_code == 403
    ok = c.post("/api/admin/activate", json={"plan": "weekly", "user_id": uid}, headers={"X-Admin-Key": "admin-secret-key"})
    assert ok.status_code == 200 and ok.json()["user"]["plan"] == "weekly"
    u = c.get("/api/user/me", headers=h).json()["usage"]
    assert u["plan"] == "weekly" and u["videos_total"] == 14 and u["videos_remaining"] == 14  # new plan period starts fresh
    r3 = c.post("/api/dub", json={"url": "https://youtu.be/CCCCCCCCCCC"}, headers=h); assert r3.status_code == 200

def test_duration_limit_and_expiry():
    from backend.pipeline.quota import check_duration_quota, QuotaError, effective_plan_key
    from backend.models import User
    u = User(plan="free")
    check_duration_quota(u, 29 * 60)
    with pytest.raises(QuotaError): check_duration_quota(u, 31 * 60)
    paid = User(plan="monthly", plan_expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=3))
    assert effective_plan_key(paid) == "monthly"
    expired = User(plan="vip", plan_expires_at=datetime.datetime.utcnow() - datetime.timedelta(days=1))
    assert effective_plan_key(expired) == "free"

def test_google_auth_rejects_fake_tokens(client):
    c, _ = client
    for tok in ["mock_x", "demo_token_x", "garbage"]:
        assert c.post("/api/auth/google", json={"credential": tok}).status_code == 400

def test_cleanup_removes_only_old(env):
    from backend import runtime
    old = env.OUTPUT_DIR / "old.mp4"; new = env.OUTPUT_DIR / "new.mp4"
    old.write_bytes(b"1"); new.write_bytes(b"1")
    t = datetime.datetime.now().timestamp() - 30 * 3600
    os.utime(old, (t, t))
    runtime.OUTPUT_DIR = env.OUTPUT_DIR; runtime.TEMP_DIR = env.TEMP_DIR
    stale = env.TEMP_DIR / "stale"; stale.mkdir(); os.utime(stale, (t, t))
    res = runtime.cleanup_storage_once(24)
    assert not old.exists() and new.exists() and not stale.exists() and res["outputs"] == 1

def test_bot_helpers():
    from backend import telegram_bot as tb
    assert not tb.is_public_url("http://localhost:8000") and tb.is_public_url("https://tilmochbek.uz")
    assert tb.YOUTUBE_URL_REGEX.search("qara https://youtu.be/dQw4w9WgXcQ?si=1 zo'r")
    assert tb.esc("<b>&") == "&lt;b&gt;&amp;"
    kb = tb.main_keyboard()  # must not contain localhost url buttons
    assert all(not (b.url and "localhost" in b.url) for row in kb.inline_keyboard for b in row)
    assert "Tariflar" in tb.plans_text() or "Tariflar" in tb.plans_text().replace("<b>", "")

def test_service_pipeline_cleanup_and_failure(env, monkeypatch):
    import backend.service as svc
    from backend.models import User
    from backend.pipeline.quota import QuotaError
    async def run():
        from backend.db import init_db
        await init_db()
        svc.TEMP_DIR = env.TEMP_DIR; svc.OUTPUT_DIR = env.OUTPUT_DIR
        calls = []
        def fake_audio(url, d):
            (d / "audio.mp3").write_bytes(b"a"); return {"audio_path": str(d / "audio.mp3"), "title": "Vid", "duration": 100.0, "thumbnail": ""}
        def fake_video(url, d):
            (d / "video.mp4").write_bytes(b"v"); return str(d / "video.mp4")
        async def fake_tts(segs, d, g):
            return [dict(s, speech_path=str(d / "s.wav")) for s in segs]
        def fake_mix(video, segs, out, bg, gain, jd):
            open(out, "wb").write(b"x" * 5000); return out
        monkeypatch.setattr(svc, "download_youtube_audio", fake_audio)
        monkeypatch.setattr(svc, "download_youtube_video_stream", fake_video)
        monkeypatch.setattr(svc, "transcribe_audio", lambda p, k: [{"start": 0, "end": 1, "text": "hi"}])
        monkeypatch.setattr(svc, "translate_segments", lambda s, l: [dict(x, translated="salom") for x in s])
        monkeypatch.setattr(svc, "synthesize_uzbek_speech", fake_tts)
        monkeypatch.setattr(svc, "process_dubbed_video", fake_mix)
        monkeypatch.setattr(svc, "save_cached_dub", lambda **k: calls.append("cache"))
        async def prog(*a): calls.append(a[0])
        user = User(id=None, plan="free")
        # success: temp dir removed, output kept, no server paths in segments
        res = await svc.execute_dubbing(job_id="job1", source="https://youtu.be/AAAAAAAAAAA", is_youtube=True,
                                        voice_gender="male", user=user, progress=prog)
        assert not (env.TEMP_DIR / "job1").exists() and (env.OUTPUT_DIR / "job1.mp4").exists()
        assert all("speech_path" not in s for s in res["segments"]) and "cache" in calls
        # too long for free plan (30 min): QuotaError, nothing left on disk
        monkeypatch.setattr(svc, "download_youtube_audio",
                            lambda u, d: {"audio_path": str(d / "a.mp3"), "title": "Long", "duration": 4000.0, "thumbnail": ""})
        with pytest.raises(QuotaError):
            await svc.execute_dubbing(job_id="job2", source="https://youtu.be/BBBBBBBBBBB", is_youtube=True,
                                      voice_gender="male", user=user, progress=prog)
        assert not (env.TEMP_DIR / "job2").exists() and not (env.OUTPUT_DIR / "job2.mp4").exists()
    asyncio.run(run())

def test_friendly_error_hides_internals():
    from backend.service import friendly_error
    msg = friendly_error(RuntimeError("Traceback C:\\Users\\Dell\\secret gsk_ABC123"))
    assert "gsk_" not in msg and "Users" not in msg
    assert "yopiq" in friendly_error(Exception("ERROR: Private video. Sign in"))


def test_click_flow(env, monkeypatch):
    import backend.billing as b
    monkeypatch.setattr(b, "CLICK_SERVICE_ID", "777"); monkeypatch.setattr(b, "CLICK_MERCHANT_ID", "55")
    monkeypatch.setattr(b, "CLICK_SECRET_KEY", "SECRET")
    from backend.db import init_db, get_db_context
    from backend.models import User
    async def run():
        await init_db()
        async with get_db_context() as db:
            u = User(full_name="Pay", telegram_id=None); db.add(u); await db.commit(); await db.refresh(u)
            pay, url = await b.create_click_payment(db, u, "monthly")
            assert "service_id=777" in url and "amount=99900.00" in url and f"transaction_param={pay.id}" in url
            base = {"click_trans_id": "9001", "service_id": "777", "merchant_trans_id": pay.id, "amount": "99900", "sign_time": "2026-11-24 19:00:00"}
            prep = dict(base, action="0", sign_string=b.make_sign("9001", "777", "SECRET", pay.id, "99900", "0", base["sign_time"]))
            assert (await b.handle_click(db, dict(prep, sign_string="bad")))["error"] == -1          # wrong signature
            assert (await b.handle_click(db, dict(prep, amount="1000", sign_string=b.make_sign("9001", "777", "SECRET", pay.id, "1000", "0", base["sign_time"]))))["error"] == -2  # wrong amount
            r = await b.handle_click(db, prep); assert r["error"] == 0; pid = r["merchant_prepare_id"]
            comp = dict(base, action="1", merchant_prepare_id=str(pid), error="0",
                        sign_string=b.make_sign("9001", "777", "SECRET", pay.id, pid, "99900", "1", base["sign_time"]))
            assert (await b.handle_click(db, dict(comp, merchant_prepare_id="1", sign_string=b.make_sign("9001", "777", "SECRET", pay.id, "1", "99900", "1", base["sign_time"]))))["error"] == -6
            r2 = await b.handle_click(db, comp); assert r2["error"] == 0
            await db.refresh(u); assert u.plan == "monthly" and u.plan_expires_at is not None
            assert (await b.handle_click(db, comp))["error"] == -4                                     # replay is rejected
            assert (await b.handle_click(db, dict(base, merchant_trans_id="nope", action="0", sign_string=b.make_sign("9001", "777", "SECRET", "nope", "99900", "0", base["sign_time"]))))["error"] == -5
    asyncio.run(run())
