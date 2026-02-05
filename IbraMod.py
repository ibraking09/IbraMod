import customtkinter as ctk
import minecraft_launcher_lib as mclib
import subprocess
import threading
import json
import os
import sys
import requests
import zipfile
import shutil
import platform
import time
from pathlib import Path
from tkinter import messagebox, filedialog
from PIL import Image

# --- DISCORD RPC SETUP ---
try:
    from pypresence import Presence
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    print("pypresence not installed. Discord RPC disabled.")

DISCORD_CLIENT_ID = "1468848872154468352"

# --- PATH FIX ---
if getattr(sys, 'frozen', False):
    # If running as an EXE:
    ROOT_DIR = Path(sys.executable).parent      # Save files next to the EXE
    ASSET_DIR = Path(sys._MEIPASS)              # Load icon from Temp folder
else:
    # If running as a script (VS Code):
    ROOT_DIR = Path(__file__).parent            # Save files next to the script
    ASSET_DIR = ROOT_DIR                        # Load icon from the script folder

# --- CONSTANTS ---
APP_NAME = "IbraMod Launcher v3.0"
BASE_DIR = ROOT_DIR / "instances"
CACHE_FILE = ROOT_DIR / "name_cache.json"
SETTINGS_FILE = ROOT_DIR / "settings.json"
TEMP_DIR = ROOT_DIR / "temp"

# Define the Icon Path here so i can use it later
ICON_FILE = ASSET_DIR / "app_icon.ico"
ICON_PNG = ASSET_DIR / "app_icon.png"

# Create folders if they don't exist
if not BASE_DIR.exists(): BASE_DIR.mkdir(parents=True)
if not TEMP_DIR.exists(): TEMP_DIR.mkdir(parents=True)

