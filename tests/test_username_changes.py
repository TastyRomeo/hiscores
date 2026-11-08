import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import players
import updateHiscores as refresh
from players import PlayerCheck


def score(name, **values):
    row = dict.fromkeys(players.FIELDS, "")
    row.update(RSN=name, HPLVL=10, HPEXP=1154, TOTEXP=100_000, TIME=900)
    row.update(values)
    return row


class UsernameChangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.names_path = self.root / "usernames.csv"
        self.scores_path = self.root / "hiscores.csv"
        self.inbox_path = self.root / "username_inbox.csv"
        self.badges_path = self.root / "ironman-badges.csv"
        self.state_path = self.root / "username-maintenance.json"
        players.write_usernames(self.names_path, ["Old"])
        players.write_csv(self.scores_path, players.FIELDS, [])
        players.write_csv(self.badges_path, ["RSN", "BADGE"], [])
        self.state_path.write_text('{"missing": {}}\n', encoding="utf-8")
        self.inbox_path.write_text("Old,New\n", encoding="utf-8")
        stack = self.enterContext(contextlib.ExitStack())
        stack.enter_context(patch.object(refresh, "USERNAMES_PATH", self.names_path))
        stack.enter_context(patch.object(refresh, "SCORES_PATH", self.scores_path))
        stack.enter_context(patch.object(refresh, "create_session", return_value=contextlib.nullcontext(object())))
        stack.enter_context(patch.object(refresh.time, "time", return_value=1000))
        stack.enter_context(patch.object(sys, "argv", ["updateHiscores.py", "--inactive-days", "14"]))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def run_update(self, result=None, side_effect=None):
        if side_effect is None:
            side_effect = lambda session, name, *args: result or PlayerCheck("eligible", score(name))
        with patch.object(refresh, "check_player", side_effect=side_effect) as check:
            refresh.main()
        return [call.args[1] for call in check.call_args_list]

    def rows(self):
        return players.read_scores(self.scores_path)[0]

    def assert_empty_inbox(self):
        self.assertFalse(self.inbox_path.read_text(encoding="utf-8").strip())

    def test_mixed_additions_and_changes_replace_old_names_before_requests(self):
        players.write_usernames(self.names_path, ["Old", "Old Friend"])
        self.inbox_path.write_text(",Added\n,Old\nOld,New\n", encoding="utf-8")

        self.assertEqual(self.run_update(), ["Added", "New", "Old Friend"])
        self.assertEqual(players.read_usernames(self.names_path), ["Added", "New", "Old Friend"])
        self.assertEqual(set(self.rows()), {"added", "new", "old friend"})
        self.assert_empty_inbox()

    def test_normalized_source_matching_and_explicit_target_spelling(self):
        players.write_usernames(self.names_path, ["Old_Name", "new_name"])
        self.inbox_path.write_text("\ufeff OLD\xa0NAME , NEW NAME \n", encoding="utf-8")

        self.assertEqual(self.run_update(), ["NEW NAME"])
        self.assertEqual(players.read_usernames(self.names_path), ["NEW NAME"])
        self.assertEqual(self.rows()["new name"]["RSN"], "NEW NAME")

    def test_case_only_and_exact_self_changes_are_valid(self):
        for entry, expected in (("old,OLD\n", "OLD"), ("Old,Old\n", "Old")):
            with self.subTest(entry=entry):
                players.write_usernames(self.names_path, ["Old"])
                self.inbox_path.write_text(entry, encoding="utf-8")
                self.assertEqual(self.run_update(), [expected])
                self.assertEqual(players.read_usernames(self.names_path), [expected])
                self.assert_empty_inbox()

    def test_chains_reach_final_target_regardless_of_row_order(self):
        for entries in ("Old,Middle\nMiddle,Final\n", "Middle,Final\nOld,Middle\n"):
            with self.subTest(entries=entries):
                players.write_usernames(self.names_path, ["Old"])
                players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42)])
                self.inbox_path.write_text(entries, encoding="utf-8")
                self.assertEqual(self.run_update(), ["Final"])
                self.assertEqual(set(self.rows()), {"final"})
                self.assertEqual(self.rows()["final"]["QPOINTS"], "42")

    def test_unknown_source_tracks_target_and_retries_are_idempotent(self):
        players.write_usernames(self.names_path, [])
        for _ in range(2):
            self.inbox_path.write_text("Old,New\n", encoding="utf-8")
            self.assertEqual(self.run_update(), ["New"])
            self.assertEqual(players.read_usernames(self.names_path), ["New"])
            self.assert_empty_inbox()

    def test_cached_scores_follow_rename_during_missing_data_grace_period(self):
        old = score("Old", QPOINTS=42, RSCORE=73)
        players.write_csv(self.scores_path, players.FIELDS, [old])

        self.assertEqual(self.run_update(PlayerCheck("unavailable")), ["New"])
        expected = {field: str(value) for field, value in old.items()}
        expected["RSN"] = "New"
        self.assertEqual(self.rows(), {"new": expected})

    def test_fresh_scores_preserve_renamed_cached_optional_values(self):
        players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42, RSCORE=73)])

        self.run_update()

        self.assertEqual(self.rows()["new"]["QPOINTS"], "42")
        self.assertEqual(self.rows()["new"]["RSCORE"], "73")
        self.assertNotIn("old", self.rows())

    def test_duplicate_target_keeps_latest_cache_and_fills_optional_values(self):
        for old_time, old_xp, new_time, new_xp in ((901, 100, 900, 200), (900, 200, 900, 100)):
            with self.subTest(old_time=old_time, old_xp=old_xp):
                players.write_usernames(self.names_path, ["Old", "New"])
                players.write_csv(self.scores_path, players.FIELDS, [
                    score("Old", TIME=old_time, TOTEXP=old_xp),
                    score("New", TIME=new_time, TOTEXP=new_xp, QPOINTS=42, RSCORE=73),
                ])
                self.inbox_path.write_text("Old,New\n", encoding="utf-8")
                self.assertEqual(self.run_update(PlayerCheck("not_found")), ["New"])
                row = self.rows()["new"]
                self.assertEqual((row["TIME"], row["TOTEXP"]), (str(old_time), str(old_xp)))
                self.assertEqual((row["QPOINTS"], row["RSCORE"]), ("42", "73"))

    def test_multiple_sources_merge_to_one_explicit_target(self):
        players.write_usernames(self.names_path, ["Old", "Other"])
        self.inbox_path.write_text("Old,New\nOther,New\n", encoding="utf-8")

        self.assertEqual(self.run_update(), ["New"])
        self.assertEqual(players.read_usernames(self.names_path), ["New"])

    def test_badges_move_and_missing_observations_reset(self):
        archive_path = self.root / "removed-usernames.csv"
        archive_row = {"RSN": "Old", "REMOVED_AT": "2026-11-01", "REASON": "not_found", "LAST_VALID_AT": ""}
        players.write_csv(archive_path, archive_row.keys(), [archive_row])
        players.write_csv(self.badges_path, ["RSN", "BADGE"], [
            {"RSN": "Old", "BADGE": "hardcore"},
            {"RSN": "New", "BADGE": "hardcore"},
            {"RSN": "Keep", "BADGE": "ironman"},
        ])
        observation = {"first": 1, "last": 900, "status": "not_found"}
        self.state_path.write_text(json.dumps({"missing": dict.fromkeys(("old", "new", "keep"), observation)}))

        self.run_update()

        with self.badges_path.open(newline="", encoding="utf-8") as file:
            badges = list(csv.DictReader(file))
        self.assertCountEqual(badges, [{"RSN": "New", "BADGE": "hardcore"}, {"RSN": "Keep", "BADGE": "ironman"}])
        self.assertEqual(json.loads(self.state_path.read_text())["missing"], {"keep": observation})
        with archive_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.DictReader(file)), [dict(archive_row, RSN="New")])

    def test_invalid_changes_fail_before_requests_or_output_writes(self):
        invalid = ("Old\n", "Old,___\n", ",___\n", "___,\n", '"Old,New\n', "Old,New,Extra\n",
                   "Old,New\nOld,Other\n", "Old,New\nNew,Old\n")
        for entry in invalid:
            with self.subTest(entry=entry):
                self.inbox_path.write_text(entry, encoding="utf-8")
                before = {path: path.read_bytes() for path in self.root.iterdir()}
                with patch.object(refresh, "check_player") as check, contextlib.redirect_stderr(io.StringIO()), \
                     self.assertRaises((ValueError, SystemExit)):
                    refresh.main()
                check.assert_not_called()
                self.assertEqual({path: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_remove_only_clears_related_records_and_preserves_other_players(self):
        players.write_usernames(self.names_path, ["Old_Name", "Old Name Friend", "Keep"])
        players.write_csv(self.scores_path, players.FIELDS, [
            score("Old_Name", QPOINTS=42, RSCORE=73), score("Old Name Friend"), score("Keep"),
        ])
        players.write_csv(self.badges_path, ["RSN", "BADGE"], [
            {"RSN": "Old_Name", "BADGE": "hardcore"}, {"RSN": "Keep", "BADGE": "ironman"},
        ])
        archive_path = self.root / "removed-usernames.csv"
        archived = {"RSN": "Old_Name", "REMOVED_AT": "2026-11-01", "REASON": "not_found", "LAST_VALID_AT": ""}
        players.write_csv(archive_path, archived.keys(), [archived, dict(archived, RSN="Keep")])
        observation = {"first": 1, "last": 900, "status": "not_found"}
        self.state_path.write_text(json.dumps({"missing": {"old name": observation, "keep": observation}}))
        self.inbox_path.write_text(" OLD\xa0NAME ,\n", encoding="utf-8")

        self.assertEqual(self.run_update(PlayerCheck("unavailable")), ["Keep", "Old Name Friend"])

        self.assertEqual(players.read_usernames(self.names_path), ["Keep", "Old Name Friend"])
        self.assertEqual(set(self.rows()), {"keep", "old name friend"})
        with self.badges_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.DictReader(file)), [{"RSN": "Keep", "BADGE": "ironman"}])
        with archive_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.DictReader(file)), [dict(archived, RSN="Keep")])
        self.assertEqual(json.loads(self.state_path.read_text())["missing"], {"keep": observation})
        self.assert_empty_inbox()

    def test_removing_last_player_clears_scores_without_lookups(self):
        players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42)])
        self.inbox_path.write_text("Old,\n", encoding="utf-8")

        self.assertEqual(self.run_update(), [])
        self.assertEqual(players.read_usernames(self.names_path), [])
        self.assertEqual(self.rows(), {})
        self.assert_empty_inbox()

    def test_separate_removal_and_addition_do_not_transfer_cached_data_or_badges(self):
        players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42, RSCORE=73)])
        players.write_csv(self.badges_path, ["RSN", "BADGE"], [{"RSN": "Old", "BADGE": "hardcore"}])
        self.inbox_path.write_text("Old,\n,New\n", encoding="utf-8")

        self.assertEqual(self.run_update(PlayerCheck("unavailable")), ["New"])

        self.assertEqual(players.read_usernames(self.names_path), ["New"])
        self.assertEqual(self.rows(), {})
        with self.badges_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.DictReader(file)), [])
        self.assert_empty_inbox()

    def test_separate_removal_and_addition_publish_fresh_optional_values(self):
        players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42, RSCORE=73)])
        self.inbox_path.write_text("Old,\n,New\n", encoding="utf-8")

        self.assertEqual(self.run_update(), ["New"])

        self.assertEqual(set(self.rows()), {"new"})
        self.assertEqual((self.rows()["new"]["QPOINTS"], self.rows()["new"]["RSCORE"]), ("", ""))

    def test_remove_and_readd_same_normalized_name_resets_cache_and_badges(self):
        for status in ("eligible", "unavailable"):
            with self.subTest(status=status):
                players.write_usernames(self.names_path, ["Old_Name"])
                players.write_csv(self.scores_path, players.FIELDS, [score("Old_Name", QPOINTS=42, RSCORE=73)])
                players.write_csv(self.badges_path, ["RSN", "BADGE"], [{"RSN": "Old_Name", "BADGE": "hardcore"}])
                self.inbox_path.write_text("old name,\n,OLD_NAME\n", encoding="utf-8")

                result = PlayerCheck(status, score("OLD_NAME") if status == "eligible" else None)
                self.assertEqual(self.run_update(result), ["OLD_NAME"])

                self.assertEqual(players.read_usernames(self.names_path), ["OLD_NAME"])
                if status == "eligible":
                    self.assertEqual((self.rows()["old name"]["QPOINTS"], self.rows()["old name"]["RSCORE"]), ("", ""))
                else:
                    self.assertEqual(self.rows(), {})
                with self.badges_path.open(newline="", encoding="utf-8") as file:
                    self.assertEqual(list(csv.DictReader(file)), [])
                self.assert_empty_inbox()

    def test_changes_appended_during_refresh_keep_both_columns(self):
        players.write_usernames(self.names_path, ["Old", "LaterOld"])

        def append_change(session, name, *args):
            if name == "New":
                with self.inbox_path.open("a", encoding="utf-8") as file:
                    file.write("LaterOld,LaterNew\n")
            return PlayerCheck("eligible", score(name))

        self.assertEqual(self.run_update(side_effect=append_change), ["LaterOld", "New"])
        with self.inbox_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.reader(file)), [["LaterOld", "LaterNew"]])
        self.assertEqual(self.run_update(), ["LaterNew", "New"])
        self.assert_empty_inbox()

    def test_refresh_or_score_write_failure_keeps_changes_queued(self):
        for target, error in (("refresh_players", RuntimeError("refresh failed")), ("write_csv", OSError("write failed"))):
            with self.subTest(target=target):
                self.inbox_path.write_text("Old,New\n", encoding="utf-8")
                with patch.object(refresh, target, side_effect=error), self.assertRaises((RuntimeError, OSError)):
                    self.run_update()
                self.assertEqual(self.inbox_path.read_text(), "Old,New\n")

    def test_retry_after_partial_outputs_preserves_renamed_cache(self):
        players.write_csv(self.scores_path, players.FIELDS, [score("Old", QPOINTS=42, RSCORE=73)])
        self.state_path.write_text(json.dumps({"missing": {"old": {"first": 1, "last": 900, "status": "not_found"}}}))
        replace = Path.replace

        def fail_state_save(path, target):
            if Path(target) == self.state_path:
                raise OSError("state write failed")
            return replace(path, target)

        with patch.object(Path, "replace", fail_state_save), self.assertRaises(OSError):
            self.run_update(PlayerCheck("unavailable"))
        self.assertEqual(self.inbox_path.read_text(), "Old,New\n")

        self.assertEqual(self.run_update(PlayerCheck("unavailable")), ["New"])
        self.assertEqual(set(self.rows()), {"new"})
        self.assertEqual((self.rows()["new"]["QPOINTS"], self.rows()["new"]["RSCORE"]), ("42", "73"))
        self.assert_empty_inbox()


if __name__ == "__main__":
    unittest.main()
