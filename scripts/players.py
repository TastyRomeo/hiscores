import csv
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests


HISCORES_URL = "https://secure.runescape.com/m=hiscore/index_lite.json"
PLAYER_DATA_URL = "https://secure.runescape.com/m=adventurers-log/api/v1/playerData"
USER_AGENT = "RS3 Low HP Hiscores (+https://github.com/TastyRomeo/hiscores)"
REQUEST_TIMEOUT = (5, 12)
MAX_CONSECUTIVE_FAILURES = 5
USERNAMES_PATH = Path("data/usernames.csv")
SCORES_PATH = Path("data/hiscores.csv")
FIELDS = (
    "RSN", "HPLVL", "HPEXP", "TOTLVL", "TOTLVLADJ", "VIRLVL", "VIRLVLADJ",
    "TOTEXP", "TOTEXPADJ", "CMBLVL", "CMBLVLADJ", "CMBEXPADJ", "RSCORE",
    "QPOINTS", "TIME",
)

COMBAT_SKILLS = ("Attack", "Strength", "Magic", "Ranged", "Necromancy",
                 "Defence", "Hitpoints", "Prayer", "Summoning")
BASE_SKILLS = set(COMBAT_SKILLS) | {
    "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting",
    "Smithing", "Mining", "Herblore", "Agility", "Thieving", "Slayer", "Farming",
    "Runecraft", "Hunter", "Construction", "Dungeoneering", "Divination",
    "Invention", "Archaeology",
}
REQUIRED_SKILLS = BASE_SKILLS | {"Overall"}
ADJUSTED_HP_LEVEL = 10
ADJUSTED_HP_XP = 1154
MAX_HP_LEVEL = 15
MIN_COMBAT_XP = 100_000


class DataUnavailable(Exception):
    """A network or upstream problem that cannot be attributed to a player."""


class RateLimited(DataUnavailable):
    """Jagex asked this client to stop sending requests."""


class PlayerNameMismatch(DataUnavailable):
    """The API returned another account's name."""


@dataclass
class PlayerCheck:
    status: str
    row: dict | None = None
    error: DataUnavailable | None = None
    source: str | None = None


def username_key(name):
    return name.replace("_", " ").replace("\xa0", " ").strip().casefold()


def validate_player_name(payload, username):
    name = payload.get("name")
    if not isinstance(name, str) or not username_key(name):
        raise DataUnavailable("The API returned an invalid player name")
    if username_key(name).replace("-", " ") != username_key(username).replace("-", " "):
        raise PlayerNameMismatch(f"Requested {username!r}, received {name!r}")
    return name


def read_usernames(path=USERNAMES_PATH):
    """Read the headerless roster, preserving the first spelling for each name."""
    names = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.reader(file):
            if row and row[0].strip():
                name = row[0].replace("\xa0", " ").strip()
                names.setdefault(username_key(name), name)
    return sorted(names.values(), key=username_key)


def read_scores(path):
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = {}
        for row in reader:
            key = username_key(row["RSN"])
            if key not in rows or int(row["TIME"]) >= int(rows[key]["TIME"]):
                rows[key] = row
        return rows, reader.fieldnames


def write_csv(path, fieldnames, rows):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_usernames(path, usernames):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file, lineterminator="\n").writerows((name,) for name in usernames)
    temporary.replace(path)


def create_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request(session, url, **kwargs):
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as error:
        raise DataUnavailable(str(error)) from error
    if response.status_code == 429:
        raise RateLimited("Jagex returned HTTP 429")
    return response


def response_json(response):
    try:
        payload = response.json()
    except ValueError as error:
        raise DataUnavailable("invalid JSON from Jagex") from error
    if not isinstance(payload, dict):
        raise DataUnavailable("expected a JSON object from Jagex")
    return payload


def get_hiscores(session, username, url=HISCORES_URL):
    response = request(session, url, params={"player": username})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DataUnavailable(f"Index Lite returned HTTP {response.status_code}")
    payload = response_json(response)
    validate_player_name(payload, username)
    return payload


def get_player_data(session, username, cache=None):
    key = username_key(username)
    if cache is not None and key in cache:
        return cache[key]

    name = quote(username.replace("_", " "), safe="")
    response = request(session, f"{PLAYER_DATA_URL}/{name}", params={
        "includeSkills": "true", "includeQuests": "true",
    })
    if response.status_code in (403, 404):
        error = response_json(response).get("error")
        if error == "PLAYER_NOT_FOUND":
            if cache is not None:
                cache[key] = None
            return None
        if error == "PLAYER_NOT_MEMBER":
            raise DataUnavailable("Player Data is unavailable for this account's membership status")
    if response.status_code != 200:
        raise DataUnavailable(f"Player Data returned HTTP {response.status_code}")
    payload = response_json(response)
    canonical_name = validate_player_name(payload, username)
    payload = {field: payload[field] for field in ("name", "skills", "qp") if field in payload}
    if cache is not None:
        cache[key] = payload
        cache[username_key(canonical_name)] = payload
    return payload


def parse_quest_points(payload):
    points = payload.get("qp") if isinstance(payload, dict) else None
    if type(points) is not int or points < 0:
        raise DataUnavailable("Player Data returned an invalid quest point total")
    return points



