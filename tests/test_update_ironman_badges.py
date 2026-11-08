import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import updateIronmanBadges as badges
import players
from players import DataUnavailable, RateLimited


def record(death=None, **fields):
    overall = {"name": "Overall", **fields}
    if death is not None:
        overall["death"] = death
    return {"skills": [
        overall,
        {"name": "Hitpoints", "level": 1, "xp": -1},
        {"name": "Invention", "level": 1, "xp": -1},
        {"name": "Necromancy", "level": 1, "xp": -1},
    ]}


class BadgeTests(unittest.TestCase):
    def run_refresh(self, usernames, old_badges, lookup, failure_limit=None):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "badges.csv"
            with patch.object(badges, "read_badges", return_value=old_badges), \
                 patch.object(badges, "read_usernames", return_value=usernames), \
                 patch.object(badges, "create_session", return_value=contextlib.nullcontext(object())), \
                 patch.object(badges, "get_hiscores", side_effect=lookup) as requests, \
                 patch.object(badges, "BADGES_PATH", output), \
                 patch.object(badges, "MAX_CONSECUTIVE_FAILURES", failure_limit or 5), \
                 contextlib.redirect_stdout(io.StringIO()):
                badges.main()
            with output.open(newline="") as file:
                return list(csv.DictReader(file)), requests.call_args_list

    def test_partial_hardcore_record_does_not_need_any_xp(self):
        with patch.object(badges, "get_hiscores", return_value=record()) as lookup:
            self.assertEqual(badges.find_badge(object(), "10HP SPITE"), "hardcore")
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(lookup.call_args.args[2], badges.MODE_URLS[0][1])

    def test_xp_and_level_values_do_not_affect_badges(self):
        for xp in (-1, "unavailable", True, 9_102_938):
            with self.subTest(xp=xp), patch.object(badges, "get_hiscores", return_value=record(xp=xp, level=-1)):
                self.assertEqual(badges.find_badge(object(), "Player"), "hardcore")

    def test_dead_hardcore_record_falls_back_to_ironman(self):
        with patch.object(badges, "get_hiscores", side_effect=[
            record(death={"timeStamp": 1440533715888}), record(),
        ]) as lookup:
            self.assertEqual(badges.find_badge(object(), "10Hp Seafood"), "ironman")
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(lookup.call_args_list[1].args[2], badges.MODE_URLS[1][1])

    def test_missing_hardcore_record_can_have_an_ironman_badge(self):
        with patch.object(badges, "get_hiscores", side_effect=[None, record()]):
            self.assertEqual(badges.find_badge(object(), "Player"), "ironman")

    def test_hardcore_lookup_failure_still_checks_ironman(self):
        with patch.object(badges, "get_hiscores", side_effect=[DataUnavailable("500"), record()]):
            self.assertEqual(badges.find_badge(object(), "Player"), "ironman")

    def test_no_mode_records_mean_no_badge(self):
        with patch.object(badges, "get_hiscores", side_effect=[None, None]) as lookup:
            self.assertIsNone(badges.find_badge(object(), "Player"))
        self.assertEqual(lookup.call_count, 2)

    def test_dead_hardcore_without_ironman_record_has_no_badge(self):
        with patch.object(badges, "get_hiscores", side_effect=[record(death={}), None]):
            self.assertIsNone(badges.find_badge(object(), "Player"))

    def test_both_mode_failures_are_reported_after_both_attempts(self):
        with patch.object(badges, "get_hiscores", side_effect=DataUnavailable("500")) as lookup:
            with self.assertRaises(DataUnavailable):
                badges.find_badge(object(), "Player")
        self.assertEqual(lookup.call_count, 2)

    def test_malformed_mode_record_can_fall_back_to_other_mode(self):
        malformed = [
            {},
            {"skills": []},
            {"skills": [{"name": "Overall"}, {"name": "Overall"}]},
        ]
        for payload in malformed:
            with self.subTest(payload=payload), patch.object(badges, "get_hiscores", side_effect=[payload, record()]):
                self.assertEqual(badges.find_badge(object(), "Player"), "ironman")

    def test_rate_limit_stops_before_the_second_mode_lookup(self):
        with patch.object(badges, "get_hiscores", side_effect=RateLimited("429")) as lookup:
            with self.assertRaises(RateLimited):
                badges.find_badge(object(), "Player")
        self.assertEqual(lookup.call_count, 1)

    def test_main_assigns_both_reported_badges_without_standard_lookups(self):
        responses = {
            ("10HP SPITE", badges.MODE_URLS[0][1]): record(),
            ("10Hp Seafood", badges.MODE_URLS[0][1]): record(death={}),
            ("10Hp Seafood", badges.MODE_URLS[1][1]): record(),
        }

        def lookup(session, username, url):
            return responses.get((username, url))

        rows, calls = self.run_refresh(["10HP SPITE", "10Hp Seafood"], {}, lookup)
        self.assertEqual(rows, [
            {"RSN": "10HP SPITE", "BADGE": "hardcore"},
            {"RSN": "10Hp Seafood", "BADGE": "ironman"},
        ])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.args[2] in dict(badges.MODE_URLS).values() for call in calls))

    def test_main_clears_old_badge_when_both_mode_records_are_missing(self):
        rows, calls = self.run_refresh(["Player"], {"player": "hardcore"}, lambda *args: None)
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 2)

    def test_main_clears_old_badge_when_both_mode_lookups_fail(self):
        def fail(*args):
            raise DataUnavailable("500")

        rows, calls = self.run_refresh(["Player"], {"player": "hardcore"}, fail)
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 2)

    def test_wrong_player_responses_cannot_grant_a_badge(self):
        payload = record()
        payload["name"] = "Unrelated Account"
        response = Mock(status_code=200)
        response.json.return_value = payload
        with patch.object(players, "request", return_value=response) as requests:
            rows, _ = self.run_refresh(
                ["Requested Player"], {"requested player": "hardcore"}, players.get_hiscores,
            )
        self.assertEqual(rows, [])
        self.assertEqual(requests.call_count, 2)

    def test_rate_limit_keeps_only_unchecked_players_previous_badges(self):
        def lookup(session, username, url):
            if username == "Known":
                return record()
            raise RateLimited("429")

        rows, calls = self.run_refresh(
            ["Known", "Limited", "Unchecked"],
            {"known": "ironman", "limited": "hardcore", "unchecked": "ironman", "removed": "hardcore"},
            lookup,
        )
        self.assertEqual(rows, [
            {"RSN": "Known", "BADGE": "hardcore"},
            {"RSN": "Unchecked", "BADGE": "ironman"},
        ])
        self.assertEqual([call.args[1] for call in calls], ["Known", "Limited"])

    def test_failure_circuit_preserves_only_unchecked_players(self):
        def fail(*args):
            raise DataUnavailable("500")

        rows, calls = self.run_refresh(
            ["First", "Second", "Unchecked"],
            {"first": "hardcore", "second": "ironman", "unchecked": "hardcore"},
            fail,
            failure_limit=2,
        )
        self.assertEqual(rows, [{"RSN": "Unchecked", "BADGE": "hardcore"}])
        self.assertEqual(len(calls), 4)

    def test_all_tracked_players_are_checked_without_a_scores_file(self):
        usernames = [f"Player{index:04}" for index in range(405)]
        rows, calls = self.run_refresh(usernames, {}, lambda *args: None)
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 810)


if __name__ == "__main__":
    unittest.main()
