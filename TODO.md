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
- The Windows installer is `perMachine` (Program Files), but the self-updater rewrites
  its own exe, which an unelevated process can't do there. `release-python-gui.yml`
  doesn't expose the installer action's `install_scope: perUser` yet; that is a change
  to KVG_Standards. Until then, in-app updates on a Program Files install need admin.
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
