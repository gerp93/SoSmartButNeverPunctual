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
        self.assertEqual((method, url), ("POST", punctual.TOKEN_URLS_REFRESH[0]))
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

    def test_token_request_tries_next_address_and_reports_which_worked(self):
        replies = [(401, {}), (404, {}), (200, token_payload())]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake:
            payload, url = punctual.token_request("cid", "sec", {"grant_type": "x"}, punctual.TOKEN_URLS_EXCHANGE)
        self.assertEqual(url, punctual.TOKEN_URLS_EXCHANGE[2])
        self.assertEqual([c[0][1] for c in fake.call_args_list], list(punctual.TOKEN_URLS_EXCHANGE))

    def test_token_request_stops_on_400_so_a_used_code_is_not_retried(self):
        with mock.patch.object(punctual, "http", return_value=(400, {"error": "invalid_grant"})) as fake:
            with self.assertRaises(punctual.AuthRequired):
                punctual.token_request("cid", "sec", {}, punctual.TOKEN_URLS_EXCHANGE)
        self.assertEqual(fake.call_count, 1)

    def test_working_address_is_remembered_and_tried_first_next_time(self):
        state = self.logged_in_state()
        state["token_url"] = punctual.TOKEN_URLS_REFRESH[2]
        with mock.patch.object(punctual, "http", return_value=(200, token_payload())) as fake:
            punctual.refresh(self.store, state)
        self.assertEqual(fake.call_args[0][1], punctual.TOKEN_URLS_REFRESH[2])
        self.assertEqual(self.store.load_state()["token_url"], punctual.TOKEN_URLS_REFRESH[2])

    def test_sign_in_page_uses_the_oauthin_host(self):
        self.assertTrue(punctual.AUTHORIZE_URL.startswith("https://oauthin-regional.api.smartthings.com/"))

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


class RunSyncTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.logged_in_state(expires_in=punctual.REFRESH_MARGIN_SECONDS + 3600)
        self.store.save_config({"devices": [{"id": "d1", "name": "Range"}]})

    def test_success_is_recorded(self):
        ok = (200, {"results": [{"status": "COMPLETED"}]})
        with mock.patch.object(punctual, "http", return_value=ok), redirect_stdout(io.StringIO()):
            succeeded, failed = punctual.run_sync(self.store)
        self.assertEqual((succeeded, failed), (["Range"], []))
        last = self.store.load_config()["last_sync"]
        self.assertTrue(last["ok"])
        self.assertIn("Range", last["summary"])
        self.assertEqual(self.store.load_config()["devices"], [{"id": "d1", "name": "Range"}])  # config kept

    def test_device_failure_is_recorded(self):
        with mock.patch.object(punctual, "http", return_value=(409, {"error": "offline"})), \
                redirect_stdout(io.StringIO()):
            succeeded, failed = punctual.run_sync(self.store)
        self.assertEqual(failed, ["Range"])
        self.assertFalse(self.store.load_config()["last_sync"]["ok"])

    def test_cannot_start_is_recorded_and_raised(self):
        self.store.save_config({})
        with self.assertRaises(punctual.PunctualError):
            punctual.run_sync(self.store)
        self.assertFalse(self.store.load_config()["last_sync"]["ok"])

    def test_dry_run_records_nothing(self):
        with redirect_stdout(io.StringIO()):
            punctual.run_sync(self.store, dry_run=True)
        self.assertNotIn("last_sync", self.store.load_config())


NO_APPS = (200, {"items": []})


