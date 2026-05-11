import os
import subprocess
import sys
import requests
import base64
from pathlib import Path

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "noelle-ps/Leviathan"
BRANCH = "main"
API = "https://api.github.com"

headers = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
}


def get_changed_files():
    result = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True
    )
    untracked = subprocess.run(
        ["git", "--no-optional-locks", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True
    )
    files = set()
    for line in result.stdout.strip().splitlines():
        if line:
            files.add(line.strip())
    for line in untracked.stdout.strip().splitlines():
        if line:
            files.add(line.strip())
    return files


def get_remote_sha(path):
    r = requests.get(f"{API}/repos/{REPO}/contents/{path}?ref={BRANCH}", headers=headers)
    if r.status_code == 200:
        return r.json().get("sha")
    return None


def push_file(path, commit_message):
    try:
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
    except FileNotFoundError:
        print(f"  Skipping {path} (not found locally)")
        return False

    sha = get_remote_sha(path)
    payload = {
        "message": commit_message,
        "content": content,
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(f"{API}/repos/{REPO}/contents/{path}", headers=headers, json=payload)
    if r.status_code in (200, 201):
        print(f"  Pushed: {path}")
        return True
    else:
        print(f"  Failed {path}: {r.status_code} {r.text[:200]}")
        return False


def push_files(files, commit_message="Auto-push from Replit"):
    if not files:
        print("Nothing to push.")
        return
    print(f"Pushing {len(files)} file(s) to GitHub ({REPO})...")
    for f in files:
        push_file(f, commit_message)
    print("Done.")


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Auto-push from Replit"
    changed = get_changed_files()
    if not changed:
        print("No changed files detected. Specify files manually:")
        print("  python autopush.py <file1> <file2> -- <commit message>")
    else:
        push_files(changed, msg)
