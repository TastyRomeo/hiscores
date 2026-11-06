import argparse
import csv
from pathlib import Path

from updateHiscores import (
    FIELDS,
    MAX_CONSECUTIVE_FAILURES,
    NEW_SCORES_PATH,
    DataUnavailable,
    RateLimited,
    create_session,
    get_quest_points,
    read_usernames,
    username_key,
)


SCORES_PATH = Path("data/hiscores.csv")


def read_scores(path):
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = {}
        for row in reader:
            key = username_key(row["RSN"])
            if key not in rows or int(row["TIME"]) >= int(rows[key]["TIME"]):
                rows[key] = row
        return rows, reader.fieldnames


def merge_scores(old_scores, new_scores, usernames):
    rows = []
    for name in usernames:
        key = username_key(name)
        old = old_scores.get(key)
        new = new_scores.get(key)
        if old is None and new is None:
            continue
        if new is None or (old is not None and int(old["TIME"]) > int(new["TIME"])):
            score = dict(old)
        else:
            score = dict(new)
        score["RSN"] = name
        if score.get("QPOINTS") in (None, "") and old is not None:
            score["QPOINTS"] = old.get("QPOINTS", "")
        rows.append(score)
    return rows


def refresh_quest_points(rows):
    counts = {"updated": 0, "no public data": 0, "unavailable": 0}
    consecutive_failures = 0

    with create_session() as session:
        for row in rows:
            try:
                points = get_quest_points(session, row["RSN"])
            except DataUnavailable as error:
                counts["unavailable"] += 1
                consecutive_failures += 1
                print(f"{row['RSN']}: {error}")
                if (isinstance(error, RateLimited)
                        or consecutive_failures >= MAX_CONSECUTIVE_FAILURES):
                    print("Stopping the Quest Points refresh. Keeping cached values for unchecked players.")
                    break
                continue
            consecutive_failures = 0
            row["QPOINTS"] = points
            counts["no public data" if points is None else "updated"] += 1

    print("Quest Points " + ", ".join(f"{label}: {count}" for label, count in counts.items()))


def main():
    parser = argparse.ArgumentParser(description="Merge new scores into the leaderboard.")
    parser.add_argument("--refresh-quest-points", action="store_true",
                        help="Fetch Quest Points for all published scores.")
    args = parser.parse_args()

    old, old_columns = read_scores(SCORES_PATH)
    new, _ = read_scores(NEW_SCORES_PATH)
    rows = merge_scores(old, new, read_usernames())
    if args.refresh_quest_points or "QPOINTS" not in old_columns:
        refresh_quest_points(rows)

    temporary = SCORES_PATH.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(SCORES_PATH)
    NEW_SCORES_PATH.unlink()


if __name__ == "__main__":
    main()
