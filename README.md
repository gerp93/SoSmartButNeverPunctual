# If you're so smart, what time is it?

**SoSmartButNeverPunctual** keeps Samsung appliance clocks correct, automatically.

Samsung ranges, microwaves and other appliances stay on Wi-Fi 24/7, yet their
clocks drift, and after a power outage they come back wrong. The SmartThings
app has a **Sync Time** button for these appliances, but you have to open the
app and press it by hand every time. There is no routine, automation or
schedule that does it for you.

This script presses that button for you, on a schedule. It sends the same
"set the time" command through the SmartThings cloud API and keeps its own
login alive, so it can run unattended from cron, systemd or Windows Task
Scheduler.

> **Unofficial.** Not affiliated with or endorsed by Samsung or SmartThings.
> The clock command is **undocumented**; the SmartThings community found it and
> Samsung could change it in a firmware update. Try it on one appliance and
> look at its display before you schedule anything.

- Standard library only, **Python 3.9+**. No `pip install`.
- Works on Windows, macOS and Linux (a Raspberry Pi or NAS is ideal).
- Tokens are stored per-user, outside the repo, and encrypted with Windows
  DPAPI on Windows (owner-only file permissions elsewhere).

## Two ways to use it

| | Files to download | Needs |
|---|---|---|
| **Window** (point and click) | `punctual.py` **and** `punctual_gui.py` | Python 3.9+ with Tkinter |
| **Script only** (terminal, servers, cron) | just `punctual.py` | Python 3.9+ |

Both do the same thing and share the same saved login and settings, so you
can switch between them. `punctual.py` never needs the window file.

**Window:** run `python punctual_gui.py` (on Windows, double-click it). It has
four boxes: log in, tick your appliances, "Sync clocks now", and a daily schedule
switch. Tkinter comes with Python on Windows and macOS. On Linux install it first:

```
sudo apt install python3-tk        # Debian, Ubuntu, Raspberry Pi OS
sudo dnf install python3-tkinter   # Fedora
sudo pacman -S tk                  # Arch
```

The rest of this page describes the script; the window does the same steps with buttons.

## Does it work on my appliance?

Confirmed on a Samsung smart range (NE63A6751SS) and an over-the-range
microwave. The SmartThings community reports it working on ovens and ranges.
Whether it works on dishwashers, washers, dryers or fridges is untested; `select` marks
which devices accept the command, and you can only know for sure by trying it.
Success from the API doesn't prove the clock changed. Look at the display.

## Why OAuth and not a personal access token?

SmartThings personal access tokens now expire after about 24 hours, so they
can't run a daily job. SmartThings says [OAuth is the only supported method for
long-term use](https://developer.smartthings.com/docs/getting-started/quickstart).
You set up OAuth once (below). After that, every run swaps the stored refresh
token for a new access token and a new refresh token, and saves both. A refresh
token lapses after roughly 29-30 days without use, so a daily schedule keeps it
alive. If the machine is off for a month, run `setup` again.

## Setup

### 1. Create your own SmartThings OAuth-In app (once)

Each person uses their own app, so nobody shares credentials.

**Shortcut (experimental): let the script create it.** Create a personal access
token at <https://account.smartthings.com/tokens> (any name; tick the Apps and
Devices permissions), then use it once:

```
python punctual.py setup --token <the-token>
```

That creates the app, logs in and discards the token, so you can skip the rest of
this step and step 2. It relies on SmartThings' app-creation API behaving as
documented by the community. If it fails, use the manual route below. The window
version has the same shortcut on its Connect screen.

**Manual route:**

