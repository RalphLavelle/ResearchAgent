"""Read-only diagnostic: why do reports keep showing the same crawled hosts?

Prints, for the active topic's MongoDB:
  * how many venues exist and how many have a stored events_link
  * each linked venue's host, last_mined, and events_link_checked
  * the last few run reports' searches count + crawled hosts + memory seed

Run (PowerShell):
  $env:PYTHONPATH = "src"
  venv/Scripts/python.exe scripts/diag_crawl.py
"""

from __future__ import annotations

from urllib.parse import urlparse

from agent import config, report_store, venue_store

db = config.ACTIVE_TOPIC.db
print(f"Active topic: {config.ACTIVE_TOPIC_ID}  db={db}")
print(f"MAX_VENUE_SEEDS={config.MAX_VENUE_SEEDS}  "
      f"MAX_CRAWL_SEEDS={config.MAX_CRAWL_SEEDS}  "
      f"MAX_CRAWL_PAGES_TOTAL={config.MAX_CRAWL_PAGES_TOTAL}  "
      f"PER_SEED={config.MAX_CRAWL_PAGES_PER_SEED}  "
      f"TTL_DAYS={config.VENUE_EVENTS_LINK_TTL_DAYS}")

venues = venue_store.list_venues(db)
linked = [v for v in venues if str(v.get("events_link") or "").strip()]
print(f"\nVenues: {len(venues)} total, {len(linked)} with events_link")
print("-- venues with events_link (name | host | last_mined | events_link_checked) --")
for v in linked:
    host = urlparse(str(v.get("events_link"))).netloc
    print(f"  {str(v.get('name')):30.30} | {host:28.28} | "
          f"last_mined={str(v.get('last_mined') or '—'):27.27} | "
          f"checked={str(v.get('events_link_checked') or '—')}")

print("\n-- last 3 reports (newest first) --")
for rep in report_store.list_reports(db, limit=3):
    urls = rep.get("urls") or {}
    hosts = list(urls.keys()) if isinstance(urls, dict) else urls
    print(f"\n  {rep.get('datetime')}  searches={len(rep.get('searches') or [])}  "
          f"memory_seed={rep.get('memory_seed') or '—'}")
    print(f"    crawled hosts ({len(hosts)}): {hosts}")
    print(f"    searches: {rep.get('searches')}")
