import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import punctual  # noqa: E402


def token_payload(access="new-access", refresh="new-refresh", expires_in=86399):
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = punctual.Store(Path(self._tmp.name) / "cfg")

    def logged_in_state(self, expires_in=3600):
        state = {
            "client_id": "cid",
            "client_secret": "secret",
            "redirect_uri": "http://localhost:8123/callback",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": time.time() + expires_in,
        }
        self.store.save_state(state)
        return state


class StorageTests(StoreTestCase):
    def test_state_round_trip(self):
        state = {"client_id": "abc", "refresh_token": "r1"}
        self.store.save_state(state)
        self.assertEqual(self.store.load_state(), state)

    def test_state_is_not_plain_json_on_windows(self):
        self.store.save_state({"refresh_token": "plainly-visible"})
        raw = self.store.state_path.read_bytes()
        if sys.platform == "win32":
            self.assertTrue(raw.startswith(punctual.DPAPI_PREFIX))
            self.assertNotIn(b"plainly-visible", raw)
        else:
            self.assertEqual(oct(os.stat(self.store.state_path).st_mode & 0o777), "0o600")

    def test_missing_state_is_none(self):
        self.assertIsNone(self.store.load_state())

    def test_lock_blocks_second_holder(self):
        with self.store.lock():
            with self.assertRaises(punctual.PunctualError):
                with self.store.lock():
                    pass
        with self.store.lock():  # released afterwards
            pass

    def test_stale_lock_is_replaced(self):
        self.store.dir.mkdir(parents=True)
        lock = self.store.dir / "lock"
        lock.write_text("")
        old = time.time() - punctual.LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        with self.store.lock():
            pass


class TokenTests(StoreTestCase):
    def test_refresh_sends_basic_auth_and_saves_both_tokens(self):
        state = self.logged_in_state()
        with mock.patch.object(punctual, "http", return_value=(200, token_payload())) as fake:
            punctual.refresh(self.store, state)
        (method, url), kwargs = fake.call_args
        self.assertEqual((method, url), ("POST", punctual.TOKEN_URL))
        expected = base64.b64encode(b"cid:secret").decode()
        self.assertEqual(kwargs["headers"]["Authorization"], f"Basic {expected}")
        form = urllib.parse.parse_qs(kwargs["body"].decode())
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertEqual(form["refresh_token"], ["old-refresh"])
        saved = self.store.load_state()
        self.assertEqual(saved["access_token"], "new-access")
        self.assertEqual(saved["refresh_token"], "new-refresh")

    def test_rejected_refresh_requires_setup(self):
        state = self.logged_in_state()
        with mock.patch.object(punctual, "http", return_value=(400, {"error": "invalid_grant"})):
            with self.assertRaises(punctual.AuthRequired):
                punctual.refresh(self.store, state)

    def test_not_set_up_requires_setup(self):
        with self.assertRaises(punctual.AuthRequired):
            punctual.load_logged_in(self.store)

    def test_fresh_token_is_not_refreshed(self):
        self.logged_in_state(expires_in=punctual.REFRESH_MARGIN_SECONDS + 3600)
        with mock.patch.object(punctual, "http") as fake:
            state = punctual.load_logged_in(self.store)
        fake.assert_not_called()
        self.assertEqual(state["access_token"], "old-access")

    def test_token_near_expiry_is_refreshed(self):
        self.logged_in_state(expires_in=60)
        with mock.patch.object(punctual, "http", return_value=(200, token_payload())):
            state = punctual.load_logged_in(self.store)
        self.assertEqual(state["access_token"], "new-access")

    def test_api_refreshes_once_on_401_then_retries(self):
        state = self.logged_in_state()
        responses = [(401, {}), (200, token_payload(access="fresh")), (200, {"ok": True})]
        with mock.patch.object(punctual, "http", side_effect=responses) as fake:
            status, data = punctual.api(self.store, state, "GET", "/devices")
        self.assertEqual((status, data), (200, {"ok": True}))
        self.assertEqual(fake.call_count, 3)
        self.assertEqual(fake.call_args[1]["headers"]["Authorization"], "Bearer fresh")


