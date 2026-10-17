# Updates — automatic delivery over WiFi / LAN

Simpl'd updates itself from **release channels**. Each channel publishes the
same pair: an Inno Setup installer (`Simpld-Setup-X.Y.Z.exe`) and its
SHA-256. The app never installs anything whose hash it cannot verify.

| channel | who uses it | needs internet |
|---------|-------------|----------------|
| **GitHub Releases** on `BlurdStudios/simpld` | any PC with internet | yes |
| **LAN channel** — `/updates` on the packager's HTTPS service | dispatcher PC(s) | no |
| **Local folder** — `<app data>/updates` (`SIMPLD_UPDATE_DIR`) | the packager itself (USB / share) | no |

## What the operator sees

1. A few seconds after the window opens, the app checks silently. Offline or
   no newer version → nothing visible happens.
2. Newer version found → an info toast: *"Update available: v1.0.1 — open
   About to install"*.
3. **About → Check for updates** also works any time; it reports either the
   newer version (with *Update now* / *Later*) or *"up to date"*.
4. **Update now** → the installer downloads with a progress dialog and is
   SHA-256-verified → Windows asks for administrator confirmation once (the
   app installs into Program Files) → Simpl'd closes → the installer replaces
   the files (small progress window) → **Simpl'd reopens by itself** as the
   logged-in operator.
5. Anything goes wrong (offline, corrupted download, no admin rights) → an
   error dialog or a log line; the installed version keeps running untouched.

Factory data is never touched by an update: the database, hardware config,
backups and logs live in the user's `%LOCALAPPDATA%\Simpld`, outside the
install directory.

## Releasing a new version (developer)

1. Bump `factory/version.py` to `X.Y.Z` and commit it.
2. Tag and push the tag:

   ```
   git tag v1.0.1
   git push origin v1.0.1
   ```

3. CI (`.github/workflows/build-windows.yml`) runs the test suite, builds the
   Windows installer, writes `Simpld-Setup-1.0.1.exe.sha256`, and publishes a
   **GitHub Release** with both files. If the tag does not match
   `factory/version.py` the build fails before anything is released — the
   tag, the About dialog and the installer can never disagree.
4. Copy the release body into release notes at will — it is shown in the
   update prompt (`--generate-notes` fills it from commits).

That is the whole release process. Every internet-connected machine picks the
update up on its next launch; machines on the isolated LAN need step 4 below.

## Publishing to the LAN channel (no-internet factory)

On any machine with internet, download `Simpld-Setup-X.Y.Z.exe` from the
GitHub release. Move it to the packager computer, then:

```
python3 tools/publish_update.py Simpld-Setup-1.0.1.exe \
    --dir data/updates --notes "Carton sticker fix"
```

This writes the installer, its `.sha256` and a `latest.json` manifest into the
folder. Point the LAN service at it:

```
python3 -m factory.lan_server --bind 192.168.1.10 --port 8443 \
    --db /path/to/factory.db --cert lan-cert.pem --key lan-key.pem \
    --updates-dir data/updates
```

- **Dispatcher PC(s)** already point at the packager via `SIMPLD_SERVER_*`;
  they check `https://<packager>:8443/updates/latest.json` automatically.
- **The packager itself** reads the folder directly. On the installed Windows
  app the default folder is `%LOCALAPPDATA%\Simpld\updates`; if the installer
  files are published elsewhere, set `SIMPLD_UPDATE_DIR` on the packager to
  the same path.

Republishing the same version is allowed (idempotent); a **downgrade is
refused** unless `--force` is passed, so the fleet cannot be silently rolled
back. `--prune` deletes older installers from the folder after publishing.

## Security properties

- **Hash-verified or nothing.** Every install candidate carries a SHA-256 from
  the same channel it was downloaded from; a missing or mismatched hash
  refuses the update and logs why.
- **HTTPS only.** GitHub is HTTPS; the LAN channel rides the same TLS service
  with the packager's certificate the app already trusts.
- **Small surface on the LAN.** `/updates/…` only ever serves
  `latest.json` and files matching `Simpld-Setup-*.exe[.sha256]` from the
  configured folder — no directory listing, no traversal, nothing else.
  It is unauthenticated by design (machines update before anyone logs in);
  it exposes exactly the bytes that are public on the GitHub release anyway.
- **Known gap: code signing.** The installer is unsigned, so SmartScreen/UAC
  shows "Unknown publisher". When Blur'd has a code-signing certificate, sign
  `Simpld.exe` + the installer in CI; nothing else changes. Until then the
  SHA-256 + HTTPS are the integrity story.
- Release integrity is GitHub account security: keep 2FA on the org and
  restrict who can push tags.

## Switches (environment, per machine)

| variable | effect |
|----------|--------|
| `SIMPLD_UPDATE_DISABLE=1` | no update checks at all (kill switch) |
| `SIMPLD_UPDATE_GITHUB=0` | skip the GitHub channel (fully offline fleet) |
| `SIMPLD_UPDATE_REPO=owner/name` | check a different repo (fork/testing) |
| `SIMPLD_UPDATE_DIR=/path` | local updates folder (packager / USB) |
| `SIMPLD_UPDATE_FORCE=1` | allow the startup check in a source (dev) run |
| `SIMPLD_SERVER_HOST/PORT/CA_CERT` | existing LAN wiring — also selects the LAN update channel |

## Troubleshooting

- **No toast, no update.** Expected when offline, already current, or
  `SIMPLD_UPDATE_DISABLE=1`. Check `%LOCALAPPDATA%\Simpld\logs\simpld.log`
  for lines from `simpld.updater` (each channel's miss/failure is logged).
- **"No newer version was found" but GitHub has one.** The machine cannot
  reach `api.github.com` — use the LAN channel (above), or a local folder.
- **Update prompt appears, install fails at UAC.** The Windows account needs
  administrator approval (or admin credentials) for this one step. The
  running version stays unaffected.
- **The app installed under a different user than the one clicking UAC.**
  Covered: the installer relaunches Simpl'd as the original user
  (`runasoriginaluser` in `simpld.iss`), so data keeps going to the right
  `%LOCALAPPDATA%`.
- **Need to roll back.** Install the older installer manually; the LAN
  channel refuses downgrades by design (`--force` overrides for a deliberate
  fleet rollback).

## Rollout note

The shipped 1.0.0 predates the updater, so the **first** update-enabled
release must be installed manually once per machine (double-click the
installer). From that version on, every update is automatic. The updater
checks after login on the main window; the login screen itself never blocks
on the network.
