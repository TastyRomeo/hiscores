import csv
import time
from collections import Counter
from pathlib import Path

from mergeHiscores import SCORES_PATH, read_scores
from updateHiscores import (
    MAX_CONSECUTIVE_FAILURES,
    DataUnavailable,
    RateLimited,
    create_session,
    get_hiscores,
    parse_skills,
    read_usernames,
    username_key,
)


BADGES_PATH = Path("data/ironman-badges.csv")
BADGE_FIELDS = ("RSN", "BADGE")
MAX_SCORE_AGE = 2 * 60 * 60
MODE_URLS = (
    ("hardcore", "https://secure.runescape.com/m=hiscore_hardcore_ironman/index_lite.json"),
    ("ironman", "https://secure.runescape.com/m=hiscore_ironman/index_lite.json"),
)


def matches_cached_score(stats, score):
    total_level, total_xp, skills = stats
    hp_level, hp_xp = skills["Hitpoints"]
    return (total_level, total_xp, hp_level, hp_xp) == tuple(
        int(score[field]) for field in ("TOTLVL", "TOTEXP", "HPLVL", "HPEXP")
    )


def find_badge(session, username, recent_score, previous_badge):
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
            if recent_score is not None and matches_cached_score(stats, recent_score):
                return badge
            live = get_hiscores(session, username)
            if live is None:
                return None
            standard = parse_skills(live)
            if standard is None:
                raise DataUnavailable("Incomplete standard skill record")
        if stats == standard:
            return badge

    if previous_badge and recent_score is None and standard is None:
        # Confirm standard Hiscores when clearing a badge without a recent score.
        if get_hiscores(session, username) is None:
            print(f"{username}: no standard Hiscores record")
    return None


def read_badges():
    with BADGES_PATH.open(newline="", encoding="utf-8") as file:
        return {username_key(row["RSN"]): row["BADGE"] for row in csv.DictReader(file)}


def main():
    scores, _ = read_scores(SCORES_PATH)
    old_badges = read_badges()
    rows = []
    counts = Counter()
    consecutive_failures = 0
    now = int(time.time())

    with create_session() as session:
        for username in read_usernames():
            key = username_key(username)
            score = scores.get(key)
            if score is None:
                continue

            previous_badge = old_badges.get(key)
            recent_score = score if 0 <= now - int(score["TIME"]) <= MAX_SCORE_AGE else None
            try:
                badge = find_badge(session, username, recent_score, previous_badge)
            except RateLimited as error:
                raise SystemExit(f"Keeping the previous badges. {error}") from error
            except DataUnavailable as error:
                consecutive_failures += 1
                counts["unavailable"] += 1
                print(f"{username}: {error}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SystemExit("Keeping the previous badges because Index Lite is unavailable.")
                badge = previous_badge
            else:
                consecutive_failures = 0

            counts[badge or "no badge"] += 1
            if badge:
                rows.append((username, badge))

    temporary = BADGES_PATH.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(BADGE_FIELDS)
        writer.writerows(rows)
    temporary.replace(BADGES_PATH)

    print(", ".join(f"{label}: {count}" for label, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
