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

    def test_flash_crowd_surge_is_allowed_above_normal_source_limit(self) -> None:
        guard = TrafficGuard()
        paths = ("/home", "/products", "/search", "/detail", "/cart", "/checkout")
        last_decision = None
        # 30 người dùng thật cùng truy cập qua 1 IP (30 * 9 = 270 req/s > 250 limit thường)
        for step in range(9):
            for user_idx in range(1, 31):
                path = paths[(user_idx + step) % len(paths)]
                last_decision = guard.check(
                    "TCP", f"crowd_user_{user_idx:02d}", "127.0.0.1", path=path
                )
                self.assertTrue(last_decision.allowed)

        assert last_decision is not None
        self.assertFalse(last_decision.suspicious)
        self.assertEqual(last_decision.traffic_pattern, "FLASH_CROWD")
        self.assertGreaterEqual(last_decision.trust_score, 90)

    def test_ddos_bot_flood_is_detected_while_human_on_same_ip_is_allowed(self) -> None:
        guard = TrafficGuard()
        # Người dùng hợp lệ đã duyệt qua 2 trang trên cùng IP
        guard.check("TCP", "real_user_01", "127.0.0.1", path="/home")
        guard.check("TCP", "real_user_01", "127.0.0.1", path="/products")

        # 20 bot DDoS cùng bắn vào 1 endpoint /login vượt ngưỡng DDOS_BOT_SOURCE_RPS_THRESHOLD (80)
        bot_decision = None
        for _ in range(5):
            for bot_idx in range(1, 21):
                bot_decision = guard.check(
                    "TCP", f"ddos_client_{bot_idx:02d}", "127.0.0.1", path="/login"
                )

        assert bot_decision is not None
        self.assertFalse(bot_decision.allowed)
        self.assertEqual(bot_decision.reason, "DDOS_BOT_DETECTED")
        self.assertEqual(bot_decision.traffic_pattern, "DDOS_ATTACK")

        # Ngay giữa lúc DDoS đang bị chặn trên IP 127.0.0.1, người dùng thật vẫn truy cập được!
        human_decision = guard.check("TCP", "real_user_01", "127.0.0.1", path="/cart")
        self.assertTrue(human_decision.allowed)
        self.assertIsNone(human_decision.reason)

    @patch("common.config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND", 1)
    @patch("common.config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND", 2)
    def test_disabling_defense_does_not_block_at_gate_but_triggers_overload_delay(self) -> None:
        guard = TrafficGuard(enabled=False)
        guard.check("TCP", "dos_source_01", "127.0.0.1")
        guard.check("TCP", "dos_source_01", "127.0.0.1")
        third = guard.check("TCP", "dos_source_01", "127.0.0.1")

        self.assertTrue(third.allowed)
        self.assertGreater(third.overload_delay_seconds, 0.0)
        self.assertEqual(third.traffic_pattern, "DOS_ATTACK")

    def test_udp_spoofing_flood_is_detected_and_blocked(self) -> None:
        guard = TrafficGuard()
        decision = None
        for idx in range(25):
            spoofed_ip = f"10.0.{idx // 10 + 1}.{idx % 250 + 1}"
            decision = guard.check(
                "UDP",
                f"spoof_udp_{idx:04d}",
                spoofed_ip,
                path="/login",
                ip_mode="UDP_SPOOF",
            )
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "UDP_SPOOF_DETECTED")
        self.assertEqual(decision.traffic_pattern, "UDP_SPOOF_ATTACK")


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
