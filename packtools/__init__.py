"""packtools — the MakerPlane data pack builder (Leg 1).

Produces signed, versioned navigation-data packs and a single catalog
manifest from upstream FAA / Copernicus / OSM sources. See
docs/data_manager_strategy.md and docs/data_manager_implementation.md.

The manifest is the contract between all three legs of the system
(builder, distribution, on-Pi updater); keep its schema stable.
"""

__version__ = "0.1.6"

# Format version of the catalog manifest. Bump only on breaking changes;
# the Pi-side reader checks this.
MANIFEST_VERSION = 1
