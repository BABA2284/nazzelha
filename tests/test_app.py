import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

os.environ["APP_PASSWORD"] = "test-password-not-for-production"

import main


class NazzelhaAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_health_does_not_require_auth(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(response.json()["password_configured"])

    def test_auth_rejects_wrong_password(self):
        response = self.client.post("/api/auth", json={"password": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_auth_accepts_configured_password(self):
        response = self.client.post("/api/auth", json={"password": "test-password-not-for-production"})
        self.assertEqual(response.status_code, 200)

    def test_info_requires_password(self):
        response = self.client.post("/api/info", json={"url": "https://youtu.be/example"})
        self.assertEqual(response.status_code, 401)

    @patch("main.extract_info", side_effect=RuntimeError("Sign in to confirm you're not a bot"))
    def test_youtube_bot_check_explains_device_login_is_not_shared(self, _mock_extract):
        response = self.client.post(
            "/api/info",
            json={"url": "https://www.youtube.com/watch?v=example"},
            headers={"X-App-Password": "test-password-not-for-production"},
        )
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("لا ينقل جلسة حسابك", detail)
        self.assertIn("لا تدخل كلمة مرورك", detail)
        self.assertNotIn("Cookies", detail)

    def test_rejects_non_https(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("http://youtube.com/watch?v=example")

    def test_rejects_unlisted_domain(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("https://example.com/video")

    def test_rejects_ip_literal(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("https://127.0.0.1/video")

    def test_format_selector_audio_uses_audio_stream(self):
        self.assertEqual(main.format_selector("audio"), "bestaudio/best")

    def test_format_selector_quality_caps_height(self):
        self.assertIn("height<=720", main.format_selector("720"))

    def test_accepts_known_provider_host(self):
        self.assertEqual(main.validate_url("https://youtu.be/example"), "https://youtu.be/example")


if __name__ == "__main__":
    unittest.main()
