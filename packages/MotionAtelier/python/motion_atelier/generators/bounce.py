# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Bounce: free fall under constant gravity with a coefficient of restitution.

Physics (classical mechanics; units chosen so the first fall lasts 1 and the drop height
is 1, hence g = 2): height ``h(t) = 1 − t²`` until the first contact at ``c₀ = 1`` with
impact speed 2. Rebound ``k ≥ 1`` leaves the ground at speed ``2eᵏ`` (restitution ``e``),
peaks at height ``e^(2k)`` after ``eᵏ`` and lands after ``2eᵏ``. The total duration is
``T = c_m``; time is normalised by ``x = t/T``.

The curve is shown as progress towards the target, ``y = 1 − h``: it starts at 0, reaches
1 at each contact, and dips back towards 0 at each apex.

Sparse exact representation: one cubic per half-flight. Each half-flight is a parabola,
and a cubic Hermite segment with the exact end slopes and one-third weights (linear ``Bx``)
reproduces quadratics exactly (Fritsch & Carlson's Hermite form). Contacts are broken knots
(the velocity reverses); apexes are smooth with zero slope.
"""

from __future__ import annotations

from motion_atelier.curve.errors import Notice
from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.generators.base import GeneratorOutput, ShapeKind
from motion_atelier.generators.params import FLOAT, INT, Params, ParamSpec

VERSION = 1
THIRD = 1.0 / 3.0

# Rebounds whose apex is below this fraction of the drop height are omitted. 1e-3 is 0.1 % of
# the motion (0.1 unit on a 100-unit channel) — below what reads on screen — and it keeps the
# shortest half-flight at least √1e-3 ≈ 3 % of the first fall, so knots stay well separated.
MIN_APEX_HEIGHT = 1e-3

ROUNDS_DROPPED = "bounce_rebounds_dropped"

PARAMS = (
    ParamSpec("bounces", INT, 0, 12, 3, "param.bounce.bounces", step=1, decimals=0),
    ParamSpec("restitution", FLOAT, 0.05, 0.95, 0.5, "param.bounce.restitution", step=0.01,
              decimals=3),
)


def flight_plan(bounces: int, restitution: float) -> tuple[list[float], list[float], int]:
    """Return ``(contact_times, rebound_speeds, kept)`` in physical time units.

    ``contact_times[0] = 1``; ``rebound_speeds[k-1] = 2eᵏ``; ``kept`` ≤ ``bounces`` is the
    number of rebounds above :data:`MIN_APEX_HEIGHT`.
    """
    contacts = [1.0]
    speeds: list[float] = []
    kept = 0
    for k in range(1, bounces + 1):
        ek = restitution**k
        if ek * ek < MIN_APEX_HEIGHT:
            break
        speeds.append(2.0 * ek)
        contacts.append(contacts[-1] + 2.0 * ek)
        kept += 1
    return contacts, speeds, kept


class Bounce:
    kind = ShapeKind.BOUNCE
    version = VERSION
    params_schema = PARAMS

    def build(self, params: Params) -> GeneratorOutput:
        values = dict(params)
        bounces = int(values["bounces"])
        e = float(values["restitution"])
        contacts, speeds, kept = flight_plan(bounces, e)
        total = contacts[-1]
        knots: list[Knot] = [Knot(0.0, 0.0, None, Tangent(0.0, THIRD))]
        impact = 2.0 * total  # dy/dx = (dy/dt)·T; first impact speed is 2
        for k, launch in enumerate(speeds, start=1):
            contact_x = contacts[k - 1] / total
            launch_slope = -launch * total
            knots.append(Knot(contact_x, 1.0, Tangent(impact, THIRD), Tangent(launch_slope, THIRD),
                              False))
            apex_t = contacts[k - 1] + launch / 2.0
            apex_h = (launch / 2.0) ** 2
            knots.append(Knot(apex_t / total, 1.0 - apex_h, Tangent(0.0, THIRD),
                              Tangent(0.0, THIRD), True))
            impact = launch * total
        knots.append(Knot(1.0, 1.0, Tangent(impact, THIRD), None))
        notices: tuple[Notice, ...] = ()
        if kept < bounces:
            notices = (Notice(ROUNDS_DROPPED, f"{bounces - kept} rebounds below visibility"),)
        return GeneratorOutput(Curve(tuple(knots)), notices)


GENERATOR = Bounce()
