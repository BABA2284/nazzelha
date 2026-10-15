import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi.testclient import TestClient

import main


class NazzelhaAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_health_does_not_require_auth(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["authentication"], "not_required")

    def test_shared_password_login_is_disabled(self):
        response = self.client.post("/api/auth", json={"password": "anything"})
        self.assertEqual(response.status_code, 410)

    @patch("main.extract_info", return_value={"title": "Public video", "thumbnail": "", "duration": 30, "uploader": "Channel", "platform": "YouTube"})
    def test_public_video_info_does_not_require_account_login(self, _mock_extract):
        response = self.client.post("/api/info", json={"url": "https://youtu.be/example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Public video")

    @patch("main.public_user", return_value={"id": "user-1", "name": "Test User", "provider": "google"})
    @patch("main.extract_info", side_effect=RuntimeError("Sign in to confirm you are not a bot"))
    def test_authenticated_user_gets_youtube_explanation(self, _mock_extract, _mock_user):
        response = self.client.post("/api/info", json={"url": "https://www.youtube.com/watch?v=example"})
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("لا تدخل كلمة مرورك", detail)
    def test_auth_providers_reports_social_providers_and_no_password(self):
        response = self.client.get("/api/auth/providers")
        self.assertEqual(response.status_code, 200)
        providers = response.json()["providers"]
        self.assertEqual(set(providers), {"google", "facebook", "tiktok"})
        self.assertFalse(response.json()["password"])
        self.assertFalse(response.json()["registration_required"])

    def test_me_starts_unauthenticated(self):
        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["authenticated"])

    def test_old_session_without_version_is_rejected(self):
        old_request = SimpleNamespace(session={"user": {"id": "old-user", "name": "Old session", "provider": "google"}})
        self.assertIsNone(main.public_user(old_request))
        with self.assertRaises(main.HTTPException):
            main.require_auth(old_request)
    def test_social_login_route_does_not_fake_success_when_unconfigured(self):
        response = self.client.get("/auth/google")
        self.assertEqual(response.status_code, 503)

    def test_rejects_non_https(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("http://youtube.com/watch?v=example")

    def test_rejects_unlisted_domain(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("https://example.com/video")

    def test_rejects_ip_literal(self):
        with self.assertRaises(main.HTTPException):
            main.validate_url("https://127.0.0.1/video")

    def test_youtube_uses_mweb_client_and_local_po_token_provider(self):
        options = main.ydl_base_options()
        self.assertEqual(options["extractor_args"]["youtube"]["player_client"], ["mweb"])
        self.assertEqual(
            options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://127.0.0.1:4416"],
        )
        self.assertTrue(options["verbose"])
        self.assertFalse(options["quiet"])

    @patch("main.extract_info", side_effect=RuntimeError("HTTP Error 429: Too Many Requests"))
    def test_youtube_429_has_clear_rate_limit_message(self, _mock_extract):
        response = self.client.post("/api/info", json={"url": "https://www.youtube.com/watch?v=example"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("فرض حدًا مؤقتًا", response.json()["detail"])

    def test_youtube_throttle_configuration_is_conservative(self):
        self.assertGreaterEqual(main.YOUTUBE_MIN_REQUEST_INTERVAL, 5)
        self.assertGreaterEqual(main.YOUTUBE_COOLDOWN_SECONDS, 60)

    def test_format_selector_audio_uses_audio_stream(self):
        self.assertEqual(main.format_selector("audio"), "bestaudio/best")

    def test_format_selector_quality_caps_height(self):
        self.assertIn("height<=720", main.format_selector("720"))

    def test_accepts_known_provider_host(self):
        self.assertEqual(main.validate_url("https://youtu.be/example"), "https://youtu.be/example")


if __name__ == "__main__":
    unittest.main()
