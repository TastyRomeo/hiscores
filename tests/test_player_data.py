import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import players


def player_data(name="Player"):
    skills = {name.lower(): {"level": 1, "fine_xp": 0} for name in players.BASE_SKILLS}
    skills["hitpoints"] = {"level": 10, "fine_xp": 12042}
    skills["magic"] = {"level": 50, "fine_xp": 1013333}
    return {"name": name, "skills": skills, "qp": 42, "quests": {"257": {"status": "COMPLETED"}}}


def hiscores_data(name="Player", source=None):
    source = player_data(name) if source is None else source
    total_level, total_xp, skills = players.parse_player_data(source)
    return {
        "name": name,
        "skills": [
            {"name": "Overall", "level": total_level, "xp": total_xp},
            *({"name": skill, "level": level, "xp": xp} for skill, (level, xp) in skills.items()),
        ],
        "activities": [{"name": "RuneScore", "score": 73}],
    }


def response(status, payload):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class PlayerDataTests(unittest.TestCase):
    def test_request_combines_skills_and_quests_and_encodes_name(self):
        session = object()
        with patch.object(players, "request", return_value=response(200, player_data("Two Names"))) as request:
            payload = players.get_player_data(session, "Two_Names")
        self.assertEqual(payload["qp"], 42)
        args, kwargs = request.call_args
        self.assertIs(args[0], session)
        self.assertTrue(args[1].endswith("/playerData/Two%20Names"))
        self.assertEqual(kwargs["params"], {"includeSkills": "true", "includeQuests": "true"})

    def test_cache_keeps_only_fields_used_by_the_scripts(self):
        cache = {}
        with patch.object(players, "request", return_value=response(200, player_data())):
            payload = players.get_player_data(object(), "Player", cache)
        self.assertEqual(set(payload), {"name", "skills", "qp"})
        self.assertIs(cache["player"], payload)
        self.assertNotIn("quests", payload)

    def test_alias_cache_reuses_one_request_after_canonical_correction(self):
        cache = {}
        with patch.object(players, "request", return_value=response(200, player_data("Player-Name"))) as request:
            payload = players.get_player_data(object(), "player name", cache)
            self.assertIs(players.get_player_data(object(), "Player-Name", cache), payload)
            self.assertIs(players.get_player_data(object(), "PLAYER_NAME", cache), payload)
        self.assertEqual(request.call_count, 1)

    def test_fractional_xp_is_truncated_per_skill_before_summing(self):
        payload = player_data()
        payload["skills"]["hitpoints"]["fine_xp"] = 12049
        payload["skills"]["magic"]["fine_xp"] = 1013339
        total_level, total_xp, skills = players.parse_player_data(payload)
        self.assertEqual(total_level, 87)
        self.assertEqual(total_xp, 102537)
        self.assertEqual(skills["Hitpoints"], (10, 1204))
        self.assertEqual(skills["Magic"], (50, 101333))

    def test_unranked_player_uses_one_combined_player_data_request(self):
        with patch.object(players, "request", side_effect=[
            response(404, {}), response(200, player_data()),
        ]) as request:
            result = players.check_player(object(), "Player", {}, refresh_player_data=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")
        self.assertEqual(result.row["HPLVL"], 10)
        self.assertEqual(result.row["HPEXP"], 1204)
        self.assertEqual(result.row["TOTEXP"], 102537)
        self.assertEqual(result.row["QPOINTS"], 42)
        self.assertEqual(result.row["RSCORE"], "")

    def test_ordinary_hourly_lookup_does_not_request_player_data(self):
        with patch.object(players, "request", return_value=response(200, hiscores_data())) as request:
            result = players.check_player(object(), "Player")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "hiscores")
        self.assertEqual(result.row["RSCORE"], 73)
        self.assertNotIn("QPOINTS", result.row)

    def test_daily_lookup_builds_one_result_from_two_requests(self):
        with patch.object(players, "request", side_effect=[
            response(200, hiscores_data()), response(200, player_data()),
        ]) as request:
            result = players.check_player(object(), "Player", {}, refresh_player_data=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["TOTEXP"], 102537)
        self.assertEqual(result.row["QPOINTS"], 42)
        self.assertEqual(result.row["RSCORE"], 73)

    def test_legacy_partial_hiscores_use_player_data(self):
        legacy = {"name": "Player", "skills": [
            {"name": "Overall", "level": 87, "xp": 102537},
            *({"name": name, "level": 1, "xp": -1} for name in players.BASE_SKILLS),
        ]}
        with patch.object(players, "request", side_effect=[response(200, legacy), response(200, player_data())]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")

    def test_missing_hiscores_fields_use_canonical_player_data(self):
        with patch.object(players, "request", side_effect=[
            response(200, {"name": "player name", "skills": []}),
            response(200, player_data("Player-Name")),
        ]):
            result = players.check_player(object(), "player name")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["RSN"], "Player-Name")
        self.assertEqual(result.source, "player_data")

    def test_malformed_hiscores_json_uses_player_data(self):
        malformed = response(200, {})
        malformed.json.side_effect = ValueError("invalid JSON")
        with patch.object(players, "request", side_effect=[malformed, response(200, player_data())]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")

    def test_unavailable_hiscores_can_recover_from_player_data(self):
        with patch.object(players, "get_hiscores", side_effect=players.DataUnavailable("500")), \
             patch.object(players, "get_player_data", return_value=player_data()):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")

    def test_invalid_optional_quest_points_do_not_invalidate_usable_skills(self):
        payload = player_data()
        payload["qp"] = "not available"
        with patch.object(players, "request", side_effect=[response(404, {}), response(200, payload)]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["TOTEXP"], 102537)
        self.assertNotIn("QPOINTS", result.row)
        self.assertIsInstance(result.error, players.DataUnavailable)

    def test_optional_player_data_failure_preserves_valid_hiscores(self):
        with patch.object(players, "get_hiscores", return_value=hiscores_data()), \
             patch.object(players, "get_player_data", side_effect=players.DataUnavailable("500")):
            result = players.check_player(object(), "Player", refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "hiscores")
        self.assertEqual(result.row["RSCORE"], 73)
        self.assertNotIn("QPOINTS", result.row)

    def test_bad_optional_skills_do_not_lose_quest_points_or_hiscores(self):
        payload = player_data()
        del payload["skills"]["hitpoints"]
        with patch.object(players, "get_hiscores", return_value=hiscores_data()), \
             patch.object(players, "get_player_data", return_value=payload):
            result = players.check_player(object(), "Player", refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "hiscores")
        self.assertEqual(result.row["QPOINTS"], 42)

    def test_confirmed_ineligible_hiscores_are_not_replaced_with_older_player_data(self):
        latest = player_data()
        latest["skills"]["hitpoints"] = {"level": 99, "fine_xp": 130344310}
        with patch.object(players, "get_hiscores", return_value=hiscores_data(source=latest)), \
             patch.object(players, "get_player_data", return_value=player_data()):
            result = players.check_player(object(), "Player", refresh_player_data=True)
        self.assertEqual(result.status, "constitution")
        self.assertEqual(result.source, "hiscores")
        self.assertIsNone(result.row)

    def test_player_data_with_higher_xp_is_selected(self):
        latest = player_data()
        latest["skills"]["magic"]["fine_xp"] += 100
        with patch.object(players, "get_hiscores", return_value=hiscores_data()), \
             patch.object(players, "get_player_data", return_value=latest):
            result = players.check_player(object(), "Player", refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")
        self.assertEqual(result.row["TOTEXP"], 102547)

    def test_fresher_player_data_can_confirm_ineligibility(self):
        latest = player_data()
        latest["skills"]["hitpoints"] = {"level": 99, "fine_xp": 130344310}
        with patch.object(players, "get_hiscores", return_value=hiscores_data()), \
             patch.object(players, "get_player_data", return_value=latest):
            result = players.check_player(object(), "Player", refresh_player_data=True)
        self.assertEqual(result.status, "constitution")
        self.assertEqual(result.source, "player_data")
        self.assertIsNone(result.row)

    def test_canonical_case_is_corrected_while_fresher_hiscores_win(self):
        latest = player_data()
        latest["skills"]["magic"]["fine_xp"] += 100
        with patch.object(players, "get_hiscores", return_value=hiscores_data("player", latest)), \
             patch.object(players, "get_player_data", return_value=player_data("Player")):
            result = players.check_player(object(), "player", refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "hiscores")
        self.assertEqual(result.row["RSN"], "Player")
        self.assertEqual(result.row["TOTEXP"], 102547)

    def test_space_hyphen_correction_uses_player_data_entire_skill_record(self):
        other_stats = player_data("player name")
        other_stats["skills"]["hitpoints"] = {"level": 99, "fine_xp": 130344310}
        with patch.object(players, "get_hiscores", return_value=hiscores_data("player name", other_stats)), \
             patch.object(players, "get_player_data", return_value=player_data("Player-Name")):
            result = players.check_player(object(), "player name", refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")
        self.assertEqual(result.row["RSN"], "Player-Name")
        self.assertEqual(result.row["HPLVL"], 10)
        self.assertEqual(result.row["TOTEXP"], 102537)

    def test_missing_both_sources_is_not_found(self):
        with patch.object(players, "request", side_effect=[
            response(404, {}), response(404, {"error": "PLAYER_NOT_FOUND"}),
        ]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.row)

    def test_service_failures_remain_unavailable(self):
        with patch.object(players, "get_hiscores", side_effect=players.DataUnavailable("500")), \
             patch.object(players, "get_player_data", side_effect=players.DataUnavailable("500")):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "unavailable")
        self.assertIsInstance(result.error, players.DataUnavailable)

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
                 patch.object(players, "get_hiscores", return_value=None), \
                 patch.object(players, "get_player_data", return_value=payload):
                result = players.check_player(object(), "Player")
                self.assertEqual(result.status, "unavailable")
                self.assertIsInstance(result.error, players.DataUnavailable)

    def test_supported_name_format_variants_are_valid(self):
        for requested in ("PLAYER NAME", "Player_Name", "player-name", " player name "):
            with self.subTest(requested=requested):
                self.assertEqual(players.validate_player_name({"name": "Player-Name"}, requested), "Player-Name")

    def test_unrelated_and_invalid_player_data_names_are_rejected_before_caching(self):
        for name in (None, "", "   ", 123, "Unrelated", "Player-Namf"):
            cache = {}
            payload = player_data()
            payload["name"] = name
            with self.subTest(name=name), patch.object(players, "request", return_value=response(200, payload)):
                with self.assertRaises(players.DataUnavailable):
                    players.get_player_data(object(), "Player", cache)
            self.assertEqual(cache, {})

    def test_wrong_hiscores_name_is_rejected(self):
        with patch.object(players, "request", return_value=response(200, hiscores_data("Other Player"))):
            with self.assertRaises(players.PlayerNameMismatch):
                players.get_hiscores(object(), "Player")

    def test_invalid_hiscores_name_is_rejected(self):
        for name in (None, "", "   ", 123):
            payload = hiscores_data()
            payload["name"] = name
            with self.subTest(name=name), patch.object(players, "request", return_value=response(200, payload)):
                with self.assertRaises(players.DataUnavailable):
                    players.get_hiscores(object(), "Player")

    def test_wrong_hiscores_name_can_recover_from_player_data(self):
        with patch.object(players, "request", side_effect=[
            response(200, hiscores_data("Other Player")), response(200, player_data()),
        ]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "player_data")
        self.assertEqual(result.row["RSN"], "Player")

    def test_both_wrong_names_become_missing_without_publishing_the_other_account(self):
        cache = {}
        with patch.object(players, "request", side_effect=[
            response(200, hiscores_data("Other Player")), response(200, player_data("Other Player")),
        ]):
            result = players.check_player(object(), "Player", cache)
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.row)
        self.assertEqual(cache, {})

    def test_wrong_player_data_name_does_not_replace_valid_hiscores(self):
        cache = {}
        with patch.object(players, "request", side_effect=[
            response(200, hiscores_data()), response(200, player_data("Other Player")),
        ]):
            result = players.check_player(object(), "Player", cache, refresh_player_data=True)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.source, "hiscores")
        self.assertEqual(result.row["RSN"], "Player")
        self.assertNotIn("QPOINTS", result.row)
        self.assertEqual(cache, {})

    def test_missing_player_response_is_cached(self):
        cache = {}
        with patch.object(players, "request", return_value=response(404, {"error": "PLAYER_NOT_FOUND"})) as request:
            self.assertIsNone(players.get_player_data(object(), "Player", cache))
            self.assertIsNone(players.get_player_data(object(), "player", cache))
        self.assertEqual(request.call_count, 1)

    def test_membership_restriction_is_unavailable_not_missing(self):
        with patch.object(players, "request", side_effect=[
            response(404, {}), response(403, {"error": "PLAYER_NOT_MEMBER"}),
        ]):
            result = players.check_player(object(), "Player")
        self.assertEqual(result.status, "unavailable")
        self.assertIsInstance(result.error, players.DataUnavailable)

    def test_hiscores_rate_limit_stops_before_other_api_is_called(self):
        session = Mock()
        session.get.return_value = response(429, {})
        result = players.check_player(session, "Player", refresh_player_data=True)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(result.status, "unavailable")
        self.assertIsInstance(result.error, players.RateLimited)

    def test_optional_player_data_rate_limit_keeps_core_stats_and_signals_stop(self):
        session = Mock()
        session.get.side_effect = [response(200, hiscores_data()), response(429, {})]
        result = players.check_player(session, "Player", refresh_player_data=True)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.row["TOTEXP"], 102537)
        self.assertIsInstance(result.error, players.RateLimited)


if __name__ == "__main__":
    unittest.main()
