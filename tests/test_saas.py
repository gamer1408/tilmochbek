import time
import requests
import json
import os
from pathlib import Path

BASE_URL = "http://localhost:8000"

def test_tilmochbek_flow():
    print("==================================================")
    print("      RUNNING TILMOCHBEK E2E SAAS PIPELINE TEST    ")
    print("==================================================")

    # 1. Test Public Config
    cfg_res = requests.get(f"{BASE_URL}/api/config")
    assert cfg_res.status_code == 200, f"Config error: {cfg_res.text}"
    cfg = cfg_res.json()
    assert cfg["platform_name"] == "Tilmochbek"
    assert cfg["unlimited"] is False  # plans enforced since v7
    print(f"[OK] Step 1: Public config verified (Platform: {cfg['platform_name']}).")

    # 2. Test Guest Profile
    res = requests.get(f"{BASE_URL}/api/user/me")
    assert res.status_code == 200
    guest_data = res.json()
    assert guest_data["guest"] is True
    print("[OK] Step 2: Guest profile verification passed.")

    # 3. Test User Registration & Login (Demo Auth / Google Auth)
    login_res = requests.post(f"{BASE_URL}/api/auth/demo-login", json={"name": "Tilmochbek User"})
    assert login_res.status_code == 200
    login_data = login_res.json()
    token = login_data["access_token"]
    user = login_data["user"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[OK] Step 3: User auth passed. User ID: {user['id']} (Name: {user['full_name']}).")

    # 4. Test Telegram Link Generation
    tg_res = requests.post(f"{BASE_URL}/api/user/telegram-link", headers=headers)
    assert tg_res.status_code == 200
    tg_data = tg_res.json()
    assert "link_code" in tg_data
    print(f"[OK] Step 4: Telegram link generated: {tg_data['telegram_bot_url']}")

    # 5. Test YouTube Dubbing with Instant Cache
    yt_res = requests.post(
        f"{BASE_URL}/api/dub",
        headers=headers,
        json={"url": "https://youtu.be/o-X_OCQ_ZI0", "voice_gender": "male"}
    )
    assert yt_res.status_code == 200
    yt_data = yt_res.json()
    print(f"[OK] Step 5: YouTube dub request successful (Job ID: {yt_data.get('job_id')}).")

    # 6. Test User History Retrieval
    hist_res = requests.get(f"{BASE_URL}/api/user/history", headers=headers)
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    history_items = hist_data.get("history", [])
    print(f"[OK] Step 6: User history retrieved successfully ({len(history_items)} items).")
    assert len(history_items) > 0, "Expected at least 1 item in user history!"

    print("==================================================")
    print("      ALL TILMOCHBEK TESTS PASSED 100%!           ")
    print("==================================================")

if __name__ == "__main__":
    test_tilmochbek_flow()
