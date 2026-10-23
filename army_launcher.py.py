import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import minecraft_launcher_lib as mll

# ---------- Пути ----------
APP_DIR = os.path.join(os.path.expanduser("~"), ".army-launcher")
COMMON_DIR = os.path.join(APP_DIR, "common")        # общие версии и библиотеки
INSTANCES_DIR = os.path.join(APP_DIR, "instances")  # папки профилей: моды, сохранения
SKINS_DIR = os.path.join(APP_DIR, "skins")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
for _d in (APP_DIR, COMMON_DIR, INSTANCES_DIR, SKINS_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------- Оформление ----------
C = {
    "side": "#16111f", "main": "#1d1934", "panel": "#181428",
    "card": "#2a2350", "sel": "#3a2f6b", "accent": "#8a6cff",
    "green": "#3f9d3f", "green_h": "#4fb84f", "text": "#ece9f7",
    "muted": "#968fb8", "red": "#c0392b",
}
LOADERS = {"Ванильный": "vanilla", "Fabric": "fabric", "Forge": "forge"}
LOADER_NAMES = {v: k for k, v in LOADERS.items()}
CARD_COLORS = {"vanilla": "#3a7d44", "fabric": "#8a6b2a", "forge": "#2a5c8a", "import": "#5a3a8a"}

# ---------- Режимы производительности ----------
PRESETS = {
    "normal": {
        "label": "Обычный", "ram_cap": None, "jvm": [], "options": {},
    },
    "lite": {
        "label": "Лайт", "ram_cap": 3, "jvm": ["-XX:+UseSerialGC"],
        "options": {
            "renderDistance": "4", "simulationDistance": "4", "fancyGraphics": "false",
            "particles": "2", "entityShadows": "false", "enableVsync": "false",
            "mipmapLevels": "0", "smoothLighting": "false", "biomeBlendRadius": "0",
            "maxFps": "60", "fboEnable": "false",
        },
    },
    "ultra": {
        "label": "Ультра-лайт", "ram_cap": 2, "jvm": ["-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1"],
        "options": {
            "renderDistance": "2", "simulationDistance": "2", "fancyGraphics": "false",
            "particles": "2", "entityShadows": "false", "enableVsync": "false",
            "mipmapLevels": "0", "smoothLighting": "false", "biomeBlendRadius": "0",
            "maxFps": "30", "guiScale": "2", "fboEnable": "false", "graphicsMode": "0",
            "renderClouds": "false", "ao": "false",
        },
    },
}
PRESET_ORDER = ["normal", "lite", "ultra"]
LIGHT_VERSIONS_HINT = "Для слабых ПК: b1.7.3, 1.6.4, 1.8.9, 1.12.2"


def lerp(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------- Конфиг ----------
def load_config():
    cfg = {"nickname": "Player", "ram": 4, "java": "", "profiles": [], "servers": [], "selected": None}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- Установка и запуск ----------
def installed_ids(root):
    os.makedirs(root, exist_ok=True)
    return {v["id"] for v in mll.utils.get_installed_versions(root)}


def install_version(mc, loader, cb, java=None):
    """Ставит Minecraft и при необходимости Fabric/Forge в COMMON_DIR. Возвращает id для запуска."""
    if mc not in installed_ids(COMMON_DIR):
        cb["setStatus"](f"Установка Minecraft {mc}...")
        mll.install.install_minecraft_version(mc, COMMON_DIR, callback=cb)

    if loader == "vanilla":
        return mc

    if loader == "fabric":
        loader_ver = mll.fabric.get_latest_loader_version()
        launch_id = f"fabric-loader-{loader_ver}-{mc}"
        if launch_id not in installed_ids(COMMON_DIR):
            cb["setStatus"]("Установка Fabric...")
            mll.fabric.install_fabric(mc, COMMON_DIR, loader_version=loader_ver, callback=cb)
        return launch_id

    if loader == "forge":
        forge_full = mll.forge.find_forge_version(mc)
        if not forge_full:
            raise RuntimeError(f"Forge для версии {mc} не найден")
        launch_id = mll.forge.forge_to_installed_version(forge_full)
        if launch_id not in installed_ids(COMMON_DIR):
            cb["setStatus"]("Установка Forge (это может занять время)...")
            mll.forge.install_forge_version(forge_full, COMMON_DIR, java=java, callback=cb)
        return launch_id

    raise RuntimeError(f"Неизвестный загрузчик: {loader}")


def effective_ram(cfg, profile):
    base = int(cfg.get("ram", 4))
    cap = PRESETS.get(profile.get("preset", "normal"), PRESETS["normal"])["ram_cap"]
    return min(base, cap) if cap else base


def apply_preset_options(instance, preset_key):
    """Дописывает настройки графики в options.txt профиля, не трогая остальные строки."""
    opts = PRESETS[preset_key]["options"]
    if not opts:
        return
    os.makedirs(instance, exist_ok=True)
    path = os.path.join(instance, "options.txt")
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    done, out = set(), []
    for line in lines:
        key = line.split(":", 1)[0]
        if key in opts:
            out.append(f"{key}:{opts[key]}")
            done.add(key)
        else:
            out.append(line)
    for k, v in opts.items():
        if k not in done:
            out.append(f"{k}:{v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def mods_count(profile):
    md = os.path.join(profile.get("instance", ""), "mods")
    if not os.path.isdir(md):
        return 0
    return sum(1 for f in os.listdir(md) if f.endswith(".jar"))


def open_path(path):
    os.makedirs(path, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------- Менеджер модов ----------
class ModManager(tk.Frame):
    def __init__(self, master, app, **kw):
        super().__init__(master, bg=kw.pop("bg", C["panel"]), **kw)
        self.app = app
        self.instance = None

        btns = tk.Frame(self, bg=self.cget("bg"))
        btns.pack(fill="x", pady=(0, 6))
        self._btn(btns, "+ Мод (.jar)", self.add_mod).pack(side="left", fill="x", expand=True)
        self._btn(btns, "Папка", self.open_folder).pack(side="left", padx=(6, 0))

        wrap = tk.Frame(self, bg=self.cget("bg"))
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=self.cget("bg"), highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=self.cget("bg"))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, bg=C["card"], fg=C["text"],
                         activebackground=C["sel"], activeforeground=C["text"],
                         bd=0, padx=8, pady=5, cursor="hand2")

    def set_instance(self, path):
        self.instance = path
        self.refresh()

    def mods_dir(self):
        return os.path.join(self.instance, "mods")

    def refresh(self):
        for w in self.inner.winfo_children():
            w.destroy()
        bg = self.cget("bg")
        if not self.instance:
            tk.Label(self.inner, text="Выберите профиль", bg=bg, fg=C["muted"]).pack(anchor="w", padx=6, pady=6)
            return
        os.makedirs(self.mods_dir(), exist_ok=True)
        files = sorted(f for f in os.listdir(self.mods_dir()) if f.endswith((".jar", ".jar.disabled")))
        if not files:
            tk.Label(self.inner, text="Модов нет. Добавьте .jar", bg=bg, fg=C["muted"]).pack(anchor="w", padx=6, pady=6)
            return
        for f in files:
            enabled = f.endswith(".jar")
            var = tk.BooleanVar(value=enabled)
            name = f[:-len(".disabled")] if f.endswith(".disabled") else f
            tk.Checkbutton(self.inner, text=name, variable=var, anchor="w",
                           command=lambda f=f, v=var: self.toggle(f, v),
                           bg=bg, fg=C["text"], selectcolor=C["card"],
                           activebackground=bg, activeforeground=C["text"]
                           ).pack(fill="x", anchor="w", padx=4)

    def toggle(self, f, var):
        md = self.mods_dir()
        base = f[:-len(".disabled")] if f.endswith(".disabled") else f
        new = base if var.get() else base + ".disabled"
        if new != f:
            os.rename(os.path.join(md, f), os.path.join(md, new))
        self.after(10, self.app.refresh_all)

    def add_mod(self):
        if not self.instance:
            return messagebox.showinfo("Инфо", "Сначала выберите профиль")
        paths = filedialog.askopenfilenames(title="Выберите моды", filetypes=[("Minecraft mod", "*.jar")])
        if not paths:
            return
        os.makedirs(self.mods_dir(), exist_ok=True)
        for p in paths:
            shutil.copy2(p, self.mods_dir())
        self.app.refresh_all()

    def open_folder(self):
        if self.instance:
            open_path(self.mods_dir())


# ---------- Приложение ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Army Launcher")
        self.geometry("1180x720")
        self.minsize(1000, 640)
        self.configure(bg=C["main"])

        self.cfg = load_config()
        self.status_var = tk.StringVar(value="Готово")
        self.busy = False
        self.all_versions = []
        self.v_items = []
        self._queue = queue.Queue()

        self._build_layout()
        self.show_page("home")
        self.refresh_all()
        self.after(100, self._poll)

    # --- поток-безопасный вызов UI ---
    def ui(self, fn, *a, **kw):
        self._queue.put(lambda: fn(*a, **kw))

    def _poll(self):
        while True:
            try:
                self._queue.get_nowait()()
            except queue.Empty:
                break
            except Exception as e:
                print("UI error:", e)
        self.after(100, self._poll)

    def run_task(self, func, on_done=None):
        if self.busy:
            return messagebox.showinfo("Подождите", "Уже выполняется другая задача")
        self.busy = True

        def worker():
            state = {"max": 1}

            def set_max(v):
                state["max"] = max(v, 1)

            def set_progress(v):
                self.ui(self.progress.configure, value=min(100, v * 100 / state["max"]))

            def set_status(t):
                self.ui(self.status_var.set, str(t))

            cb = {"setStatus": set_status, "setMax": set_max, "setProgress": set_progress}
            try:
                msg = func(cb)
                self.ui(self.status_var.set, msg or "Готово")
                if on_done:
                    self.ui(on_done)
            except Exception as e:
                self.ui(self.status_var.set, "Ошибка")
                self.ui(messagebox.showerror, "Ошибка", str(e))
            finally:
                self.busy = False
                self.ui(self.progress.configure, value=0)

        threading.Thread(target=worker, daemon=True).start()

    # --- раскладка ---
    def _build_layout(self):
        self.sidebar = tk.Frame(self, bg=C["side"], width=200)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo = tk.Canvas(self.sidebar, width=200, height=80, bg=C["side"], highlightthickness=0)
        logo.pack(pady=(14, 14))
        logo.create_oval(16, 16, 60, 60, fill="#4b8b3b", outline="")
        logo.create_text(38, 38, text="A", fill="white", font=("Segoe UI", 16, "bold"))
        logo.create_text(72, 30, text="ARMY", anchor="w", fill=C["text"], font=("Segoe UI", 15, "bold"))
        logo.create_text(72, 50, text="LAUNCHER", anchor="w", fill=C["accent"], font=("Segoe UI", 9, "bold"))

        self.nav_buttons = {}
        self._nav("home", "Главная", lambda: self.show_page("home"))
        self._nav("play", "Играть", self.on_play)
        self._nav("versions", "Версии", lambda: self.show_page("versions"))
        self._nav("mods", "Сборки модов", lambda: self.show_page("mods"))
        self._nav("skins", "Скины", lambda: self.show_page("skins"))
        self._nav("servers", "Мультиплеер", lambda: self.show_page("servers"))
        tk.Frame(self.sidebar, bg=C["side"]).pack(expand=True, fill="both")
        self._nav("settings", "Настройки", lambda: self.show_page("settings"))

        self.content = tk.Frame(self, bg=C["main"])
        self.content.pack(side="left", fill="both", expand=True)

        self.pages = {
            "home": self._build_home(),
            "versions": self._build_versions(),
            "mods": self._build_mods_page(),
            "skins": self._build_skins(),
            "servers": self._build_servers(),
            "settings": self._build_settings(),
        }

    def _nav(self, key, label, cmd):
        b = tk.Button(self.sidebar, text="   " + label, anchor="w", font=("Segoe UI", 11),
                      bg=C["side"], fg=C["text"], activebackground=C["sel"], activeforeground=C["text"],
                      bd=0, relief="flat", padx=12, pady=9, cursor="hand2", command=cmd)
        b.pack(fill="x", padx=10, pady=2)
        self.nav_buttons[key] = b

    def show_page(self, key):
        for pg in self.pages.values():
            pg.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for k, b in self.nav_buttons.items():
            b.configure(bg=C["sel"] if k == key else C["side"])

        if key == "versions":
            if self.all_versions:
                self.fill_versions()
            else:
                self.load_versions()
        elif key == "settings":
            self.nick_var.set(self.cfg.get("nickname", "Player"))
            self.ram_var.set(str(self.cfg.get("ram", 4)))
            self.java_var.set(self.cfg.get("java", ""))
        elif key == "skins":
            self.draw_skin()
        elif key == "servers":
            self.refresh_servers()
        elif key == "mods":
            self.refresh_all()

    # --- Главная ---
    def _build_home(self):
        page = tk.Frame(self.content, bg=C["main"])

        right = tk.Frame(page, bg=C["panel"], width=290)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(right, text="Управление профилями", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(14, 6))
        self.profiles_box = tk.Frame(right, bg=C["panel"])
        self.profiles_box.pack(fill="x", padx=10)

        row = tk.Frame(right, bg=C["panel"])
        row.pack(fill="x", padx=10, pady=8)
        for text, cmd in [("+ Добавить профиль", self.add_profile_dialog),
                          ("Импорт из TLauncher", lambda: self.import_profiles("TLauncher")),
                          ("Импорт из K-Launcher", lambda: self.import_profiles("K-Launcher")),
                          ("Добавить .exe лаунчера", self.add_exe)]:
            tk.Button(row, text=text, command=cmd, bg=C["card"], fg=C["text"],
                      activebackground=C["sel"], activeforeground=C["text"],
                      bd=0, pady=5, cursor="hand2").pack(fill="x", pady=2)
        self.preset_btn = tk.Button(row, text="Режим: —", command=self.cycle_preset, bg=C["card"],
                                    fg=C["text"], activebackground=C["sel"], activeforeground=C["text"],
                                    bd=0, pady=5, cursor="hand2")
        self.preset_btn.pack(fill="x", pady=2)

        tk.Label(right, text="Менеджер модов", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        self.home_mods = ModManager(right, self)
        self.home_mods.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = tk.Frame(page, bg=C["main"])
        left.pack(side="left", fill="both", expand=True, padx=16, pady=16)

        self.hero = tk.Canvas(left, height=250, bg=C["main"], highlightthickness=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda e: self._draw_hero())

        self.play_btn = tk.Button(left, text="ИГРАТЬ", font=("Segoe UI", 12, "bold"),
                                  bg=C["green"], fg="white", activebackground=C["green_h"],
                                  activeforeground="white", bd=0, padx=50, pady=8,
                                  cursor="hand2", command=self.on_play)
        self.play_btn.pack(pady=(10, 14))

        tk.Label(left, text="Установленные версии", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.cards_frame = tk.Frame(left, bg=C["main"])
        self.cards_frame.pack(fill="x", pady=8)

        self.progress = ttk.Progressbar(left, mode="determinate", maximum=100)
        self.progress.pack(fill="x", side="bottom", pady=(4, 0))
        tk.Label(left, textvariable=self.status_var, bg=C["main"], fg=C["muted"],
                 anchor="w").pack(fill="x", side="bottom")
        return page

    def _draw_hero(self):
        c = self.hero
        c.delete("all")
        w = max(c.winfo_width(), 300)
        h = 250
        steps = 50
        for i in range(steps):
            c.create_rectangle(0, i * h / steps, w, (i + 1) * h / steps + 1,
                               fill=lerp("#4b3a94", "#1d1934", i / steps), outline="")
        c.create_polygon(0, h - 70, w * 0.2, h - 110, w * 0.45, h - 70, w * 0.7, h - 120, w, h - 75,
                         w, h, 0, h, fill="#2f5d2a", outline="")
        c.create_rectangle(0, h - 60, w, h, fill="#3f7d36", outline="")

        p = self.selected_profile()
        title = p["name"] if p else "Профиль не выбран"
        sub = self.profile_subtitle(p) if p else "Создайте профиль, чтобы начать"
        c.create_text(w / 2, h - 96, text=title, fill="white", font=("Segoe UI", 16, "bold"))
        c.create_text(w / 2, h - 74, text=sub, fill="#e4dffa", font=("Segoe UI", 10))

    def profile_subtitle(self, p):
        if p.get("kind") == "exe":
            return "Внешний лаунчер (.exe)"
        ver = p.get("mc") or p.get("launch_id", "")
        kind = LOADER_NAMES.get(p.get("loader"), "Импорт")
        preset = PRESETS.get(p.get("preset", "normal"), PRESETS["normal"])["label"]
        return f"{ver} · {kind} · {preset} · модов: {mods_count(p)}"

    def refresh_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()
        sel = self.cfg.get("selected")
        for p in self.cfg["profiles"][:6]:
            cv = tk.Canvas(self.cards_frame, width=150, height=92, bg=C["main"],
                           highlightthickness=0, cursor="hand2")
            cv.pack(side="left", padx=6)
            color = "#444" if p.get("kind") == "exe" else CARD_COLORS.get(p.get("loader") or "import")
            cv.create_rectangle(0, 0, 150, 92, fill=color,
                                outline=C["accent"] if p["id"] == sel else "", width=3)
            cv.create_text(75, 40, text=(p.get("mc") or p.get("launch_id", "EXE"))[:18],
                           fill="white", font=("Segoe UI", 11, "bold"))
            cv.create_text(75, 62, text=p["name"][:20], fill="#e4dffa", font=("Segoe UI", 9))
            cv.bind("<Button-1>", lambda e, pid=p["id"]: self.select_profile(pid))

    def refresh_profiles(self):
        for w in self.profiles_box.winfo_children():
            w.destroy()
        sel = self.cfg.get("selected")
        if not self.cfg["profiles"]:
            tk.Label(self.profiles_box, text="Профилей пока нет", bg=C["panel"], fg=C["muted"]).pack(anchor="w")
        for p in self.cfg["profiles"]:
            bg = C["sel"] if p["id"] == sel else C["card"]
            row = tk.Frame(self.profiles_box, bg=bg, cursor="hand2")
            row.pack(fill="x", pady=3)
            l1 = tk.Label(row, text=p["name"], bg=bg, fg=C["text"], font=("Segoe UI", 10, "bold"), anchor="w")
            l2 = tk.Label(row, text=self.profile_subtitle(p), bg=bg, fg=C["muted"], anchor="w",
                          font=("Segoe UI", 8))
            l1.pack(fill="x", padx=10, pady=(6, 0))
            l2.pack(fill="x", padx=10, pady=(0, 6))
            for w in (row, l1, l2):
                w.bind("<Button-1>", lambda e, pid=p["id"]: self.select_profile(pid))
                w.bind("<Button-3>", lambda e, pid=p["id"]: self.delete_profile(pid))

    def refresh_all(self):
        self.refresh_profiles()
        self.refresh_cards()
        self._draw_hero()
        p = self.selected_profile()
        path = p["instance"] if p else None
        self.home_mods.set_instance(path)
        self.mods_page_mgr.set_instance(path)
        self.mods_page_info.configure(
            text=(f"Выбран профиль: {p['name']}  ·  {self.profile_subtitle(p)}" if p
                  else "Выберите профиль на главной странице"))
        if p and p.get("kind") != "exe":
            self.preset_btn.configure(text=f"Режим: {PRESETS.get(p.get('preset', 'normal'))['label']}",
                                      state="normal")
        else:
            self.preset_btn.configure(text="Режим: —", state="disabled")

    def selected_profile(self):
        for p in self.cfg["profiles"]:
            if p["id"] == self.cfg.get("selected"):
                return p
        return None

    def select_profile(self, pid):
        self.cfg["selected"] = pid
        save_config(self.cfg)
        self.refresh_all()

    def cycle_preset(self):
        p = self.selected_profile()
        if not p or p.get("kind") == "exe":
            return
        cur = p.get("preset", "normal")
        idx = PRESET_ORDER.index(cur) if cur in PRESET_ORDER else 0
        p["preset"] = PRESET_ORDER[(idx + 1) % len(PRESET_ORDER)]
        save_config(self.cfg)
        self.refresh_all()

    def delete_profile(self, pid):
        p = next((x for x in self.cfg["profiles"] if x["id"] == pid), None)
        if p and messagebox.askyesno("Удалить", f"Удалить профиль «{p['name']}» из лаунчера?\n"
                                                "Файлы модов и сохранений не удаляются."):
            self.cfg["profiles"].remove(p)
            if self.cfg.get("selected") == pid:
                self.cfg["selected"] = self.cfg["profiles"][0]["id"] if self.cfg["profiles"] else None
            save_config(self.cfg)
            self.refresh_all()

    def on_play(self):
        p = self.selected_profile()
        if not p:
            return messagebox.showinfo("Инфо", "Сначала создайте или выберите профиль")
        if p.get("kind") == "exe":
            try:
                subprocess.Popen([p["exe"]], cwd=os.path.dirname(p["exe"]))
                self.status_var.set(f"Запущен {os.path.basename(p['exe'])}")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
            return
        self.run_task(lambda cb: self.launch_profile(p, cb))

    def launch_profile(self, p, cb):
        java = self.cfg.get("java") or None
        if p.get("loader"):
            launch_id = install_version(p["mc"], p["loader"], cb, java)
            root = COMMON_DIR
        else:
            launch_id = p["launch_id"]
            root = p["root"]
            if launch_id not in installed_ids(root):
                raise RuntimeError(
                    f"Версия {launch_id} не найдена в папке {root}.\n"
                    "Установите её во вкладке «Версии» или запустите её через исходный лаунчер.")

        inst = p["instance"]
        os.makedirs(inst, exist_ok=True)
        preset_key = p.get("preset", "normal")
        preset = PRESETS.get(preset_key, PRESETS["normal"])
        apply_preset_options(inst, preset_key)

        nick = self.cfg.get("nickname") or "Player"
        options = {
            "username": nick,
            "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, nick)),
            "token": "",
            "launcherName": "ArmyLauncher",
            "launcherVersion": "1.0",
            "gameDirectory": inst,
            "jvmArguments": [f"-Xmx{effective_ram(self.cfg, p)}G"] + preset["jvm"],
        }
        if java:
            options["executablePath"] = java

        cb["setStatus"]("Запуск игры...")
        cmd = mll.command.get_minecraft_command(launch_id, root, options)
        subprocess.Popen(cmd, cwd=inst)
        return f"Игра {launch_id} запущена ({preset['label']})"

    # --- Создание профиля ---
    def add_profile_dialog(self, preset_mc=None):
        win = tk.Toplevel(self)
        win.title("Новый профиль")
        win.configure(bg=C["main"])
        win.geometry("420x440")
        win.transient(self)
        win.grab_set()

        def lbl(t):
            tk.Label(win, text=t, bg=C["main"], fg=C["text"]).pack(anchor="w", padx=18, pady=(10, 2))

        lbl("Название профиля")
        name_var = tk.StringVar(value=f"Minecraft {preset_mc}" if preset_mc else "Мой профиль")
        tk.Entry(win, textvariable=name_var).pack(fill="x", padx=18)

        lbl("Версия Minecraft")
        releases = [v["id"] for v in self.all_versions if v["type"] == "release"]
        if not releases:
            releases = ["1.20.4", "1.19.4", "1.12.2", "1.8.9", "1.6.4", "b1.7.3"]
        mc_var = tk.StringVar(value=preset_mc or releases[0])
        ttk.Combobox(win, textvariable=mc_var, values=releases, state="readonly").pack(fill="x", padx=18)

        lbl("Загрузчик модов")
        loader_var = tk.StringVar(value="Ванильный")
        ttk.Combobox(win, textvariable=loader_var, values=list(LOADERS), state="readonly").pack(fill="x", padx=18)

        lbl("Режим производительности")
        preset_var = tk.StringVar(value=PRESETS["normal"]["label"])
        ttk.Combobox(win, textvariable=preset_var,
                     values=[PRESETS[k]["label"] for k in PRESET_ORDER], state="readonly").pack(fill="x", padx=18)

        tk.Label(win, text="Лайт и Ультра-лайт снижают графику и память.\n"
                           "Ультра-лайт рекомендуется для версий до 1.12.2.\n" + LIGHT_VERSIONS_HINT,
                 bg=C["main"], fg=C["muted"], justify="left").pack(anchor="w", padx=18, pady=8)

        def create():
            mc = mc_var.get()
            loader = LOADERS[loader_var.get()]
            preset_key = next(k for k in PRESET_ORDER if PRESETS[k]["label"] == preset_var.get())
            pid = str(uuid.uuid4())
            prof = {
                "id": pid,
                "name": name_var.get().strip() or mc,
                "kind": "minecraft",
                "mc": mc,
                "loader": loader,
                "preset": preset_key,
                "root": COMMON_DIR,
                "instance": os.path.join(INSTANCES_DIR, pid),
            }
            self.cfg["profiles"].append(prof)
            self.cfg["selected"] = pid
            save_config(self.cfg)
            self.refresh_all()
            win.destroy()
            java = self.cfg.get("java") or None
            self.run_task(lambda cb: (install_version(mc, loader, cb, java),
                                      f"Профиль «{prof['name']}» готов")[1])

        tk.Button(win, text="Создать", command=create, bg=C["green"], fg="white",
                  activebackground=C["green_h"], bd=0, padx=30, pady=6).pack(pady=10)

    def import_profiles(self, launcher_name):
        path = filedialog.askopenfilename(title=f"Файл профилей {launcher_name} (launcher_profiles.json)",
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = os.path.dirname(path)
            added = 0
            for key, p in data.get("profiles", {}).items():
                lid = p.get("lastVersionId")
                if not lid:
                    continue
                game_dir = p.get("gameDir") or base
                if not os.path.isabs(game_dir):
                    game_dir = os.path.join(base, game_dir)
                pid = str(uuid.uuid4())
                self.cfg["profiles"].append({
                    "id": pid,
                    "name": f"{p.get('name', key)} ({launcher_name})",
                    "kind": "minecraft",
                    "launch_id": lid,
                    "preset": "normal",
                    "root": base,
                    "instance": game_dir,
                })
                self.cfg["selected"] = pid
                added += 1
            save_config(self.cfg)
            self.refresh_all()
            messagebox.showinfo("Готово", f"Импортировано профилей: {added}")
        except Exception as e:
            messagebox.showerror("Ошибка импорта", str(e))

    def add_exe(self):
        path = filedialog.askopenfilename(title="Выберите .exe лаунчера", filetypes=[("EXE", "*.exe")])
        if not path:
            return
        pid = str(uuid.uuid4())
        self.cfg["profiles"].append({
            "id": pid,
            "name": os.path.splitext(os.path.basename(path))[0],
            "kind": "exe",
            "exe": path,
            "instance": os.path.dirname(path),
        })
        self.cfg["selected"] = pid
        save_config(self.cfg)
        self.refresh_all()

    # --- Версии ---
    def _build_versions(self):
        page = tk.Frame(self.content, bg=C["main"])
        pad = tk.Frame(page, bg=C["main"])
        pad.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(pad, text="Версии Minecraft", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self.v_status = tk.StringVar(value="")
        tk.Label(pad, textvariable=self.v_status, bg=C["main"], fg=C["muted"]).pack(anchor="w", pady=(0, 4))
        tk.Label(pad, text=LIGHT_VERSIONS_HINT, bg=C["main"], fg=C["muted"]).pack(anchor="w", pady=(0, 8))

        filters = tk.Frame(pad, bg=C["main"])
        filters.pack(anchor="w")
        self.v_release = tk.BooleanVar(value=True)
        self.v_snap = tk.BooleanVar(value=False)
        self.v_old = tk.BooleanVar(value=False)
        for text, var in [("Релизы", self.v_release), ("Снапшоты", self.v_snap),
                          ("Старые beta/alpha", self.v_old)]:
            tk.Checkbutton(filters, text=text, variable=var, command=self.fill_versions,
                           bg=C["main"], fg=C["text"], selectcolor=C["card"],
                           activebackground=C["main"], activeforeground=C["text"]).pack(side="left", padx=(0, 14))

        lf = tk.Frame(pad, bg=C["main"])
        lf.pack(fill="both", expand=True, pady=8)
        self.v_list = tk.Listbox(lf, bg=C["card"], fg=C["text"], selectbackground=C["accent"],
                                 bd=0, highlightthickness=0, font=("Consolas", 10))
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.v_list.yview)
        self.v_list.configure(yscrollcommand=sb.set)
        self.v_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = tk.Frame(pad, bg=C["main"])
        btns.pack(anchor="w")
        for text, cmd in [("Установить", self.install_selected_version),
                          ("Создать профиль из версии", self.create_profile_from_version)]:
            tk.Button(btns, text=text, command=cmd, bg=C["card"], fg=C["text"],
                      activebackground=C["sel"], activeforeground=C["text"],
                      bd=0, padx=14, pady=6, cursor="hand2").pack(side="left", padx=(0, 8))
        return page

    def load_versions(self):
        self.v_status.set("Загрузка списка версий...")

        def worker():
            try:
                versions = mll.utils.get_version_list()
                self.ui(self._set_versions, versions)
            except Exception as e:
                self.ui(self.v_status.set, f"Не удалось загрузить список: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _set_versions(self, versions):
        self.all_versions = versions
        self.fill_versions()

    def fill_versions(self):
        allowed = set()
        if self.v_release.get():
            allowed.add("release")
        if self.v_snap.get():
            allowed.add("snapshot")
        if self.v_old.get():
            allowed.update({"old_beta", "old_alpha"})
        installed = installed_ids(COMMON_DIR)
        self.v_list.delete(0, "end")
        self.v_items = []
        for v in self.all_versions:
            if v["type"] not in allowed:
                continue
            mark = "   ✓ установлена" if v["id"] in installed else ""
            self.v_list.insert("end", f"{v['id']:<14} [{v['type']}]{mark}")
            self.v_items.append(v["id"])
        self.v_status.set(f"Показано версий: {len(self.v_items)}")

    def _selected_version(self):
        sel = self.v_list.curselection()
        return self.v_items[sel[0]] if sel else None

    def install_selected_version(self):
        mc = self._selected_version()
        if not mc:
            return messagebox.showinfo("Инфо", "Выберите версию в списке")
        java = self.cfg.get("java") or None
        self.run_task(lambda cb: (install_version(mc, "vanilla", cb, java), f"Версия {mc} установлена")[1],
                      on_done=self.fill_versions)

    def create_profile_from_version(self):
        mc = self._selected_version()
        if not mc:
            return messagebox.showinfo("Инфо", "Выберите версию в списке")
        self.add_profile_dialog(preset_mc=mc)

    # --- Сборки модов ---
    def _build_mods_page(self):
        page = tk.Frame(self.content, bg=C["main"])
        pad = tk.Frame(page, bg=C["main"])
        pad.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(pad, text="Сборки модов", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self.mods_page_info = tk.Label(pad, text="", bg=C["main"], fg=C["muted"])
        self.mods_page_info.pack(anchor="w", pady=(4, 10))
        self.mods_page_mgr = ModManager(pad, self, bg=C["main"])
        self.mods_page_mgr.pack(fill="both", expand=True)
        tk.Label(pad, text="Моды включаются и выключаются галочками: выключенный мод переименовывается "
                           "в .jar.disabled и не загружается.", bg=C["main"], fg=C["muted"],
                 justify="left").pack(anchor="w", pady=(8, 0))
        return page

    # --- Скины ---
    def _build_skins(self):
        page = tk.Frame(self.content, bg=C["main"])
        pad = tk.Frame(page, bg=C["main"])
        pad.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(pad, text="Скины", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")

        self.skin_canvas = tk.Canvas(pad, width=260, height=320, bg=C["card"], highlightthickness=0)
        self.skin_canvas.pack(side="left", padx=(0, 20), pady=10)

        right = tk.Frame(pad, bg=C["main"])
        right.pack(side="left", fill="both", expand=True)

        self.skin_status = tk.StringVar(value="Скин не загружен")
        tk.Label(right, textvariable=self.skin_status, bg=C["main"], fg=C["muted"]).pack(anchor="w")

        tk.Button(right, text="Загрузить скин (PNG 64x64 или 64x32)",
                  command=self.load_skin, bg=C["card"], fg=C["text"],
                  activebackground=C["sel"], activeforeground=C["text"],
                  bd=0, padx=14, pady=6, cursor="hand2").pack(anchor="w", pady=8)

        tk.Label(right, text="Скин привязан к выбранному профилю и показывается здесь.\n"
                             "В игру он автоматически не попадает: для этого нужен мод\n"
                             "скинов или официальный аккаунт Mojang/Microsoft.",
                 bg=C["main"], fg=C["muted"], justify="left").pack(anchor="w", pady=(10, 0))
        self._skin_img = None
        return page

    def load_skin(self):
        p = self.selected_profile()
        if not p:
            return messagebox.showinfo("Инфо", "Сначала выберите профиль")
        path = filedialog.askopenfilename(title="Скин", filetypes=[("PNG", "*.png")])
        if not path:
            return
        try:
            img = tk.PhotoImage(file=path)
            if img.width() != 64 or img.height() not in (32, 64):
                return messagebox.showerror("Ошибка", "Скин должен быть 64×64 или 64×32")
        except Exception:
            return messagebox.showerror("Ошибка", "Не удалось открыть PNG")
        dst = os.path.join(SKINS_DIR, f"{p['id']}.png")
        shutil.copy2(path, dst)
        p["skin"] = dst
        save_config(self.cfg)
        self.draw_skin()

    def draw_skin(self):
        c = self.skin_canvas
        c.delete("all")
        c.create_rectangle(0, 0, 260, 320, fill=C["card"], outline="")
        p = self.selected_profile()
        skin = p.get("skin") if p else None
        if skin and os.path.exists(skin):
            try:
                self._skin_img = tk.PhotoImage(file=skin).zoom(4)  # 64 px -> 256 px
                c.create_image(130, 160, image=self._skin_img)
                self.skin_status.set(f"Профиль: {p['name']}")
                return
            except Exception:
                pass
        self._skin_img = None
        c.create_text(130, 160, text="Нет скина", fill=C["muted"], font=("Segoe UI", 11))
        self.skin_status.set(f"Профиль: {p['name']}, скин не загружен" if p else "Выберите профиль на главной")

    # --- Мультиплеер ---
    def _build_servers(self):
        page = tk.Frame(self.content, bg=C["main"])
        pad = tk.Frame(page, bg=C["main"])
        pad.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(pad, text="Мультиплеер", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")

        form = tk.Frame(pad, bg=C["main"])
        form.pack(anchor="w", pady=10, fill="x")
        tk.Label(form, text="Название", bg=C["main"], fg=C["text"]).grid(row=0, column=0, sticky="w")
        self.srv_name = tk.StringVar()
        tk.Entry(form, textvariable=self.srv_name, width=24).grid(row=0, column=1, padx=6)
        tk.Label(form, text="Адрес", bg=C["main"], fg=C["text"]).grid(row=0, column=2, sticky="w")
        self.srv_addr = tk.StringVar()
        tk.Entry(form, textvariable=self.srv_addr, width=32).grid(row=0, column=3, padx=6)
        tk.Button(form, text="+ Добавить", command=self.add_server, bg=C["card"], fg=C["text"],
                  activebackground=C["sel"], activeforeground=C["text"],
                  bd=0, padx=10, pady=3, cursor="hand2").grid(row=0, column=4)

        self.servers_box = tk.Frame(pad, bg=C["main"])
        self.servers_box.pack(fill="both", expand=True, pady=10)

        tk.Label(pad, text="Адрес копируется в буфер обмена кнопкой «Копировать».\n"
                           "В игре: «Мультиплеер» → «Добавить сервер» → вставьте адрес (Ctrl+V).",
                 bg=C["main"], fg=C["muted"], justify="left").pack(anchor="w")
        return page

    def add_server(self):
        name = self.srv_name.get().strip()
        addr = self.srv_addr.get().strip()
        if not name or not addr:
            return messagebox.showinfo("Инфо", "Введите название и адрес")
        self.cfg["servers"].append({"name": name, "addr": addr})
        save_config(self.cfg)
        self.srv_name.set("")
        self.srv_addr.set("")
        self.refresh_servers()

    def refresh_servers(self):
        if not hasattr(self, "servers_box"):
            return
        for w in self.servers_box.winfo_children():
            w.destroy()
        if not self.cfg["servers"]:
            tk.Label(self.servers_box, text="Серверов пока нет", bg=C["main"], fg=C["muted"]).pack(anchor="w")
            return
        for i, s in enumerate(self.cfg["servers"]):
            addr = s.get("addr", s.get("address", ""))
            row = tk.Frame(self.servers_box, bg=C["card"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{s.get('name', addr)}  —  {addr}", bg=C["card"], fg=C["text"],
                     anchor="w").pack(side="left", fill="x", expand=True, padx=10, pady=6)
            tk.Button(row, text="✕", command=lambda i=i: self.del_server(i),
                      bg=C["card"], fg=C["red"], bd=0, cursor="hand2").pack(side="right", padx=6)
            tk.Button(row, text="Копировать", command=lambda a=addr: self.copy_addr(a),
                      bg=C["card"], fg=C["text"], bd=0, padx=8, cursor="hand2").pack(side="right", padx=4)

    def copy_addr(self, addr):
        self.clipboard_clear()
        self.clipboard_append(addr)
        self.status_var.set("Адрес скопирован в буфер обмена")

    def del_server(self, i):
        del self.cfg["servers"][i]
        save_config(self.cfg)
        self.refresh_servers()

    # --- Настройки ---
    def _build_settings(self):
        page = tk.Frame(self.content, bg=C["main"])
        pad = tk.Frame(page, bg=C["main"])
        pad.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(pad, text="Настройки", bg=C["main"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")

        tk.Label(pad, text="Ник в игре", bg=C["main"], fg=C["text"]).pack(anchor="w", pady=(14, 2))
        self.nick_var = tk.StringVar(value=self.cfg.get("nickname", "Player"))
        tk.Entry(pad, textvariable=self.nick_var, width=40).pack(anchor="w")

        tk.Label(pad, text="Оперативная память (ГБ). Режимы Лайт и Ультра-лайт ограничивают её сами.",
                 bg=C["main"], fg=C["text"]).pack(anchor="w", pady=(14, 2))
        self.ram_var = tk.StringVar(value=str(self.cfg.get("ram", 4)))
        ttk.Combobox(pad, textvariable=self.ram_var, values=[str(x) for x in (1, 2, 4, 6, 8, 12, 16)],
                     width=10).pack(anchor="w")

        tk.Label(pad, text="Путь к Java (пусто = автоматически)", bg=C["main"], fg=C["text"]).pack(
            anchor="w", pady=(14, 2))
        jf = tk.Frame(pad, bg=C["main"])
        jf.pack(anchor="w")
        self.java_var = tk.StringVar(value=self.cfg.get("java", ""))
        tk.Entry(jf, textvariable=self.java_var, width=50).pack(side="left")
        tk.Button(jf, text="…", command=self.pick_java, bg=C["card"], fg=C["text"],
                  bd=0, padx=8, cursor="hand2").pack(side="left", padx=6)

        btns = tk.Frame(pad, bg=C["main"])
        btns.pack(anchor="w", pady=20)
        tk.Button(btns, text="Сохранить настройки", command=self.save_settings,
                  bg=C["green"], fg="white", activebackground=C["green_h"],
                  bd=0, padx=20, pady=6, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Открыть папку лаунчера", command=lambda: open_path(APP_DIR),
                  bg=C["card"], fg=C["text"], activebackground=C["sel"],
                  bd=0, padx=14, pady=6, cursor="hand2").pack(side="left")
        return page

    def pick_java(self):
        path = filedialog.askopenfilename(title="java.exe / java",
                                          filetypes=[("Java", "java*"), ("Все файлы", "*.*")])
        if path:
            self.java_var.set(path)

    def save_settings(self):
        try:
            ram = int(self.ram_var.get())
            if not 1 <= ram <= 64:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Ошибка", "RAM должна быть числом от 1 до 64")
        self.cfg["nickname"] = self.nick_var.get().strip() or "Player"
        self.cfg["ram"] = ram
        self.cfg["java"] = self.java_var.get().strip()
        save_config(self.cfg)
        self.status_var.set("Настройки сохранены")


if __name__ == "__main__":
    App().mainloop()