class ClockTests(StoreTestCase):
    def test_timestamp_format(self):
        stamp = punctual.clock_timestamp(use_utc=True)
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_command_shape(self):
        command = punctual.clock_command("2026-10-20T21:40:23")["commands"][0]
        self.assertEqual(command["capability"], "execute")
        self.assertEqual(command["arguments"][0], "/configuration/vs/0")
        self.assertEqual(command["arguments"][1], {"x.com.samsung.da.currentTime": "2026-10-20T21:40:23"})

    def test_set_clock_reports_status(self):
        state = self.logged_in_state()
        with mock.patch.object(punctual, "http", return_value=(200, {"results": [{"status": "COMPLETED"}]})):
            self.assertEqual(punctual.set_clock(self.store, state, "dev", "t"), (True, "COMPLETED"))
        with mock.patch.object(punctual, "http", return_value=(200, {"results": [{"status": "FAILED"}]})):
            self.assertEqual(punctual.set_clock(self.store, state, "dev", "t"), (False, "FAILED"))
        with mock.patch.object(punctual, "http", return_value=(409, {"error": {"code": "Conflict"}})):
            ok, detail = punctual.set_clock(self.store, state, "dev", "t")
        self.assertFalse(ok)
        self.assertIn("409", detail)

    def _sync_args(self, **overrides):
        args = punctual.build_parser().parse_args(["sync"])
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_sync_dry_run_sends_nothing(self):
        self.logged_in_state(expires_in=punctual.REFRESH_MARGIN_SECONDS + 3600)
        self.store.save_config({"devices": [{"id": "d1", "name": "Range"}]})
        with mock.patch.object(punctual, "http") as fake, redirect_stdout(io.StringIO()) as out:
            code = punctual.cmd_sync(self.store, self._sync_args(dry_run=True))
        self.assertEqual(code, 0)
        fake.assert_not_called()
        self.assertIn("Range", out.getvalue())

    def test_sync_reports_failure_but_tries_every_device(self):
        self.logged_in_state(expires_in=punctual.REFRESH_MARGIN_SECONDS + 3600)
        self.store.save_config({"devices": [{"id": "d1", "name": "Range"}, {"id": "d2", "name": "Microwave"}]})
        replies = [(409, {"error": "offline"}), (200, {"results": [{"status": "COMPLETED"}]})]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake, redirect_stdout(io.StringIO()):
            code = punctual.cmd_sync(self.store, self._sync_args())
        self.assertEqual(code, 1)
        self.assertEqual(fake.call_count, 2)

    def test_sync_without_devices_explains_how(self):
        self.logged_in_state(expires_in=punctual.REFRESH_MARGIN_SECONDS + 3600)
        with self.assertRaises(punctual.PunctualError):
            punctual.cmd_sync(self.store, self._sync_args())


class ParsingTests(unittest.TestCase):
    def test_parse_full_url(self):
        code, state, error = punctual.parse_redirect("https://x.example/cb?code=abc123&state=s1")
        self.assertEqual((code, state, error), ("abc123", "s1", None))

    def test_parse_bare_code(self):
        self.assertEqual(punctual.parse_redirect("  abc123 "), ("abc123", None, None))

    def test_parse_denied(self):
        _, _, error = punctual.parse_redirect("https://x.example/cb?error=access_denied&state=s1")
        self.assertEqual(error, "access_denied")

    def test_loopback_detection(self):
        self.assertTrue(punctual.is_loopback_redirect("http://localhost:8123/callback"))
        self.assertTrue(punctual.is_loopback_redirect("http://127.0.0.1:8123/"))
        self.assertFalse(punctual.is_loopback_redirect("https://localhost:8123/callback"))
        self.assertFalse(punctual.is_loopback_redirect("http://localhost/callback"))
        self.assertFalse(punctual.is_loopback_redirect("https://example.com/callback"))


if __name__ == "__main__":
    unittest.main()
