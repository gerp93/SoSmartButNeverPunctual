# SoSmartButNeverPunctual

Single-file Python script (`punctual.py`, stdlib only, Python 3.9+) that keeps
Samsung appliance clocks in sync via the SmartThings API. See `README.md`.

This repo follows [KVG_Standards](https://github.com/gerp93/KVG_Standards)
(AGPL-3.0 `LICENSE`, `TODO.md`, branch + PR for every change, merged without
asking per the owner's global rules). It's a headless script, so the desktop-app
categories (theming, installer, update-check, logo) don't apply.

- Tests: `python -m unittest discover -s tests -v`
- Keep it dependency-free and public-friendly: nothing specific to the owner's
  devices, network or accounts goes in the repo. Tokens live outside the repo.
- Refresh tokens are single-use: save new tokens immediately after any refresh,
  and keep runs serialized by the lock.
- The clock command (`/configuration/vs/0`, `x.com.samsung.da.currentTime`) is
  undocumented; treat any change in its behavior as a Samsung-side break.
