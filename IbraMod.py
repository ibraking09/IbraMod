import customtkinter as ctk
import minecraft_launcher_lib as mclib
import subprocess
import threading
import json
import os
import requests
import zipfile
import shutil
from pathlib import Path
from tkinter import messagebox

# --- Constants ---
APP_NAME = "IbraMod Launcher"
BASE_DIR = Path.cwd() / "instances"
CACHE_FILE = Path.cwd() / "name_cache.json"
SETTINGS_FILE = Path.cwd() / "settings.json"
TEMP_DIR = Path.cwd() / "temp"

if not BASE_DIR.exists(): BASE_DIR.mkdir(parents=True)
if not TEMP_DIR.exists(): TEMP_DIR.mkdir(parents=True)

# --- Modrinth API Client ---
class Modrinth:
    BASE = "https://api.modrinth.com/v2"

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

        params = {
            'query': query, 
            'limit': 20, 
            'index': index, 
            'facets': json.dumps(facets_list)
        }
        
        try: 
            return requests.get(f"{self.BASE}/search", params=params).json().get('hits', [])
        except Exception as e: 
            print(f"Search error: {e}")
            return []

    def get_latest_version_file(self, project_id, loaders, game_versions=None):
        params = {'loaders': str(loaders).replace("'", '"')}
        if game_versions: params['game_versions'] = str(game_versions).replace("'", '"')
        resp = requests.get(f"{self.BASE}/project/{project_id}/version", params=params).json()
        return resp[0]['files'][0] if resp else None

    def get_project_versions(self, project_id):
        try:
            return requests.get(f"{self.BASE}/project/{project_id}/version").json()
        except: return []

# --- Backend Logic ---
class Backend:
    def __init__(self):
        self.modrinth = Modrinth()
        self.name_cache = self.load_cache()

    def load_cache(self):
        if CACHE_FILE.exists():
            try: return json.loads(CACHE_FILE.read_text())
            except: return {}
        return {}

    def save_cache(self):
        with open(CACHE_FILE, "w") as f: json.dump(self.name_cache, f, indent=4)

    # --- RAM Settings Logic ---
    def get_ram_setting(self):
        """Returns RAM in GB (int)"""
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
                return data.get("max_ram", 4) # Default to 4GB
            except: return 4
        return 4

    def set_ram_setting(self, gb):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump({"max_ram": int(gb)}, f)
            return True
        except: return False

    def get_instances(self):
        return sorted([d.name for d in BASE_DIR.iterdir() if d.is_dir()])

    def get_instance_config(self, name):
        try: return json.loads((BASE_DIR / name / "instance.json").read_text())
        except: return {"version": "Unknown", "loader": "Vanilla"}

    def launch(self, name, username):
        inst_dir = BASE_DIR / name
        mc_dir = inst_dir / ".minecraft"
        config = self.get_instance_config(name)
        
        # 1. Get RAM setting
        ram_gb = self.get_ram_setting()
        
        # 2. Try to use the exact version ID we saved
        ver_id = config.get("version")
        installed = mclib.utils.get_installed_versions(str(mc_dir))
        installed_ids = [v['id'] for v in installed]
        
        # 3. If saved ID is missing or invalid, try to guess intelligently
        if not ver_id or ver_id not in installed_ids:
            print(f"Saved version {ver_id} not found. Searching...")
            loader_type = config.get("loader", "Vanilla").lower()
            ver_id = None
            
            for vid in installed_ids:
                if loader_type == "fabric" and "fabric" in vid.lower(): ver_id = vid; break
                elif loader_type == "forge" and "forge" in vid.lower(): ver_id = vid; break
                elif loader_type == "modpack" and ("fabric" in vid.lower() or "forge" in vid.lower()): ver_id = vid; break
            
            if not ver_id and installed_ids: 
                ver_id = installed_ids[0]

        print(f"Launching {ver_id} with {ram_gb}GB RAM...")

        options = {
            "launcherName": APP_NAME,
            "gameDirectory": str(mc_dir),
            "username": username,
            "uuid": "00000000-0000-0000-0000-000000000000",
            "token": "0",
            "jvmArguments": [f"-Xmx{ram_gb}G", "-Xms512M"] # Inject RAM arguments here
        }
        
        cmd = mclib.command.get_minecraft_command(ver_id, str(mc_dir), options)
        subprocess.Popen(cmd, cwd=str(mc_dir))

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
            if f.suffix in ['.jar', '.disabled']:
                found.append({'name': get_clean_name(f), 'filename': f.name, 'path': f, 'disabled': f.suffix == '.disabled'})
                if f.name not in self.name_cache: cache_updated = True
        if cache_updated: self.save_cache()
        return sorted(found, key=lambda x: x['name'].lower())

    def toggle_mod(self, path):
        p = Path(path)
        try: p.rename(p.with_suffix("" if p.suffix == ".disabled" else ".disabled")); return True
        except: return False
        
    def delete_mod(self, path):
        try: os.remove(path); return True
        except: return False

    def install_instance(self, name, version, loader):
        inst_dir = BASE_DIR / name
        if inst_dir.exists(): return False, "Instance name already exists."
        
        inst_dir.mkdir(parents=True)
        mc_dir = inst_dir / ".minecraft"
        mc_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            print(f"Installing Vanilla {version}...")
            mclib.install.install_minecraft_version(version, str(mc_dir))
            
            if loader == "Fabric":
                print("Installing Fabric...")
                mclib.fabric.install_fabric(version, str(mc_dir))
                
            elif loader == "Forge":
                print(f"Searching for Forge version for {version}...")
                forge_ver = mclib.forge.find_forge_version(version)
                if forge_ver is None: raise ValueError(f"No Forge found for {version}")
                print(f"Installing Forge {forge_ver}...")
                mclib.forge.install_forge_version(forge_ver, str(mc_dir))
            
            with open(inst_dir / "instance.json", "w") as f: 
                json.dump({"name": name, "version": version, "loader": loader}, f)
                
            return True, "Created"
            
        except Exception as e:
            if inst_dir.exists(): shutil.rmtree(inst_dir)
            print(f"Install failed: {e}")
            return False, f"Error: {str(e)}"

    def install_mod_from_store(self, project_id, instance_name):
        cfg = self.get_instance_config(instance_name)
        loader_filter = cfg['loader'].lower()
        if loader_filter == "vanilla": loader_filter = "fabric" 
        
        target = self.modrinth.get_latest_version_file(project_id, [loader_filter], [cfg['version']])
        if not target: return False, "No compatible version"
        
        save_path = BASE_DIR / instance_name / ".minecraft/mods" / target['filename']
        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(target['url'], stream=True) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f: f.write(r.content)
            return True, f"Installed {target['filename']}"
        except Exception as e: return False, str(e)

    def install_modpack_from_store(self, project_id, pack_name, version_data=None):
        if not version_data:
            return False, "Version data missing"

        inst_dir = BASE_DIR / pack_name
        if inst_dir.exists(): return False, "Name already taken"
        
        try:
            target_file = version_data['files'][0]
            temp_path = TEMP_DIR / target_file['filename']
            print(f"Downloading {target_file['filename']}...")
            with requests.get(target_file['url'], stream=True) as r:
                with open(temp_path, 'wb') as f: shutil.copyfileobj(r.raw, f)
            
            print("Installing Modpack Files...")
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
            
            if not final_version_id and installed_vers:
                final_version_id = installed_vers[0]['id']

            with open(inst_dir / "instance.json", "w") as f:
                json.dump({
                    "name": pack_name, 
                    "version": final_version_id,
                    "loader": loader_type
                }, f)
            
            os.remove(temp_path)
            return True, f"Installed {pack_name}"

        except Exception as e:
            if inst_dir.exists(): shutil.rmtree(inst_dir)
            return False, str(e)