1. Install the [SmartThings CLI](https://developer.smartthings.com/docs/sdks/cli)
   and sign in (it opens a browser; use Chrome if another browser misbehaves).
2. Run `smartthings apps:create` and choose **OAuth-In App**.
3. Answer the prompts:
   - **Display name / description:** anything, e.g. "Clock sync".
   - **Scopes:** `r:devices:*` and `x:devices:*`.
   - **Redirect URI:** `http://localhost:8123/callback`. If SmartThings won't
     accept a localhost address, use any `https://` address you like, for example
     `https://example.com/callback`. The script has a paste mode for that
     (step 2).
   - **Target URL:** not used by this script; any `https://` URL is fine.
4. Copy the **Client ID** and **Client Secret** it prints. The secret is shown
   once. Treat it like a password.

### 2. Authorize the script (once)

```
python punctual.py setup
```

Enter the client ID, secret (hidden) and the redirect URI from step 1. Your
browser opens and you approve access.

- With an `http://localhost:<port>/...` redirect URI the script catches the
  redirect itself.
- With any other redirect URI, the browser lands on a page that won't load.
  That's expected: copy the full address from the address bar and paste it into
  the terminal (this is the default for non-localhost URIs; add `--paste` to force it).

### 3. Pick the appliances

```
python punctual.py select
```

It lists your SmartThings devices as a numbered list. Type the numbers to sync,
e.g. `2,3`. Devices without the `execute` capability can't take the command.

### 4. Try it

```
python punctual.py sync --dry-run
python punctual.py sync
```

Look at the appliance display. It uses your computer's local time by default.
If the display ends up off by your UTC offset, use `sync --utc` instead.

## Run it on a schedule

Once a day is plenty. Pick an odd time such as 3:17 AM rather than midnight or
the top of the hour (the community reports load spikes and delays at round
times), and add `--jitter 20` to wait a random 0-20 minutes first.

**The easy way** (Windows Task Scheduler, or your crontab on Linux/macOS):

```
python punctual.py schedule --at 03:17
python punctual.py unschedule
```

The window's "Do it every day" box does the same. Runs are logged to `punctual.log`
in the settings folder, and `python punctual.py status` shows the last result.
On Windows the task runs as you (the saved login is only readable by you) and
catches up if the PC was off at 3:17. The manual recipes below do the same thing
by hand, or use systemd instead of cron.

### Windows (Task Scheduler)

Run this in PowerShell as the same user who ran `setup`. Windows encrypts the
saved login for that user, so the task must run as them. Use the full path to
`python`, from `(Get-Command python).Source`.

```powershell
$action   = New-ScheduledTaskAction -Execute "C:\Path\To\python.exe" -Argument "C:\Path\To\punctual.py sync --jitter 20"
$trigger  = New-ScheduledTaskTrigger -Daily -At 3:17am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "SoSmartButNeverPunctual" -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable` runs a missed job when the PC wakes.

### Linux / macOS (cron)

```
17 3 * * * /usr/bin/python3 /opt/punctual/punctual.py sync --jitter 20 >> $HOME/punctual.log 2>&1
```

### Linux (systemd timer)

`~/.config/systemd/user/punctual.service`:

```ini
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/punctual/punctual.py sync --jitter 20
```

`~/.config/systemd/user/punctual.timer`:

```ini
[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

Then `systemctl --user enable --now punctual.timer`.

## Commands

| Command | What it does |
|---|---|
| `setup` | One-time login with your OAuth-In app (or `--token` to create the app for you) |
| `schedule` / `unschedule` | Turn the daily sync on (`--at HH:MM`, `--jitter MINUTES`) or off |
| `devices` | List your SmartThings devices |
| `select` | Choose which devices to sync (saved) |
| `sync` | Set the clocks. Options: `--device NAME_OR_ID` (repeatable), `--utc`, `--jitter MINUTES`, `--dry-run` |
| `status` | Show login and device state, no network |

Exit codes: `0` ok, `1` error or a device failed, `2` login needed (run `setup`).
Schedulers can alert on non-zero.

Settings and tokens live in `%APPDATA%\SoSmartButNeverPunctual` on Windows and
`~/.config/sosmartbutneverpunctual` elsewhere. Override with `--config-dir` or
the `SSBNP_CONFIG_DIR` environment variable.

## Troubleshooting

- **`error: ... Run: python punctual.py setup`**: the login was rejected or has
  lapsed. Run `setup` again.
- **`another run is in progress`**: two runs overlapped. Refresh tokens are
  single-use, so runs are serialized with a lock; a stale lock clears itself after 10 minutes.
- **API says `COMPLETED` but the clock didn't change**: some Samsung commands
  report success and do nothing. That device probably doesn't support this command.
- **Wrong hour**: try `--utc`.

## Development

```
python -m unittest discover -s tests -v
```

`punctual.py` is the whole engine and CLI. `punctual_gui.py` is a thin Tkinter
window on top of it. Tests are in `tests/`.

## Standards

This repo follows [KVG_Standards](https://github.com/gerp93/KVG_Standards).
It is a script with a small optional window, not a packaged desktop app, so the
desktop-app parts (VisualAssault theme, installer, update-check, logo) are not
done yet; see `TODO.md`. It does follow the license, `TODO.md` and pull-request
conventions.

## License

[AGPL-3.0](LICENSE)
