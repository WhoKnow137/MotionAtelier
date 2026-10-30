# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Deterministic integer hashing for value noise (no global random state).

``lowbias32`` is the 32-bit multiply–xorshift finaliser published by C. Wellons,
"Prospecting for Hash Functions" (2018). Seeds and lattice indices are combined with
Fibonacci hashing (Knuth, TAOCP vol. 3, section 6.4): the lattice index is multiplied by
``⌊2³²/φ⌋ = 0x9E3779B9`` so consecutive indices land far apart before mixing.
"""

from __future__ import annotations

MASK32 = 0xFFFFFFFF
GOLDEN_GAMMA_32 = 0x9E3779B9  # ⌊2^32 / φ⌋, Knuth's multiplicative hashing constant


def lowbias32(x: int) -> int:
    x &= MASK32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & MASK32
    x ^= x >> 15
    x = (x * 0x846CA68B) & MASK32
    x ^= x >> 16
    return x


def lattice_hash(seed: int, index: int) -> int:
    """32-bit hash of a (seed, lattice index) pair."""
    return lowbias32((lowbias32(seed) + (index * GOLDEN_GAMMA_32)) & MASK32)


def lattice_unit(seed: int, index: int) -> float:
    """Hash mapped linearly onto ``[-1, 1]`` (both ends attainable)."""
    return lattice_hash(seed, index) / MASK32 * 2.0 - 1.0


def mix_seed(seed: int, salt: int) -> int:
    """Derive an independent stream (e.g. one per effect layer) from a seed."""
    return lowbias32((seed & MASK32) ^ lowbias32(salt))
