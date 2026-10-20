"""Single source of truth for the Simpl'd version (stdlib only, no Qt).

Read by factory/ui/brand.py (About dialog / splash), factory/updater.py
(update comparisons) and the Windows build. The release workflow refuses to
publish a version tag whose `vX.Y.Z` does not match this constant and passes
the same value to Inno Setup, so the release tag, the About dialog and the
installer can never disagree.

Release procedure: bump this value in the commit you tag.

    VERSION = "1.0.3"   ->   git tag v1.0.3 && git push origin v1.0.3

See UPDATES.md.
"""

VERSION = "1.0.3"
