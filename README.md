# IbraMod

**IbraMod** is a lightweight Minecraft Mod Manager and Launcher built with Python.

This started as a student project because I wanted a way to easily manage separate instances (like having one folder for Fabric 1.20 and another for Forge 1.19) without the bloat of larger launchers. It lets you search and install mods directly from Modrinth, and keeps your instances isolated so your modpacks don't break each other.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Instance Management:** Create separate folders for different game versions.
- **Modrinth Integration:** Search for mods and modpacks inside the app. It even detects if you already have a mod installed so you don't download duplicates.
- **Mod Management:** Enable, disable, or delete mods with a single click.
- **Clean UI:** Built with CustomTkinter for a modern dark-mode look.

## Installation

### Option 1: Download the App (Easiest)

You don't need Python installed to run this. Just grab the executable for your OS from the **[Releases](../../releases)** page.

- **Windows:** Download `IbraMod.zip` and extract it, then run IbraMod.exe.
- **Linux:** Download the binary (named `IbraMod.AppImage`), make it executable (`chmod +x IbraMod.AppImage`), and run it.

### Option 2: Run from Source (Required for MacOS)

If you prefer to run the raw Python code:

1. Clone this repository:
```bash
git clone https://github.com/ibraking09/IbraMod.git
cd IbraMod
```

2. Install the requirements:
```bash
pip install -r requirements.txt
```

3. Run the manager:
```bash
python IbraMod.py
```

## How to Update

To update the launcher to a new version without losing your worlds, mods, or instances, follow these simple steps.

### Windows
1. Download the new `IbraMod.zip` from the [Releases page](../../releases).
2. Open the folder where your current `IbraMod.exe` is located.
3. **Delete** the old `IbraMod.exe` file.
4. Open the new `IbraMod.zip` and drag the new `IbraMod.exe` into your launcher folder.
   > **Important:** Do not delete or replace your `instances` folder. That is where your data is saved.
5. Run the new `IbraMod.exe`. All your existing instances will be there.

### Linux
1. Download the new `IbraMod.AppImage` from the [Releases page](../../releases).
2. Navigate to the folder containing your current AppImage.
3. **Delete** the old `.AppImage` file.
4. Move the new `IbraMod.AppImage` into the same folder.
5. Make sure the new file is executable:
   ```bash
   chmod +x IbraMod.AppImage

## Building it Yourself

If you want to compile the `.exe` or binary yourself (or if you are on Linux and want to build the executable locally), I use **PyInstaller**.

**Linux Command:**
```bash
pyinstaller --noconfirm --onefile --windowed --name "IbraMod" --collect-all customtkinter mc_manager.py
```

**Windows:**
The project includes a GitHub Actions workflow that automatically builds the `.exe` whenever a new tag is pushed.  
Check the `.github/workflows` folder to see how it works.
