import contextlib
import copy
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import updateHiscores as hiscores


def player_data(name="Player"):
    skills = {name.lower(): {"level": 1, "fine_xp": 0} for name in hiscores.BASE_SKILLS}
    skills["hitpoints"] = {"level": 10, "fine_xp": 12042}
    skills["magic"] = {"level": 50, "fine_xp": 1013333}
    return {"name": name, "skills": skills, "qp": 42, "quests": {}}


def response(status, payload):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class PlayerDataTests(unittest.TestCase):
    def run_refresh(self, usernames, old_scores, new_scores, daily_payload=None):
        with tempfile.TemporaryDirectory() as temporary:
            scores_path = Path(temporary) / "hiscores.csv"
            usernames_path = Path(temporary) / "usernames.csv"
            usernames_path.write_text("".join(name + "\n" for name in usernames))
            args = ["updateHiscores.py"] + (["--refresh-quest-points"] if daily_payload else [])
            with patch.object(hiscores, "read_usernames", return_value=usernames), \
                 patch.object(hiscores, "read_scores", return_value=(old_scores, hiscores.FIELDS)), \
                 patch.object(hiscores, "create_session", return_value=contextlib.nullcontext(object())), \
                 patch.object(hiscores, "refresh_scores", return_value=new_scores), \
                 patch.object(hiscores, "get_player_data", return_value=daily_payload), \
                 patch.object(hiscores, "SCORES_PATH", scores_path), \
                 patch.object(hiscores, "USERNAMES_PATH", usernames_path), \
                 patch.object(sys, "argv", args), \
                 contextlib.redirect_stdout(io.StringIO()):
                hiscores.main()
            with scores_path.open(newline="") as file:
                return usernames_path.read_text().splitlines(), list(csv.DictReader(file))

    def test_request_combines_skills_and_quests_and_encodes_name(self):
        session = object()
        with patch.object(hiscores, "request", return_value=response(200, player_data("Two Names"))) as request:
            payload = hiscores.get_player_data(session, "Two_Names")
        self.assertEqual(payload["qp"], 42)
        args, kwargs = request.call_args
        self.assertIs(args[0], session)
        self.assertTrue(args[1].endswith("/playerData/Two%20Names"))
        self.assertEqual(kwargs["params"], {"includeSkills": "true", "includeQuests": "true"})

    def test_cache_reuses_data_across_normalized_name_variants(self):
        cache = {}
        with patch.object(hiscores, "request", return_value=response(200, player_data("Two Names"))) as request:
            hiscores.get_player_data(object(), "Two_Names", cache)
            self.assertEqual(hiscores.get_quest_points(object(), "two names", cache), 42)
        self.assertEqual(request.call_count, 1)

    def test_fractional_xp_is_truncated_per_skill_before_summing(self):
        payload = player_data()
        payload["skills"]["hitpoints"]["fine_xp"] = 12049
        payload["skills"]["magic"]["fine_xp"] = 1013339
        total_level, total_xp, skills = hiscores.parse_player_data(payload)
        self.assertEqual(total_level, 87)
        self.assertEqual(total_xp, 102537)
        self.assertEqual(skills["Hitpoints"], (10, 1204))
        self.assertEqual(skills["Magic"], (50, 101333))

    def test_unranked_player_is_checked_using_player_data(self):
        with patch.object(hiscores, "get_hiscores", return_value=None), \
             patch.object(hiscores, "get_player_data", return_value=player_data()):
            result = hiscores.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["HPLVL"], 10)
        self.assertEqual(result.row["HPEXP"], 1204)
        self.assertEqual(result.row["TOTEXP"], 102537)
        self.assertEqual(result.row["RSCORE"], "")

    def test_legacy_partial_hiscores_use_player_data(self):
        legacy = {"skills": [
            {"name": "Overall", "level": 87, "xp": 102537},
            *({"name": name, "level": 1, "xp": -1} for name in hiscores.BASE_SKILLS),
        ]}
        with patch.object(hiscores, "get_hiscores", return_value=legacy), \
             patch.object(hiscores, "get_player_data", return_value=player_data()):
            self.assertEqual(hiscores.check_player(object(), "Player").status, "eligible")

    def test_missing_hiscores_fields_use_canonical_player_data(self):
        with patch.object(hiscores, "get_hiscores", return_value={"skills": []}), \
             patch.object(hiscores, "get_player_data", return_value=player_data("Player-Name")):
            result = hiscores.check_player(object(), "player name")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["RSN"], "Player-Name")

    def test_missing_fields_and_unavailable_backup_remain_unavailable(self):
        with patch.object(hiscores, "get_hiscores", return_value={"skills": []}), \
             patch.object(hiscores, "get_player_data", return_value=None):
            result = hiscores.check_player(object(), "Player")
        self.assertEqual(result.status, "unavailable")
        self.assertIsInstance(result.error, hiscores.DataUnavailable)

    def test_hiscores_service_errors_preserve_failure_handling(self):
        with patch.object(hiscores, "get_hiscores", side_effect=hiscores.DataUnavailable("500")), \
             patch.object(hiscores, "get_player_data") as backup:
            self.assertEqual(hiscores.check_player(object(), "Player").status, "unavailable")
        backup.assert_not_called()

    def test_score_and_quest_phases_share_one_combined_request(self):
        cache = {}
        with patch.object(hiscores, "request", side_effect=[
            response(404, {}), response(200, player_data()),
        ]) as request, contextlib.redirect_stdout(io.StringIO()):
            scores = hiscores.refresh_scores(object(), ["Player"], cache)
            rows = list(scores.values())
            rows[0]["RSCORE"] = "73"
            hiscores.refresh_quest_points(object(), rows, cache)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(rows[0]["QPOINTS"], 42)
        self.assertEqual(rows[0]["TOTEXP"], 102537)
        self.assertEqual(rows[0]["RSCORE"], "73")

    def test_daily_quest_refresh_uses_the_returned_skills(self):
        row = {"RSN": "Player", "QPOINTS": "0", "TOTEXP": "1", "RSCORE": "73", "TIME": "1"}
        with patch.object(hiscores, "get_player_data", return_value=player_data()), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.refresh_quest_points(object(), [row])
        self.assertEqual(row["TOTEXP"], 102537)
        self.assertEqual(row["HPEXP"], 1204)
        self.assertEqual(row["QPOINTS"], 42)
        self.assertEqual(row["RSCORE"], "73")
        self.assertGreater(int(row["TIME"]), 1)

    def test_daily_refresh_does_not_publish_ineligible_new_stats(self):
        payload = player_data()
        payload["skills"]["hitpoints"] = {"level": 99, "fine_xp": 130344310}
        row = {"RSN": "Player", "QPOINTS": "0", "HPLVL": "10", "TOTEXP": "1", "TIME": "1"}
        with patch.object(hiscores, "get_player_data", return_value=payload), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.refresh_quest_points(object(), [row])
        self.assertEqual(row["HPLVL"], "10")
        self.assertEqual(row["TOTEXP"], "1")
        self.assertEqual(row["TIME"], "1")
        self.assertEqual(row["QPOINTS"], 42)

    def test_cached_player_data_does_not_replace_newer_hiscores(self):
        row = {"RSN": "Player", "QPOINTS": "0", "TOTEXP": "200000", "RSCORE": "73", "TIME": "123"}
        with patch.object(hiscores, "get_player_data", return_value=player_data()), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.refresh_quest_points(object(), [row])
        self.assertEqual(row["TOTEXP"], "200000")
        self.assertEqual(row["TIME"], "123")
        self.assertEqual(row["RSCORE"], "73")
        self.assertEqual(row["QPOINTS"], 42)

    def test_canonical_name_is_corrected_even_when_stats_are_newer(self):
        row = {"RSN": "player name", "QPOINTS": "0", "TOTEXP": "200000", "TIME": "123"}
        with patch.object(hiscores, "get_player_data", return_value=player_data("Player-Name")), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.refresh_quest_points(object(), [row])
        self.assertEqual(row["RSN"], "Player-Name")
        self.assertEqual(row["TOTEXP"], "200000")
        self.assertEqual(row["TIME"], "123")

    def test_ineligible_player_data_does_not_correct_the_name(self):
        payload = player_data("Player-Name")
        payload["skills"]["hitpoints"] = {"level": 99, "fine_xp": 130344310}
        row = {"RSN": "player name", "TOTEXP": "1", "TIME": "123"}
        with patch.object(hiscores, "get_player_data", return_value=payload), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.refresh_quest_points(object(), [row])
        self.assertEqual(row["RSN"], "player name")

    def test_bad_player_data_is_unavailable_instead_of_ineligible(self):
        missing = player_data()
        del missing["skills"]["hitpoints"]
        malformed = [missing]
        for field, value in (("fine_xp", -1), ("fine_xp", True), ("fine_xp", 2_000_000_010), ("level", 0), ("level", True)):
            payload = player_data()
            payload["skills"]["hitpoints"][field] = value
            malformed.append(payload)
        for payload in malformed:
            with self.subTest(payload=payload), \
                 patch.object(hiscores, "get_hiscores", return_value=None), \
                 patch.object(hiscores, "get_player_data", return_value=payload):
                result = hiscores.check_player(object(), "Player")
                self.assertEqual(result.status, "unavailable")
                self.assertIsInstance(result.error, hiscores.DataUnavailable)

    def test_canonical_name_correction_is_accepted_and_cached(self):
        cache = {}
        with patch.object(hiscores, "request", return_value=response(200, player_data("Player-Name"))) as request:
            payload = hiscores.get_player_data(object(), "player name", cache)
            self.assertEqual(payload["name"], "Player-Name")
            self.assertIs(hiscores.get_player_data(object(), "Player-Name", cache), payload)
        self.assertEqual(request.call_count, 1)

    def test_invalid_canonical_names_remain_unavailable(self):
        for name in (None, "", "   ", 123):
            payload = player_data()
            payload["name"] = name
            with self.subTest(name=name), patch.object(hiscores, "request", return_value=response(200, payload)):
                with self.assertRaises(hiscores.DataUnavailable):
                    hiscores.get_player_data(object(), "Player")

    def test_known_no_data_responses_are_cached(self):
        for status, error in ((404, "PLAYER_NOT_FOUND"), (403, "PLAYER_NOT_MEMBER")):
            cache = {}
            with self.subTest(error=error), \
                 patch.object(hiscores, "request", return_value=response(status, {"error": error})) as request:
                self.assertIsNone(hiscores.get_player_data(object(), "Player", cache))
                self.assertIsNone(hiscores.get_player_data(object(), "player", cache))
                self.assertEqual(request.call_count, 1)

    def test_player_data_service_errors_do_not_retire_players(self):
        for status in (429, 500):
            session = Mock()
            session.get.return_value = response(status, {"error": "INTERNAL_ERROR"})
            with self.subTest(status=status), patch.object(hiscores, "get_hiscores", return_value=None):
                result = hiscores.check_player(session, "Player")
                self.assertEqual(result.status, "unavailable")
                self.assertIsInstance(result.error, hiscores.RateLimited if status == 429 else hiscores.DataUnavailable)

    def test_main_preserves_cached_runescore_for_player_data_rows(self):
        old = {"RSN": "Player", "TIME": "1", "RSCORE": "73", "QPOINTS": "42"}
        fresh = copy.deepcopy(old)
        fresh.update({"TIME": 2, "RSCORE": ""})
        with patch.object(hiscores, "read_usernames", return_value=["Player"]), \
             patch.object(hiscores, "read_scores", return_value=({"player": old}, hiscores.FIELDS)), \
             patch.object(hiscores, "create_session", return_value=contextlib.nullcontext(object())), \
             patch.object(hiscores, "refresh_scores", return_value={"player": fresh}), \
             patch.object(hiscores, "write_csv") as write, \
             patch.object(sys, "argv", ["updateHiscores.py"]), \
             contextlib.redirect_stdout(io.StringIO()):
            hiscores.main()
        self.assertEqual(write.call_args.args[2][0]["RSCORE"], "73")

    def test_main_persists_canonical_names_and_merges_duplicate_aliases(self):
        first = hiscores.score_player("Player-Name", hiscores.parse_player_data(player_data())).row
        first.update({"TIME": 20, "QPOINTS": "", "RSCORE": ""})
        second = dict(first, TIME=30, TOTEXP=first["TOTEXP"] + 1)
        old = dict(first, RSN="player name", TIME="1", QPOINTS="42", RSCORE="73")
        usernames, rows = self.run_refresh(
            ["player name", "Player-Name", "Unscored"],
            {"player name": old},
            {"player name": first, "player-name": second},
        )
        self.assertEqual(usernames, ["Player-Name", "Unscored"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["RSN"], "Player-Name")
        self.assertEqual(rows[0]["TIME"], "30")
        self.assertEqual(rows[0]["TOTEXP"], str(second["TOTEXP"]))
        self.assertEqual(rows[0]["QPOINTS"], "42")
        self.assertEqual(rows[0]["RSCORE"], "73")

    def test_daily_correction_is_saved_without_replacing_newer_stats(self):
        fresh = hiscores.score_player("player name", hiscores.parse_player_data(player_data())).row
        fresh.update({"TIME": 123, "TOTEXP": 200000})
        usernames, rows = self.run_refresh(
            ["player name"], {}, {"player name": fresh}, player_data("Player-Name"),
        )
        self.assertEqual(usernames, ["Player-Name"])
        self.assertEqual(rows[0]["RSN"], "Player-Name")
        self.assertEqual(rows[0]["TOTEXP"], "200000")
        self.assertEqual(rows[0]["TIME"], "123")
        self.assertEqual(rows[0]["QPOINTS"], "42")

    def test_correction_reuses_scores_already_saved_under_canonical_name(self):
        fresh = hiscores.score_player("Player-Name", hiscores.parse_player_data(player_data())).row
        fresh.update({"TIME": 20, "RSCORE": ""})
        old = dict(fresh, TIME="1", QPOINTS="42", RSCORE="73")
        usernames, rows = self.run_refresh(
            ["player name"], {"player-name": old}, {"player name": fresh},
        )
        self.assertEqual(usernames, ["Player-Name"])
        self.assertEqual(rows[0]["QPOINTS"], "42")
        self.assertEqual(rows[0]["RSCORE"], "73")


if __name__ == "__main__":
    unittest.main()
