import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import maintainUsernames as maintenance
import updateHiscores as hiscores
from updateHiscores import DataUnavailable, PlayerCheck, RateLimited


class ClanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clans = Path(self.temporary.name) / "clans.txt"
        self.clans.write_text("Test Clan\n")
        self.session = object()

    def discover(self, members, results, active=None):
        with patch.object(maintenance, "clan_members", return_value=members), \
             patch.object(maintenance, "check_player", side_effect=results) as check, \
             contextlib.redirect_stdout(io.StringIO()):
            additions = maintenance.discover(self.session, self.clans, active or {})
        return additions, check

    def test_adds_canonical_name_from_eligible_player_data(self):
        additions, _ = self.discover(
            ["wrong spelling"], [PlayerCheck("eligible", {"RSN": "Correct-Name"})],
        )
        self.assertEqual(additions, ["Correct-Name"])

    def test_incomplete_hiscores_fall_back_to_canonical_player_data(self):
        skills = {name.lower(): {"level": 1, "fine_xp": 0} for name in hiscores.BASE_SKILLS}
        skills["hitpoints"] = {"level": 10, "fine_xp": 11542}
        skills["magic"] = {"level": 50, "fine_xp": 1013333}
        payload = {"name": "Correct-Name", "skills": skills, "qp": 42}
        with patch.object(maintenance, "clan_members", return_value=["Correct Name"]), \
             patch.object(hiscores, "get_hiscores", return_value={"skills": []}), \
             patch.object(hiscores, "get_player_data", return_value=payload) as fallback, \
             contextlib.redirect_stdout(io.StringIO()):
            additions = maintenance.discover(self.session, self.clans, {})
        self.assertEqual(additions, ["Correct-Name"])
        fallback.assert_called_once_with(self.session, "Correct Name", None)

    def test_name_variants_resolving_to_one_player_are_deduplicated(self):
        additions, check = self.discover(
            ["Some Name", "Some-Name", "SOME_NAME"],
            [PlayerCheck("eligible", {"RSN": "Some-Name"})] * 2,
        )
        self.assertEqual(additions, ["Some-Name"])
        self.assertEqual(check.call_count, 2)

    def test_canonical_name_already_tracked_is_not_added_again(self):
        additions, check = self.discover(
            ["Some Name"], [PlayerCheck("eligible", {"RSN": "Some-Name"})],
            {"some-name": "Some-Name"},
        )
        self.assertEqual(additions, [])
        self.assertEqual(check.call_count, 1)

    def test_existing_normalized_names_do_not_need_rechecking(self):
        additions, check = self.discover(
            ["SOME_NAME"], [], {"some name": "Some Name"},
        )
        self.assertEqual(additions, [])
        check.assert_not_called()

    def test_ineligible_corrected_names_are_not_added(self):
        additions, check = self.discover(
            ["High HP", "Low Combat XP"],
            [PlayerCheck("constitution"), PlayerCheck("combat_xp")],
        )
        self.assertEqual(additions, [])
        self.assertEqual(check.call_count, 2)

    def test_all_candidates_are_checked_above_the_old_limit(self):
        members = [f"Member{index:04}" for index in range(405)]
        additions, check = self.discover(
            members, [PlayerCheck("eligible", {"RSN": name}) for name in members],
        )
        self.assertEqual(additions, members)
        self.assertEqual(check.call_count, 405)

    def test_three_consecutive_service_failures_still_stop_discovery(self):
        additions, check = self.discover(
            [f"Member{index}" for index in range(5)],
            [PlayerCheck("unavailable", error=DataUnavailable("missing data"))] * 5,
        )
        self.assertEqual(additions, [])
        self.assertEqual(check.call_count, 3)

    def test_rate_limit_still_propagates(self):
        with self.assertRaises(RateLimited):
            self.discover(
                ["Player"], [PlayerCheck("unavailable", error=RateLimited("429"))],
            )


if __name__ == "__main__":
    unittest.main()
