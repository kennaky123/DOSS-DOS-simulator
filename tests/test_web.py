from __future__ import annotations

import unittest

from web.app import STATIC_ROOT, WebController


class WebControllerTests(unittest.TestCase):
    def test_initial_status_is_idle_and_serializable(self) -> None:
        status = WebController().status()
        self.assertFalse(status["server"]["running"])
        self.assertFalse(status["simulation"]["running"])
        self.assertEqual(status["limits"]["clientRps"], 40)

    def test_public_target_is_rejected(self) -> None:
        controller = WebController()
        with self.assertRaisesRegex(ValueError, "localhost"):
            controller.start_simulation(
                {
                    "host": "8.8.8.8",
                    "port": 53,
                    "protocol": "UDP",
                    "mode": "NORMAL",
                    "clients": 1,
                    "rate": 1,
                    "duration": 1,
                }
            )

    def test_mode_hard_limit_is_enforced(self) -> None:
        controller = WebController()
        with self.assertRaisesRegex(ValueError, "1..5"):
            controller.start_simulation(
                {
                    "host": "127.0.0.1",
                    "port": 9000,
                    "protocol": "TCP",
                    "mode": "NORMAL",
                    "clients": 6,
                    "rate": 1,
                    "duration": 1,
                }
            )

    def test_frontend_assets_exist(self) -> None:
        for filename in ("index.html", "app.css", "app.js"):
            self.assertTrue((STATIC_ROOT / filename).is_file())

    def test_flash_crowd_mode_is_exposed_in_limits(self) -> None:
        status = WebController().status()
        self.assertIn("FLASH_CROWD", status["limits"]["modes"])

    def test_defense_can_be_toggled_via_controller(self) -> None:
        controller = WebController()
        self.assertTrue(controller.status()["server"]["defenseEnabled"])
        controller.set_defense({"enabled": False})
        self.assertFalse(controller.status()["server"]["defenseEnabled"])
        controller.set_defense({"enabled": True})
        self.assertTrue(controller.status()["server"]["defenseEnabled"])

    def test_live_service_portal_returns_catalog_when_server_online(self) -> None:
        controller = WebController()
        controller.start_server({"host": "127.0.0.1", "port": 19129, "protocol": "TCP"})
        try:
            res = controller.call_service(
                {"host": "127.0.0.1", "port": 19129, "path": "/products"}
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["httpStatus"], 200)
            self.assertIn("items", res["response"]["service"])
        finally:
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
