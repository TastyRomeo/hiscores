import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import maintainUsernames as maintenance
import players
from players import DataUnavailable, PlayerCheck, RateLimited


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
        skills = {name.lower(): {"level": 1, "fine_xp": 0} for name in players.BASE_SKILLS}
        skills["hitpoints"] = {"level": 10, "fine_xp": 11542}
        skills["magic"] = {"level": 50, "fine_xp": 1013333}
        payload = {"name": "Correct-Name", "skills": skills, "qp": 42}
        with patch.object(maintenance, "clan_members", return_value=["Correct Name"]), \
             patch.object(players, "get_hiscores", return_value={"skills": []}), \
             patch.object(players, "get_player_data", return_value=payload) as fallback, \
             contextlib.redirect_stdout(io.StringIO()):
            additions = maintenance.discover(self.session, self.clans, {})
        self.assertEqual(additions, ["Correct-Name"])
        fallback.assert_called_once()
        self.assertEqual(fallback.call_args.args[:2], (self.session, "Correct Name"))

    def test_name_variants_resolving_to_one_player_are_deduplicated(self):
        additions, check = self.discover(
            ["Some Name", "Some-Name", "SOME_NAME"],
            [PlayerCheck("eligible", {"RSN": "Some-Name"})],
        )
        self.assertEqual(additions, ["Some-Name"])
        self.assertEqual(check.call_count, 1)

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


