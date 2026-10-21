#!/usr/bin/env python3
"""Bare-bones window for punctual.py: connect, pick appliances, sync, schedule.

Needs Tkinter (bundled with Python on Windows/macOS; on Debian/Ubuntu install
`python3-tk`). The script-only version, punctual.py, does not need this file.

    python punctual_gui.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:  # pragma: no cover - depends on the machine
    sys.exit(
        "Tkinter is not installed.\n"
        "  Debian/Ubuntu/Raspberry Pi: sudo apt install python3-tk\n"
        "  Fedora: sudo dnf install python3-tkinter\n"
        "  Arch: sudo pacman -S tk\n"
        "Or use the script version without a window: python punctual.py --help"
    )

import punctual
import theming

try:  # only does anything in a packaged build; needs requirements.txt when run from source
    import updater
except ImportError:
    updater = None

TITLE = "SoSmartButNeverPunctual"
TOKENS_URL = "https://account.smartthings.com/tokens"
README_URL = "https://github.com/gerp93/SoSmartButNeverPunctual#advanced-use-your-own-smartthings-app"


def resource_path(relative: str) -> Path:
    """Find a bundled file both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.store = punctual.Store(punctual.config_dir())
        punctual.set_log_file(self.store.dir / "punctual.log")
        self.events: queue.Queue = queue.Queue()
        punctual.LOG_SINKS.append(lambda line: self.events.put(("log", line)))
        self.busy = False
        self.device_vars: list = []  # (BooleanVar, device dict)

        root.title(TITLE)
        root.minsize(520, 560)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.setup_icon(header)
        ttk.Label(header, text="If you're so smart,\nwhat time is it?", font=("TkDefaultFont", 12, "bold")).pack(
            side="left", padx=8)
        if theming.THEMES:
            picker = ttk.Frame(header)
            picker.pack(side="right")
            ttk.Label(picker, text="Theme").pack(anchor="e")
            self.theme_var = tk.StringVar()
            combo = ttk.Combobox(picker, textvariable=self.theme_var, state="readonly", width=18,
                                 values=[theming.DEFAULT_LABEL] + list(theming.THEME_NAMES.values()))
            combo.pack()
            combo.bind("<<ComboboxSelected>>", self.on_theme_chosen)

        connect = ttk.LabelFrame(frame, text="1. SmartThings login", padding=8)
        connect.grid(row=1, column=0, sticky="ew", pady=4)
        self.connection_text = tk.StringVar()
        ttk.Label(connect, textvariable=self.connection_text).pack(side="left")
        ttk.Button(connect, text="Connect...", command=self.open_connect).pack(side="right")

        devices = ttk.LabelFrame(frame, text="2. Appliances to sync", padding=8)
        devices.grid(row=2, column=0, sticky="ew", pady=4)
        self.device_box = ttk.Frame(devices)
        self.device_box.pack(fill="x")
        self.device_hint = tk.StringVar(value="Connect first, then press Refresh.")
        ttk.Label(devices, textvariable=self.device_hint).pack(anchor="w", pady=(4, 0))
        ttk.Button(devices, text="Refresh list", command=self.refresh_devices).pack(anchor="e")

        sync = ttk.LabelFrame(frame, text="3. Set the clocks", padding=8)
        sync.grid(row=3, column=0, sticky="ew", pady=4)
        ttk.Button(sync, text="Sync clocks now", command=self.sync_now).pack(side="left")
        self.last_sync_text = tk.StringVar()
        ttk.Label(sync, textvariable=self.last_sync_text, wraplength=300, justify="left").pack(side="left", padx=10)

        schedule = ttk.LabelFrame(frame, text="4. Do it every day", padding=8)
        schedule.grid(row=4, column=0, sticky="ew", pady=4)
        row = ttk.Frame(schedule)
        row.pack(fill="x")
        ttk.Label(row, text="Time (24-hour):").pack(side="left")
        self.time_var = tk.StringVar(value="03:17")
        ttk.Entry(row, textvariable=self.time_var, width=7).pack(side="left", padx=6)
        self.schedule_button = ttk.Button(row, text="Turn on", command=self.schedule_on)
        self.schedule_button.pack(side="left")
        ttk.Button(row, text="Turn off", command=self.schedule_off).pack(side="left", padx=4)
        self.schedule_text = tk.StringVar()
        ttk.Label(schedule, textvariable=self.schedule_text).pack(anchor="w", pady=(4, 0))
        how = "Windows Task Scheduler" if sys.platform == "win32" else "your crontab"
        ttk.Label(schedule, wraplength=470, justify="left", text=(
            f"Uses {how}. It runs at this time plus a random wait of up to 20 minutes, so a test right "
            "after turning it on will look like nothing happens. Check \"Last sync\" above a while "
            "later. An odd time like 03:17 is kinder to SmartThings than midnight.")).pack(anchor="w")

        frame.rowconfigure(5, weight=1)
        logframe = ttk.LabelFrame(frame, text="Log", padding=4)
        logframe.grid(row=5, column=0, sticky="nsew", pady=4)
        self.log_view = tk.Text(logframe, height=9, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(logframe, command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_view.pack(side="left", fill="both", expand=True)

        footer = ttk.Frame(frame)
        footer.grid(row=6, column=0, sticky="ew")
        ttk.Label(footer, text=f"Version {punctual.__version__}").pack(side="left")
        self.update_button = ttk.Button(footer, text="", command=self.install_update)
        self.update_info = None  # {"version", "download_url"} when a newer release exists

        theming.capture_defaults(root)
        self.apply_saved_theme()
        self.refresh_state()
        self.pump()
        self.root.after(1500, self.check_for_updates)
        if not self.store.load_state_safe():
            self.root.after(500, self.open_connect)  # first run: start the setup right away
        if self.store.load_state_safe():
            self.refresh_devices()

    # ----- plumbing -------------------------------------------------------

    def pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    if kind == "log":
                        self.write_log(payload)
                    else:
                        payload()
                except Exception as exc:  # one bad callback must never stop the message loop
                    self.write_log(f"(internal error: {exc!r})")
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def write_log(self, line: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", line + "\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def background(self, work, done=None, quiet: bool = False) -> None:
        """Run work() off the UI thread. quiet=True is for optional checks: it doesn't block the
        buttons and failures only go to the log."""
        if not quiet:
            if self.busy:
                messagebox.showinfo(TITLE, "Still working on the last thing. Give it a moment.")
                return
            self.busy = True
            self.root.configure(cursor="watch")

        def finish(callback):
            if not quiet:
                self.busy = False
                self.root.configure(cursor="")
            callback()

        def failed(exc):
            if quiet:
                self.write_log(f"(skipped: {exc})")
            else:
                self.show_error(exc)

        def target():
            try:
                result = work()
            except Exception as exc:  # shown to the user, not swallowed
                error = exc  # `exc` is deleted when this block ends, so the callback must not use it
                self.events.put(("call", lambda: finish(lambda: failed(error))))
            else:
                self.events.put(("call", lambda: finish(lambda: done(result) if done else None)))

        threading.Thread(target=target, daemon=True).start()

    def show_error(self, exc: Exception) -> None:
        punctual.log(f"error: {exc}")  # file + log box
        messagebox.showerror(TITLE, str(exc))
        self.refresh_state()

    # ----- look and updates -----------------------------------------------

    def setup_icon(self, header) -> None:
        """Window/taskbar icon plus a small logo next to the title (skipped if the file is missing)."""
        try:
            icon = tk.PhotoImage(file=str(resource_path("assets/icon.png")))
        except tk.TclError:
            return
        self.root.iconphoto(True, icon)
        self._logo = icon.subsample(4)  # keep a reference or Tk drops the image
        ttk.Label(header, image=self._logo).pack(side="left")

    def apply_saved_theme(self) -> None:
        if not theming.THEMES:
            return
        config = self.store.load_config()
        theme_id = config["theme"] if "theme" in config else theming.DEFAULT_THEME_ID
        if theme_id not in theming.THEMES:
            theme_id = None  # System Default (also covers a theme that a newer package removed)
        theming.apply_theme(self.root, theme_id)
        self.theme_var.set(theming.THEME_NAMES.get(theme_id, theming.DEFAULT_LABEL))

    def on_theme_chosen(self, _event=None) -> None:
        by_name = {name: theme_id for theme_id, name in theming.THEME_NAMES.items()}
        theme_id = by_name.get(self.theme_var.get())  # None = System Default
        theming.apply_theme(self.root, theme_id)
        config = self.store.load_config()
        config["theme"] = theme_id or ""
        self.store.save_config(config)

    def check_for_updates(self) -> None:
        if updater is None:
            return

        def done(info):
            if info:
                self.update_info = info
                self.update_button.configure(text=f"Update to v{info['version']}")
                self.update_button.pack(side="right")
                self.write_log(f"Version {info['version']} is available.")

        self.background(updater.check_for_update, done, quiet=True)

    def install_update(self) -> None:
        if not self.update_info or not messagebox.askyesno(
                TITLE, f"Download version {self.update_info['version']} and restart now?"):
            return
        url = self.update_info["download_url"]

        def work():
            path = updater.download_update(url)
            updater.apply_update_and_restart(path)  # exits this process

        self.background(work)

    # ----- state ----------------------------------------------------------

    def refresh_state(self) -> None:
        try:
            connected = bool(self.store.load_state())
            self.connection_text.set("Connected to SmartThings." if connected else "Not connected yet. Click Connect to start.")
        except punctual.PunctualError as exc:
            self.connection_text.set(f"Problem with saved login: {exc}")
        config = self.store.load_config()
        last = config.get("last_sync")
        self.last_sync_text.set(
            f"Last sync {last['at']}: {'ok' if last['ok'] else 'FAILED'}\n{last['summary']}" if last else "Not synced yet.")
        when = config.get("schedule", {}).get("time")
        if when:
            self.time_var.set(when)
        self.schedule_on_now = bool(when and punctual.schedule_exists())
        if self.schedule_on_now:
            self.schedule_button.configure(text="Change time")
            self.schedule_text.set(f"On: every day at {when}. There is only ever one daily task; "
                                   "Change time replaces it.")
        else:
            self.schedule_button.configure(text="Turn on")
            self.schedule_text.set("Off.")

    # ----- 1. login -------------------------------------------------------

    def open_connect(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Connect to SmartThings")
        win.configure(background=theming.background())
        win.transient(self.root)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        wrap = 470

        ttk.Label(body, wraplength=wrap, justify="left", font=("TkDefaultFont", 10, "bold"), text=(
            "This app needs your permission to set the clocks on your appliances. "
            "You do two quick things on the SmartThings website:")).pack(anchor="w")
        ttk.Label(body, wraplength=wrap, justify="left", text=(
            "1.  Click the button below. Sign in with your Samsung account.\n\n"
            "2.  Click \"Generate new token\". Give it any name (for example: clock). Tick every box "
            "under Devices and Apps (or just tick everything). Click \"Generate token\".\n\n"
            "3.  Copy the token it shows (you only see it once) and paste it in the box below.\n\n"
            "4.  Click Connect. Your web browser opens a SmartThings page asking you to allow access. "
            "Click Allow, then come back here.")).pack(anchor="w", pady=8)
        ttk.Button(body, text="Open the SmartThings token page", command=lambda: webbrowser.open(TOKENS_URL)).pack(anchor="w")
        ttk.Label(body, text="Paste the token here:").pack(anchor="w", pady=(10, 0))
        token_var = tk.StringVar()
        ttk.Entry(body, textvariable=token_var, show="*", width=58).pack(anchor="w")
        ttk.Label(body, text="The token is only used once, to set things up. It is not saved.").pack(anchor="w", pady=(2, 0))

        # Advanced: for people who already have their own SmartThings app (client ID + secret)
        advanced = ttk.Frame(body)
        id_var, secret_var = tk.StringVar(), tk.StringVar()
        redirect_var = tk.StringVar(value=punctual.DEFAULT_REDIRECT_URI)
        ttk.Label(advanced, text="Client ID").pack(anchor="w")
        ttk.Entry(advanced, textvariable=id_var, width=58).pack(anchor="w")
        ttk.Label(advanced, text="Client secret").pack(anchor="w")
        ttk.Entry(advanced, textvariable=secret_var, show="*", width=58).pack(anchor="w")
        ttk.Label(advanced, text="Redirect address (leave as is)").pack(anchor="w")
        ttk.Entry(advanced, textvariable=redirect_var, width=58).pack(anchor="w")
        ttk.Button(advanced, text="Instructions for making your own app",
                   command=lambda: webbrowser.open(README_URL)).pack(anchor="w", pady=4)

        def toggle_advanced():
            if advanced.winfo_ismapped():
                advanced.pack_forget()
                toggle.configure(text="Advanced: I already have a Client ID and secret  >")
            else:
                advanced.pack(anchor="w", pady=4, before=buttons)
                toggle.configure(text="Advanced: I already have a Client ID and secret  v")

        toggle = ttk.Button(body, text="Advanced: I already have a Client ID and secret  >", command=toggle_advanced)
        toggle.pack(anchor="w", pady=(12, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))

        def go():
            token, client_id, secret = token_var.get().strip(), id_var.get().strip(), secret_var.get().strip()
            redirect = redirect_var.get().strip()
            if not token and not (client_id and secret):
                messagebox.showinfo(TITLE, "Paste the token from the SmartThings website into the box first.", parent=win)
                return
            if not punctual.is_loopback_redirect(redirect):
                messagebox.showinfo(TITLE, "The redirect address must look like http://localhost:8123/callback "
                                           "(the window version needs a localhost address).", parent=win)
                return
            win.destroy()
            self.write_log("Connecting... your web browser will open so you can allow access.")

            def work():
                cid, sec = (punctual.create_oauth_app(token, redirect) if token else (client_id, secret))
                if token:
                    punctual.log("Set up your SmartThings access.")
                punctual.authorize(self.store, cid, sec, redirect)

            def done(_):
                self.refresh_state()
                messagebox.showinfo(TITLE, "Connected.\n\nNext: tick the appliances you want kept in sync, "
                                           "then press \"Sync clocks now\" and check their displays.")
                self.refresh_devices()

            self.background(work, done)

        ttk.Button(buttons, text="Connect", command=go).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # ----- 2. appliances --------------------------------------------------

    def refresh_devices(self) -> None:
        self.device_hint.set("Loading...")
        self.background(lambda: punctual.fetch_devices(self.store), self.show_devices)

    def show_devices(self, devices: list) -> None:
        for child in self.device_box.winfo_children():
            child.destroy()
        self.device_vars = []
        selected = {d["id"] for d in self.store.load_config().get("devices", [])}
        for device in devices:
            var = tk.BooleanVar(value=device["id"] in selected and device["execute"])
            text = device["name"] if device["execute"] else f"{device['name']}  (no clock command)"
            check = ttk.Checkbutton(self.device_box, text=text, variable=var, command=self.save_selection)
            if not device["execute"]:
                check.state(["disabled"])
            check.pack(anchor="w")
            self.device_vars.append((var, device))
        self.device_hint.set("Tick the ones to keep in sync. Try one first and check its display."
                             if devices else "No devices found on this SmartThings account.")

    def save_selection(self) -> None:
        config = self.store.load_config()
        config["devices"] = [{"id": d["id"], "name": d["name"]} for var, d in self.device_vars if var.get()]
        self.store.save_config(config)

    # ----- 3 + 4. sync and schedule --------------------------------------

    def sync_now(self) -> None:
        def done(result):
            succeeded, failed = result
            self.refresh_state()
            if failed:
                messagebox.showwarning(TITLE, "These did not accept the new time: " + ", ".join(failed))

        self.background(lambda: punctual.run_sync(self.store), done)

    def schedule_on(self) -> None:
        at = self.time_var.get()
        verb = "changed to" if getattr(self, "schedule_on_now", False) else "turned on at"
        self.background(lambda: punctual.schedule_install(self.store, at),
                        lambda _: (self.refresh_state(), self.write_log(f"Daily sync {verb} {at}.")))

    def schedule_off(self) -> None:
        self.background(lambda: punctual.schedule_remove(self.store),
                        lambda _: (self.refresh_state(), self.write_log("Daily sync turned off.")))


def main() -> int:
    if len(sys.argv) > 1:
        # The packaged app is also the scheduled job: `App.exe sync ...` runs the script
        # engine without opening a window (see punctual.sync_command_line).
        return punctual.main()
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
