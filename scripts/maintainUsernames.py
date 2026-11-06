import argparse
import csv
import json
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from mergeHiscores import read_scores
from updateHiscores import (
    DataUnavailable, RateLimited, check_player, create_session,
    read_usernames, request, username_key,
)


DAY = 24 * 60 * 60
CLAN_MEMBERS_URL = "https://secure.runescape.com/m=clan-hiscores/members_lite.ws"
CLAN_MIN_XP = 100_000
ARCHIVE_COLUMNS = ["RSN", "REMOVED_AT", "REASON", "LAST_VALID_AT"]
INVALID_PLAYER_RESULTS = {"not_found", "incomplete", "constitution", "combat_xp"}


def clan_members(session, clan):
    response = request(
        session,
        CLAN_MEMBERS_URL,
        params={"clanName": clan},
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}")

    try:
        body = response.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        body = response.content.decode("cp1252")

    reader = csv.DictReader(body.splitlines(), skipinitialspace=True)
    if not {"Clanmate", "Total XP"}.issubset(reader.fieldnames or []):
        raise ValueError("unexpected clan CSV header")
    members = []
    for row in reader:
        clanmate, xp = row["Clanmate"], row["Total XP"]
        if clanmate is None or xp is None:
            raise ValueError("incomplete clan CSV row")
        name = clanmate.replace("\xa0", " ").strip()
        if name and not name.startswith("[#") and int(xp) >= CLAN_MIN_XP:
            members.append(name)
    return members


def player_status(session, name):
    result = check_player(session, name)
    if isinstance(result.error, RateLimited):
        raise result.error
    return result.status


def log_check(phase, position, total, name, status):
    if position in (1, total) or position % 25 == 0 or status in {"eligible", "unavailable"}:
        print(f"{phase} [{position}/{total}] {name!r}: {status.replace('_', ' ')}")


def discover(session, clans_path, active, state, limit):
    if not limit:
        print("Clan discovery is disabled.")
        return []

    candidates = {}
    lines = clans_path.read_text(encoding="utf-8").splitlines()
    clans = [clan for clan in map(str.strip, lines) if clan and not clan.startswith("#")]
    print(f"Scanning {len(clans)} configured clans")
    for position, clan in enumerate(clans, start=1):
        progress = f"Clan scan [{position}/{len(clans)}] {clan!r}"
        try:
            members = clan_members(session, clan)
            for name in members:
                candidates.setdefault(username_key(name), name)
            print(f"{progress}: {len(members)} members passed the clan XP filter")
        except RateLimited:
            raise
        except (DataUnavailable, ValueError) as exc:
            print(f"{progress}: could not read roster ({exc})")

    keys = sorted(candidates.keys() - active.keys())
    offset = bisect_right(keys, state["discovery_cursor"])
    selected = (keys[offset:] + keys[:offset])[:limit]
    print(f"Checking up to {len(selected)} of {len(keys)} clan candidates")

    additions = []
    unavailable = 0
    for position, key in enumerate(selected, start=1):
        name = candidates[key]
        status = player_status(session, name)
        log_check("Clan candidates", position, len(selected), name, status)
        state["discovery_cursor"] = key
        unavailable = unavailable + 1 if status == "unavailable" else 0
        if unavailable == 3:
            print("Stopping clan candidate checks after three consecutive unavailable results")
            break
        if status == "eligible":
            additions.append(name)
    return additions


