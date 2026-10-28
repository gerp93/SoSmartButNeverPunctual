# TODO

This app's own backlog of future features and fixes — not a KVG_Standards
compliance checklist (see [KVG_Standards](https://github.com/gerp93/KVG_Standards)
and this repo's `REPO_SCOPE.md` entry for that). Just what's not built yet.

## Features

- Window: a paste box for non-localhost redirect URIs (the terminal already has one).
- Window and script: a "last run failed" notice when the scheduled task could not log in.
- Add a CI workflow that runs the unit tests on push/PR (Python, Windows + Linux).
  KVG_Standards has no shared Python CI yet, so this needs its design approved first;
  until then nothing runs the tests automatically.
- Decided (owner): the Windows installer stays `perMachine` (Program Files, installed as
  admin). The in-app update button rewrites the installed exe, so it only works when the app
  is started as administrator; otherwise update by running the new `Setup.exe`. If this
  becomes annoying, `release-python-gui.yml` in KVG_Standards would need to expose the
  installer action's `install_scope: perUser`.
- Pin `kvg-updater` to a KVG_Standards tag once one exists (currently `@main`, the
  documented interim exception).
- The logo (`assets/logo.png`, drawn by `scripts/draw_logo.py`) is a placeholder; replace
  it with any square image and run `scripts/generate_icons.py`.
- Optional `--timezone` (IANA name) instead of only local/UTC.
- Optional notification when a run fails (webhook or email), beyond the exit code.
- Fetch the device list through the refreshed token once in `sync` and
  warn when a selected device has disappeared from the account.

## Fixes

-

## Needs real-world verification

- The `.deb` end to end on a real Debian server (CI verifies build, install, version and removal on an Ubuntu
  runner): install it, run `setup` with the SSH-forwarded sign-in, `schedule`, and a scheduled run.
- The first real release build: PyInstaller exe on all three OSes, the Windows `Setup.exe`,
  the update check against a published release, and the scheduled task running the packaged
  exe (`SoSmartButNeverPunctual.exe sync`) with no window.
- `setup --token` / the window's Connect shortcut: it calls `POST /apps` and
  `POST /apps/{id}/oauth/generate` with a personal access token. Unconfirmed that
  SmartThings allows this and which token permissions it needs.
- The window on Linux/macOS, and `schedule` through a real crontab (tested only
  with mocks; the Windows Task Scheduler path was checked on a real machine).
- Whether SmartThings accepts an `http://localhost:<port>/...` redirect URI for
  an OAuth-In app, and whether the `https://` + paste fallback works end to end.
- The full OAuth flow against a real account (`setup`, a refresh after
  the access token expires, and a refresh token surviving ~weeks of daily runs).
  The code is unit-tested against mocked responses only.
- Whether the range clock still blinks after a script sync, versus after the app's
  manual **Sync Time** (maybe the app sends something extra).
- Fridges and dishwashers: do they have a clock the command can set?
