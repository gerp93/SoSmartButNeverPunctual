#!/usr/bin/env python3
"""Bare-bones window for punctual.py: connect, pick appliances, sync, schedule.

Needs Tkinter (bundled with Python on Windows/macOS; on Debian/Ubuntu install
`python3-tk`). The script-only version, punctual.py, does not need this file.

    python punctual_gui.py
"""
from __future__ import annotations

import queue
import sys
import threading
import webbrowser

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

TITLE = "SoSmartButNeverPunctual"
TOKENS_URL = "https://account.smartthings.com/tokens"
README_URL = "https://github.com/gerp93/SoSmartButNeverPunctual#setup"


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

        ttk.Label(frame, text="If you're so smart, what time is it?", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8))

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
        ttk.Label(devices, textvariable=self.device_hint, foreground="gray40").pack(anchor="w", pady=(4, 0))
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
        ttk.Button(row, text="Turn on", command=self.schedule_on).pack(side="left")
        ttk.Button(row, text="Turn off", command=self.schedule_off).pack(side="left", padx=4)
        self.schedule_text = tk.StringVar()
        ttk.Label(schedule, textvariable=self.schedule_text).pack(anchor="w", pady=(4, 0))
        how = "Windows Task Scheduler" if sys.platform == "win32" else "your crontab"
        ttk.Label(schedule, text=f"Uses {how}. An odd time is kinder to SmartThings than midnight.",
                  foreground="gray40").pack(anchor="w")

        frame.rowconfigure(5, weight=1)
        logframe = ttk.LabelFrame(frame, text="Log", padding=4)
        logframe.grid(row=5, column=0, sticky="nsew", pady=4)
        self.log_view = tk.Text(logframe, height=9, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(logframe, command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_view.pack(side="left", fill="both", expand=True)

        self.refresh_state()
        self.pump()
        if self.store.load_state_safe():
            self.refresh_devices()

    # ----- plumbing -------------------------------------------------------

    def pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.write_log(payload)
                else:
                    payload()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def write_log(self, line: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", line + "\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def background(self, work, done=None) -> None:
        if self.busy:
            messagebox.showinfo(TITLE, "Still working on the last thing. Give it a moment.")
            return
        self.busy = True
        self.root.configure(cursor="watch")

        def finish(callback):
            self.busy = False
            self.root.configure(cursor="")
            callback()

        def target():
            try:
                result = work()
            except Exception as exc:  # shown to the user, not swallowed
                self.events.put(("call", lambda: finish(lambda: self.show_error(exc))))
            else:
                self.events.put(("call", lambda: finish(lambda: done(result) if done else None)))

        threading.Thread(target=target, daemon=True).start()

    def show_error(self, exc: Exception) -> None:
        self.write_log(f"error: {exc}")
        messagebox.showerror(TITLE, str(exc))
        self.refresh_state()

    # ----- state ----------------------------------------------------------

    def refresh_state(self) -> None:
        try:
            connected = bool(self.store.load_state())
            self.connection_text.set("Connected to SmartThings." if connected else "Not connected yet.")
        except punctual.PunctualError as exc:
            self.connection_text.set(f"Problem with saved login: {exc}")
        config = self.store.load_config()
        last = config.get("last_sync")
        self.last_sync_text.set(
            f"Last sync {last['at']}: {'ok' if last['ok'] else 'FAILED'}\n{last['summary']}" if last else "Not synced yet.")
        when = config.get("schedule", {}).get("time")
        if when:
            self.time_var.set(when)
        self.schedule_text.set(f"On: every day at {when}." if when and punctual.schedule_exists() else "Off.")

    # ----- 1. login -------------------------------------------------------

    def open_connect(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Connect to SmartThings")
        win.transient(self.root)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)

        easy = ttk.LabelFrame(body, text="Easiest: let this app create its own SmartThings app", padding=8)
        easy.pack(fill="x", pady=4)
        ttk.Label(easy, wraplength=440, justify="left", text=(
            "1. Open the token page, sign in, and create a token (any name). Tick the permissions for "
            "Apps and Devices.\n2. Paste the token below. It is used once to set things up and is never saved."
        )).pack(anchor="w")
        ttk.Button(easy, text="Open the token page", command=lambda: webbrowser.open(TOKENS_URL)).pack(anchor="w", pady=4)
        token_var = tk.StringVar()
        ttk.Entry(easy, textvariable=token_var, show="*", width=52).pack(anchor="w")

        manual = ttk.LabelFrame(body, text="Or: you already made a SmartThings OAuth-In app", padding=8)
        manual.pack(fill="x", pady=4)
        id_var, secret_var = tk.StringVar(), tk.StringVar()
        ttk.Label(manual, text="Client ID").pack(anchor="w")
        ttk.Entry(manual, textvariable=id_var, width=52).pack(anchor="w")
        ttk.Label(manual, text="Client secret").pack(anchor="w")
        ttk.Entry(manual, textvariable=secret_var, show="*", width=52).pack(anchor="w")
        ttk.Button(manual, text="How to make one", command=lambda: webbrowser.open(README_URL)).pack(anchor="w", pady=4)

        redirect_var = tk.StringVar(value=punctual.DEFAULT_REDIRECT_URI)
        ttk.Label(body, text="Redirect address (leave as is):").pack(anchor="w", pady=(6, 0))
        ttk.Entry(body, textvariable=redirect_var, width=52).pack(anchor="w")

        def go():
            token, client_id, secret = token_var.get().strip(), id_var.get().strip(), secret_var.get().strip()
            redirect = redirect_var.get().strip()
            if not token and not (client_id and secret):
                messagebox.showinfo(TITLE, "Paste a token, or fill in the client ID and secret.", parent=win)
                return
            if not punctual.is_loopback_redirect(redirect):
                messagebox.showinfo(TITLE, "The redirect address must look like http://localhost:8123/callback "
                                           "(the window version needs a localhost address).", parent=win)
                return
            win.destroy()
            self.write_log("Connecting... your browser will open so you can approve access.")

            def work():
                cid, sec = (punctual.create_oauth_app(token, redirect) if token else (client_id, secret))
                if token:
                    punctual.log("Created your SmartThings app.")
                punctual.authorize(self.store, cid, sec, redirect)

            def done(_):
                self.refresh_state()
                messagebox.showinfo(TITLE, "Connected. Now pick your appliances.")
                self.refresh_devices()

            self.background(work, done)

        ttk.Button(body, text="Connect", command=go).pack(anchor="e", pady=(10, 0))

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
        self.background(lambda: punctual.schedule_install(self.store, at),
                        lambda _: (self.refresh_state(), self.write_log(f"Daily sync turned on at {at}.")))

    def schedule_off(self) -> None:
        self.background(lambda: punctual.schedule_remove(self.store),
                        lambda _: (self.refresh_state(), self.write_log("Daily sync turned off.")))


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