def parse_player_data(payload):
    records = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(records, dict) or not {name.lower() for name in BASE_SKILLS}.issubset(records):
        raise DataUnavailable("Player Data omitted expected skill records")

    skills = {}
    for name in BASE_SKILLS:
        record = records[name.lower()]
        if not isinstance(record, dict):
            raise DataUnavailable("Player Data returned an invalid skill record")
        level, fine_xp = record.get("level"), record.get("fine_xp")
        if (type(level) is not int or not 1 <= level <= 126
                or type(fine_xp) is not int or not 0 <= fine_xp <= 2_000_000_000):
            raise DataUnavailable("Player Data returned invalid skill levels or XP")
        skills[name] = (level, fine_xp // 10)
    return sum(level for level, _ in skills.values()), sum(xp for _, xp in skills.values()), skills


def parse_skills(payload):
    """Read named skills and recover an older partial record when possible."""
    try:
        records = payload["skills"]
        skills = {entry["name"]: (entry["level"], entry["xp"]) for entry in records}
    except (KeyError, TypeError) as error:
        raise DataUnavailable("invalid Index Lite skills") from error
    if len(skills) != len(records) or not REQUIRED_SKILLS.issubset(skills):
        raise DataUnavailable("Index Lite omitted expected skill records")
    if any(
        not isinstance(name, str) or type(level) is not int or level == 0 or level < -1
        or type(xp) is not int or xp < -1
        for name, (level, xp) in skills.items()
    ):
        raise DataUnavailable("Index Lite returned invalid skill levels or XP")

    total_level, total_xp = skills.pop("Overall")
    if total_level == -1 or total_xp == -1:
        return None

    missing_xp = [name for name, (_, xp) in skills.items() if xp == -1]
    if len(missing_xp) == 1:
        name = missing_xp[0]
        level, _ = skills[name]
        recovered_xp = total_xp - sum(xp for _, xp in skills.values() if xp >= 0)
        if level == 1:
            # Older records can also use level 1 as the placeholder for this skill.
            level = total_level - sum(value for other, (value, _) in skills.items()
                                      if other != name)
        skills[name] = (level, recovered_xp)

    missing_levels = [name for name, (level, _) in skills.items() if level == -1]
    if len(missing_levels) == 1:
        name = missing_levels[0]
        recovered_level = total_level - sum(level for level, _ in skills.values()
                                            if level >= 1)
        skills[name] = (recovered_level, skills[name][1])

    if any(not 1 <= level <= 126 or not 0 <= xp <= 200_000_000
           for level, xp in skills.values()):
        return None
    return total_level, total_xp, skills


def rune_score(payload):
    activities = payload.get("activities")
    if not isinstance(activities, list):
        return ""
    for activity in activities:
        if isinstance(activity, dict) and activity.get("name") == "RuneScore":
            score = activity.get("score")
            return score if type(score) is int and score >= 0 else ""
    return ""


def combat_level(attack, strength, magic, ranged, necromancy, defence, hitpoints,
                 prayer, summoning):
    offensive = max(attack + strength, 2 * magic, 2 * ranged, 2 * necromancy)
    defensive = defence + hitpoints + prayer // 2 + summoning // 2
    return (13 * offensive + 10 * defensive) // 40


VIRTUAL_XP = (
    0, 83, 174, 276, 388, 512, 650, 801, 969, 1154, 1358,
    1584, 1833, 2107, 2411, 2746, 3115, 3523, 3973, 4470, 5018, 5624,
    6291, 7028, 7842, 8740, 9730, 10824, 12031, 13363, 14833, 16456, 18247,
    20224, 22406, 24815, 27473, 30408, 33648, 37224, 41171, 45529, 50339, 55649,
    61512, 67983, 75127, 83014, 91721, 101333, 111945, 123660, 136594, 150872, 166636,
    184040, 203254, 224466, 247886, 273742, 302288, 333804, 368599, 407015, 449428, 496254,
    547953, 605032, 668051, 737627, 814445, 899257, 992895, 1096278, 1210421, 1336443, 1475581,
    1629200, 1798808, 1986068, 2192818, 2421087, 2673114, 2951373, 3258594, 3597792, 3972294, 4385776,
    4842295, 5346332, 5902831, 6517253, 7195629, 7944614, 8771558, 9684577, 10692629, 11805606, 13034431,
    14391160, 15889109, 17542976, 19368992, 21385073, 23611006, 26068632, 28782069, 31777943, 35085654, 38737661,
    42769801, 47221641, 52136869, 57563718, 63555443, 70170840, 77474828, 85539082, 94442737, 104273167,
)

INVENTION_VIRTUAL_XP = (
    0, 830, 1861, 2902, 3980, 5126, 6390, 7787, 9400, 11275, 13605,
    16372, 19656, 23546, 28138, 33520, 39809, 47109, 55535, 64802, 77190, 90811,
    106221, 123573, 143025, 164742, 188893, 215651, 245196, 277713, 316311, 358547, 404634,
    454796, 509259, 568254, 632019, 700797, 774834, 854383, 946227, 1044569, 1149696, 1261903,
    1381488, 1508756, 1644015, 1787581, 1939773, 2100917, 2283490, 2476369, 2679907, 2894505, 3120508,
    3358307, 3608290, 3870846, 4146374, 4435275, 4758122, 5096111, 5449685, 5819299, 6205407, 6608473,
    7028964, 7467354, 7924122, 8399751, 8925664, 9472665, 10041285, 10632061, 11245538, 11882262, 12542789,
    13227679, 13937496, 14672812, 15478994, 16313404, 17176661, 18069395, 18992239, 19945833, 20930821, 21947856,
    22997593, 24080695, 25259906, 26475754, 27728955, 29020233, 30350318, 31719944, 33129852, 34580790, 36073511,
    37608773, 39270442, 40978509, 42733789, 44537107, 46389292, 48291180, 50243611, 52247435, 54303504, 56412678,
    58575823, 60793812, 63067521, 65397835, 67785643, 70231841, 72737330, 75303019, 77929820, 80618654, 83370445,
    86186124, 89066630, 92012904, 95025896, 98106559, 101255855, 104474750, 107764216, 111125230, 114558777, 118065845,
    121647430, 125304532, 129038159, 132849323, 136739041, 140708338, 144758242, 148889790, 153104021, 157401983, 161784728,
    166253312, 170808801, 175452262, 180184770, 185007406, 189921255, 194927409,
)


def check_player(session, username, player_data_cache=None, refresh_player_data=False):
    """Resolve the account once, then check its selected skill record."""
    payload = None
    hiscores = None
    errors = []
    try:
        payload = get_hiscores(session, username)
        if payload is not None:
            hiscores = parse_skills(payload)
    except RateLimited as error:
        return PlayerCheck("unavailable", error=error)
    except DataUnavailable as error:
        errors.append(error)

    player_data = None
    data_skills = None
    points_available = False
    points = None
    if refresh_player_data or hiscores is None:
        try:
            player_data = get_player_data(session, username, player_data_cache)
            if player_data is None:
                points_available = refresh_player_data
            else:
                try:
                    data_skills = parse_player_data(player_data)
                except DataUnavailable as error:
                    errors.append(error)
                try:
                    points = parse_quest_points(player_data)
                    points_available = True
                except DataUnavailable as error:
                    errors.append(error)
        except DataUnavailable as error:
            errors.append(error)

    error = next((error for error in errors if isinstance(error, RateLimited)),
                 errors[-1] if errors else None)
    if hiscores is None and data_skills is None:
        failures = [error for error in errors if not isinstance(error, PlayerNameMismatch)]
        if failures:
            return PlayerCheck("unavailable", error=error)
        return PlayerCheck("incomplete" if payload is not None else "not_found", error=error)

    parsed = hiscores
    source = "hiscores"
    canonical_name = username
    if data_skills is not None:
        canonical_name = player_data["name"]
        if (hiscores is None or username_key(canonical_name) != username_key(username)
                or data_skills[1] > hiscores[1]):
            parsed = data_skills
            source = "player_data"

    result = score_player(canonical_name, parsed, rune_score(payload) if payload is not None else "")
    result.source = source
    result.error = error
    if result.row is not None and points_available:
        result.row["QPOINTS"] = points
    return result


def score_player(username, parsed, rscore=""):
    if parsed is None:
        return PlayerCheck("incomplete")
    total_level, total_xp, skills = parsed
    hp_level, hp_xp = skills["Hitpoints"]
    if hp_level > MAX_HP_LEVEL:
        return PlayerCheck("constitution")

    adjusted_combat_xp = ADJUSTED_HP_XP + sum(
        skills[name][1] for name in COMBAT_SKILLS if name != "Hitpoints"
    )
    if adjusted_combat_xp < MIN_COMBAT_XP:
        return PlayerCheck("combat_xp")

    virtual_levels = {
        name: bisect_right(INVENTION_VIRTUAL_XP if name == "Invention" else VIRTUAL_XP, xp)
        for name, (_, xp) in skills.items()
    }
    virtual_level = sum(virtual_levels.values())
    combat_levels = [skills[name][0] for name in COMBAT_SKILLS]
    combat = combat_level(*combat_levels)
    combat_levels[COMBAT_SKILLS.index("Hitpoints")] = ADJUSTED_HP_LEVEL

    return PlayerCheck("eligible", {
        "RSN": username,
        "HPLVL": hp_level,
        "HPEXP": hp_xp,
        "TOTLVL": total_level,
        "TOTLVLADJ": total_level - hp_level + ADJUSTED_HP_LEVEL,
        "VIRLVL": virtual_level,
        "VIRLVLADJ": virtual_level - virtual_levels["Hitpoints"] + ADJUSTED_HP_LEVEL,
        "TOTEXP": total_xp,
        "TOTEXPADJ": total_xp - hp_xp + ADJUSTED_HP_XP,
        "CMBLVL": combat,
        "CMBLVLADJ": combat_level(*combat_levels),
        "CMBEXPADJ": adjusted_combat_xp,
        "RSCORE": rscore,
        "TIME": int(time.time()),
    })

