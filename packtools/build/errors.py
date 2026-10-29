# SPDX-License-Identifier: Apache-2.0
"""The one build-failure type every builder raises.

Its own module so the per-kind builders (airspace, procedures, plates) can
subclass it without importing the package ``__init__``, which imports them.
``run_cyclical`` treats any :class:`BuildError` as a HARD failure: the source
ships nothing and the run exits nonzero.
"""


class BuildError(RuntimeError):
    pass
