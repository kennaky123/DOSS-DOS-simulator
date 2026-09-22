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


if __name__ == "__main__":
    unittest.main()
