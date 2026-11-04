# Updates — automatic delivery over WiFi / LAN

Simpl'd updates itself from **release channels**. Each channel publishes the
same pair: an Inno Setup installer (`Simpld-Setup-X.Y.Z.exe`) and its
SHA-256. The app never installs anything whose hash it cannot verify.

| channel | who uses it | needs internet |
|---------|-------------|----------------|
| **GitHub Releases** on the public mirror `BlurdStudios/simpld-releases` | any PC with internet | yes |
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
   Windows installer, writes `Simpld-Setup-X.Y.Z.exe.sha256`, and publishes a
   **GitHub Release** to the public mirror with both files. If the tag does not
   match `factory/version.py` the build fails before anything is released — the
   tag, the About dialog and the installer can never disagree.
4. The release body is generated from the private repo's commits and is shown
   in the update prompt. Edit it on the mirror if you want operator-facing
   wording instead.

That is the whole release process. Every internet-connected machine picks the
update up on its next launch; machines on the isolated LAN need the next
section.

## Demo installers (USB handout, never auto-updated)

Every tag build also produces `Simpld-Setup-X.Y.Z-demo.exe` (same commit,
demo edition). It is a CI **artifact**, not a release: it is never published
to the mirror, so neither the fleet nor the demo laptop ever auto-updates
into or out of it. Download both `Simpld-Setup` and `Simpld-Setup-demo`
artifacts from the tag's workflow run and hand them out on USB.

Do not install a demo build on a factory floor PC: both editions share the
install folder and per-user data dir, so a demo install replaces prod on
that machine (and its simulated weights would land in the real database).

## Release mirror setup (one time)

The source repo is **private**; GitHub does not serve releases from a private
repo to an unauthenticated client (the API returns 404). The app therefore
reads a **public mirror** that contains only release binaries — never source.
No token ships in the app.

1. Create a public repo, `BlurdStudios/simpld-releases`, with a README and no
   code. Keep it public.
2. Create a **fine-grained personal access token** scoped to that repo only:
   *Repository access:* only `simpld-releases`; *Permissions:* **Contents:
   Read and write** (Metadata is implicit). Set a long expiry and a reminder.
3. In the **private** repo: *Settings → Secrets and variables → Actions* →
   add the secret **`RELEASE_MIRROR_TOKEN`** with that token.
4. Optional: add the repository **variable** `RELEASE_MIRROR_REPO` if the
   mirror lives at a different `owner/name`. The workflow defaults to
   `BlurdStudios/simpld-releases`.
5. Set the private repo's visibility to private. The tag build publishes with
   the token; installed copies keep reading the public mirror.

If the mirror name ever changes, update `DEFAULT_REPO` in
`factory/updater.py` and ship a build that carries the new name. Machines can
also override it per-machine with `SIMPLD_UPDATE_REPO`.

## Code signing (SmartScreen + Smart App Control)

Without a signature, every release trips two Windows gates:

| gate | unsigned behaviour | fix |
|------|-------------------|-----|
| SmartScreen | blue "Unknown publisher — Windows protected your PC" dialog | click **More info → Run anyway** (per file, no setting changed) |
| Smart App Control (Win 11, on by default on new PCs) | installer blocked outright, no "run anyway" offered | sign the release (below) — or install that one version from an admin account with App Control in evaluation mode; never turn App Control off system-wide |

The permanent fix is signing both `Simpld.exe` and the installer with an
OV (organization-validated) code-signing certificate — any public CA
(DigiCert, Sectigo, GlobalSign…) — or Azure Trusted Signing, which is the
same trust without a USB token:

1. Buy the certificate (OV is enough; EV is not required) **or** create an
   Azure Trusted Signing account + certificate profile.
2. PFX route: export the certificate with its private key to a `.pfx`,
   base64 it (`certutil -encode cert.pfx cert.b64` on Windows), and add two
   repository secrets to the **private** repo: `WINDOWS_CERT_PFX_B64` (the
   base64 text) and `WINDOWS_CERT_PASSWORD` (leave empty when the PFX has
   no password).
3. Nothing else to change: the `build-windows.yml` workflow signs the exe
   before Inno embeds it and signs the installer after compiling, but only
   when those secrets exist — builds without them ship unsigned exactly as
   before. The `.sha256` sidecar is written after signing, so it always
   covers the signed bytes the fleet verifies.
4. Azure route: instead of a PFX, wire the `AzureSignTool` step with your
   tenant/client/endpoint secrets in place of the PFX step (same position
   in the workflow: exe before compile, installer before checksum).

First signed release: SmartScreen may still show once while reputation
builds; Smart App Control accepts a valid signature immediately.

## Floor requirement: 64-bit Windows 10 or newer