# --- UI ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.backend = Backend()
        self.current_inst = None
        self.title(APP_NAME)
        self.geometry("1100x700")
        
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
        
        # NEW SETTINGS BUTTON
        ctk.CTkButton(self.login_frame, text="⚙ Settings (RAM)", fg_color="#555", command=self.dialog_settings).pack(fill="x", pady=5)

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
        ctk.CTkButton(frame, text="Find Skin Mods", width=120, fg_color="#8E44AD", command=self.find_skin_mods).pack(side="right", padx=5)
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
        if self.current_inst: threading.Thread(target=lambda: self.backend.launch(self.current_inst, user)).start()

    def find_skin_mods(self):
        if not self.current_inst: messagebox.showerror("Error", "Select instance first!"); return
        config = self.backend.get_instance_config(self.current_inst)
        loader = config.get('loader', 'Vanilla').lower()
        term = "Fabric Tailor" if loader == "fabric" else "Custom Skin Loader"
        if loader == "vanilla": term = "skin"
        self.entry_mod.delete(0, 'end'); self.entry_mod.insert(0, term)
        self.search_store("mod")

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
        if stype == "mod" and self.current_inst: installed = {m['name'].lower() for m in self.backend.get_mods(self.current_inst)}
        
        for hit in hits:
            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", pady=5)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10)
            ctk.CTkLabel(info, text=hit['title'], font=("Arial", 14, "bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(info, text=(hit['description'] or "")[:80]+"...", text_color="gray", anchor="w").pack(fill="x")
            
            if stype == "mod":
                if hit['title'].strip().lower() in installed: 
                    ctk.CTkButton(row, text="Installed", width=80, state="disabled", fg_color="gray").pack(side="right", padx=10)
                else: 
                    ctk.CTkButton(row, text="Install", width=80, command=lambda pid=hit['project_id']: self.install_mod(pid)).pack(side="right", padx=10)
            else:
                ctk.CTkButton(row, text="Install Pack", width=100, fg_color="#D35400", command=lambda pid=hit['project_id'], t=hit['title']: self.install_pack_dialog(pid, t)).pack(side="right", padx=10)

    def install_mod(self, pid):
        if not self.current_inst: return messagebox.showerror("Error", "Select an instance first!")
        def task():
            res, msg = self.backend.install_mod_from_store(pid, self.current_inst)
            print(msg); self.after(0, lambda: self.load_instance(self.current_inst))
        threading.Thread(target=task).start()

    def install_pack_dialog(self, pid, title):
        d = ctk.CTkToplevel(self)
        d.geometry("300x150")
        d.title("Install Pack")
        ctk.CTkLabel(d, text=f"Install '{title}' as:").pack(pady=10)
        e_name = ctk.CTkEntry(d); e_name.pack(); e_name.insert(0, title)
        
        def run_check():
            name = e_name.get()
            d.destroy()
            self.initiate_pack_install(pid, name)
            
        ctk.CTkButton(d, text="Next", command=run_check).pack(pady=10)

    def initiate_pack_install(self, pid, name):
        self.open_version_selector(pid, name, loading=True)

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
        else:
            self.populate_versions(scroll, pid, name, versions, top)

    def fetch_versions_async(self, pid, name, top, scroll, lbl):
        versions = self.backend.modrinth.get_project_versions(pid)
        self.after(0, lambda: self.update_version_list(top, scroll, lbl, pid, name, versions))

    def update_version_list(self, top, scroll, lbl, pid, name, versions):
        if not top.winfo_exists(): return 
        lbl.destroy()
        if not versions:
            ctk.CTkLabel(scroll, text="No versions found.").pack(pady=20)
        else:
            self.populate_versions(scroll, pid, name, versions, top)

    def populate_versions(self, scroll, pid, name, versions, top):
        for v in versions:
            v_name = f"{v['name']} ({v['game_versions'][0]})"
            ctk.CTkButton(scroll, text=v_name, fg_color="#333", anchor="w",
                          command=lambda v_data=v: [top.destroy(), self.run_pack_install(pid, name, v_data)]).pack(fill="x", pady=2)

    def run_pack_install(self, pid, name, vdata):
        def task():
            res, msg = self.backend.install_modpack_from_store(pid, name, vdata)
            if res:
                self.after(0, lambda: messagebox.showinfo("Success", msg))
                self.after(0, self.refresh_instances)
            else:
                self.after(0, lambda: messagebox.showerror("Error", msg))
        threading.Thread(target=task).start()

    # --- SETTINGS DIALOG (NEW) ---
    def dialog_settings(self):
        d = ctk.CTkToplevel(self)
        d.geometry("400x250")
        d.title("Settings")
        
        ctk.CTkLabel(d, text="Max RAM (GB)", font=("Arial", 16, "bold")).pack(pady=(20, 10))
        
        current_ram = self.backend.get_ram_setting()
        
        lbl_val = ctk.CTkLabel(d, text=f"{current_ram} GB", font=("Arial", 14))
        lbl_val.pack(pady=5)
        
        def update_label(val):
            lbl_val.configure(text=f"{int(val)} GB")

        slider = ctk.CTkSlider(d, from_=1, to=16, number_of_steps=15, command=update_label)
        slider.pack(pady=10, fill="x", padx=40)
        slider.set(current_ram)
        
        def save():
            self.backend.set_ram_setting(int(slider.get()))
            messagebox.showinfo("Saved", f"RAM set to {int(slider.get())} GB")
            d.destroy()

        ctk.CTkButton(d, text="Save Settings", command=save, fg_color="green").pack(pady=20)

    def dialog_create(self):
        d = ctk.CTkToplevel(self)
        d.geometry("300x300")
        d.title("Create Instance")
        ctk.CTkLabel(d, text="Instance Name").pack(pady=(10,0))
        en = ctk.CTkEntry(d); en.pack(pady=5)
        ctk.CTkLabel(d, text="Game Version (e.g. 1.20.1)").pack(pady=(10,0))
        ev = ctk.CTkEntry(d); ev.pack(pady=5); ev.insert(0, "1.20.1")
        ctk.CTkLabel(d, text="Mod Loader").pack(pady=(10,0))
        loader_var = ctk.StringVar(value="Fabric")
        ctk.CTkOptionMenu(d, values=["Vanilla", "Fabric", "Forge"], variable=loader_var).pack(pady=5)
        
        def run():
            res, msg = self.backend.install_instance(en.get(), ev.get(), loader_var.get())
            if not res: self.after(0, lambda: messagebox.showerror("Error", msg))
            else: self.after(0, self.refresh_instances)
            self.after(0, d.destroy)
        
        ctk.CTkButton(d, text="Create", command=lambda: [ctk.CTkButton(d, text="Creating...", state="disabled").pack(pady=20), threading.Thread(target=run).start()]).pack(pady=20)

if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    App().mainloop()
