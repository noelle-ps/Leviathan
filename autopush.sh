#!/bin/bash
REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/noelle-ps/Leviathan.git"
git config user.email "bot@replit.com"
git config user.name "Replit Agent"
git remote set-url origin "$REPO_URL"
git add -A
git diff --cached --quiet && echo "Nothing to push." && exit 0
git commit -m "${1:-Auto-push from Replit}"
git push origin main
echo "Pushed to GitHub successfully."