class ClanRosterTests(unittest.TestCase):
    def test_clan_xp_does_not_filter_players(self):
        response = Mock(status_code=200, content=(
            b"Clanmate,Clan Rank,Total XP,Kills\n"
            b"New Member,Recruit,0,0\n"
            b"Low XP Member,Recruit,1,0\n"
            b"Veteran,Owner,999999999,0\n"
        ))
        with patch.object(maintenance, "request", return_value=response):
            self.assertEqual(maintenance.clan_members(object(), "Clan"), [
                "New Member", "Low XP Member", "Veteran",
            ])

    def test_roster_requires_only_the_name_column(self):
        response = Mock(status_code=200, content=b"Clanmate\nNew Member\n")
        with patch.object(maintenance, "request", return_value=response):
            self.assertEqual(maintenance.clan_members(object(), "Clan"), ["New Member"])

    def test_invalid_clan_xp_does_not_block_name_discovery(self):
        response = Mock(status_code=200, content=b"Clanmate,Total XP\nNew Member,unknown\n")
        with patch.object(maintenance, "request", return_value=response):
            self.assertEqual(maintenance.clan_members(object(), "Clan"), ["New Member"])


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.now = 100 * maintenance.DAY
        self.active = {"control": "Control", "missing": "Missing"}
        self.score_times = {"control": self.now - 30 * maintenance.DAY}
        self.missing = {"missing": {
            "first": self.now - 20 * maintenance.DAY,
            "last": self.now - maintenance.DAY,
            "status": "not_found",
        }}

    def check_missing(self, statuses, active=None, score_times=None, missing=None):
        def check(session, name, cache=None):
            value = statuses[name.lower()]
            return value if isinstance(value, PlayerCheck) else PlayerCheck(value)

        with patch.object(maintenance, "check_player", side_effect=check) as lookup, \
             contextlib.redirect_stdout(io.StringIO()):
            retired = maintenance.check_missing(
                object(), self.active if active is None else active,
                self.score_times if score_times is None else score_times,
                self.missing if missing is None else missing, self.now, 14,
            )
        return retired, lookup

    def test_ineligible_control_still_proves_the_api_is_healthy(self):
        retired, _ = self.check_missing({"control": "constitution", "missing": "not_found"})
        self.assertEqual(retired, {"missing": "not_found"})

    def test_missing_data_retires_only_after_the_grace_period(self):
        self.missing["missing"]["first"] = self.now - 13 * maintenance.DAY
        retired, _ = self.check_missing({"control": "eligible", "missing": "not_found"})
        self.assertEqual(retired, {})
        self.assertEqual(self.missing["missing"]["first"], self.now - 13 * maintenance.DAY)

    def test_legacy_missing_observations_start_a_new_grace_period(self):
        for previous_status in (None, "constitution", "combat_xp"):
            observation = {
                "first": self.now - 20 * maintenance.DAY,
                "last": self.now - maintenance.DAY,
            }
            if previous_status is not None:
                observation["status"] = previous_status
            self.missing = {"missing": observation}
            with self.subTest(previous_status=previous_status):
                retired, _ = self.check_missing({"control": "eligible", "missing": "not_found"})
                self.assertEqual(retired, {})
                self.assertEqual(self.missing["missing"], {
                    "first": self.now, "last": self.now, "status": "not_found",
                })

    def test_missing_status_transitions_keep_the_same_grace_period(self):
        original_now = self.now
        for previous_status, status in (("not_found", "incomplete"), ("incomplete", "not_found")):
            self.now = original_now
            first = self.now - 13 * maintenance.DAY
            self.missing = {"missing": {
                "first": first, "last": self.now - maintenance.DAY, "status": previous_status,
            }}
            with self.subTest(previous_status=previous_status, status=status):
                retired, _ = self.check_missing({"control": "eligible", "missing": status})
                self.assertEqual(retired, {})
                self.assertEqual(self.missing["missing"], {
                    "first": first, "last": self.now, "status": status,
                })
                self.now += maintenance.DAY
                retired, _ = self.check_missing({"control": "eligible", "missing": status})
                self.assertEqual(retired, {"missing": status})

    def test_valid_but_ineligible_account_stays_tracked(self):
        self.missing["control"] = dict(self.missing["missing"])
        retired, _ = self.check_missing({"control": "constitution", "missing": "combat_xp"})
        self.assertEqual(retired, {})
        self.assertEqual(self.missing, {})

    def test_hidden_ineligible_accounts_can_be_health_controls_without_scores(self):
        retired, lookup = self.check_missing(
            {"control": "constitution", "missing": "combat_xp"}, score_times={},
        )
        self.assertEqual(retired, {})
        self.assertEqual(self.missing, {})
        self.assertEqual(lookup.call_count, 2)

    def test_missing_fallback_controls_do_not_establish_api_health(self):
        retired, _ = self.check_missing(
            {"control": "not_found", "missing": "not_found"}, score_times={},
        )
        self.assertEqual(retired, {})
        self.assertIn("missing", self.missing)

    def test_one_valid_fallback_control_allows_missing_account_retirement(self):
        retired, _ = self.check_missing(
            {"control": "constitution", "missing": "not_found"}, score_times={},
        )
        self.assertEqual(retired, {"missing": "not_found"})

    def test_restored_eligible_stats_clear_missing_observations(self):
        retired, _ = self.check_missing({"control": "eligible", "missing": "eligible"})
        self.assertEqual(retired, {})
        self.assertEqual(self.missing, {})

    def test_service_failure_does_not_retire_a_player(self):
        retired, _ = self.check_missing({
            "control": "eligible",
            "missing": PlayerCheck("unavailable", error=DataUnavailable("500")),
        })
        self.assertEqual(retired, {})
        self.assertEqual(self.missing, {})

    def test_failed_health_check_prevents_retirement(self):
        retired, _ = self.check_missing({
            "control": PlayerCheck("unavailable", error=DataUnavailable("500")),
        })
        self.assertEqual(retired, {})
        self.assertIn("missing", self.missing)

    def test_recent_valid_scores_clear_old_missing_observations(self):
        retired, lookup = self.check_missing(
            {}, active={"missing": "Missing"}, score_times={"missing": self.now - 1},
        )
        self.assertEqual(retired, {})
        self.assertEqual(self.missing, {})
        lookup.assert_not_called()

    def test_controls_are_not_queried_twice_during_retirement_checks(self):
        _, lookup = self.check_missing({"control": "eligible", "missing": "not_found"})
        self.assertEqual(lookup.call_count, 2)

    def test_result_cache_is_shared_with_clan_discovery(self):
        cache = {}
        data_cache = {}
        session = object()
        with tempfile.TemporaryDirectory() as temporary:
            clans = Path(temporary) / "clans.txt"
            clans.write_text("Clan\n")
            result = PlayerCheck("eligible", {"RSN": "Correct-Name"})
            with patch.object(maintenance, "check_player", return_value=result) as check, \
                 patch.object(maintenance, "clan_members", return_value=["Correct-Name"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                maintenance.player_status(session, "Correct Name", cache, data_cache)
                self.assertEqual(maintenance.discover(session, clans, {}, cache, data_cache), ["Correct-Name"])
            self.assertEqual(check.call_count, 1)

    def test_rate_limit_still_stops_retirement_checks(self):
        with self.assertRaises(RateLimited):
            self.check_missing({"control": PlayerCheck("unavailable", error=RateLimited("429"))})


if __name__ == "__main__":
    unittest.main()
