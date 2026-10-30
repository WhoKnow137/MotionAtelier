# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Cubic Bezier primitives in Bernstein form (Farin, *Curves and Surfaces for CAGD*).

Includes evaluation and derivatives, de Casteljau subdivision, the safeguarded
Newton–bisection inversion of the monotone time polynomial (Press et al., *Numerical
Recipes*, ``rtsafe``), and exact extremum location of the value polynomial.
"""

from __future__ import annotations

import math
import sys

from motion_atelier.curve import errors as E
from motion_atelier.curve.errors import CurveError

Quad = tuple[float, float, float, float]

_EPS = sys.float_info.epsilon

# The inverse solver accepts u when |Bx(u) − x| ≤ SOLVE_TOLERANCE_ULPS · ε · scale, where scale
# is the magnitude of the segment's x coordinates. Bernstein evaluation of a cubic with
# non-negative basis weights has a forward error of a few ε·scale, so 64ε·scale is always
# reachable (adjacent doubles in u move Bx by at most 3·d·ε) yet still ~1e-14 on [0, 1].
SOLVE_TOLERANCE_ULPS = 64.0

# Hard iteration cap for the inverse solver. Each iteration at least halves the bracket
# (bisection fallback), so 53 iterations already exhaust double precision; 200 can only be
# reached through a defect, in which case the solver raises instead of returning a guess.
SOLVE_MAX_ITERATIONS = 200

# A derivative is treated as zero when it is below this fraction of the segment's span. Used
# for velocity limits at degenerate (zero-length or full-length) handles.
DERIVATIVE_ZERO_GUARD = 1e-12


def bernstein(p: Quad, u: float) -> float:
    mt = 1.0 - u
    return mt * mt * mt * p[0] + 3.0 * mt * u * (mt * p[1] + u * p[2]) + u * u * u * p[3]


def derivative(p: Quad, u: float) -> float:
    mt = 1.0 - u
    return 3.0 * (mt * mt * (p[1] - p[0]) + 2.0 * mt * u * (p[2] - p[1]) + u * u * (p[3] - p[2]))


def second_derivative(p: Quad, u: float) -> float:
    return 6.0 * ((1.0 - u) * (p[2] - 2.0 * p[1] + p[0]) + u * (p[3] - 2.0 * p[2] + p[1]))


def third_derivative(p: Quad) -> float:
    return 6.0 * (p[3] - 3.0 * p[2] + 3.0 * p[1] - p[0])


def nth_derivative(p: Quad, u: float, order: int) -> float:
    if order == 1:
        return derivative(p, u)
    if order == 2:
        return second_derivative(p, u)
    if order == 3:
        return third_derivative(p)
    raise ValueError(order)


def split(px: Quad, py: Quad, u: float) -> tuple[tuple[Quad, Quad], tuple[Quad, Quad]]:
    """de Casteljau subdivision at ``u``: returns ``((lx, ly), (rx, ry))``."""

    def one(p: Quad) -> tuple[Quad, Quad]:
        a = p[0] + u * (p[1] - p[0])
        b = p[1] + u * (p[2] - p[1])
        c = p[2] + u * (p[3] - p[2])
        ab = a + u * (b - a)
        bc = b + u * (c - b)
        m = ab + u * (bc - ab)
        return (p[0], a, ab, m), (m, bc, c, p[3])

    lx, rx = one(px)
    ly, ry = one(py)
    return (lx, ly), (rx, ry)


def solve_tolerance(px: Quad) -> float:
    scale = max(abs(px[0]), abs(px[3]), px[3] - px[0])
    return SOLVE_TOLERANCE_ULPS * _EPS * scale


def solve_u(px: Quad, x: float) -> float:
    """Return ``u ∈ [0, 1]`` with ``Bx(u) = x`` for a non-decreasing time polynomial.

    Safeguarded Newton iteration on a maintained bracket ``[lo, hi]`` with
    ``Bx(lo) ≤ x ≤ Bx(hi)``: a Newton step is taken only if it stays inside the bracket and
    shrinks the correction at least geometrically; otherwise the bracket is bisected. The
    returned root is always verified against :func:`solve_tolerance`; if verification fails
    (only possible through a defect or invalid input) :class:`CurveError` ``solve_failed`` is
    raised rather than returning an approximation.
    """
    x0, x3 = px[0], px[3]
    tol = solve_tolerance(px)
    if not math.isfinite(x):
        raise CurveError(E.NON_FINITE, f"cannot solve for x={x!r}")
    if x <= x0 + tol:
        if x < x0 - tol:
            raise CurveError(E.SOLVE_FAILED, f"x={x!r} below segment start {x0!r}")
        return 0.0
    if x >= x3 - tol:
        if x > x3 + tol:
            raise CurveError(E.SOLVE_FAILED, f"x={x!r} beyond segment end {x3!r}")
        return 1.0
    lo, hi = 0.0, 1.0
    u = (x - x0) / (x3 - x0)
    step_old = 1.0
    step = 1.0
    for _ in range(SOLVE_MAX_ITERATIONS):
        f = bernstein(px, u) - x
        if abs(f) <= tol:
            return u
        if f < 0.0:
            lo = u
        else:
            hi = u
        df = derivative(px, u)
        newton_ok = (
            df > 0.0
            and ((u - hi) * df - f) * ((u - lo) * df - f) < 0.0
            and abs(2.0 * f) <= abs(step_old * df)
        )
        step_old = step
        if newton_ok:
            step = f / df
            u_next = u - step
        else:
            step = 0.5 * (hi - lo)
            u_next = lo + step
        if u_next <= lo or u_next >= hi:
            u_next = 0.5 * (lo + hi)
            if u_next <= lo or u_next >= hi:
                break  # bracket exhausted at double precision
        u = u_next
    # Bracket collapsed (or cap reached): accept the better end only if it verifies.
    best = min((lo, hi, u), key=lambda c: abs(bernstein(px, c) - x))
    if abs(bernstein(px, best) - x) <= tol:
        return best
    raise CurveError(E.SOLVE_FAILED, f"no verified root for x={x!r} in [{x0!r}, {x3!r}]")


def slope_at(px: Quad, py: Quad, u: float) -> float:
    """``dy/dx`` at parameter ``u``, taking the analytic limit where ``Bx'`` vanishes.

    If the first derivative of ``x`` is zero (zero-length handle at an end, or the stationary
    point of a full-length handle pair), L'Hôpital's rule is applied with the first
    non-vanishing derivative order. A vertical tangent (``y`` moves while ``x`` is stationary)
    yields a signed infinity; it can only arise from full-length handles, which the Houdini
    adapter rejects before writing.
    """
    span_x = px[3] - px[0]
    span_y = max(abs(py[1] - py[0]), abs(py[2] - py[1]), abs(py[3] - py[2]), span_x)
    for order in (1, 2, 3):
        dx = nth_derivative(px, u, order)
        dy = nth_derivative(py, u, order)
        if dx > DERIVATIVE_ZERO_GUARD * span_x:
            return dy / dx
        if abs(dy) > DERIVATIVE_ZERO_GUARD * span_y:
            return math.copysign(math.inf, dy)
    return 0.0


def value_extrema_params(py: Quad) -> tuple[float, ...]:
    """Parameters ``u ∈ (0, 1)`` where ``By'(u) = 0`` (sorted, de-duplicated)."""
    a = py[1] - py[0]
    b = py[2] - py[1]
    c = py[3] - py[2]
    qa = a - 2.0 * b + c
    qb = 2.0 * (b - a)
    qc = a
    scale = max(abs(a), abs(b), abs(c))
    if scale == 0.0:
        return ()
    roots: list[float] = []
    if abs(qa) <= 1e-14 * scale:
        if abs(qb) > 1e-14 * scale:
            roots.append(-qc / qb)
    else:
        disc = qb * qb - 4.0 * qa * qc
        if disc >= 0.0:
            sq = math.sqrt(disc)
            q = -0.5 * (qb + math.copysign(sq, qb))
            if q != 0.0:
                roots.append(q / qa)
                roots.append(qc / q)
            else:
                roots.append(-qb / (2.0 * qa))
    out = sorted({r for r in roots if 0.0 < r < 1.0})
    return tuple(out)
