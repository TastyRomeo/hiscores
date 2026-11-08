import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from players import (
    DataUnavailable, RateLimited, check_player, create_session,
    read_scores, read_usernames, request, username_key, write_csv, write_usernames,
)


DAY = 24 * 60 * 60
CLAN_MEMBERS_URL = "https://secure.runescape.com/m=clan-hiscores/members_lite.ws"
ARCHIVE_COLUMNS = ["RSN", "REMOVED_AT", "REASON", "LAST_VALID_AT"]
MISSING_PLAYER_RESULTS = {"not_found", "incomplete"}
VALID_PLAYER_RESULTS = {"eligible", "constitution", "combat_xp"}


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
    if "Clanmate" not in (reader.fieldnames or []):
        raise ValueError("unexpected clan CSV header")
    members = []
    for row in reader:
        clanmate = row["Clanmate"]
        if clanmate is None:
            raise ValueError("incomplete clan CSV row")
        name = clanmate.replace("\xa0", " ").strip()
        if name and not name.startswith("[#"):
            members.append(name)
    return members


def player_result(session, name, check_cache=None, player_data_cache=None):
    key = username_key(name)
    if check_cache is not None and key in check_cache:
        return check_cache[key]
    result = check_player(session, name, player_data_cache)
    if isinstance(result.error, RateLimited):
        raise result.error
    if check_cache is not None:
        check_cache[key] = result
        if result.row is not None:
            check_cache[username_key(result.row["RSN"])] = result
    return result


def player_status(session, name, check_cache=None, player_data_cache=None):
    result = player_result(session, name, check_cache, player_data_cache)
    return result.status


def log_check(phase, position, total, name, status):
    if position in (1, total) or position % 25 == 0 or status in {"eligible", "unavailable"}:
        print(f"{phase} [{position}/{total}] {name!r}: {status.replace('_', ' ')}")


def discover(session, clans_path, active, check_cache=None, player_data_cache=None):
    if check_cache is None:
        check_cache = {}
    if player_data_cache is None:
        player_data_cache = {}
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
            print(f"{progress}: {len(members)} members")
        except RateLimited:
            raise
        except (DataUnavailable, ValueError) as exc:
            print(f"{progress}: could not read roster ({exc})")

    keys = sorted(candidates.keys() - active.keys())
    print(f"Checking all {len(keys)} clan candidates")

    additions = []
    known = set(active)
    unavailable = 0
    for position, key in enumerate(keys, start=1):
        name = candidates[key]
        result = player_result(session, name, check_cache, player_data_cache)
        status = result.status
        log_check("Clan candidates", position, len(keys), name, status)
        unavailable = unavailable + 1 if status == "unavailable" else 0
        if unavailable == 3:
            print("Stopping clan candidate checks after three consecutive unavailable results")
            break
        if status == "eligible":
            canonical_name = result.row["RSN"]
            canonical_key = username_key(canonical_name)
            if canonical_key not in known:
                additions.append(canonical_name)
                known.add(canonical_key)
    return additions


