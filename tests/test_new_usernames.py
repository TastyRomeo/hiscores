import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import players
import updateHiscores as refresh
from players import DataUnavailable, PlayerCheck, RateLimited


def score(name):
    row = dict.fromkeys(players.FIELDS, "")
    row.update(RSN=name, HPLVL=10, HPEXP=1154, TOTEXP=100_000, TIME=100)
    return row


class NewUsernameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.names_path = root / "usernames.csv"
        self.scores_path = root / "hiscores.csv"
        self.inbox_path = root / "username_inbox.csv"
        players.write_usernames(self.names_path, [])
        players.write_csv(self.scores_path, players.FIELDS, [])
        stack = self.enterContext(contextlib.ExitStack())
        stack.enter_context(patch.object(refresh, "USERNAMES_PATH", self.names_path))
        stack.enter_context(patch.object(refresh, "SCORES_PATH", self.scores_path))
        stack.enter_context(patch.object(refresh, "create_session", return_value=contextlib.nullcontext(object())))
        stack.enter_context(patch.object(sys, "argv", ["updateHiscores.py", "--inactive-days", "14"]))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def run_update(self, result=None, side_effect=None):
        if side_effect is None:
            side_effect = lambda session, name, *args: result or PlayerCheck("eligible", score(name))
        with patch.object(refresh, "check_player", side_effect=side_effect) as check:
            refresh.main()
        return check

    def assert_empty_inbox(self):
        self.assertIn(self.inbox_path.read_bytes(), (b"", b"\n"))

    def write_additions(self, names):
        with self.inbox_path.open("w", newline="", encoding="utf-8") as file:
            csv.writer(file, lineterminator="\n").writerows(("", name) for name in names)

    def test_merges_normalized_names_without_changing_existing_spelling_or_replaying_additions(self):
        players.write_usernames(self.names_path, ["Existing_Name", "Zed"])
        self.inbox_path.write_text("\ufeff\n, existing name \n\n, NEW_Name \n,new name\n,New\xa0Name\n,Zed\n", encoding="utf-8")

        for _ in range(2):
            check = self.run_update()
            self.assertEqual([call.args[1] for call in check.call_args_list], ["Existing_Name", "NEW_Name", "Zed"])
            self.assertEqual(players.read_usernames(self.names_path), ["Existing_Name", "NEW_Name", "Zed"])
            scores, _ = players.read_scores(self.scores_path)
            self.assertEqual(set(scores), {"existing name", "new name", "zed"})
            self.assert_empty_inbox()

    def test_ineligible_missing_and_unchecked_additions_are_still_saved_to_roster(self):
        for status in ("constitution", "combat_xp", "not_found", "incomplete", "unavailable", "limited"):
            with self.subTest(status=status):
                players.write_usernames(self.names_path, [])
                players.write_csv(self.scores_path, players.FIELDS, [])
                self.write_additions(["Alpha", "Zulu"])
                if status == "limited":
                    result = PlayerCheck("unavailable", error=RateLimited("HTTP 429"))
                else:
                    result = PlayerCheck(status, error=DataUnavailable("HTTP 503") if status == "unavailable" else None)

                check = self.run_update(result)

                self.assertEqual(check.call_count, 1 if status == "limited" else 2)
                self.assertEqual(players.read_usernames(self.names_path), ["Alpha", "Zulu"])
                self.assertEqual(players.read_scores(self.scores_path)[0], {})
                self.assert_empty_inbox()

    def test_refresh_or_output_failure_preserves_pending_names(self):
        write_usernames = refresh.write_usernames

        def fail_roster_write(path, names):
            if path == self.names_path:
                raise OSError("roster write failed")
            write_usernames(path, names)

        failures = (
            ("refresh_players", RuntimeError("refresh failed")),
            ("write_csv", OSError("scores write failed")),
            ("write_usernames", fail_roster_write),
        )
        for target, failure in failures:
            with self.subTest(target=target):
                players.write_usernames(self.names_path, [])
                players.write_csv(self.scores_path, players.FIELDS, [])
                self.inbox_path.write_text(",New\n", encoding="utf-8")

                with patch.object(refresh, target, side_effect=failure), self.assertRaises((RuntimeError, OSError)):
                    self.run_update()

                self.assertEqual(self.inbox_path.read_text(encoding="utf-8"), ",New\n")

    def test_names_appended_during_refresh_remain_queued_for_the_next_update(self):
        self.write_additions(["New_User"])

        def append_while_refreshing(session, name, *args):
            with self.inbox_path.open("a", encoding="utf-8") as file:
                file.write(",Later\n,new user\n")
            return PlayerCheck("eligible", score(name))

        first_check = self.run_update(side_effect=append_while_refreshing)

        self.assertEqual(first_check.call_count, 1)
        self.assertEqual(players.read_usernames(self.names_path), ["New_User"])
        with self.inbox_path.open(newline="", encoding="utf-8") as file:
            self.assertEqual(list(csv.reader(file)), [["", "Later"]])

        second_check = self.run_update()

        self.assertEqual([call.args[1] for call in second_check.call_args_list], ["Later", "New_User"])
        self.assertEqual(players.read_usernames(self.names_path), ["Later", "New_User"])
        self.assert_empty_inbox()

    def test_missing_or_empty_inbox_keeps_normal_refresh_working(self):
        for contents in (None, "", "\n\n"):
            with self.subTest(contents=contents):
                players.write_usernames(self.names_path, ["Existing"])
                if contents is None:
                    self.inbox_path.unlink(missing_ok=True)
                else:
                    self.inbox_path.write_text(contents, encoding="utf-8")

                check = self.run_update()

                self.assertEqual(check.call_count, 1)
                self.assertEqual(check.call_args.args[1], "Existing")
                self.assertEqual(players.read_usernames(self.names_path), ["Existing"])
                if self.inbox_path.exists():
                    self.assertEqual(players.read_usernames(self.inbox_path), [])

    def test_inbox_alias_merges_with_canonical_tracked_name_and_preserves_cached_optional_values(self):
        players.write_usernames(self.names_path, ["Player-Name"])
        self.write_additions(["player name", "PLAYER_NAME"])
        old = score("Player-Name")
        old.update(TIME=1, QPOINTS=42, RSCORE=73)
        players.write_csv(self.scores_path, players.FIELDS, [old])

        check = self.run_update(PlayerCheck("eligible", score("Player-Name")))

        self.assertEqual(check.call_count, 1)
        self.assertEqual(players.read_usernames(self.names_path), ["Player-Name"])
        scores, _ = players.read_scores(self.scores_path)
        self.assertEqual(set(scores), {"player-name"})
        self.assertEqual(scores["player-name"]["QPOINTS"], "42")
        self.assertEqual(scores["player-name"]["RSCORE"], "73")
        self.assert_empty_inbox()

    def test_rows_with_both_fields_empty_do_not_add_or_query_players(self):
        self.inbox_path.write_text(',\n  ,  \n"",""\n', encoding="utf-8")

        check = self.run_update()

        check.assert_not_called()
        self.assertEqual(players.read_usernames(self.names_path), [])
        self.assertEqual(players.read_scores(self.scores_path)[0], {})


if __name__ == "__main__":
    unittest.main()
