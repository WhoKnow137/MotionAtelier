# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# Free software under the GNU General Public License, version 3 or (at your option) any later
# version, with the Houdini integration permission (section 7 additional permission).
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""MotionAtelier: an open-source animation-curve panel for SideFX Houdini.

Layer packages (dependency direction strictly downward, enforced by
``tests/architecture``): ``curve`` → ``generators`` → ``effects`` / ``fitting`` →
``records`` → ``store`` → ``app`` → ``host`` / ``ui`` → ``bootstrap``.
"""

__version__ = "0.4.3"
PRODUCT_NAME = "MotionAtelier"
