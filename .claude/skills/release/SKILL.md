---
name: release
description: Cut a new Helix release — bump version, update CHANGELOG, tag, and optionally push a GitHub release
user-invocable: true
---

Guide the user through cutting a new Helix release interactively.

## Step 1 — Check working tree

Run `git status` to confirm the working tree is clean. If there are uncommitted changes, warn the user and stop:

> "Your working tree has uncommitted changes. Please commit or stash them before releasing."

Also run `git log --oneline -5` to show recent commits as context.

## Step 2 — Determine the new version

Read the current version from `pyproject.toml` (`version = "..."` under `[project]`).

Ask the user what kind of release this is:
- **patch** — bug fixes only (e.g. 1.1.0 → 1.1.1)
- **minor** — new backwards-compatible features (e.g. 1.1.0 → 1.2.0)
- **major** — breaking changes (e.g. 1.1.0 → 2.0.0)
- **custom** — let me type the version myself

Compute the new version from the current one using semver rules, or use the custom value the user provides.

Confirm with the user before proceeding:
> "Releasing **v{NEW_VERSION}** (was {CURRENT_VERSION}). Continue? [y/N]"

## Step 3 — Update `pyproject.toml`

Edit `pyproject.toml`, replacing the `version = "..."` line under `[project]` with the new version.

## Step 4 — Update `CHANGELOG.md`

Read `CHANGELOG.md`.

1. Find the `## [Unreleased]` section.
2. If it has no entries under it, warn the user:
   > "The [Unreleased] section is empty. Add your changes there first, or continue anyway to create an empty release entry."
   Ask them to confirm.
3. Replace `## [Unreleased]` with two blocks:
   ```
   ## [Unreleased]

   ---

   ## [{NEW_VERSION}] — {TODAY_DATE}
   ```
   Where `{TODAY_DATE}` is today's date in `YYYY-MM-DD` format.
4. Keep all content that was under `[Unreleased]` under the new version heading.

## Step 5 — Commit and tag

Stage both files and create a commit:
```
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release v{NEW_VERSION}"
```

Then create an annotated tag:
```
git tag -a "v{NEW_VERSION}" -m "Release v{NEW_VERSION}"
```

## Step 6 — Push (optional)

Ask the user:
> "Push the commit and tag to origin? [y/N]"

If yes:
```bash
git push origin main
git push origin "v{NEW_VERSION}"
```

## Step 7 — GitHub release (optional)

Ask the user:
> "Create a GitHub release for v{NEW_VERSION}? [y/N]"

If yes, extract the changelog content for this version (everything between the new version heading and the next `---` separator) and run:
```bash
gh release create "v{NEW_VERSION}" \
  --title "v{NEW_VERSION}" \
  --notes "{CHANGELOG_SECTION}"
```

If `gh` is not installed, tell the user to install the GitHub CLI or create the release manually at https://github.com/88hours/helix-community/releases/new.

## Final summary

Print a summary:
```
Released v{NEW_VERSION}
  pyproject.toml  updated
  CHANGELOG.md    updated
  git tag         v{NEW_VERSION}
  pushed          yes/no
  GitHub release  yes/no
```
