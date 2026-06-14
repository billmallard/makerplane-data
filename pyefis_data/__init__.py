"""pyefis_data — the on-Pi navigation-data updater CLI (Leg 3).

Implemented in Phase C. Responsible for: fetching the signed manifest,
verifying signature + per-pack sha256, staging downloads, and the
verify-then-atomic-swap install. Shares the signing and manifest
modules with packtools so the build and consume sides can never drift.
"""

__version__ = "0.1.6"