# --- Modrinth API Client ---
class Modrinth:
    BASE = "https://api.modrinth.com/v2"
    HEADERS = {"User-Agent": "IbraMod-Launcher/3.0"}

    def search(self, query="", index="relevance", facet_type="mod", version=None, loader=None):
        if not query: return []
        facets_list = [[f"project_type:{facet_type}"]]
        if version and facet_type == "mod":
            facets_list.append([f"versions:{version}"])
        if loader and facet_type == "mod":
            l = loader.lower()
            if l == "vanilla": l = "fabric" 
            if l in ["forge", "fabric", "neoforge"]:
                facets_list.append([f"categories:{l}"])
        params = {'query': query, 'limit': 20, 'index': index, 'facets': json.dumps(facets_list)}
        try: 
            resp = requests.get(f"{self.BASE}/search", params=params, headers=self.HEADERS)
            return resp.json().get('hits', [])
        except: return []

    def get_latest_version_file(self, project_id, loaders, game_versions=None):
        params = {'loaders': str(loaders).replace("'", '"')}
        if game_versions: params['game_versions'] = str(game_versions).replace("'", '"')
        try:
            resp = requests.get(f"{self.BASE}/project/{project_id}/version", params=params, headers=self.HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                if data: return data[0]['files'][0]
        except: pass
        return None

    def get_project_versions(self, project_id):
        try: return requests.get(f"{self.BASE}/project/{project_id}/version", headers=self.HEADERS).json()
        except: return []

# --- Backend Logic ---
class Backend:
    def __init__(self):
        self.modrinth = Modrinth()
        self.name_cache = self.load_cache()
        self.discord_rpc = None
        self.connect_discord()

    def connect_discord(self):
        if not HAS_DISCORD: return
        try:
            self.discord_rpc = Presence(DISCORD_CLIENT_ID)
            self.discord_rpc.connect()
            self.update_discord("Idling", "In Launcher")
        except Exception as e:
            print(f"Discord connection failed: {e}")
            self.discord_rpc = None

    def update_discord(self, state, details, start_time=None):
        if not self.discord_rpc: return
        try:
            self.discord_rpc.update(state=state, details=details, start=start_time, large_image="minecraft_icon", large_text="IbraMod Launcher")
        except: pass

    def load_cache(self):
        if CACHE_FILE.exists():
            try: return json.loads(CACHE_FILE.read_text())
            except: return {}
        return {}

    def save_cache(self):
        with open(CACHE_FILE, "w") as f: json.dump(self.name_cache, f, indent=4)

    def get_settings(self):
        if SETTINGS_FILE.exists():
            try: return json.loads(SETTINGS_FILE.read_text())
            except: pass
        return {"max_ram": 4, "java_path": "Auto", "low_end_mode": False}

    def save_settings(self, data):
        with open(SETTINGS_FILE, "w") as f: json.dump(data, f, indent=4)

    # --- UPDATED JAVA LOGIC (Windows + Linux Support) ---
    def find_java_paths(self):
        paths = ["Auto"]
        system = platform.system()
        
        # 1. Check the system's default "java" command
        default_java = shutil.which("java")
        if default_java:
            paths.append(str(Path(default_java).resolve()))

        # 2. Scan standard installation folders based on OS
        search_dirs = []
        if system == "Windows":
            search_dirs = [
                Path("C:/Program Files/Java"), 
                Path("C:/Program Files (x86)/Java"),
                Path.home() / "AppData/Local/Programs/Eclipse Adoptium",
                Path.home() / ".jdks"
            ]
        elif system == "Linux":
            search_dirs = [
                Path("/usr/lib/jvm"),
                Path("/usr/java"),
                Path.home() / ".sdkman/candidates/java"
            ]
        elif system == "Darwin": # MacOS
             search_dirs = [
                Path("/Library/Java/JavaVirtualMachines"),
                Path.home() / "Library/Java/JavaVirtualMachines"
             ]
            
        for d in search_dirs:
            if d.exists():
                for sub in d.iterdir():
                    bin_name = "javaw.exe" if system == "Windows" else "java"
                    bin_path = sub / "bin" / bin_name
                    if bin_path.exists():
                        paths.append(str(bin_path))
        
        return list(dict.fromkeys(paths))

    def get_smart_java(self, mc_version, user_setting="Auto"):
        if user_setting != "Auto" and user_setting:
            return user_setting

        # 1. Determine which Java version i need
        req_ver = 8  # Default for old versions
        
        try:
            # Simple way to parse version
            clean_ver = "".join([c for c in mc_version if c.isdigit() or c == "."])
            parts = [int(x) for x in clean_ver.split(".") if x]
            
            if len(parts) >= 2:
                major, minor = parts[0], parts[1]
                patch = parts[2] if len(parts) > 2 else 0
                
                if major == 1:
                    if minor >= 21:           # 1.21+ -> Java 21
                        req_ver = 21
                    elif minor == 20:         # 1.20.x logic
                        if patch >= 5: req_ver = 21
                        else:          req_ver = 17
                    elif minor >= 17:         # 1.17 - 1.19 -> Java 17
                        req_ver = 17
        except:
            print(f"Could not parse version {mc_version}, defaulting to Java 8")

        print(f"Version {mc_version} requires Java {req_ver}")

        # 2. Find the best match
        available_paths = self.find_java_paths()
        best_match = None
        
        for p in available_paths:
            if p == "Auto": continue
            path_str = p.lower()
            
            # Look for version numbers in the path string
            if req_ver == 21 and ("21" in path_str): return p
            if req_ver == 17 and ("17" in path_str): best_match = p
            if req_ver == 8 and ("1.8" in path_str or "8" in path_str): best_match = p

        return best_match

    def get_latest_mc_version(self):
        try: return mclib.utils.get_latest_version()["release"]
        except: return None

    def get_instances(self):
        return sorted([d.name for d in BASE_DIR.iterdir() if d.is_dir()])

    def get_instance_config(self, name):
        try: return json.loads((BASE_DIR / name / "instance.json").read_text())
        except: return {"version": "Unknown", "loader": "Vanilla"}

    def launch(self, name, username):
        inst_dir = BASE_DIR / name
        mc_dir = inst_dir / ".minecraft"
        config = self.get_instance_config(name)
        settings = self.get_settings()
        
        ram_gb = settings.get("max_ram", 4)
        low_end = settings.get("low_end_mode", False)
        
        java_path = self.get_smart_java(config.get("version", "1.20"), settings.get("java_path", "Auto"))

        # --- VERSION LOGIC ---
        ver_id = config.get("version")
        installed = mclib.utils.get_installed_versions(str(mc_dir))
        installed_ids = [v['id'] for v in installed]
        
        if not ver_id or ver_id not in installed_ids:
            loader_type = config.get("loader", "Vanilla").lower()
            ver_id = None
            for vid in installed_ids:
                if loader_type == "fabric" and "fabric" in vid.lower(): ver_id = vid; break
                elif loader_type == "forge" and "forge" in vid.lower(): ver_id = vid; break
                elif loader_type == "modpack" and ("fabric" in vid.lower() or "forge" in vid.lower()): ver_id = vid; break
            if not ver_id and installed_ids: ver_id = installed_ids[0]

        # --- JVM ARGUMENTS ---
        jvm_args = [f"-Xmx{ram_gb}G", "-Xms512M"]
        if low_end:
            print("Enabling Low End PC Optimizations...")
            jvm_args.extend([
                "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200",
                "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC", "-XX:+AlwaysPreTouch",
                "-XX:G1NewSizePercent=30", "-XX:G1MaxNewSizePercent=40", "-XX:G1HeapRegionSize=8M",
                "-XX:G1ReservePercent=20", "-XX:G1HeapWastePercent=5", "-XX:G1MixedGCCountTarget=4"
            ])

        options = {
            "launcherName": APP_NAME,
            "gameDirectory": str(mc_dir),
            "username": username,
            "uuid": "00000000-0000-0000-0000-000000000000",
            "token": "0",
            "jvmArguments": jvm_args
        }
        
        if java_path:
            options["executablePath"] = java_path
            print(f"Using Java: {java_path}")
        else:
            print("Using System Default Java")

        print(f"Launching {ver_id}...")
        
        self.update_discord("Playing Minecraft", f"{name} ({config.get('loader')})", start_time=int(time.time()))

        cmd = mclib.command.get_minecraft_command(ver_id, str(mc_dir), options)
        
        process = subprocess.Popen(cmd, cwd=str(mc_dir))
        process.wait()
        
        self.update_discord("Idling", "In Launcher")

    def delete_instance(self, name):
        try: shutil.rmtree(BASE_DIR / name); return True
        except: return False

    def get_mods(self, instance_name):
        mods_dir = BASE_DIR / instance_name / ".minecraft/mods"
        if not mods_dir.exists(): return []
        found = []
        cache_updated = False
        def get_clean_name(path):
            key = f"{path.name}_{path.stat().st_size}"
            if key in self.name_cache: return self.name_cache[key]
            clean = path.name
            try:
                with zipfile.ZipFile(path, 'r') as z:
                    if 'fabric.mod.json' in z.namelist():
                        clean = json.loads(z.read('fabric.mod.json')).get('name', path.name)
            except: pass
            self.name_cache[key] = clean
            return clean
        
        for f in mods_dir.iterdir():
            if f.name.endswith('.jar') or f.name.endswith('.disabled'):
                found.append({'name': get_clean_name(f), 'filename': f.name, 'path': f, 'disabled': f.name.endswith('.disabled')})
                if f.name not in self.name_cache: cache_updated = True
        
        if cache_updated: self.save_cache()
        return sorted(found, key=lambda x: x['name'].lower())

    def toggle_mod(self, path):
        p = Path(path)
        try:
            if p.name.endswith(".disabled"): p.rename(p.parent / p.name[:-9])
            else: p.rename(p.parent / (p.name + ".disabled"))
            return True
        except: return False
    
    def delete_mod(self, path):
        try: os.remove(path); return True
        except: return False

    # --- INSTALLATION WITH CALLBACKS ---
    def install_instance(self, name, version, loader, callback=None):
        inst_dir = BASE_DIR / name
        if inst_dir.exists(): return False, "Instance name already exists."
        
        inst_dir.mkdir(parents=True)
        mc_dir = inst_dir / ".minecraft"
        mc_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            print(f"Installing Vanilla {version}...")
            mclib.install.install_minecraft_version(version, str(mc_dir), callback=callback)
            
            if loader == "Fabric":
                if callback: callback['setStatus']("Installing Fabric Loader...")
                mclib.fabric.install_fabric(version, str(mc_dir))
            elif loader == "Forge":
                if callback: callback['setStatus']("Searching for Forge...")
                forge_ver = mclib.forge.find_forge_version(version)
                if forge_ver is None: raise ValueError(f"No Forge found for {version}")
                if callback: callback['setStatus'](f"Installing Forge {forge_ver}...")
                mclib.forge.install_forge_version(forge_ver, str(mc_dir))
            
            with open(inst_dir / "instance.json", "w") as f: 
                json.dump({"name": name, "version": version, "loader": loader}, f)
                
            return True, "Created"
        except Exception as e:
            if inst_dir.exists(): shutil.rmtree(inst_dir)
            return False, f"Error: {str(e)}"

    def install_mod_from_store(self, project_id, instance_name, callback=None):
        cfg = self.get_instance_config(instance_name)
        loader_filter = cfg['loader'].lower()
        if loader_filter == "vanilla": loader_filter = "fabric" 
        target = self.modrinth.get_latest_version_file(project_id, [loader_filter], [cfg['version']])
        if not target: return False, "No compatible version found on Modrinth."
        
        save_path = BASE_DIR / instance_name / ".minecraft/mods" / target['filename']
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            if callback: callback['setStatus'](f"Downloading {target['filename']}...")
            with requests.get(target['url'], stream=True, headers=self.modrinth.HEADERS) as r:
                r.raise_for_status()
                total_len = int(r.headers.get('content-length', 0))
                dl = 0
                with open(save_path, 'wb') as f:
                     for chunk in r.iter_content(chunk_size=4096):
                        dl += len(chunk)
                        f.write(chunk)
                        if callback and total_len > 0:
                            callback['setProgress'](int((dl / total_len) * 100))
                            callback['setMax'](100)
            return True, f"Installed {target['filename']}"
        except Exception as e: return False, str(e)

    def install_modpack_from_store(self, project_id, pack_name, version_data, callback=None):
        inst_dir = BASE_DIR / pack_name
        if inst_dir.exists(): return False, "Name already taken"
        
        try:
            target_file = version_data['files'][0]
            temp_path = TEMP_DIR / target_file['filename']
            if callback: callback['setStatus'](f"Downloading {target_file['filename']}...")
            with requests.get(target_file['url'], stream=True, headers=self.modrinth.HEADERS) as r:
                total_len = int(r.headers.get('content-length', 0))
                dl = 0
                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=4096):
                        dl += len(chunk)
                        f.write(chunk)
                        if callback and total_len > 0: 
                             callback['setProgress'](int((dl / total_len) * 100))
                             callback['setMax'](100)

            if callback: callback['setStatus']("Extracting & Installing Modpack...")
            inst_dir.mkdir(parents=True)
            mc_dir = inst_dir / ".minecraft"
            mclib.mrpack.install_mrpack(str(temp_path), str(mc_dir))
            
            installed_vers = mclib.utils.get_installed_versions(str(mc_dir))
            final_version_id = None
            loader_type = "Modpack"
            for v in installed_vers:
                vid = v['id'].lower()
                if "fabric" in vid or "forge" in vid or "quilt" in vid or "neoforge" in vid:
                    final_version_id = v['id']
                    if "fabric" in vid: loader_type = "Fabric"
                    elif "forge" in vid: loader_type = "Forge"
                    break
            if not final_version_id and installed_vers: final_version_id = installed_vers[0]['id']

            with open(inst_dir / "instance.json", "w") as f:
                json.dump({"name": pack_name, "version": final_version_id, "loader": loader_type}, f)
            
            os.remove(temp_path)
            return True, f"Installed {pack_name}"
        except Exception as e:
            if inst_dir.exists(): shutil.rmtree(inst_dir)
            return False, str(e)

