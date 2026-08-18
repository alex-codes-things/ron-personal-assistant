# Moving from the old Ron layout

The cleaned layout is safest to use as a new folder first. Keep the old Ron folder untouched until the new one starts correctly.

## 1. Extract the cleaned project

Extract `Ron_Clean.zip` beside your existing Ron project, not over it.

## 2. Copy your runtime files

From the cleaned project folder, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\import_old_runtime.ps1 -OldProject "C:\Development\Fun-Projects\RonPersonal"
```

Change the path if your old Ron project lives somewhere else. The helper **copies** your old `data`, `models`, `logs`, `recordings`, and `.env` into the new layout. It does not delete or modify the old project.

Your large voice models will end up under:

```text
runtime\models\voice\
```

## 3. Create a fresh virtual environment

Do not copy the old `.venv`; it contains generated package files and editable-install paths for the old layout.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[voice,desktop-preview,dev]"
```

## 4. Verify Ron

```powershell
python -m pytest
python -m ron
```

If the Nexus 7 needs the current app installed again:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_tablet_face.ps1
```

## 5. Only then retire the old folder

Once voice, Spotify, reminders and the tablet are working, the old project can remain as a backup or be archived.

### Optional: preserve Git history

If the old project is a Git repository, you can copy its `.git` directory into the cleaned project before committing the refactor. Run `git status` and review the large layout change before committing it.
