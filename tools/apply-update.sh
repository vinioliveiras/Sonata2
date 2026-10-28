#!/usr/bin/env bash
# Apply an update Claude made (a .mbox of commits) to this clone, then push,
# install and restart Sonata -- without the usual failures: a stuck earlier
# `git am`, a clone behind GitHub, commits already applied, the browser
# saving "file (1).mbox".
#
#   tools/apply-update.sh                 # the newest ~/Downloads/sonata2-*.mbox
#   tools/apply-update.sh path/to/x.mbox
#   tools/apply-update.sh --no-install    # apply and push only
#
# Stops (changing nothing more) when this clone has edits not committed, or
# a commit doesn't apply and isn't already here -- and says why.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
INSTALL=1
MBOX=""
for a in "$@"; do
  case "$a" in
    --no-install) INSTALL=0 ;;
    *) MBOX="$a" ;;
  esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

# 1. a stuck earlier attempt
if [ -d .git/rebase-apply ] || [ -d .git/rebase-merge ]; then
  say "An earlier update was left half-applied: undoing it"
  git am --abort >/dev/null 2>&1 || true
  rm -rf .git/rebase-apply .git/rebase-merge
fi

# 2. nothing of yours is touched
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no
  fail "These files have changes not committed. Commit or stash them first (git stash), then run this again."
fi

# 3. up to date with GitHub
say "Getting what's on GitHub"
git fetch --quiet origin || fail "Couldn't reach GitHub."
git merge --ff-only --quiet origin/main 2>/dev/null || fail "This clone and GitHub went different ways (git status)."

# 4. the update
if [ -z "$MBOX" ]; then
  MBOX=$(ls -t "$HOME"/Downloads/sonata2-*.mbox 2>/dev/null | head -1)
fi
[ -n "$MBOX" ] && [ -f "$MBOX" ] || fail "No update found (~/Downloads/sonata2-*.mbox)."
say "Applying $(basename "$MBOX") ($(grep -c '^From [0-9a-f]\{40\}' "$MBOX") commits)"

before=$(git rev-parse HEAD)
skipped=0
if ! git am -3 --quiet "$MBOX" 2>/dev/null; then
  # one by one: a commit already here (same subject, nothing left to change) is skipped
  while [ -d .git/rebase-apply ]; do
    subject=$(head -1 .git/rebase-apply/final-commit 2>/dev/null)     # (the subject, unwrapped)
    if [ -n "$subject" ] && grep -qxF -- "$subject" <<<"$(git log --format=%s -300)"; then   # (no pipe: pipefail)
      say "Already here, skipped: $subject"
      skipped=$((skipped + 1))
      git checkout -- . 2>/dev/null; git am --skip --quiet 2>/dev/null || true
    else
      git am --abort >/dev/null 2>&1
      fail "\"$subject\" doesn't apply to this clone. Nothing was changed: send Claude the output of: git log --oneline -5"
    fi
  done
fi
applied=$(git rev-list --count "$before"..HEAD)
skipped=$(( $(grep -c '^From [0-9a-f]\{40\}' "$MBOX") - applied ))
say "$applied applied, $skipped already here"
git log --oneline -3

# 5. GitHub, the install, Sonata
if [ "$applied" -gt 0 ]; then
  say "Sending to GitHub"
  git push --quiet origin HEAD:main || fail "The push failed (git push to retry)."
fi
mkdir -p "$HOME/.cache/sonata2/applied" && mv "$MBOX" "$HOME/.cache/sonata2/applied/" 2>/dev/null
if [ "$INSTALL" = 1 ]; then
  say "Installing"
  ./install.sh || fail "install.sh failed (its output is above)."
  command -v sonata2 >/dev/null && { say "Restarting Sonata"; sonata2 restart; }
fi
say "Done."
