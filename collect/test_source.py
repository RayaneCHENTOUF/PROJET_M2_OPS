"""
test_source.py — lecture du flux RSS Ouest-France
Usage : python collect/test_source.py
"""

import json
import feedparser # type: ignore 

URL = "https://www.ouest-france.fr/rss/une"

feed = feedparser.parse(URL)

print(f"{len(feed.entries)} articles récupérés\n")

for entry in feed.entries[:3]:
    article = {
        "source":    "Ouest-France",
        "titre":     entry.get("title", ""),
        "url":       entry.get("link", ""),
        "publie_le": entry.get("published", ""),
        "chapo":     entry.get("summary", ""),
    }
    print(json.dumps(article, ensure_ascii=False, indent=2))