PySide6 6.x, Python 3.12 and the installer itself (Inno `MinVersion=10.0`,
x64) all need 64-bit Windows 10+. An older PC (Windows 7/8.1, or 32-bit
Windows) cannot run current Simpl'd builds at all — that is a stack limit,
not a setting. The updater enforces the same gate **before** quitting for
an install (`_install_block_reason` in `factory/updater.py`): on an old PC
the operator gets "updates need 64-bit Windows 10 or newer" and the
running version keeps working, instead of the app closing for an
installer that exits silently under `/SILENT`.
Keep one old-PC plan: leave it on its last working version, or move it to
a supported PC.

## Publishing to the LAN channel (no-internet factory)

On any machine with internet, download `Simpld-Setup-X.Y.Z.exe` from the
release mirror. Move it to the packager computer, then:

```
python3 tools/publish_update.py Simpld-Setup-1.0.1.exe \
    --dir data/updates --notes "Box sticker fix"
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
  it exposes exactly the bytes that are public on the release mirror anyway.
- **Private source, public binaries.** The source repo is private; only the
  installer and its checksum are mirrored. The installer is a PyInstaller
  bundle, so treat what it contains as extractable — no credentials or
  secrets belong in the client.
- **Unsigned builds: SmartScreen + Smart App Control.**
  The installer is unsigned until the company holds a code-signing
  certificate (see *Code signing* below), so Windows SmartScreen says
  "Unknown publisher" and Smart App Control may block the file outright.
  That is purely "this file is unsigned" — the SHA-256 + HTTPS story above
  is unchanged. Never turn off Smart App Control or SmartScreen
  system-wide for this: right-click the installer → Properties → check
  **Unblock** if present → run it → on the blue SmartScreen dialog click
  **More info → Run anyway**. One signed release with growing reputation
  ends the warnings for good.
- Release integrity is GitHub account security: keep 2FA on the BlurdStudios
  account, keep the mirror token scoped to `simpld-releases`, and restrict who
  can push tags to the private repo.

## Switches (environment, per machine)

| variable | effect |
|----------|--------|
| `SIMPLD_UPDATE_DISABLE=1` | no update checks at all (kill switch) |
| `SIMPLD_UPDATE_GITHUB=0` | skip the GitHub channel (fully offline fleet) |
| `SIMPLD_UPDATE_REPO=owner/name` | check a different repo (default: the public mirror) |
| `SIMPLD_UPDATE_DIR=/path` | local updates folder (packager / USB) |
| `SIMPLD_UPDATE_FORCE=1` | allow the startup check in a source (dev) run |
| `SIMPLD_SERVER_HOST/PORT/CA_CERT` | existing LAN wiring — also selects the LAN update channel |
| `SSL_CERT_FILE=/path/ca-bundle.pem` | trust this CA bundle for update downloads instead of the PC's store |

## Troubleshooting

- **No toast, no update.** Expected when offline, already current, or
  `SIMPLD_UPDATE_DISABLE=1`. Check `%LOCALAPPDATA%\Simpld\logs\simpld.log`
  for lines from `simpld.updater` (each channel's miss/failure is logged).
- **"No newer version was found" but the mirror has one.** The machine cannot
  reach `api.github.com` — use the LAN channel (above), or a local folder.
  If the log shows `HTTP 404 from https://api.github.com/repos/...`, the
  updater is pointing at a repo it cannot read. It must point at the public
  mirror (`SIMPLD_UPDATE_REPO` overrides it); releases in the private repo are
  invisible to installed copies by design.
- **Log shows `certificate verify failed` but the browser works.** That PC's
  Windows certificate store is broken (stale roots, AV interception) — the
  browser uses its own certificates, the app used to use the store. Since
  1.4.1 the app pins update downloads to CA certificates shipped with it,
  so this heals itself on upgrade; install that version once via USB. For
  an immediate trust override without upgrading, point `SSL_CERT_FILE` at a
  valid CA bundle file.
- **Tag build fails: `RELEASE_MIRROR_TOKEN is not set`.** Complete
  *Release mirror setup* above, then re-run the tag build.
- **Update prompt appears, install fails at UAC.** The Windows account needs
  administrator approval (or admin credentials) for this one step. The
  running version stays unaffected.
- **Old PC: "updates need 64-bit Windows 10 or newer".** That PC cannot
  take current builds (see *Floor requirement* above). Leave it on its
  version or move it to a supported PC — do not force the installer.
- **Smart App Control blocks the installer.** Expected for unsigned
  releases — it is not a corrupt download (the SHA-256 already verified).
  See *Code signing*: unblock the single file / Run anyway, never disable
  App Control system-wide. The block disappears permanently once releases
  are signed.
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
installer). From that version on, every update is automatic.

**Moving to the private repo (1.0.2).** 1.0.1 checks `BlurdStudios/simpld`
directly. 1.0.2 is the first build that checks the public mirror, so a 1.0.1
machine must reach 1.0.2 once: update in-app while the source repo is still
public, or install `Simpld-Setup-1.0.2.exe` from the mirror manually. From
1.0.2 on, updates keep working after the source repo is made private. The updater
checks after login on the main window; the login screen itself never blocks
on the network.
