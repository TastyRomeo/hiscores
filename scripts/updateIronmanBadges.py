import csv
from collections import Counter
from pathlib import Path

from players import (
    MAX_CONSECUTIVE_FAILURES,
    DataUnavailable,
    RateLimited,
    create_session,
    get_hiscores,
    read_usernames,
    username_key,
)


BADGES_PATH = Path("data/ironman-badges.csv")
BADGE_FIELDS = ("RSN", "BADGE")
MODE_URLS = (
    ("hardcore", "https://secure.runescape.com/m=hiscore_hardcore_ironman/index_lite.json"),
    ("ironman", "https://secure.runescape.com/m=hiscore_ironman/index_lite.json"),
)


def overall_record(payload):
    records = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise DataUnavailable("Invalid Index Lite skills")
    overall = [record for record in records if record.get("name") == "Overall"]
    if len(overall) != 1:
        raise DataUnavailable("Index Lite must contain one Overall record")
    return overall[0]


def find_badge(session, username):
    errors = []
    for badge, url in MODE_URLS:
        try:
            payload = get_hiscores(session, username, url)
            overall = overall_record(payload) if payload is not None else None
        except RateLimited:
            raise
        except DataUnavailable as error:
            errors.append(f"{badge}: {error}")
            continue
        if payload is None:
            continue
        if badge == "hardcore" and isinstance(overall.get("death"), dict):
            continue
        return badge

    if errors:
        raise DataUnavailable("; ".join(errors))
    return None


def read_badges():
    with BADGES_PATH.open(newline="", encoding="utf-8") as file:
        return {username_key(row["RSN"]): row["BADGE"] for row in csv.DictReader(file)}


def main():
    old_badges = read_badges()
    usernames = read_usernames()
    total = len(usernames)
    print(f"Checking Ironman badges for {total:,} tracked players.", flush=True)
    rows = []
    counts = Counter()
    changes = 0
    consecutive_failures = 0
    checked = 0
    stopped = None

    with create_session() as session:
        for index, username in enumerate(usernames, start=1):
            key = username_key(username)
            progress = f"[{index:,}/{total:,}] {username}"
            previous_badge = old_badges.get(key)
            try:
                badge = find_badge(session, username)
            except RateLimited as error:
                badge = None
                stopped = f"rate limited ({error})"
                print(f"{progress}: {stopped}, clearing badge", flush=True)
            except DataUnavailable as error:
                consecutive_failures += 1
                counts["unavailable"] += 1
                print(f"{progress}: unavailable ({error}), clearing badge",
                      flush=True)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    stopped = "consecutive lookup failures"
                badge = None
            else:
                consecutive_failures = 0
            if badge != previous_badge:
                changes += 1
                print(f"{progress}: {previous_badge or 'no badge'} -> {badge or 'no badge'}",
                      flush=True)
            elif index in (1, total) or index % 25 == 0:
                print(f"{progress}: {badge or 'no badge'}", flush=True)

            checked = index
            counts[badge or "no badge"] += 1
            if badge:
                rows.append((username, badge))
            if stopped:
                break

    if stopped:
        for username in usernames[checked:]:
            badge = old_badges.get(username_key(username))
            if badge:
                rows.append((username, badge))
        print(f"Stopped after {checked:,}/{total:,} checks: {stopped}. "
              "Keeping previous badges for unchecked players.", flush=True)

    temporary = BADGES_PATH.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(BADGE_FIELDS)
        writer.writerows(rows)
    temporary.replace(BADGES_PATH)

    print(f"Finished {checked:,} checks with {changes:,} badge changes. Wrote {len(rows):,} badges.",
          flush=True)
    if counts:
        print(", ".join(f"{label}: {count}" for label, count in sorted(counts.items())), flush=True)


if __name__ == "__main__":
    main()
