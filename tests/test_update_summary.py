import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import players
import updateHiscores as refresh
from players import DataUnavailable, PlayerCheck, RateLimited, username_key


def score(name, timestamp):
    return {"RSN": name, "TIME": timestamp, "TOTEXP": 100_000}


class SummaryTests(unittest.TestCase):
    def summary(self, usernames, checks, old_scores=None, inactive_days=14, now=None):
        old_scores = old_scores or {}
        now = 30 * refresh.DAY if now is None else now
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _, rows = refresh.publish_scores(usernames, checks, old_scores, inactive_days, now)
            refresh.print_summary(usernames, checks, old_scores, rows, inactive_days, now)
        return output.getvalue().splitlines()

    def test_summary_names_players_in_each_category_and_preserves_api_warnings(self):
        usernames = ["Ruined", "Skiller", "Missing", "Incomplete", "Unavailable", "Warning"]
        checks = {
            "ruined": PlayerCheck("constitution"),
            "skiller": PlayerCheck("combat_xp"),
            "missing": PlayerCheck("not_found"),
            "incomplete": PlayerCheck("incomplete"),
            "unavailable": PlayerCheck("unavailable", error=DataUnavailable("HTTP 503")),
            "warning": PlayerCheck("eligible", score("Warning", 100), error=DataUnavailable("Quest Points unavailable")),
        }
        lines = self.summary(usernames, checks)

        for expected in (
            "HiScores update summary",
            "Published 1 scores from 6 tracked accounts.",
            "Accounts ruined (Constitution above 15): 1 (Ruined)",
            "Not ranked (adjusted combat XP below 100,000): 1 (Skiller)",
            "Missing players / possible RSN changes: 1 (Missing)",
            "Incomplete player data: 1 (Incomplete)",
            "Unavailable player data: 1 (Unavailable (HTTP 503))",
            "Unchecked accounts: 0",
            "API warnings (usable stats): 1 (Warning (Quest Points unavailable))",
        ):
            self.assertIn(expected, lines)

    def test_cache_summary_respects_grace_boundary_and_immediate_exclusions(self):
        now = 30 * refresh.DAY
        for status in ("not_found", "incomplete", "unavailable", None):
            with self.subTest(status=status):
                usernames = ["Recent", "Expired", "Ruined", "Skiller", "No cache"]
                checks = {
                    "ruined": PlayerCheck("constitution"),
                    "skiller": PlayerCheck("combat_xp"),
                    "no cache": PlayerCheck("not_found"),
                }
                if status is not None:
                    checks.update({"recent": PlayerCheck(status), "expired": PlayerCheck(status)})
                old_scores = {
                    "recent": score("Recent", now - 14 * refresh.DAY + 1),
                    "expired": score("Expired", now - 14 * refresh.DAY),
                    "ruined": score("Ruined", now - 1),
                    "skiller": score("Skiller", now - 15 * refresh.DAY),
                }
                lines = self.summary(usernames, checks, old_scores, now=now)

                self.assertIn("Cached scores retained (14-day grace period): 1 (Recent)", lines)
                self.assertIn("Cached scores expired (14-day grace period): 1 (Expired)", lines)
                self.assertIn("Published 1 scores from 5 tracked accounts.", lines)

    def test_rate_limit_lists_unchecked_players_separately_from_missing_players(self):
        usernames = ["Limited", "Next", "Unchecked"]
        result = PlayerCheck("unavailable", error=RateLimited("HTTP 429"))
        with patch.object(refresh, "check_player", return_value=result) as check, \
             contextlib.redirect_stdout(io.StringIO()):
            checks = refresh.refresh_players(object(), usernames)
        old_scores = {username_key(name): score(name, 30 * refresh.DAY - 1) for name in usernames}
        lines = self.summary(usernames, checks, old_scores)

        self.assertEqual(check.call_count, 1)
        self.assertIn("Unavailable player data: 1 (Limited (HTTP 429))", lines)
        self.assertIn("Unchecked accounts: 2 (Next, Unchecked)", lines)
        self.assertIn("Missing players / possible RSN changes: 0", lines)
        self.assertIn("Cached scores retained (14-day grace period): 3 (Limited, Next, Unchecked)", lines)

    def test_canonical_corrections_are_distinct_from_possible_rsn_changes(self):
        fresh = score("Player-Name", 30 * refresh.DAY)
        checks = {"player name": PlayerCheck("eligible", fresh), "missing": PlayerCheck("not_found")}
        lines = self.summary(["player name", "Missing"], checks)

        self.assertIn("Username corrections: 1 ('player name' -> 'Player-Name')", lines)
        self.assertIn("Missing players / possible RSN changes: 1 (Missing)", lines)
        self.assertIn("Cached scores retained (14-day grace period): 0", lines)
        self.assertIn("Cached scores expired (14-day grace period): 0", lines)

    def test_fresh_alias_is_not_reported_as_cached_when_rate_limit_skips_canonical_name(self):
        now = 30 * refresh.DAY
        usernames = ["player name", "Player-Name"]
        fresh = {**score("Player-Name", now), "HPLVL": 10, "HPEXP": 1154}
        result = PlayerCheck("eligible", fresh, error=RateLimited("HTTP 429"))
        with patch.object(refresh, "check_player", return_value=result), \
             contextlib.redirect_stdout(io.StringIO()):
            checks = refresh.refresh_players(object(), usernames)

        for age in (13, 15):
            with self.subTest(cache_age=age):
                old_scores = {"player-name": score("Player-Name", now - age * refresh.DAY)}
                lines = self.summary(usernames, checks, old_scores, now=now)

                self.assertIn("Published 1 scores from 2 tracked accounts.", lines)
                self.assertIn("Unchecked accounts: 1 (Player-Name)", lines)
                self.assertIn("API warnings (usable stats): 1 (Player-Name (HTTP 429))", lines)
                self.assertIn("Cached scores retained (14-day grace period): 0", lines)
                self.assertIn("Cached scores expired (14-day grace period): 0", lines)

    def test_empty_roster_has_a_complete_zero_summary_and_configured_grace_period(self):
        lines = self.summary([], {}, inactive_days=7)

        self.assertIn("HiScores update summary", lines)
        self.assertIn("Published 0 scores from 0 tracked accounts.", lines)
        for label in (
            "Accounts ruined (Constitution above 15)",
            "Not ranked (adjusted combat XP below 100,000)",
            "Missing players / possible RSN changes",
            "Incomplete player data",
            "Unavailable player data",
            "Unchecked accounts",
            "Username corrections",
            "API warnings (usable stats)",
            "Cached scores retained (7-day grace period)",
            "Cached scores expired (7-day grace period)",
        ):
            self.assertIn(f"{label}: 0", lines)

    def test_main_prints_summary_after_publishing_scores_and_username_corrections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names_path, scores_path = root / "usernames.csv", root / "hiscores.csv"
            players.write_usernames(names_path, ["player name"])
            players.write_csv(scores_path, players.FIELDS, [])
            checks = {"player name": PlayerCheck("eligible", score("Player-Name", 100))}
            print_summary = refresh.print_summary
            output = io.StringIO()

            def summarize(*args):
                self.assertEqual(players.read_usernames(names_path), ["Player-Name"])
                published, _ = players.read_scores(scores_path)
                self.assertEqual(published["player-name"]["RSN"], "Player-Name")
                print_summary(*args)

            with patch.object(refresh, "USERNAMES_PATH", names_path), \
                 patch.object(refresh, "SCORES_PATH", scores_path), \
                 patch.object(refresh, "create_session", return_value=contextlib.nullcontext(object())), \
                 patch.object(refresh, "refresh_players", return_value=checks), \
                 patch.object(refresh, "print_summary", side_effect=summarize) as summary, \
                 patch.object(sys, "argv", ["updateHiscores.py", "--inactive-days", "14"]), \
                 contextlib.redirect_stdout(output):
                refresh.main()

            summary.assert_called_once()
            lines = output.getvalue().splitlines()
            self.assertIn("HiScores update summary", lines)
            self.assertIn("Published 1 scores from 1 tracked accounts.", lines)
            self.assertIn("Username corrections: 1 ('player name' -> 'Player-Name')", lines)


if __name__ == "__main__":
    unittest.main()