# --- UI COMPONENTS ---
class ProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent, title="Processing..."):
        super().__init__(parent)
        self.geometry("400x150")
        self.title(title)
        self.resizable(False, False)
        self.max_val = 100
        self.attributes("-topmost", True)
        self.lbl_status = ctk.CTkLabel(self, text="Starting...", font=("Arial", 12))
        self.lbl_status.pack(pady=(20, 5))
        self.progress = ctk.CTkProgressBar(self, width=300)
        self.progress.pack(pady=10)
        self.progress.set(0)
        self.lbl_percent = ctk.CTkLabel(self, text="0%", font=("Arial", 10, "bold"), text_color="gray")
        self.lbl_percent.pack(pady=(0, 20))

    def update_status(self, text): self.lbl_status.configure(text=text)
    def set_max(self, val): self.max_val = val
    def update_progress(self, val):
        if self.max_val > 0:
            perc = val / self.max_val
            self.progress.set(perc)
            self.lbl_percent.configure(text=f"{int(perc*100)}%")

# --- MAIN APP ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # --- ICON & TASKBAR FIX ---
        if platform.system() == "Windows":
            try:
                from ctypes import windll
                myappid = 'ibramod.launcher.v3.0'
                windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except: pass

        self.title(APP_NAME)
        self.geometry("1100x700")

        # Set Icon
        # Set Icon
        try:
            if platform.system() == "Windows":
                self.iconbitmap(ICON_FILE)
            else:
                # Linux/Mac support
                if ICON_PNG.exists():
                    img = ctk.CTkImage(Image.open(ICON_PNG))
                    self.iconphoto(True, img)
        except Exception as e:
            print(f"Icon load failed: {e}")

        self.backend = Backend()
        self.current_inst = None
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="INSTANCES", font=("Arial", 18, "bold")).pack(pady=(20,10))
        ctk.CTkButton(self.sidebar, text="+ New Instance", command=self.dialog_create).pack(pady=5)
        self.inst_list = ctk.CTkScrollableFrame(self.sidebar)
        self.inst_list.pack(fill="both", expand=True, padx=5, pady=10)
        
        # Login & Settings
        self.login_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.login_frame.pack(side="bottom", fill="x", padx=10, pady=20)
        ctk.CTkLabel(self.login_frame, text="Username:", font=("Arial", 12)).pack(anchor="w")
        self.entry_user = ctk.CTkEntry(self.login_frame, placeholder_text="Player")
        self.entry_user.pack(fill="x", pady=(0,5))
        ctk.CTkButton(self.login_frame, text="⚙ Launcher Settings", fg_color="#555", command=self.dialog_settings).pack(fill="x", pady=5)

        # Main
        self.main = ctk.CTkFrame(self, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        
        self.header = ctk.CTkFrame(self.main, height=60, fg_color="transparent")
        self.header.pack(fill="x", padx=20, pady=10)
        self.lbl_title = ctk.CTkLabel(self.header, text="Select Instance", font=("Arial", 24))
        self.lbl_title.pack(side="left")
        
        self.header_btns = ctk.CTkFrame(self.header, fg_color="transparent")
        self.header_btns.pack(side="right")
        self.btn_delete = ctk.CTkButton(self.header_btns, text="DELETE", font=("Arial", 14, "bold"), fg_color="#C0392B", width=100, height=40, state="disabled", command=self.confirm_delete)
        self.btn_delete.pack(side="left", padx=10)
        self.btn_play = ctk.CTkButton(self.header_btns, text="PLAY", font=("Arial", 18, "bold"), fg_color="green", width=150, height=40, state="disabled", command=self.launch)
        self.btn_play.pack(side="left")

        self.tabs = ctk.CTkTabview(self.main)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=10)
        self.tab_mods = self.tabs.add("My Mods")
        self.tab_getmods = self.tabs.add("Get Mods")
        self.tab_packs = self.tabs.add("Get Modpacks")
        
        self._setup_mymods()
        self._setup_getmods()
        self._setup_getpacks()
        self.refresh_instances()

    def _setup_mymods(self):
        self.mymods_scroll = ctk.CTkScrollableFrame(self.tab_mods)
        self.mymods_scroll.pack(fill="both", expand=True)

    def _setup_getmods(self):
        frame = ctk.CTkFrame(self.tab_getmods, fg_color="transparent")
        frame.pack(fill="x", pady=5)
        self.entry_mod = ctk.CTkEntry(frame, placeholder_text="Search Mods...")
        self.entry_mod.pack(side="left", fill="x", expand=True, padx=(0,5))
        self.entry_mod.bind("<Return>", lambda e: self.search_store("mod"))
        ctk.CTkButton(frame, text="Search", width=80, command=lambda: self.search_store("mod")).pack(side="right")
        self.store_mod_scroll = ctk.CTkScrollableFrame(self.tab_getmods)
        self.store_mod_scroll.pack(fill="both", expand=True)

    def _setup_getpacks(self):
        frame = ctk.CTkFrame(self.tab_packs, fg_color="transparent")
        frame.pack(fill="x", pady=5)
        self.entry_pack = ctk.CTkEntry(frame, placeholder_text="Search Modpacks...")
        self.entry_pack.pack(side="left", fill="x", expand=True, padx=(0,5))
        self.entry_pack.bind("<Return>", lambda e: self.search_store("modpack"))
        ctk.CTkButton(frame, text="Search", width=80, command=lambda: self.search_store("modpack")).pack(side="right")
        self.store_pack_scroll = ctk.CTkScrollableFrame(self.tab_packs)
        self.store_pack_scroll.pack(fill="both", expand=True)

    def refresh_instances(self):
        for w in self.inst_list.winfo_children(): w.destroy()
        for i in self.backend.get_instances():
            ctk.CTkButton(self.inst_list, text=i, fg_color="transparent", border_width=1, command=lambda x=i: self.load_instance(x)).pack(fill="x", pady=2)

    def load_instance(self, name):
        self.current_inst = name
        self.lbl_title.configure(text=name)
        self.btn_play.configure(state="normal")
        self.btn_delete.configure(state="normal")
        threading.Thread(target=self.refresh_mymods_async, daemon=True).start()

    def confirm_delete(self):
        if not self.current_inst: return
        answer = messagebox.askyesno("Delete", f"Delete '{self.current_inst}'?")
        if answer:
            self.backend.delete_instance(self.current_inst)
            self.current_inst = None
            self.lbl_title.configure(text="Select Instance")
            self.btn_play.configure(state="disabled")
            self.btn_delete.configure(state="disabled")
            for w in self.mymods_scroll.winfo_children(): w.destroy()
            self.refresh_instances()

    def refresh_mymods_async(self):
        mods = self.backend.get_mods(self.current_inst)
        self.after(0, lambda: self.render_mymods(mods))

    def render_mymods(self, mods):
        for w in self.mymods_scroll.winfo_children(): w.destroy()
        if not mods: ctk.CTkLabel(self.mymods_scroll, text="No mods installed.").pack(pady=20); return
        for m in mods:
            row = ctk.CTkFrame(self.mymods_scroll)
            row.pack(fill="x", pady=2)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", padx=10)
            ctk.CTkLabel(info, text=m['name'], font=("Arial", 14, "bold")).pack(anchor="w")
            ctk.CTkLabel(info, text=m['filename'], font=("Arial", 10), text_color="gray").pack(anchor="w")
            ctk.CTkButton(row, text="X", width=30, fg_color="#C0392B", command=lambda p=m['path']: [self.backend.delete_mod(p), self.load_instance(self.current_inst)]).pack(side="right", padx=5)
            state_text, col = ("Enable", "green") if m['disabled'] else ("Disable", "#444")
            ctk.CTkButton(row, text=state_text, width=60, fg_color=col, command=lambda p=m['path']: [self.backend.toggle_mod(p), self.load_instance(self.current_inst)]).pack(side="right", padx=5)

    def launch(self):
        user = self.entry_user.get()
        if not user: user = "Player"
        if self.current_inst: 
            self.btn_play.configure(text="RUNNING...", state="disabled", fg_color="gray")
            def run():
                self.backend.launch(self.current_inst, user)
                self.after(0, lambda: self.btn_play.configure(text="PLAY", state="normal", fg_color="green"))
            threading.Thread(target=run).start()

    def search_store(self, stype):
        query = self.entry_mod.get() if stype == "mod" else self.entry_pack.get()
        if not query: return
        scroll = self.store_mod_scroll if stype == "mod" else self.store_pack_scroll
        for w in scroll.winfo_children(): w.destroy()
        ctk.CTkLabel(scroll, text="Searching...").pack(pady=20)
        ver, loader = None, None
        if stype == "mod" and self.current_inst:
            config = self.backend.get_instance_config(self.current_inst)
            ver, loader = config.get('version'), config.get('loader')
        def task():
            hits = self.backend.modrinth.search(query, facet_type=stype, version=ver, loader=loader)
            self.after(0, lambda: self.render_results(hits, stype, scroll))
        threading.Thread(target=task).start()

    def render_results(self, hits, stype, scroll):
        for w in scroll.winfo_children(): w.destroy()
        if not hits: ctk.CTkLabel(scroll, text="No results.").pack(pady=20); return
        installed = set()
        if stype == "mod" and self.current_inst: 
            installed = {m['name'].strip().lower() for m in self.backend.get_mods(self.current_inst)}
        for hit in hits:
            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", pady=5)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10)
            title = hit['title']
            ctk.CTkLabel(info, text=title, font=("Arial", 14, "bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(info, text=(hit['description'] or "")[:80]+"...", text_color="gray", anchor="w").pack(fill="x")
            if stype == "mod":
                if title.strip().lower() in installed:
                    ctk.CTkButton(row, text="✓ Installed", width=100, state="disabled", fg_color="gray").pack(side="right", padx=10)
                else:
                    ctk.CTkButton(row, text="Install", width=100, command=lambda pid=hit['project_id'], t=title: self.install_mod(pid, t)).pack(side="right", padx=10)
            else:
                ctk.CTkButton(row, text="Install Pack", width=100, fg_color="#D35400", command=lambda pid=hit['project_id'], t=title: self.install_pack_dialog(pid, t)).pack(side="right", padx=10)

    def install_mod(self, pid, title):
        if not self.current_inst: return messagebox.showerror("Error", "Select an instance first!")
        prog = ProgressDialog(self, title=f"Installing {title}...")
        prog.protocol("WM_DELETE_WINDOW", lambda: None)
        callback = {
            "setStatus": lambda text: self.after(0, lambda: prog.update_status(text)),
            "setProgress": lambda val: self.after(0, lambda: prog.update_progress(val)),
            "setMax": lambda val: self.after(0, lambda: prog.set_max(val))
        }
        def task():
            res, msg = self.backend.install_mod_from_store(pid, self.current_inst, callback)
            self.after(0, prog.destroy)
            if res:
                self.after(0, lambda: self.load_instance(self.current_inst)) 
                self.after(0, lambda: self.search_store("mod"))
            else:
                self.after(0, lambda: messagebox.showerror("Error", msg))
        threading.Thread(target=task).start()

    def install_pack_dialog(self, pid, title):
        d = ctk.CTkToplevel(self)
        d.geometry("300x150")
        d.title("Install Pack")
        ctk.CTkLabel(d, text=f"Install '{title}' as:").pack(pady=10)
        e_name = ctk.CTkEntry(d)
        e_name.pack()
        e_name.insert(0, title)
        def next_step():
            pack_name = e_name.get()
            d.destroy()
            self.open_version_selector(pid, pack_name, loading=True)
        ctk.CTkButton(d, text="Next", command=next_step).pack(pady=10)

    def open_version_selector(self, pid, name, versions=None, loading=False):
        top = ctk.CTkToplevel(self)
        top.title(f"Select Version: {name}")
        top.geometry("400x500")
        ctk.CTkLabel(top, text="Choose Version", font=("Arial", 16, "bold")).pack(pady=10)
        scroll = ctk.CTkScrollableFrame(top)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)
        if loading:
            lbl = ctk.CTkLabel(scroll, text="Fetching versions...", font=("Arial", 14))
            lbl.pack(pady=50)
            threading.Thread(target=lambda: self.fetch_versions_async(pid, name, top, scroll, lbl)).start()
        else: self.populate_versions(scroll, pid, name, versions, top)

    def fetch_versions_async(self, pid, name, top, scroll, lbl):
        versions = self.backend.modrinth.get_project_versions(pid)
        self.after(0, lambda: self.update_version_list(top, scroll, lbl, pid, name, versions))

    def update_version_list(self, top, scroll, lbl, pid, name, versions):
        if not top.winfo_exists(): return 
        lbl.destroy()
        if not versions: ctk.CTkLabel(scroll, text="No versions found.").pack(pady=20)
        else: self.populate_versions(scroll, pid, name, versions, top)

    def populate_versions(self, scroll, pid, name, versions, top):
        for v in versions:
            v_name = f"{v['name']} ({v['game_versions'][0]})"
            ctk.CTkButton(scroll, text=v_name, fg_color="#333", anchor="w",
                          command=lambda v_data=v: [top.destroy(), self.run_pack_install(pid, name, v_data)]).pack(fill="x", pady=2)

    def run_pack_install(self, pid, name, vdata):
        prog = ProgressDialog(self, title=f"Installing {name}")
        prog.protocol("WM_DELETE_WINDOW", lambda: None)
        callback = {
            "setStatus": lambda text: self.after(0, lambda: prog.update_status(text)),
            "setProgress": lambda val: self.after(0, lambda: prog.update_progress(val)),
            "setMax": lambda val: self.after(0, lambda: prog.set_max(val))
        }
        def task():
            res, msg = self.backend.install_modpack_from_store(pid, name, vdata, callback)
            self.after(0, prog.destroy)
            if res:
                self.after(0, lambda: messagebox.showinfo("Success", msg))
                self.after(0, self.refresh_instances)
            else:
                self.after(0, lambda: messagebox.showerror("Error", msg))
        threading.Thread(target=task).start()

    def dialog_settings(self):
        d = ctk.CTkToplevel(self)
        d.geometry("450x450")
        d.title("Settings")
        
        settings = self.backend.get_settings()
        
        # RAM
        ctk.CTkLabel(d, text="Max RAM (GB)", font=("Arial", 14, "bold")).pack(pady=(20, 5))
        lbl_ram = ctk.CTkLabel(d, text=f"{settings['max_ram']} GB")
        lbl_ram.pack()
        slider_ram = ctk.CTkSlider(d, from_=1, to=16, number_of_steps=15, command=lambda v: lbl_ram.configure(text=f"{int(v)} GB"))
        slider_ram.set(settings['max_ram'])
        slider_ram.pack(fill="x", padx=40, pady=5)
        
        # Low End Mode
        ctk.CTkLabel(d, text="Performance", font=("Arial", 14, "bold")).pack(pady=(20, 5))
        var_lowend = ctk.BooleanVar(value=settings['low_end_mode'])
        sw_lowend = ctk.CTkSwitch(d, text="Low End PC Mode (FPS Boost)", variable=var_lowend)
        sw_lowend.pack(pady=5)
        
        # Java Path
        ctk.CTkLabel(d, text="Java Executable", font=("Arial", 14, "bold")).pack(pady=(20, 5))
        java_paths = self.backend.find_java_paths()
        combo_java = ctk.CTkComboBox(d, values=java_paths, width=300)
        combo_java.set(settings.get("java_path", "Auto"))
        combo_java.pack(pady=5)
        ctk.CTkLabel(d, text="Set to 'Auto' to let IbraMod pick Java 8/17/21 automatically.", text_color="gray", font=("Arial", 10)).pack()

        def save():
            new_data = {
                "max_ram": int(slider_ram.get()),
                "low_end_mode": var_lowend.get(),
                "java_path": combo_java.get()
            }
            self.backend.save_settings(new_data)
            messagebox.showinfo("Saved", "Settings Updated!")
            d.destroy()
            
        ctk.CTkButton(d, text="Save Settings", command=save, fg_color="green").pack(pady=30)

    def dialog_create(self):
        d = ctk.CTkToplevel(self)
        d.geometry("300x350") 
        d.title("Create Instance")
        ctk.CTkLabel(d, text="Instance Name").pack(pady=(10,0))
        en = ctk.CTkEntry(d)
        en.pack(pady=5)
        ctk.CTkLabel(d, text="Game Version").pack(pady=(10,0))
        ver_frame = ctk.CTkFrame(d, fg_color="transparent")
        ver_frame.pack(fill="x", padx=40)
        ev = ctk.CTkEntry(ver_frame)
        ev.pack(side="left", fill="x", expand=True)
        ev.insert(0, "1.20.1")
        def fetch_latest():
            btn_latest.configure(text="Fetching...", state="disabled")
            def run():
                latest = self.backend.get_latest_mc_version()
                self.after(0, lambda: [ev.delete(0, 'end'), ev.insert(0, latest if latest else "Error"), btn_latest.configure(text="Get Latest", state="normal")])
            threading.Thread(target=run).start()
        btn_latest = ctk.CTkButton(ver_frame, text="Get Latest", width=80, command=fetch_latest)
        btn_latest.pack(side="right", padx=(5,0))
        ctk.CTkLabel(d, text="Mod Loader").pack(pady=(10,0))
        loader_var = ctk.StringVar(value="Fabric")
        ctk.CTkOptionMenu(d, values=["Vanilla", "Fabric", "Forge"], variable=loader_var).pack(pady=5)
        def run_install():
            name_val = en.get()
            ver_val = ev.get()
            loader_val = loader_var.get()
            if not name_val: return messagebox.showerror("Error", "Please enter a name")
            d.destroy()
            prog = ProgressDialog(self, title=f"Installing {name_val}...")
            prog.protocol("WM_DELETE_WINDOW", lambda: None)
            callback = {
                "setStatus": lambda text: self.after(0, lambda: prog.update_status(text)),
                "setProgress": lambda val: self.after(0, lambda: prog.update_progress(val)),
                "setMax": lambda val: self.after(0, lambda: prog.set_max(val))
            }
            def task():
                res, msg = self.backend.install_instance(name_val, ver_val, loader_val, callback)
                self.after(0, prog.destroy)
                if not res: self.after(0, lambda: messagebox.showerror("Error", msg))
                else: self.after(0, self.refresh_instances)
            threading.Thread(target=task).start()
        ctk.CTkButton(d, text="Create", command=run_install).pack(pady=20)

if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    App().mainloop()
