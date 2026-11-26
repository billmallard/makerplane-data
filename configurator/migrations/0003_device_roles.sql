-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Display roles per device (DSP1, AER-6686; src/device_roles.ts). NULL in
-- either column means "automatic": the first device in a project plays the
-- alert tones and node numbers follow creation order. Additive only: an
-- older Worker never reads these columns.
ALTER TABLE devices ADD COLUMN aural INTEGER;    -- 1 plays the alert tones, 0 does not
ALTER TABLE devices ADD COLUMN node_id INTEGER;  -- pyEfis main: nodeID, unique in the project
