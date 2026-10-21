# TODO

This app's own backlog of future features and fixes — not a KVG_Standards
compliance checklist (see [KVG_Standards](https://github.com/gerp93/KVG_Standards)
and this repo's `REPO_SCOPE.md` entry for that). Just what's not built yet.

## Features

- Decide a release/CI shape for a plain script. None of KVG_Standards' categories
  covers a CLI utility, so this needs approval first (a tag-only release? a
  Python test workflow?). Until then there is no release pipeline or CI.
- Optional `--timezone` (IANA name) instead of only local/UTC.
- Optional notification when a run fails (webhook or email), beyond the exit code.
- Fetch the device list through the refreshed token once in `sync` and
  warn when a selected device has disappeared from the account.

## Fixes

-

## Needs real-world verification

- Whether SmartThings accepts an `http://localhost:<port>/...` redirect URI for
  an OAuth-In app, and whether the `https://` + paste fallback works end to end.
- The full OAuth flow against a real account (`setup`, a refresh after
  the access token expires, and a refresh token surviving ~weeks of daily runs).
  The code is unit-tested against mocked responses only.
- Whether the range clock still blinks after a script sync, versus after the app's
  manual **Sync Time** (maybe the app sends something extra).
- Fridges and dishwashers: do they have a clock the command can set?
