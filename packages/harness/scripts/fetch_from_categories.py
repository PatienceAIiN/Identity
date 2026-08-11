"""Top up the acceptance set using the Commons API (real filenames, not
guesses): list category members, then fetch via Special:FilePath with the
same backoff/face-check as fetch_photos.py."""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_photos as fp

CATEGORIES = [
    "Category:Official_portrait_photographs_of_astronauts",
    "Category:Portrait_photographs_of_NASA_astronauts",
    "Category:Official_portraits_of_United_States_senators",
]
API = ("https://commons.wikimedia.org/w/api.php?action=query&list=categorymembers"
       "&cmtitle={}&cmtype=file&cmlimit=40&format=json")
TARGET_NEW = 30


def list_files(cat: str) -> list[str]:
    req = urllib.request.Request(
        API.format(urllib.parse.quote(cat)),
        headers={"User-Agent": "photobind-harness/0.1 (local test rig; contact: dev)"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        print(f"category listing failed for {cat}: {e}")
        return []
    names = [m["title"].removeprefix("File:")
             for m in data.get("query", {}).get("categorymembers", [])]
    return [n for n in names if n.lower().endswith((".jpg", ".jpeg"))]


def main():
    have = len(list(fp.OUT.glob("*.jpg"))) if fp.OUT.exists() else 0
    names = []
    for cat in CATEGORIES:
        names.extend(list_files(cat))
        time.sleep(3)
        if len(names) >= TARGET_NEW * 3:
            break
    need = max(0, 50 - have - 22)  # 22 usable portraits already in photos/ + photos_fresh
    print(f"{len(names)} candidate files from categories; have {have} already")
    fp.CANDIDATES = names[:min(TARGET_NEW, max(need, TARGET_NEW)) + 5]
    fp.main()


if __name__ == "__main__":
    main()
