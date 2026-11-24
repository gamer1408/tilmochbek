import sys
import time
from pathlib import Path

# Set console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.pipeline.translator import translate_segments

sample_segments = [
    {"id": 0, "start": 0.0, "end": 2.5, "text": "Hello everyone, you know basically today we will learn web scraping with Apify Actor."},
    {"id": 1, "start": 2.5, "end": 5.0, "text": "We will extract leads and session cookies from LinkedIn."},
    {"id": 2, "start": 5.0, "end": 7.5, "text": "And um like I said, we will verify them using MillionVerifier."}
]

print("Starting Translation Test...")
t0 = time.time()
translated = translate_segments(sample_segments, "uz")
dt = time.time() - t0

print(f"Translation completed in {dt:.3f}s!")
print(f"Total Segments: {len(translated)}")
for s in translated:
    print(f"  [{s['id']}] {s['text']}")
    print(f"   --> {s.get('translated_text')}")

assert len(translated) == 3
for s in translated:
    assert s.get("translated_text"), f"Segment {s['id']} has empty translated text"

print("\nALL TRANSLATION TESTS PASSED SUCCESSFULLY!")
