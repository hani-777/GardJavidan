# GardJavidan GitHub Chat Client

This project is built as a simple emergency chat system using GitHub, designed to work in restricted environments where normal messaging platforms may be blocked.

Because GitHub access is still partially available in iran, this tool uses GitHub Issues as a communication channel.

Warning: messages refresh every 10 seconds while active and every 60 seconds in the background.
There may be slight delays, but this is intentional to avoid GitHub rate limits and reduce the risk of being blocked.

This tool was created by the three of us because of the constant internet disruptions in Iran, so we can stay in touch ourselves and keep our work going.
[Hani](https://github.com/hani-777)
[Mani](https://github.com/Mohammadreza-Nemati)
[Rexa](https://github.com/thisisrexa)

---

## Project Structure

- `main.py` starts the app.
- `app.py` contains the CustomTkinter UI and chat behavior.
- `github_api.py` contains GitHub API requests and error handling.
- `design.py` contains colors, spacing, font, and layout constants.
- `message_format.py` contains message/reply metadata parsing and formatting.
- `config.py` contains default config values and small config helpers.

---

## First Setup

### 1. Install Python

Install Python 3.10 or newer from:

https://www.python.org/downloads/

During installation, enable:

```txt
Add Python to PATH
```

---

### 2. Install Required Packages

Open Terminal / PowerShell inside the project folder and run:

```bash
pip install customtkinter requests pillow arabic-reshaper python-bidi
```

The app bundles the free Google Font `Vazirmatn` in `assets/fonts/` and loads it at startup, so users do not need to install the font in Windows. If the bundled font cannot be loaded, the app falls back to the system UI font.

---

### 3. Create `config.json`

Create a file named:

```txt
config.json
```

Do not upload this file to GitHub.

Use this template:

```json
{
  "owner": "hani-777",
  "repo": "GardJavidan",
  "issue_number": 1,
  "token": "",
  "display_name": "Your Name",
  "github_username": "your-github-login",
  "request_timeout_seconds": 10,
  "active_poll_seconds": 10,
  "background_poll_seconds": 60,
  "image_upload_folder": "chat_uploads",
  "max_image_upload_mb": 5,
  "user_colors": {
    "hani-777": "#24332f"
  }
}
```

---

## How to Get GitHub Token

Each user must create their own GitHub token.

### Option A: Fine-grained token

Use this option if you are creating a fine-grained personal access token.

1. Open GitHub
2. Click profile picture
3. Go to `Settings`
4. Go to `Developer settings`
5. Go to `Personal access tokens`
6. Open `Fine-grained tokens`
7. Click `Generate new token`
8. Set a token name, for example `Chat Room`
9. Set an expiration date
10. Set `Repository access` to `Only select repositories`
11. Select this repository, for example `3-Soldier`

Use these repository permissions:

```txt
Contents: Read and write
Issues: Read and write
Metadata: Read-only
```

`Metadata: Read-only` is usually enabled automatically by GitHub.

Then click:

```txt
Generate token
```

If GitHub does not let you edit an existing fine-grained token, create a new token with the permissions above and replace the old token in `config.json`.

### Option B: Classic token

Use this option if you are creating a classic personal access token.

1. Open GitHub
2. Click profile picture
3. Go to `Settings`
4. Go to `Developer settings`
5. Go to `Personal access tokens`
6. Open `Tokens (classic)`
7. Click `Generate new token classic`
8. Set a note, for example `Chat Room`
9. Set an expiration date

Use this scope:

```txt
repo
```

Only the main `repo` scope is needed. It covers reading/writing issue comments and uploading image files into the repository.

### Add Token to Config

Copy the generated token and paste it into `config.json`:

```json
"token": "ghp_xxxxxxxxx"
```

Each user must create and use their own token. Never share one token between users.
Polling runs every 10 seconds while the app is active and every 60 seconds while it is minimized or in the background.

Images are uploaded into the repository under `chat_uploads/` and then posted into the same issue as Markdown image links. Keep images small; the default limit is 5 MB. Image upload requires repository contents write access, so use a classic PAT with the `repo` scope, or a fine-grained token with `Contents: Read and write` plus issue comment access.

---

## User Name and Color

Each user should set their own display name:

```json
"display_name": "Rexa"
```

Each GitHub username can also have a custom message color:

```json
"user_colors": {
  "hani-777": "#24332f",
  "another-user": "#2f2a43"
}
```

Use soft/dark colors for better readability.

---

## Run the App

```bash
python main.py
```

If `python` does not work on Windows:

```bash
py main.py
```

---

## Important Security Note

Never upload `config.json`.

It contains your private GitHub token.

Make sure `.gitignore` includes:

```gitignore
config.json
.env
__pycache__/
*.pyc
dist/
build/
*.log
```

If you accidentally upload your token:

👉 Revoke it immediately
👉 Create a new one
