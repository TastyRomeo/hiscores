import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import players
import updateHiscores as refresh
from players import PlayerCheck


def player_data(name="Player"):
    skills = {name.lower(): {"level": 1, "fine_xp": 0} for name in players.BASE_SKILLS}
    skills["hitpoints"] = {"level": 10, "fine_xp": 11540}
    skills["magic"] = {"level": 50, "fine_xp": 1013330}
    return {"name": name, "skills": skills, "qp": 42, "quests": {"unused": {}}}


def hiscores_data(name="Player"):
    data = player_data(name)
    parsed = players.parse_player_data(data)
    skills = [{"name": key, "level": level, "xp": xp} for key, (level, xp) in parsed[2].items()]
    return {"name": name, "skills": [{"name": "Overall", "level": parsed[0], "xp": parsed[1]}, *skills],
            "activities": [{"name": "RuneScore", "score": 73}]}


def score(name="Player", timestamp=1000, **fields):
    row = players.score_player(name, players.parse_player_data(player_data(name))).row
    row.update({"TIME": timestamp, "QPOINTS": "", **fields})
    return row


class PublicationTests(unittest.TestCase):
    def publish(self, status, age, **fields):
        now = 30 * refresh.DAY
        old = score(timestamp=now - age, **fields)
        with contextlib.redirect_stdout(io.StringIO()):
            roster, rows = refresh.publish_scores(["Player"], {"player": PlayerCheck(status)}, {"player": old}, 14, now)
        return old, roster, rows

    def test_missing_data_keeps_recent_cache_without_refreshing_timestamp(self):
        for status in ("not_found", "incomplete", "unavailable"):
            with self.subTest(status=status):
                old, roster, rows = self.publish(status, 13 * refresh.DAY)
                self.assertEqual(roster, ["Player"])
                self.assertEqual(rows, [old])

    def test_missing_data_expires_at_grace_boundary_but_stays_tracked(self):
        for status in ("not_found", "incomplete", "unavailable"):
            with self.subTest(status=status):
                _, roster, rows = self.publish(status, 14 * refresh.DAY)
                self.assertEqual(roster, ["Player"])
                self.assertEqual(rows, [])

    def test_confirmed_ineligible_is_hidden_immediately_and_stays_tracked(self):
        for status in ("constitution", "combat_xp"):
            with self.subTest(status=status):
                _, roster, rows = self.publish(status, 1)
                self.assertEqual(roster, ["Player"])
                self.assertEqual(rows, [])

    def test_unchecked_players_use_the_same_grace_period(self):
        old = score(timestamp=10)
        roster, rows = refresh.publish_scores(["Player"], {}, {"player": old}, 14, 11)
        self.assertEqual(roster, ["Player"])
        self.assertEqual(rows, [old])

    def test_canonical_aliases_merge_without_losing_optional_values(self):
        first = score("Player-Name", timestamp=100)
        second = score("Player-Name", timestamp=101, TOTEXP=first["TOTEXP"] + 1)
        old = score("player name", timestamp=1, QPOINTS="42", RSCORE="73")
        checks = {"player name": PlayerCheck("eligible", first), "player-name": PlayerCheck("eligible", second)}
        with contextlib.redirect_stdout(io.StringIO()):
            roster, rows = refresh.publish_scores(["player name", "Player-Name", "Unscored"], checks, {"player name": old}, 14, 102)
        self.assertEqual(roster, ["Player-Name", "Unscored"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["TIME"], 101)
        self.assertEqual(rows[0]["QPOINTS"], "42")
        self.assertEqual(rows[0]["RSCORE"], "73")

    def test_canonical_cache_key_is_used_when_input_cache_key_is_missing(self):
        fresh = score("Player-Name", timestamp=100)
        old = score("Player-Name", timestamp=1, QPOINTS="42", RSCORE="73")
        with contextlib.redirect_stdout(io.StringIO()):
            roster, rows = refresh.publish_scores(["player name"], {"player name": PlayerCheck("eligible", fresh)}, {"player-name": old}, 14, 101)
        self.assertEqual(roster, ["Player-Name"])
        self.assertEqual(rows[0]["QPOINTS"], "42")
        self.assertEqual(rows[0]["RSCORE"], "73")

    def test_confirmed_unavailable_quest_points_are_not_restored_from_cache(self):
        fresh = score(timestamp=100, QPOINTS=None)
        old = score(timestamp=1, QPOINTS="42")
        _, rows = refresh.publish_scores(["Player"], {"player": PlayerCheck("eligible", fresh)}, {"player": old}, 14, 101)
        self.assertIsNone(rows[0]["QPOINTS"])


class RefreshTests(unittest.TestCase):
    def test_daily_refresh_fetches_each_endpoint_once_and_builds_one_row(self):
        responses = []
        for payload in (hiscores_data(), player_data()):
            response = Mock(status_code=200)
            response.json.return_value = payload
            responses.append(response)
        with patch.object(players, "request", side_effect=responses) as request, \
             patch.object(players, "score_player", wraps=players.score_player) as build, \
             contextlib.redirect_stdout(io.StringIO()):
            checks = refresh.refresh_players(object(), ["Player"], refresh_player_data=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(checks["player"].row["QPOINTS"], 42)
        self.assertEqual(checks["player"].row["RSCORE"], 73)

    def test_canonical_aliases_are_not_fetched_twice(self):
        result = PlayerCheck("eligible", score("Player-Name"))
        with patch.object(refresh, "check_player", return_value=result) as check, contextlib.redirect_stdout(io.StringIO()):
            checks = refresh.refresh_players(object(), ["player name", "Player-Name"], True)
        self.assertEqual(check.call_count, 1)
        self.assertIs(checks["player name"], checks["player-name"])

    def test_optional_rate_limit_stops_requests_without_discarding_valid_stats(self):
        result = PlayerCheck("eligible", score(), error=players.RateLimited("429"), source="hiscores")
        with patch.object(refresh, "check_player", return_value=result) as check, contextlib.redirect_stdout(io.StringIO()):
            checks = refresh.refresh_players(object(), ["Player", "Unchecked"], True)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(checks["player"].status, "eligible")
        self.assertNotIn("unchecked", checks)

    def run_main(self, usernames, checks, old=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names_path, scores_path = root / "usernames.csv", root / "hiscores.csv"
            players.write_usernames(names_path, usernames)
            players.write_csv(scores_path, players.FIELDS, old or [])
            with patch.object(refresh, "USERNAMES_PATH", names_path), \
                 patch.object(refresh, "SCORES_PATH", scores_path), \
                 patch.object(refresh, "create_session", return_value=contextlib.nullcontext(object())), \
                 patch.object(refresh, "refresh_players", return_value=checks), \
                 patch.object(sys, "argv", ["updateHiscores.py"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                refresh.main()
            with scores_path.open(newline="") as file:
                return names_path.read_text().splitlines(), list(csv.DictReader(file))

    def test_empty_roster_clears_published_scores(self):
        roster, rows = self.run_main([], {}, [score()])
        self.assertEqual(roster, [])
        self.assertEqual(rows, [])

    def test_all_ineligible_accounts_clear_scores_without_dropping_tracking(self):
        roster, rows = self.run_main(["Player"], {"player": PlayerCheck("constitution")}, [score()])
        self.assertEqual(roster, ["Player"])
        self.assertEqual(rows, [])

    def test_main_persists_canonical_names_to_roster_and_scores(self):
        roster, rows = self.run_main(["player name"], {"player name": PlayerCheck("eligible", score("Player-Name"))})
        self.assertEqual(roster, ["Player-Name"])
        self.assertEqual(rows[0]["RSN"], "Player-Name")


if __name__ == "__main__":
    unittest.main()