def check_missing(session, active, score_times, missing, now, inactive_days):
    stale = [
        key for key in active
        if key not in score_times or now - score_times[key] >= DAY
    ]
    stale_keys = set(stale)
    for key in list(missing):
        if key not in stale_keys:
            del missing[key]

    if not stale:
        print("All active usernames have recent scores.")
        return []

    # Known accounts distinguish player-specific failures from an endpoint-wide failure.
    controls = sorted(active.keys() & score_times.keys(), key=lambda key: (-score_times[key], key))[:3]
    print(f"Checking Hiscores health against {len(controls)} recently scored usernames")
    results = {key: player_status(session, active[key]) for key in controls}
    eligible_controls = sum(status == "eligible" for status in results.values())
    print(f"Hiscores health check: {eligible_controls}/{len(controls)} returned eligible scores")
    if not controls or eligible_controls < min(2, len(controls)):
        print("Skipping retirement checks because the Hiscores health check failed")
        return []

    print(f"Checking {len(stale)} active usernames without a recent score")
    retirements = []
    unavailable = 0
    for position, key in enumerate(stale, start=1):
        status = results.get(key)
        if status is None:
            status = player_status(session, active[key])
        log_check("Active usernames", position, len(stale), active[key], status)

        unavailable = unavailable + 1 if status == "unavailable" else 0
        if status not in INVALID_PLAYER_RESULTS:
            missing.pop(key, None)
            if unavailable == 3:
                print("Stopping retirement checks after three consecutive unavailable results")
                break
            continue

        observation = missing.get(key)
        if (
            observation is None
            or score_times.get(key, 0) >= observation["first"]
            or now - observation["last"] > 3 * DAY
        ):
            observation = {"first": now, "last": now}
            missing[key] = observation

        observation["last"] = now
        if now - observation["first"] >= inactive_days * DAY:
            retirements.append((key, status))
            del missing[key]

    return retirements


def write_csv(path, fieldnames, rows):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def utc_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def maintain(data_dir, inactive_days, discovery_limit, dry_run):
    usernames_path = data_dir / "usernames.csv"
    scores_path = data_dir / "hiscores.csv"
    archive_path = data_dir / "removed-usernames.csv"
    state_path = data_dir / "username-maintenance.json"
    original_state = state_path.read_text(encoding="utf-8")
    state = json.loads(original_state)

    active = {username_key(name): name for name in read_usernames(usernames_path)}
    scores, columns = read_scores(scores_path)
    score_times = {key: int(row["TIME"]) for key, row in scores.items()}
    with archive_path.open(newline="", encoding="utf-8") as file:
        archive = list(csv.DictReader(file))
    archived = {username_key(row["RSN"]) for row in archive}
    now = int(time.time())

    mode = "Dry run" if dry_run else "Roster maintenance"
    print(f"{mode}: {len(active)} active usernames, {inactive_days}-day retirement grace period")
    with create_session() as session:
        retirements = check_missing(session, active, score_times, state["missing"], now, inactive_days)
        additions = discover(session, data_dir / "clans.txt", active, state, discovery_limit)

    retired_keys = set()
    for key, reason in retirements:
        name = active.pop(key)
        retired_keys.add(key)
        action = "Would retire" if dry_run else "Retiring"
        print(f"{action} {name!r} ({reason.replace('_', ' ')})")
        archive.append({
            "RSN": name,
            "REMOVED_AT": utc_timestamp(now),
            "REASON": reason,
            "LAST_VALID_AT": utc_timestamp(score_times[key]) if key in score_times else "",
        })

    for name in additions:
        key = username_key(name)
        active[key] = name
        if key in archived:
            action = "Would restore" if dry_run else "Restoring"
        else:
            action = "Would add" if dry_run else "Adding"
        print(f"{action} {name!r}")

    print(f"Roster changes: add or restore {len(additions)}, retire {len(retirements)}")
    if dry_run:
        print("Dry run complete")
        return

    if additions or retirements:
        temporary = usernames_path.with_suffix(".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerows((active[key],) for key in sorted(active))
        temporary.replace(usernames_path)

    if retirements:
        if retired_keys & scores.keys():
            write_csv(scores_path, columns, (row for key, row in scores.items() if key not in retired_keys))
        write_csv(archive_path, ARCHIVE_COLUMNS, archive)

    new_state = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if new_state != original_state:
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(new_state, encoding="utf-8")
        temporary.replace(state_path)


def main():
    parser = argparse.ArgumentParser(description="Discover and retire HiScores usernames")
    parser.add_argument("--inactive-days", type=int, default=14)
    parser.add_argument("--discovery-limit", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.inactive_days < 1 or args.discovery_limit < 0:
        parser.error("inactive days must be positive and discovery limit must be nonnegative")
    try:
        maintain(Path("data"), args.inactive_days, args.discovery_limit, args.dry_run)
    except RateLimited as error:
        raise SystemExit(f"Stopping roster maintenance: {error}") from error


if __name__ == "__main__":
    main()
