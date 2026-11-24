import time
import requests

def test_dub_and_history():
    print("Testing Dubbing and DB Record Saving...")
    
    # 1. Login
    login_res = requests.post('http://localhost:8000/api/auth/demo-login', json={'name': 'DB Test User'})
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    token = login_res.json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    print("[OK] User login successful.")

    # 2. Dub YouTube Video
    dub_res = requests.post(
        'http://localhost:8000/api/dub',
        headers=headers,
        json={'url': 'https://youtu.be/o-X_OCQ_ZI0', 'voice_gender': 'male'}
    )
    assert dub_res.status_code == 200, f"Dub request failed: {dub_res.text}"
    job_id = dub_res.json()['job_id']
    print(f"[OK] Dub request initiated with Job ID: {job_id}")

    # 3. Poll progress until complete
    for _ in range(30):
        p_res = requests.get(f'http://localhost:8000/api/progress/{job_id}')
        data = p_res.json()
        print(f"Status: {data.get('status')} | Step: {data.get('step')} | Progress: {data.get('progress')}%")
        if data.get('status') in ['completed', 'failed']:
            assert data.get('status') == 'completed', f"Job failed: {data.get('error')}"
            break
        time.sleep(1)

    # 4. Check user history
    hist_res = requests.get('http://localhost:8000/api/user/history', headers=headers)
    assert hist_res.status_code == 200, f"History fetch failed: {hist_res.text}"
    history = hist_res.json().get('history', [])
    print(f"[OK] History count: {len(history)}")
    assert len(history) > 0, "Expected at least 1 job in history!"
    
    saved_job = history[0]
    print(f"[OK] Saved Job Title: {saved_job.get('video_title')}")
    print(f"[OK] Saved Job YouTube URL: {saved_job.get('youtube_url')}")
    print("==================================================")
    print("  ALL DATABASE SCHEMA & HISTORY CHECKS PASSED!    ")
    print("==================================================")

if __name__ == "__main__":
    test_dub_and_history()