class CreateAppTests(unittest.TestCase):
    def test_reads_credentials_from_the_create_reply(self):
        replies = [NO_APPS, (200, {"app": {"appId": "app-1"}, "oauthClientId": "cid", "oauthClientSecret": "sec"})]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake:
            self.assertEqual(punctual.create_oauth_app(" token "), ("cid", "sec"))
        (method, url), kwargs = fake.call_args
        self.assertEqual((method, url), ("POST", f"{punctual.API_URL}/apps"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        body = json.loads(kwargs["body"])
        self.assertEqual(body["oauth"]["scope"], ["r:devices:*", "x:devices:*"])
        self.assertEqual(body["oauth"]["redirectUris"], [punctual.DEFAULT_REDIRECT_URI])

    def test_reuses_the_existing_app_instead_of_creating_another(self):
        ours = {"appId": "app-9", "displayName": punctual.APP_DISPLAY_NAME, "appType": "API_ONLY"}
        other = {"appId": "app-2", "displayName": "Something else", "appType": "API_ONLY"}
        replies = [(200, {"items": [other, ours]}), (200, {"oauthClientId": "cid", "oauthClientSecret": "sec"})]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake:
            self.assertEqual(punctual.create_oauth_app("token"), ("cid", "sec"))
        self.assertEqual(fake.call_args[0], ("POST", f"{punctual.API_URL}/apps/app-9/oauth/generate"))
        self.assertEqual(fake.call_count, 2)  # list + generate; never POST /apps

    def test_existing_app_that_cannot_issue_credentials_is_an_error_not_a_second_app(self):
        ours = {"appId": "app-9", "displayName": punctual.APP_DISPLAY_NAME, "appType": "API_ONLY"}
        with mock.patch.object(punctual, "http", side_effect=[(200, {"items": [ours]}), (500, {})]) as fake:
            with self.assertRaises(punctual.PunctualError):
                punctual.create_oauth_app("token")
        self.assertEqual(fake.call_count, 2)

    def test_asks_separately_when_the_reply_has_no_credentials(self):
        replies = [NO_APPS, (200, {"app": {"appId": "app-1"}}),
                   (200, {"oauthClientId": "cid", "oauthClientSecret": "sec"})]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake:
            self.assertEqual(punctual.create_oauth_app("token"), ("cid", "sec"))
        self.assertEqual(fake.call_args[0], ("POST", f"{punctual.API_URL}/apps/app-1/oauth/generate"))

    def test_flat_reply_shape_is_also_accepted(self):
        replies = [NO_APPS, (200, {"appId": "app-1", "oauthClientId": "cid", "oauthClientSecret": "sec"})]
        with mock.patch.object(punctual, "http", side_effect=replies):
            self.assertEqual(punctual.create_oauth_app("token"), ("cid", "sec"))

    def test_rejected_token_explains(self):
        with mock.patch.object(punctual, "http", return_value=(403, {"error": "forbidden"})):
            with self.assertRaises(punctual.PunctualError) as ctx:
                punctual.create_oauth_app("token")
        self.assertIn("403", str(ctx.exception))
        self.assertIn("token", str(ctx.exception))

    def test_unexpected_reply_is_not_blamed_on_the_token(self):
        with mock.patch.object(punctual, "http", side_effect=[NO_APPS, (200, {"surprise": True})]):
            with self.assertRaises(punctual.PunctualError) as ctx:
                punctual.create_oauth_app("token")
        self.assertNotIn("did not accept that token", str(ctx.exception))

    def test_half_made_app_is_removed_when_credentials_are_missing(self):
        replies = [NO_APPS, (200, {"app": {"appId": "app-1"}}), (200, {}), (200, {})]
        with mock.patch.object(punctual, "http", side_effect=replies) as fake:
            with self.assertRaises(punctual.PunctualError):
                punctual.create_oauth_app("token")
        self.assertEqual(fake.call_args[0], ("DELETE", f"{punctual.API_URL}/apps/app-1"))


class AuthorizeTests(StoreTestCase):
    def test_non_localhost_redirect_needs_paste_helper(self):
        with self.assertRaises(punctual.PunctualError):
            punctual.authorize(self.store, "cid", "sec", "https://example.com/cb")

    def test_paste_flow_saves_tokens(self):
        with mock.patch.object(punctual, "http", return_value=(200, token_payload())), \
                mock.patch.object(punctual.webbrowser, "open"):
            punctual.authorize(self.store, "cid", "sec", "https://example.com/cb",
                               ask_pasted=lambda prompt: "https://example.com/cb?code=abc", notify=lambda m: None)
        self.assertEqual(self.store.load_state()["refresh_token"], "new-refresh")

    def test_requires_all_values(self):
        with self.assertRaises(punctual.PunctualError):
            punctual.authorize(self.store, "", "sec", "http://localhost:8123/callback")


class ScheduleTests(StoreTestCase):
    def test_parse_time(self):
        self.assertEqual(punctual.parse_time("03:17"), (3, 17))
        self.assertEqual(punctual.parse_time(" 3:05 "), (3, 5))
        for bad in ("", "25:00", "12:60", "noon", "3"):
            with self.assertRaises(punctual.PunctualError):
                punctual.parse_time(bad)

    def test_command_line_runs_this_script_with_sync(self):
        command = punctual.sync_command_line(self.store, default_dir=True, jitter=20)
        self.assertTrue(command[1].endswith("punctual.py"))
        self.assertEqual(command[-3:], ["sync", "--jitter", "20"])
        custom = punctual.sync_command_line(self.store, default_dir=False, jitter=0)
        self.assertIn("--config-dir", custom)
        self.assertNotIn("--jitter", custom)  # no random wait unless asked for

    def test_cron_install_replaces_old_entry_and_remove_cleans_up(self):
        existing = "MAILTO=me\n0 1 * * * /usr/bin/other\n5 5 * * * old " + punctual.CRON_MARKER + "\n"
        written = {}

        def fake_run(command, input=None, **kwargs):
            written["input"] = input
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(punctual.sys, "platform", "linux"), \
                mock.patch.object(punctual.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(punctual, "_run", return_value=mock.Mock(returncode=0, stdout=existing)), \
                mock.patch.object(punctual.subprocess, "run", side_effect=fake_run):
            punctual.schedule_install(self.store, "04:30", 10)
            lines = written["input"].splitlines()
            self.assertEqual(sum(punctual.CRON_MARKER in ln for ln in lines), 1)
            mine = [ln for ln in lines if punctual.CRON_MARKER in ln][0]
            self.assertTrue(mine.startswith("30 4 * * * "))
            self.assertIn("/usr/bin/other", written["input"])  # other jobs untouched
            self.assertIn("MAILTO=me", written["input"])
            self.assertEqual(self.store.load_config()["schedule"]["time"], "04:30")

            written.clear()
            punctual._run.return_value = mock.Mock(returncode=0, stdout="\n".join(lines) + "\n")
            punctual.schedule_remove(self.store)
            self.assertNotIn(punctual.CRON_MARKER, written["input"])
            self.assertNotIn("schedule", self.store.load_config())

    def test_cron_missing_is_a_clear_error(self):
        with mock.patch.object(punctual.sys, "platform", "linux"), \
                mock.patch.object(punctual.shutil, "which", return_value=None):
            with self.assertRaises(punctual.PunctualError):
                punctual.schedule_install(self.store, "03:17")


class PackagedAppTests(StoreTestCase):
    def test_frozen_build_schedules_the_exe_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:\\Apps\\SoSmartButNeverPunctual.exe"):
            command = punctual.sync_command_line(self.store, default_dir=True, jitter=20)
        self.assertEqual(command, ["C:\\Apps\\SoSmartButNeverPunctual.exe", "sync", "--jitter", "20"])

    def test_packaged_exe_with_arguments_runs_the_engine_not_the_window(self):
        try:
            import punctual_gui
        except SystemExit:
            self.skipTest("Tkinter not available")
        with mock.patch.object(sys, "argv", ["app", "status"]), \
                mock.patch.dict(os.environ, {"SSBNP_CONFIG_DIR": str(self.store.dir)}), \
                mock.patch.object(punctual_gui.tk, "Tk", side_effect=AssertionError("window opened")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(punctual_gui.main(), 2)  # 2 = not set up yet, and no window was created


class ThemingTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            import theming
            self.root = tk.Tk()
        except (ImportError, SystemExit, Exception) as exc:  # no Tk, or no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.theming = theming
        self.addCleanup(self.root.destroy)

    def test_every_theme_applies_and_system_default_restores(self):
        if not self.theming.THEMES:
            self.skipTest("visual-assault-tkinter not installed")
        self.theming.capture_defaults(self.root)
        for theme_id in self.theming.THEMES:
            self.theming.apply_theme(self.root, theme_id)
            self.assertEqual(self.theming.background(), self.theming.THEMES[theme_id]["background"])
        self.theming.apply_theme(self.root, None)
        self.assertEqual(self.theming.background(), self.root.cget("background"))

    def test_default_theme_exists_when_package_installed(self):
        if self.theming.THEMES:
            self.assertIn(self.theming.DEFAULT_THEME_ID, self.theming.THEMES)


class WindowTests(StoreTestCase):
    """Regression: a failing background step must show its error and keep the window alive."""

    def setUp(self):
        super().setUp()
        try:
            import tkinter as tk
            import punctual_gui
            self.root = tk.Tk()
        except (ImportError, SystemExit, Exception) as exc:  # no Tk, or no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.gui = punctual_gui
        self.addCleanup(self.root.destroy)
        env = mock.patch.dict(os.environ, {"SSBNP_CONFIG_DIR": str(self.store.dir)})
        env.start()
        self.addCleanup(env.stop)
        for name in ("open_connect", "check_for_updates"):  # no first-run dialog, no network
            patcher = mock.patch.object(punctual_gui.App, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        fast = mock.patch.object(punctual_gui.App, "LOG_POLL_MS", 50)
        fast.start()
        self.addCleanup(fast.stop)

    def spin(self, seconds=1.0):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def test_failed_step_shows_error_and_message_loop_survives(self):
        shown = []
        with mock.patch.object(self.gui.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)), \
                redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            app.background(lambda: (_ for _ in ()).throw(punctual.PunctualError("token was rejected")))
            self.spin()
            self.assertEqual(shown, ["token was rejected"])
            self.assertFalse(app.busy)
            self.assertIn("token was rejected", app.log_view.get("1.0", "end"))
            # the loop must still deliver later messages
            punctual.log("still alive")
            self.spin(0.5)
            self.assertIn("still alive", app.log_view.get("1.0", "end"))

    def test_schedule_buttons_follow_whether_it_is_on(self):
        with redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            with mock.patch.object(punctual, "schedule_exists", return_value=False):
                app.refresh_state()
                self.spin(0.2)
                self.assertEqual(app.schedule_button.cget("text"), "Turn on")
                self.assertEqual(app.schedule_off_button.winfo_manager(), "")  # not shown

            config = self.store.load_config()
            config["schedule"] = {"time": "03:17"}
            self.store.save_config(config)
            with mock.patch.object(punctual, "schedule_exists", return_value=True):
                app.refresh_state()
                self.spin(0.2)
                self.assertEqual(app.schedule_button.cget("text"), "Change time")
                self.assertEqual(app.schedule_off_button.winfo_manager(), "pack")  # shown

            with mock.patch.object(punctual, "schedule_exists", return_value=False):
                app.refresh_state()
                self.spin(0.2)
                self.assertEqual(app.schedule_button.cget("text"), "Turn on")
                self.assertEqual(app.schedule_off_button.winfo_manager(), "")  # hidden again

    def test_window_picks_up_a_run_made_by_the_daily_task_without_any_click(self):
        with mock.patch.object(self.gui.App, "REFRESH_MS", 100), redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            self.spin(0.3)
            self.assertIn("Not synced yet", app.last_sync_text.get())
            # the scheduled task runs outside the window and records its result in the settings file
            config = self.store.load_config()
            config["last_sync"] = {"at": "2026-10-21 19:02:01", "ok": True, "summary": "set 19:02: Range, Microwave"}
            self.store.save_config(config)
            self.spin(0.6)
        self.assertIn("19:02:01", app.last_sync_text.get())

    def test_refresh_does_not_overwrite_a_time_being_typed(self):
        with mock.patch.object(self.gui.App, "REFRESH_MS", 100), redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            config = self.store.load_config()
            config["schedule"] = {"time": "03:17"}
            self.store.save_config(config)
            app.time_entry.focus_force()
            app.time_var.set("21:45")  # the user is editing
            with mock.patch.object(app.root, "focus_get", return_value=app.time_entry):
                self.spin(0.5)
            self.assertEqual(app.time_var.get(), "21:45")

    def test_log_box_shows_lines_written_by_another_process(self):
        log_file = self.store.dir / "punctual.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("2026-10-21 19:00:00  earlier run: ok\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            self.spin(0.3)
            self.assertIn("earlier run: ok", app.log_view.get("1.0", "end"))  # shown when the window opens
            # the daily task is a separate program that appends to the same file
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write("2026-10-21 19:12:01  Range: ok (COMPLETED)\n")
                handle.write("2026-10-21 19:12:01  Microwave: ok (COMPLETED)\n")
            self.spin(0.5)
        text = app.log_view.get("1.0", "end")
        self.assertIn("Range: ok (COMPLETED)", text)
        self.assertIn("Microwave: ok (COMPLETED)", text)
        self.assertEqual(text.count("earlier run: ok"), 1)  # no duplicates

    def test_half_written_line_waits_for_its_newline(self):
        log_file = self.store.dir / "punctual.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            self.spin(0.2)
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write("2026-10-21 19:12:01  Range: o")
            self.spin(0.3)
            self.assertNotIn("Range: o", app.log_view.get("1.0", "end"))
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write("k\n")
            self.spin(0.3)
        self.assertIn("Range: ok", app.log_view.get("1.0", "end"))

    def test_bad_callback_does_not_kill_the_loop(self):
        with redirect_stdout(io.StringIO()):
            app = self.gui.App(self.root)
            app.events.put(("call", lambda: 1 / 0))
            self.spin(0.5)
            punctual.log("after the crash")
            self.spin(0.5)
        self.assertIn("after the crash", app.log_view.get("1.0", "end"))


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