def check_missing(session, active, score_times, missing, now, inactive_days,
                  check_cache=None, player_data_cache=None):
    if check_cache is None:
        check_cache = {}
    if player_data_cache is None:
        player_data_cache = {}
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
        return {}

    # Known accounts distinguish player-specific failures from an endpoint-wide failure.
    control_keys = active.keys() & score_times.keys()
    scored_controls = bool(control_keys)
    if not control_keys:
        control_keys = active.keys()
    controls = sorted(control_keys, key=lambda key: (-score_times.get(key, 0), key))[:3]
    required_healthy = min(2, len(controls)) if scored_controls else 1
    print(f"Checking Hiscores health against {len(controls)} recently scored usernames")
    results = {
        key: player_status(session, active[key], check_cache, player_data_cache)
        for key in controls
    }
    healthy_controls = sum(status in VALID_PLAYER_RESULTS for status in results.values())
    print(f"Player data health check: {healthy_controls}/{len(controls)} returned usable stats")
    if not controls or healthy_controls < required_healthy:
        print("Skipping retirement checks because the player data health check failed")
        return {}

    print(f"Checking {len(stale)} active usernames without a recent score")
    retirements = {}
    unavailable = 0
    for position, key in enumerate(stale, start=1):
        status = results.get(key)
        if status is None:
            status = player_status(session, active[key], check_cache, player_data_cache)
        log_check("Active usernames", position, len(stale), active[key], status)

        unavailable = unavailable + 1 if status == "unavailable" else 0
        if status not in MISSING_PLAYER_RESULTS:
            missing.pop(key, None)
            if unavailable == 3:
                print("Stopping retirement checks after three consecutive unavailable results")
                break
            continue

        observation = missing.get(key)
        if (
            observation is None
            or observation.get("status") not in MISSING_PLAYER_RESULTS
            or score_times.get(key, 0) >= observation["first"]
            or now - observation["last"] > 3 * DAY
        ):
            observation = {"first": now, "last": now}
            missing[key] = observation

        observation["last"] = now
        observation["status"] = status
        if now - observation["first"] >= inactive_days * DAY:
            retirements[key] = status
            del missing[key]

    return retirements


def utc_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def maintain(data_dir, inactive_days, discover_clans=False):
    usernames_path = data_dir / "usernames.csv"
    scores_path = data_dir / "hiscores.csv"
    archive_path = data_dir / "removed-usernames.csv"
    state_path = data_dir / "username-maintenance.json"
    original_state = state_path.read_text(encoding="utf-8")
    state = json.loads(original_state)
    state.pop("discovery_cursor", None)

    active = {username_key(name): name for name in read_usernames(usernames_path)}
    scores, columns = read_scores(scores_path)
    score_times = {key: int(row["TIME"]) for key, row in scores.items()}
    with archive_path.open(newline="", encoding="utf-8") as file:
        archive = list(csv.DictReader(file))
    archived = {username_key(row["RSN"]) for row in archive}
    now = int(time.time())

    print(f"Roster maintenance: {len(active)} active usernames, {inactive_days}-day retirement grace period")
    check_cache = {}
    player_data_cache = {}
    with create_session() as session:
        retirements = check_missing(
            session, active, score_times, state["missing"], now, inactive_days,
            check_cache, player_data_cache,
        )
        additions = discover(
            session, data_dir / "clans.txt", active, check_cache, player_data_cache,
        ) if discover_clans else []

    for key, reason in retirements.items():
        name = active.pop(key)
        print(f"Retiring {name!r} ({reason.replace('_', ' ')})")
        archive.append({
            "RSN": name,
            "REMOVED_AT": utc_timestamp(now),
            "REASON": reason,
            "LAST_VALID_AT": utc_timestamp(score_times[key]) if key in score_times else "",
        })

    for name in additions:
        key = username_key(name)
        active[key] = name
        action = "Restoring" if key in archived else "Adding"
        print(f"{action} {name!r}")

    print(f"Roster changes: add or restore {len(additions)}, retire {len(retirements)}")

    if additions or retirements:
        write_usernames(usernames_path, [active[key] for key in sorted(active)])

    if retirements:
        if retirements.keys() & scores.keys():
            write_csv(scores_path, columns, (row for key, row in scores.items() if key not in retirements))
        write_csv(archive_path, ARCHIVE_COLUMNS, archive)

    new_state = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if new_state != original_state:
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(new_state, encoding="utf-8")
        temporary.replace(state_path)


def main():
    parser = argparse.ArgumentParser(description="Discover and retire HiScores usernames")
    parser.add_argument("--inactive-days", type=int, default=os.getenv("HISCORES_INACTIVE_DAYS") or 14)
    parser.add_argument("--discover-clans", action="store_true", help="Check all new clan candidates")
    args = parser.parse_args()
    if args.inactive_days < 1:
        parser.error("inactive days must be positive")
    try:
        maintain(Path("data"), args.inactive_days, discover_clans=args.discover_clans)
    except RateLimited as error:
        raise SystemExit(f"Stopping roster maintenance: {error}") from error


if __name__ == "__main__":
    main()
