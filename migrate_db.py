import sqlite3
from pathlib import Path

db_path = Path("storage/gapirai.db")
if db_path.exists():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(dub_jobs);")
    cols = [r[1] for r in cur.fetchall()]
    print("Initial columns:", cols)
    
    if "youtube_url" not in cols:
        cur.execute("ALTER TABLE dub_jobs ADD COLUMN youtube_url TEXT DEFAULT ''")
        if "source_url_or_path" in cols:
            cur.execute("UPDATE dub_jobs SET youtube_url = source_url_or_path WHERE youtube_url IS NULL OR youtube_url = ''")
        print("Added youtube_url column.")
        
    conn.commit()
    cur.execute("PRAGMA table_info(dub_jobs);")
    print("Final columns:", [r[1] for r in cur.fetchall()])
    conn.close()
