"""Single source of truth for the Simpl'd version (stdlib only, no Qt).

Read by factory/ui/brand.py (About dialog / splash), factory/updater.py
(update comparisons) and the Windows build. The release workflow refuses to
publish a version tag whose `vX.Y.Z` does not match this constant and passes
the same value to Inno Setup, so the release tag, the About dialog and the
installer can never disagree.

Release procedure: bump VERSION in the commit you tag.

    VERSION = "1.3.0"   ->   git tag v1.3.0 && git push origin v1.3.0

See UPDATES.md.

Editions: prod builds ship with DEMO_ENABLED = False — the Demo switch,
SIMPLD_DEMO and any stored demo_mode are ignored, so demo simulation can
never run on the factory floor. Demo installers (training / no-hardware
shows) are built from the SAME commit with this flipped to True by the CI
demo job and are named Simpld-Setup-X.Y.Z-demo.exe. They are handed out on
USB only and are never published to the update mirror, so the fleet can
never auto-update into a demo build.
"""

VERSION = "1.3.0"

DEMO_ENABLED = False
