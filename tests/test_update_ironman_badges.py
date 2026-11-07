import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import updateIronmanBadges as badges
from updateHiscores import DataUnavailable, RateLimited


def record(xp, level=1320, death=None):
    overall = {"name": "Overall", "level": level, "xp": xp}
    if death is not None:
        overall["death"] = death
    return {"skills": [
        overall,
        {"name": "Hitpoints", "level": 1, "xp": -1},
        {"name": "Invention", "level": 1, "xp": -1},
        {"name": "Necromancy", "level": 1, "xp": -1},
    ]}


class BadgeTests(unittest.TestCase):
    def test_partial_hardcore_record_matches_live_standard_xp(self):
        with patch.object(badges, "get_hiscores", side_effect=[
            record(9_102_938), record(9_102_938, level=1321),
        ]):
            self.assertEqual(badges.find_badge(object(), "10HP SPITE"), "hardcore")

    def test_partial_hardcore_record_uses_cache_when_standard_is_missing(self):
        with patch.object(badges, "get_hiscores", side_effect=[record(9_102_938), None]):
            self.assertEqual(
                badges.find_badge(object(), "10HP SPITE", cached_xp=9_102_938), "hardcore",
            )

    def test_dead_hardcore_record_falls_back_to_partial_ironman(self):
        with patch.object(badges, "get_hiscores", side_effect=[
            record(-1, death={"timeStamp": 1440533715888}),
            record(141_082_750, level=1903),
            None,
        ]) as lookup:
            self.assertEqual(
                badges.find_badge(object(), "10Hp Seafood", cached_xp=141_082_750), "ironman",
            )
        self.assertEqual(lookup.call_count, 3)
        self.assertEqual(lookup.call_args_list[1].args[2], badges.MODE_URLS[1][1])

    def test_frozen_hardcore_record_can_fall_back_to_current_ironman(self):
        with patch.object(badges, "get_hiscores", side_effect=[
            record(100), record(200), record(200),
        ]):
            self.assertEqual(badges.find_badge(object(), "Player"), "ironman")

    def test_frozen_ironman_record_does_not_receive_a_badge(self):
        with patch.object(badges, "get_hiscores", side_effect=[None, record(100), record(200)]):
            self.assertIsNone(badges.find_badge(object(), "Player"))

    def test_live_standard_takes_precedence_over_cached_xp(self):
        with patch.object(badges, "get_hiscores", side_effect=[record(100), record(200), None]):
            self.assertIsNone(badges.find_badge(object(), "Player", cached_xp=100))

    def test_no_mode_record_does_not_receive_a_badge(self):
        with patch.object(badges, "get_hiscores", side_effect=[None, None]):
            self.assertIsNone(badges.find_badge(object(), "Player", cached_xp=100))

    def test_malformed_mode_records_remain_unavailable(self):
        malformed = [
            {},
            {"skills": []},
            {"skills": [{"name": "Overall", "xp": 100}, {"name": "Overall", "xp": 100}]},
            record(-1),
            record("100"),
            record(True),
        ]
        for payload in malformed:
            with self.subTest(payload=payload), patch.object(badges, "get_hiscores", return_value=payload):
                with self.assertRaises(DataUnavailable):
                    badges.find_badge(object(), "Player", cached_xp=100)

    def test_upstream_failures_do_not_use_cached_xp(self):
        for error in (RateLimited("429"), DataUnavailable("500")):
            with self.subTest(error=error), patch.object(badges, "get_hiscores", side_effect=[record(100), error]):
                with self.assertRaises(type(error)):
                    badges.find_badge(object(), "Player", cached_xp=100)

    def test_main_assigns_both_reported_badges_from_partial_records(self):
        scores = {
            "10hp spite": {"TOTEXP": "9102938"},
            "10hp seafood": {"TOTEXP": "141082750"},
        }
        responses = {
            ("10HP SPITE", badges.MODE_URLS[0][1]): record(9_102_938),
            ("10Hp Seafood", badges.MODE_URLS[0][1]): record(81_782_129, death={}),
            ("10Hp Seafood", badges.MODE_URLS[1][1]): record(141_082_750, level=1903),
        }

        def lookup(session, username, url=None):
            return responses.get((username, url))

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "badges.csv"
            with patch.object(badges, "read_scores", return_value=(scores, [])), \
                 patch.object(badges, "read_badges", return_value={}), \
                 patch.object(badges, "read_usernames", return_value=["10HP SPITE", "10Hp Seafood"]), \
                 patch.object(badges, "create_session", return_value=contextlib.nullcontext(object())), \
                 patch.object(badges, "get_hiscores", side_effect=lookup), \
                 patch.object(badges, "BADGES_PATH", output), \
                 contextlib.redirect_stdout(io.StringIO()):
                badges.main()
            with output.open(newline="") as file:
                self.assertEqual(list(csv.DictReader(file)), [
                    {"RSN": "10HP SPITE", "BADGE": "hardcore"},
                    {"RSN": "10Hp Seafood", "BADGE": "ironman"},
                ])


if __name__ == "__main__":
    unittest.main()
