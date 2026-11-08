import argparse
import csv
import os
import time
from collections import Counter

from players import (
    MAX_CONSECUTIVE_FAILURES, MAX_HP_LEVEL, MIN_COMBAT_XP, SCORES_PATH, USERNAMES_PATH, FIELDS,
    PlayerCheck, RateLimited, check_player, create_session,
    read_scores, read_usernames, username_key, write_csv, write_usernames,
)
from usernameInbox import (
    consume_inbox, prepare_related_changes, read_inbox, rename_scores, resolve_renames,
    write_related_changes,
)


DAY = 24 * 60 * 60
INELIGIBLE_RESULTS = {"constitution", "combat_xp"}


def refresh_players(session, usernames, refresh_player_data=False):
    checks = {}
    aliases = {}
    player_data_cache = {}
    counts = Counter()
    unavailable = 0
    optional_failures = 0
    total = len(usernames)
    for index, username in enumerate(usernames, start=1):
        key = username_key(username)
        result = aliases.get(key)
        if result is None:
            result = check_player(session, username, player_data_cache, refresh_player_data)
        checks[key] = result
        counts[result.status] += 1
        if result.row is not None:
            aliases[username_key(result.row["RSN"])] = result
        progress = f"[{index:,}/{total:,}] {username}: {result.status.replace('_', ' ')}"
        if result.row is not None:
            row = result.row
            progress += f" | HP {row['HPLVL']} ({row['HPEXP']:,} XP) | Total XP {row['TOTEXP']:,}"
        if result.error is not None:
            progress += f" ({result.error})"
        print(progress, flush=True)

        if isinstance(result.error, RateLimited):
            print("Stopping requests after HTTP 429; unchecked entries use the missing-data grace period.", flush=True)
            break
        unavailable = unavailable + 1 if result.status == "unavailable" else 0
        if unavailable >= MAX_CONSECUTIVE_FAILURES:
            print("Stopping requests after repeated failures; retaining recent cached entries.", flush=True)
            break
        optional_failures = optional_failures + 1 if (
            refresh_player_data and result.source == "hiscores" and result.error is not None
        ) else 0
        if optional_failures >= MAX_CONSECUTIVE_FAILURES:
            print("Skipping optional Player Data refreshes after repeated failures.", flush=True)
            refresh_player_data = False
    print(", ".join(f"{status}: {count}" for status, count in sorted(counts.items())), flush=True)
    return checks


def publish_scores(usernames, checks, old_scores, inactive_days, now):
    roster = {}
    corrections = {}
    published = {}
    for username in usernames:
        key = username_key(username)
        result = checks.get(key, PlayerCheck("unavailable"))
        fresh = result.row if result.status == "eligible" else None
        canonical = fresh["RSN"] if fresh is not None else username
        canonical_key = username_key(canonical)
        roster.setdefault(canonical_key, canonical)
        if canonical != username:
            corrections[canonical_key] = canonical
            print(f"Correcting username {username!r} to {canonical!r}")

        old = old_scores.get(key)
        if old is None:
            old = old_scores.get(canonical_key)
        if fresh is not None:
            row = dict(fresh)
            if row.get("QPOINTS", "") == "":
                row["QPOINTS"] = old.get("QPOINTS", "") if old is not None else ""
            if row.get("RSCORE", "") == "" and old is not None:
                row["RSCORE"] = old.get("RSCORE", "")
        elif result.status in INELIGIBLE_RESULTS:
            continue
        elif old is not None and now - int(old["TIME"]) < inactive_days * DAY:
            row = dict(old)
        else:
            continue
        row["RSN"] = canonical
        if canonical_key in published:
            previous = published[canonical_key]
            if (int(previous["TIME"]), int(previous["TOTEXP"])) > (int(row["TIME"]), int(row["TOTEXP"])):
                row, previous = previous, row
            for field in ("QPOINTS", "RSCORE"):
                if row.get(field, "") == "":
                    row[field] = previous.get(field, "")
        published[canonical_key] = row
    roster.update(corrections)
    for key, row in published.items():
        row["RSN"] = roster[key]
    return [roster[key] for key in sorted(roster)], [published[key] for key in sorted(published)]


