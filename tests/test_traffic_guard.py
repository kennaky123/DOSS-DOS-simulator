from __future__ import annotations

import unittest
from unittest.mock import patch

from common.protocol import decode_json
from server.client_handler import process_udp_datagram
from server.statistics import ServerStatistics
from server.traffic_guard import TrafficGuard


class TrafficGuardTests(unittest.TestCase):
    def test_normal_traffic_is_allowed(self) -> None:
        decision = TrafficGuard().check("TCP", "normal_01", "127.0.0.1")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.suspicious)
        self.assertIsNone(decision.reason)

    @patch("common.config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND", 1)
    @patch("common.config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND", 2)
    def test_client_limit_detects_then_blocks_one_identity(self) -> None:
        guard = TrafficGuard()
        first = guard.check("TCP", "same-client", "127.0.0.1")
        second = guard.check("TCP", "same-client", "127.0.0.1")
        third = guard.check("TCP", "same-client", "127.0.0.1")

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertTrue(second.suspicious)
        self.assertFalse(third.allowed)
        self.assertEqual(third.reason, "CLIENT_RATE_LIMIT")

    @patch("common.config.ALERT_SOURCE_REQUESTS_PER_SECOND", 1)
    @patch("common.config.MAX_SOURCE_REQUESTS_PER_SECOND", 2)
    def test_source_limit_cannot_be_bypassed_with_new_client_ids(self) -> None:
        guard = TrafficGuard()
        guard.check("UDP", "client-1", "192.168.1.5")
        guard.check("UDP", "client-2", "192.168.1.5")
        decision = guard.check("UDP", "client-3", "192.168.1.5")

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.suspicious)
        self.assertEqual(decision.reason, "SOURCE_RATE_LIMIT")

    @patch("common.config.MAX_SERVER_REQUESTS_PER_SECOND", 2)
    def test_global_limit_is_shared_across_protocols_and_sources(self) -> None:
        guard = TrafficGuard()
        guard.check("TCP", "one", "10.0.0.1")
        guard.check("UDP", "two", "10.0.0.2")
        decision = guard.check("TCP", "three", "10.0.0.3")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "GLOBAL_RATE_LIMIT")


class DefenseStatisticsTests(unittest.TestCase):
    def test_dashboard_snapshot_reports_defense_state(self) -> None:
        statistics = ServerStatistics()
        statistics.defense_observation(
            suspicious=True, blocked_reason="CLIENT_RATE_LIMIT"
        )

        snapshot = statistics.snapshot()
        self.assertEqual(snapshot.defense_status, "BLOCKING")
        self.assertEqual(snapshot.suspicious_requests, 1)
        self.assertEqual(snapshot.blocked_requests, 1)
        self.assertEqual(snapshot.last_block_reason, "CLIENT_RATE_LIMIT")

    @patch("common.config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND", 1)
    def test_malformed_udp_traffic_is_also_rate_limited(self) -> None:
        statistics = ServerStatistics()
        guard = TrafficGuard()
        address = ("127.0.0.1", 12345)

        first = process_udp_datagram(b"not-json", address, statistics, guard)
        second = process_udp_datagram(b"still-not-json", address, statistics, guard)

        self.assertEqual(decode_json(first.response_data)["error"], "INVALID_REQUEST")
        self.assertEqual(decode_json(second.response_data)["error"], "CLIENT_RATE_LIMIT")
        self.assertEqual(statistics.snapshot().blocked_requests, 1)


if __name__ == "__main__":
    unittest.main()
