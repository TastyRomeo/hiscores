import csv
from collections import Counter
from pathlib import Path

from updateHiscores import (
    MAX_CONSECUTIVE_FAILURES,
    SCORES_PATH,
    DataUnavailable,
    RateLimited,
    create_session,
    get_hiscores,
    parse_skills,
    read_scores,
    read_usernames,
    username_key,
)


BADGES_PATH = Path("data/ironman-badges.csv")
BADGE_FIELDS = ("RSN", "BADGE")
MODE_URLS = (
    ("hardcore", "https://secure.runescape.com/m=hiscore_hardcore_ironman/index_lite.json"),
    ("ironman", "https://secure.runescape.com/m=hiscore_ironman/index_lite.json"),
)


def find_badge(session, username):
    standard = None
    for badge, url in MODE_URLS:
        payload = get_hiscores(session, username, url)
        if payload is None:
            continue

        stats = parse_skills(payload)
        if badge == "hardcore":
            overall = next(skill for skill in payload["skills"] if skill["name"] == "Overall")
            if isinstance(overall.get("death"), dict):
                continue
        if stats is None:
            raise DataUnavailable(f"Incomplete {badge} skill record")

        if standard is None:
            # Mode scores can freeze when a player loses eligibility.
            live = get_hiscores(session, username)
            if live is None:
                return None
            standard = parse_skills(live)
            if standard is None:
                raise DataUnavailable("Incomplete standard skill record")
        if stats == standard:
            return badge

    return None


def read_badges():
    with BADGES_PATH.open(newline="", encoding="utf-8") as file:
        return {username_key(row["RSN"]): row["BADGE"] for row in csv.DictReader(file)}


def main():
    scores, _ = read_scores(SCORES_PATH)
    old_badges = read_badges()
    usernames = read_usernames()
    candidates = [username for username in usernames if username_key(username) in scores]
    total = len(candidates)
    print(f"Checking Ironman badges for {total:,} players with scores "
          f"({len(usernames) - total:,} without scores).", flush=True)
    rows = []
    counts = Counter()
    changes = 0
    consecutive_failures = 0

    with create_session() as session:
        for index, username in enumerate(candidates, start=1):
            key = username_key(username)
            progress = f"[{index:,}/{total:,}] {username}"
            previous_badge = old_badges.get(key)
            try:
                badge = find_badge(session, username)
            except RateLimited as error:
                print(f"{progress}: rate limited ({error})", flush=True)
                raise SystemExit(f"Keeping the previous badges. {error}") from error
            except DataUnavailable as error:
                consecutive_failures += 1
                counts["unavailable"] += 1
                print(f"{progress}: unavailable ({error}), keeping {previous_badge or 'no badge'}",
                      flush=True)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SystemExit("Keeping the previous badges because Index Lite is unavailable.")
                badge = previous_badge
            else:
                consecutive_failures = 0
                if badge != previous_badge:
                    changes += 1
                    print(f"{progress}: {previous_badge or 'no badge'} -> {badge or 'no badge'}",
                          flush=True)
                elif index in (1, total) or index % 25 == 0:
                    print(f"{progress}: {badge or 'no badge'}", flush=True)

            counts[badge or "no badge"] += 1
            if badge:
                rows.append((username, badge))

    temporary = BADGES_PATH.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(BADGE_FIELDS)
        writer.writerows(rows)
    temporary.replace(BADGES_PATH)

    print(f"Finished {total:,} checks with {changes:,} badge changes. Wrote {len(rows):,} badges.",
          flush=True)
    if counts:
        print(", ".join(f"{label}: {count}" for label, count in sorted(counts.items())), flush=True)


if __name__ == "__main__":
    main()