def print_summary(usernames, checks, old_scores, rows, inactive_days, now):
    statuses = {status: [] for status in (
        "eligible", "constitution", "combat_xp", "not_found", "incomplete", "unavailable", "unchecked",
    )}
    corrections = []
    warnings = []
    retained = []
    expired = []
    published = {username_key(row["RSN"]) for row in rows}
    refreshed = {
        username_key(result.row["RSN"]) for result in checks.values()
        if result.status == "eligible" and result.row is not None
    }
    for username in sorted(usernames, key=username_key):
        key = username_key(username)
        result = checks.get(key)
        status = result.status if result is not None else "unchecked"
        detail = username
        if status == "unavailable" and result.error is not None:
            detail += f" ({result.error})"
        statuses[status].append(detail)
        if status == "eligible" and result.row is not None:
            canonical = result.row["RSN"]
            if canonical != username:
                corrections.append(f"{username!r} -> {canonical!r}")
            if result.error is not None:
                warnings.append(f"{canonical} ({result.error})")
        elif status not in INELIGIBLE_RESULTS and key not in refreshed:
            old = old_scores.get(key)
            if old is not None:
                if key in published:
                    retained.append(username)
                elif now - int(old["TIME"]) >= inactive_days * DAY:
                    expired.append(username)

    groups = (
        (f"Accounts ruined (Constitution above {MAX_HP_LEVEL})", statuses["constitution"]),
        (f"Not ranked (adjusted combat XP below {MIN_COMBAT_XP:,})", statuses["combat_xp"]),
        ("Missing players / possible RSN changes", statuses["not_found"]),
        ("Incomplete player data", statuses["incomplete"]),
        ("Unavailable player data", statuses["unavailable"]),
        ("Unchecked accounts", statuses["unchecked"]),
        ("Username corrections", corrections),
        ("API warnings (usable stats)", warnings),
        (f"Cached scores retained ({inactive_days}-day grace period)", retained),
        (f"Cached scores expired ({inactive_days}-day grace period)", expired),
    )
    checked = len(usernames) - len(statuses["unchecked"])
    print("\nHiScores update summary", flush=True)
    print(f"Published {len(rows):,} scores from {len(usernames):,} tracked accounts.", flush=True)
    print(f"Checked {checked:,}/{len(usernames):,} accounts; {len(statuses['eligible']):,} returned eligible stats.", flush=True)
    for label, names in groups:
        detail = f" ({', '.join(names)})" if names else ""
        print(f"{label}: {len(names):,}{detail}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Refresh the leaderboard.")
    parser.add_argument("--refresh-player-data", "--refresh-quest-points", dest="refresh_player_data",
                        action="store_true", help="Refresh skills and Quest Points using Player Data.")
    parser.add_argument("--inactive-days", type=int, default=os.getenv("HISCORES_INACTIVE_DAYS") or 14)
    args = parser.parse_args()
    if args.inactive_days < 1:
        parser.error("inactive days must be positive")

    tracked = read_usernames(USERNAMES_PATH)
    inbox_path = USERNAMES_PATH.with_name("username_inbox.csv")
    try:
        queued = read_inbox(inbox_path)
        renames = resolve_renames(queued)
    except (ValueError, csv.Error) as error:
        parser.error(str(error))
    renamed = [renames.get(username_key(name), name) for name in tracked]
    roster = {username_key(name): name for name in renamed if name}
    additions = []
    pending = [new for old, new in queued if not old] + [name for name in renames.values() if name]
    for name in pending:
        # A separate addition can restore a removed name without its old data.
        name = renames.get(username_key(name), name) or name
        key = username_key(name)
        if key not in roster:
            roster[key] = name
            additions.append(name)
    usernames = [roster[key] for key in sorted(roster)]
    if queued:
        detail = f" ({', '.join(additions)})" if additions else ""
        print(f"New usernames from inbox: {len(additions):,}{detail}", flush=True)
        for old, _ in queued:
            if old:
                new = renames[username_key(old)]
                message = f"Changing username {old!r} to {new!r}" if new else f"Removing username {old!r}"
                print(message, flush=True)
    old_scores, columns = read_scores(SCORES_PATH)
    old_scores = rename_scores(old_scores, renames)
    related_changes = prepare_related_changes(USERNAMES_PATH.parent, renames)
    with create_session() as session:
        checks = refresh_players(session, usernames, args.refresh_player_data or "QPOINTS" not in columns)
    now = int(time.time())
    corrected, rows = publish_scores(usernames, checks, old_scores, args.inactive_days, now)
    write_csv(SCORES_PATH, FIELDS, rows)
    if corrected != tracked:
        write_usernames(USERNAMES_PATH, corrected)
    write_related_changes(related_changes)
    if queued and inbox_path.exists():
        consume_inbox(inbox_path, queued)
    print_summary(usernames, checks, old_scores, rows, args.inactive_days, now)


if __name__ == "__main__":
    main()
