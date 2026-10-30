# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The nine effects: parameterisation, defaults and mix rules.

Every effect maps a curve to a curve. *Analytic* effects (Contrast, Timing bias) transform the
curve exactly and scale their strength by the layer's mix. *Sampled* effects evaluate the
input at ``n`` uniform samples, process, blend ``out = in + mix·(fx − in)``, optionally restore
the ends, and rebuild with PCHIP. *Follower* effects (Spring, Lag, Delay, Jiggle) integrate on
a fixed internal grid of :data:`FOLLOWER_STEPS` steps and always emit
:data:`FOLLOWER_OUTPUT` samples, so their result never depends on any display or sampling
resolution.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise

from motion_atelier.curve import bezier, ops
from motion_atelier.curve.errors import CurveError, Notice
from motion_atelier.curve.evaluate import sample_sorted, uniform_xs
from motion_atelier.curve.model import Curve, Knot
from motion_atelier.effects import sampling as S
from motion_atelier.generators.hashing import lattice_unit, mix_seed
from motion_atelier.generators.params import FLOAT, INT, ParamSpec

# Follower integration grid (steps over the normalised duration). At the highest frequency
# (30 cycles) ω·dt = 2π·30/2048 ≈ 0.092, far inside semi-implicit Euler's stability limit (2);
# its per-step phase error (ω·dt)²/24 stays below 4e-4 rad. 2048 steps halve the cost of
# interactive dragging with followers active compared with 4096 (measured 46 -> ~28 ms).
FOLLOWER_STEPS = 2048
# Follower output samples: every 4th grid point (513 knots), fixed for resolution independence.
FOLLOWER_DECIMATION = 4
FOLLOWER_OUTPUT = FOLLOWER_STEPS // FOLLOWER_DECIMATION + 1

# Arc Bias at |bias| = 1: the arc reaches the value it bows towards with this fraction of its
# original slope (sharp extrema stay visibly sharp) and bows by up to 28 % of its span; the
# far end steepens to 3 − 2·this. Chosen by the owner over an exact-slope map (which stalls
# mid-arc) and the 0.3.2 map (8x flatter, rounded extrema).
ARC_BIAS_NEAR_SLOPE = 0.25
# Pieces per segment before bending: the remapped values are exact at every knot and the
# Bezier pieces follow the bend between them closely (within ~1e-3 of the span at bias ±1).
ARC_BIAS_PIECES = 8
# Extremum parameters closer than this to a segment end are treated as at the knot.
EXTREMUM_EDGE_GUARD = 1e-9

# Seed salt separating the noise effect's stream from the noise generator's.
NOISE_EFFECT_SALT = 0x4E6F6973



@dataclass(frozen=True)
class Context:
    edge: str
    preserve_ends: bool


Apply = Callable[[Curve, dict, float, Context], tuple[Curve, list[Notice]]]


@dataclass(frozen=True)
class EffectDef:
    id: str
    version: int
    params: tuple[ParamSpec, ...]
    apply: Apply
    neutral: Callable[[dict], bool]
    kind: str  # "analytic", "sampled", "follower"
    upgrades_from: tuple[int, ...] = ()  # older stored versions loaded as this version


# -- helpers ---------------------------------------------------------------------------------


def _span(values) -> float:
    lo, hi = min(values), max(values)
    return hi - lo if hi > lo else 1.0


def _finish(xs, original, processed, amount, ctx) -> Curve:
    out = S.mix(original, processed, amount)
    if ctx.preserve_ends:
        out = S.preserve_ends(xs, original, out)
    return S.rebuild(xs, out)


def _gaussian(values, sigma_samples: float, edge: str, passes: int = 1) -> list[float]:
    if sigma_samples <= 0.0:
        return list(values)
    radius = max(1, math.ceil(3.0 * sigma_samples))
    weights = [math.exp(-(j * j) / (2.0 * sigma_samples * sigma_samples))
               for j in range(-radius, radius + 1)]
    total = sum(weights)
    weights = [w / total for w in weights]
    n = len(values)
    out = list(values)
    offsets = range(-radius, radius + 1)
    for _ in range(passes):
        src = out
        out = []
        for i in range(n):
            if radius <= i < n - radius:  # interior: no edge handling needed
                window = src[i - radius:i + radius + 1]
            else:
                window = [src[S.edge_index(i + j, n, edge)] for j in offsets]
            # Same products summed in the same order as the reference definition, so the
            # result is bit-identical (pinned by tests/unit/effects).
            out.append(sum(map(operator.mul, weights, window)))
    return out


def _grid(curve: Curve) -> tuple[list[float], list[float]]:
    xs = uniform_xs(curve.x_start, curve.x_end, FOLLOWER_STEPS + 1)
    return xs, sample_sorted(curve, xs)


def _spring(target, frequency: float, damping: float, dt: float) -> list[float]:
    omega = 2.0 * math.pi * frequency
    k = omega * omega
    c = 2.0 * damping * omega
    x, v = target[0], 0.0
    out = [x]
    for t in target[1:]:
        v += (k * (t - x) - c * v) * dt  # semi-implicit (symplectic) Euler
        x += v * dt
        out.append(x)
    return out


def _decimate(values) -> list[float]:
    return list(values[::FOLLOWER_DECIMATION])


# -- analytic effects ------------------------------------------------------------------------


def _contrast(curve: Curve, p: dict, mix: float, ctx: Context):
    factor = 1.0 + mix * p["amount"]
    knots = curve.knots
    last = len(knots) - 1
    out = []
    for i, k in enumerate(knots):
        sec_in = (k.y - knots[i - 1].y) / (k.x - knots[i - 1].x) if i > 0 else None
        sec_out = (knots[i + 1].y - k.y) / (knots[i + 1].x - k.x) if i < last else None
        tin, tout = k.tin, k.tout
        if k.smooth and tin is not None and tout is not None:
            chord = 0.5 * (sec_in + sec_out)
            s = chord + factor * (tin.slope - chord)
            tin, tout = tin.with_slope(s), tout.with_slope(s)
        else:
            if tin is not None:
                tin = tin.with_slope(sec_in + factor * (tin.slope - sec_in))
            if tout is not None:
                tout = tout.with_slope(sec_out + factor * (tout.slope - sec_out))
        out.append(Knot(k.x, k.y, tin, tout, k.smooth))
    return Curve(tuple(out)), []


def split_at_extrema(curve: Curve) -> tuple[Curve, list[Notice]]:
    """Insert a knot at every interior value extremum (exact de Casteljau insertion)."""
    xs: list[float] = []
    for i in range(curve.segment_count):
        px, py = curve.segment_controls(i)
        for u in bezier.value_extrema_params(py):
            if EXTREMUM_EDGE_GUARD < u < 1.0 - EXTREMUM_EDGE_GUARD:
                xs.append(bezier.bernstein(px, u))
    notices: list[Notice] = []
    for x in sorted(xs):
        try:
            result = ops.insert_knot(curve, x)
        except CurveError:
            continue  # within the minimum gap of an existing knot: already a knot
        curve = result.curve
        notices.extend(result.notices)
    return curve, notices


def _bow_up(u: float, t: float) -> tuple[float, float]:
    """Concave cubic Hermite map of [0, 1]: g'(1) = 1 − (1 − near)·t, g'(0) = 3 − 2·g'(1)."""
    near = 1.0 - (1.0 - ARC_BIAS_NEAR_SLOPE) * t
    far = 3.0 - 2.0 * near
    uu = u * u
    value = far * (uu * u - 2.0 * uu + u) + (3.0 * uu - 2.0 * uu * u) + near * (uu * u - uu)
    slope = far * (3.0 * uu - 4.0 * u + 1.0) + 6.0 * (u - uu) + near * (3.0 * uu - 2.0 * u)
    return value, slope


def arc_map(u: float, t: float) -> tuple[float, float]:
    """Bend of a normalised arc value ``u`` and its slope factor.

    ``t`` in [−1, 1]: ``t > 0`` bows towards 1, ``t < 0`` towards 0 (mirror image). The map
    is a concave (resp. convex) cubic with fixed ends, so it is strictly increasing (its
    slope falls monotonically from ``far`` to ``near`` > 0), lies strictly on one side of the
    identity, and adds no inflection: the bend never stalls the motion.
    """
    if t >= 0.0:
        return _bow_up(u, t)
    value, slope = _bow_up(1.0 - u, -t)
    return 1.0 - value, slope


def _subdivide(curve: Curve, pieces: int) -> Curve:
    """Split every non-flat segment into ``pieces`` equal-width parts (exact insertion)."""
    xs: list[float] = []
    for a, b in pairwise(curve.knots):
        if a.y != b.y:
            xs.extend(a.x + (b.x - a.x) * j / pieces for j in range(1, pieces))
    for x in xs:
        try:
            curve = ops.insert_knot(curve, x).curve
        except CurveError:
            continue  # too close to an existing knot or a vertical tangent: keep the segment
    return curve


def _timing_bias(curve: Curve, p: dict, mix: float, ctx: Context):
    """Arc Bias: bend every rise and fall in value, keeping its two end values.

    Each arc (a maximal run between value extrema) has its values remapped through
    ``lo + g((y − lo)/(hi − lo))·(hi − lo)`` (:func:`arc_map`); positive bias bows the arc
    towards its lower value, negative towards its upper value, whatever the arc's direction.
    Knot times, extrema, corners and the ends are unchanged; handle slopes follow the chain
    rule (``dy' = g'·dy``). Segments are subdivided first so the bend is followed between keys.
    """
    c = -mix * p["bias"]
    curve, notices = split_at_extrema(curve)
    curve = _subdivide(curve, ARC_BIAS_PIECES)
    knots = curve.knots
    n = len(knots)
    direction = [((knots[i + 1].y > knots[i].y) - (knots[i + 1].y < knots[i].y))
                 for i in range(n - 1)]
    # Arcs: maximal runs of segments with one value direction (flat runs map identically).
    arcs: list[tuple[int, int, int]] = []
    start = 0
    for i in range(1, n):
        if i == n - 1 or direction[i] != direction[start]:
            arcs.append((start, i, direction[start]))
            start = i
    new_y = [kn.y for kn in knots]
    out_scale = [1.0] * n  # g' applied to each knot's outgoing slope
    in_scale = [1.0] * n
    for a, b, sign in arcs:
        if sign == 0:
            continue
        lo, hi = min(knots[a].y, knots[b].y), max(knots[a].y, knots[b].y)
        span = hi - lo
        for j in range(a, b + 1):
            u = min(1.0, max(0.0, (knots[j].y - lo) / span))
            value, scale = arc_map(u, c)
            if a < j < b:
                new_y[j] = lo + value * span
            if j < b:
                out_scale[j] = scale
            if j > a:
                in_scale[j] = scale
    out = []
    for j, kn in enumerate(knots):
        tin = kn.tin.with_slope(kn.tin.slope * in_scale[j]) if kn.tin is not None else None
        tout = kn.tout.with_slope(kn.tout.slope * out_scale[j]) if kn.tout is not None else None
        smooth = kn.smooth
        if smooth and tin is not None and tout is not None and tin.slope != tout.slope:
            # Scales differ only at arc boundaries, which are extrema (slope 0) — but keep
            # the knot valid if rounding separated equal slopes.
            tout = tout.with_slope(tin.slope)
        out.append(Knot(kn.x, new_y[j], tin, tout, smooth))
    return Curve(tuple(out)), notices


# -- sampled effects -------------------------------------------------------------------------


def _noise(curve: Curve, p: dict, mix: float, ctx: Context):
    n = S.choose_samples(curve, 16 * int(p["frequency"]) + 1)
    xs, ys = S.sample(curve, n)
    amp = p["amplitude"] * _span(ys)
    cells = int(p["frequency"])
    seed = mix_seed(int(p["seed"]), NOISE_EFFECT_SALT)
    x0, x1 = xs[0], xs[-1]
    processed = []
    for x, y in zip(xs, ys, strict=True):
        u = (x - x0) / (x1 - x0)
        cell = u * cells
        i = min(int(math.floor(cell)), cells - 1)
        t = cell - i
        t = t * t * (3.0 - 2.0 * t)  # smoothstep between lattice values
        value = lattice_unit(seed, i) * (1.0 - t) + lattice_unit(seed, i + 1) * t
        processed.append(y + amp * math.sin(math.pi * u) * value)
    return _finish(xs, ys, processed, mix, ctx), []


def _gaussian_smooth(curve: Curve, p: dict, mix: float, ctx: Context):
    n = S.choose_samples(curve)
    xs, ys = S.sample(curve, n)
    sigma = p["radius"] / 3.0 * (n - 1)
    processed = _gaussian(ys, sigma, ctx.edge, int(p["passes"]))
    return _finish(xs, ys, processed, mix, ctx), []


def _sharpen(curve: Curve, p: dict, mix: float, ctx: Context):
    n = S.choose_samples(curve)
    xs, ys = S.sample(curve, n)
    blurred = _gaussian(ys, p["radius"] / 3.0 * (n - 1), ctx.edge)
    processed = [v + p["amount"] * (v - b) for v, b in zip(ys, blurred, strict=True)]
    return _finish(xs, ys, processed, mix, ctx), []


# -- followers (fixed internal grid) ---------------------------------------------------------


def _spring_effect(curve: Curve, p: dict, mix: float, ctx: Context):
    xs, target = _grid(curve)
    dt = (xs[-1] - xs[0]) / FOLLOWER_STEPS
    follow = _spring(target, p["frequency"], p["damping"], dt)
    return _finish(_decimate(xs), _decimate(target), _decimate(follow), mix, ctx), []


def _lag(curve: Curve, p: dict, mix: float, ctx: Context):
    xs, target = _grid(curve)
    dt = (xs[-1] - xs[0]) / FOLLOWER_STEPS
    alpha = 1.0 - math.exp(-dt / p["time"])  # exact one-pole step for a held input
    y = target[0]
    follow = [y]
    for t in target[1:]:
        y += alpha * (t - y)
        follow.append(y)
    return _finish(_decimate(xs), _decimate(target), _decimate(follow), mix, ctx), []


def _delay(curve: Curve, p: dict, mix: float, ctx: Context):
    xs = uniform_xs(curve.x_start, curve.x_end, FOLLOWER_OUTPUT)
    shift = p["time"] * (curve.x_end - curve.x_start)
    original = sample_sorted(curve, xs)
    delayed = sample_sorted(curve, [max(curve.x_start, x - shift) for x in xs])
    return _finish(xs, original, delayed, mix, ctx), []


def _jiggle(curve: Curve, p: dict, mix: float, ctx: Context):
    xs, target = _grid(curve)
    dt = (xs[-1] - xs[0]) / FOLLOWER_STEPS
    follow = _spring(target, p["frequency"], p["damping"], dt)
    limit = p["limit"] * _span(target)
    bounded = [t + limit * math.tanh((f - t) / limit) for f, t in zip(follow, target,
                                                                     strict=True)]
    return _finish(_decimate(xs), _decimate(target), _decimate(bounded), mix, ctx), []


# -- registry --------------------------------------------------------------------------------


def _p(effect: str, name: str, kind: str, lo, hi, default, **kw) -> ParamSpec:
    return ParamSpec(name, kind, lo, hi, default, f"param.fx.{effect}.{name}", **kw)


EFFECTS: dict[str, EffectDef] = {}


def _register(d: EffectDef) -> None:
    EFFECTS[d.id] = d


_register(EffectDef("contrast", 1, (
    _p("contrast", "amount", FLOAT, -1.0, 2.0, 0.5, step=0.01),
), _contrast, lambda p: p["amount"] == 0.0, "analytic"))
_register(EffectDef("timing_bias", 3, (
    _p("timing_bias", "bias", FLOAT, -1.0, 1.0, 0.4, step=0.01),
), _timing_bias, lambda p: p["bias"] == 0.0, "analytic", upgrades_from=(1, 2)))
_register(EffectDef("noise", 1, (
    _p("noise", "amplitude", FLOAT, 0.0, 0.5, 0.05, step=0.005),
    _p("noise", "frequency", INT, 1, 64, 12, step=1, decimals=0, soft_max=40),
    _p("noise", "seed", INT, 0, 2**31 - 1, 7, step=1, decimals=0, soft_max=999),
), _noise, lambda p: p["amplitude"] == 0.0, "sampled"))
_register(EffectDef("gaussian", 1, (
    _p("gaussian", "radius", FLOAT, 0.0, 0.25, 0.05, step=0.005),
    _p("gaussian", "passes", INT, 1, 4, 1, step=1, decimals=0),
), _gaussian_smooth, lambda p: p["radius"] == 0.0, "sampled"))
_register(EffectDef("sharpen", 1, (
    _p("sharpen", "amount", FLOAT, 0.0, 4.0, 1.0, step=0.05),
    _p("sharpen", "radius", FLOAT, 0.005, 0.25, 0.04, step=0.005),
), _sharpen, lambda p: p["amount"] == 0.0, "sampled"))
_register(EffectDef("spring", 1, (
    _p("spring", "frequency", FLOAT, 0.5, 30.0, 4.0, step=0.1, soft_max=15.0),
    _p("spring", "damping", FLOAT, 0.05, 2.0, 0.35, step=0.01),
), _spring_effect, lambda p: False, "follower"))
_register(EffectDef("lag", 1, (
    _p("lag", "time", FLOAT, 0.0, 0.5, 0.05, step=0.005),
), _lag, lambda p: p["time"] == 0.0, "follower"))
_register(EffectDef("delay", 1, (
    _p("delay", "time", FLOAT, 0.0, 0.5, 0.1, step=0.005),
), _delay, lambda p: p["time"] == 0.0, "follower"))
_register(EffectDef("jiggle", 1, (
    _p("jiggle", "frequency", FLOAT, 0.5, 30.0, 8.0, step=0.1, soft_max=20.0),
    _p("jiggle", "damping", FLOAT, 0.02, 1.0, 0.12, step=0.01),
    _p("jiggle", "limit", FLOAT, 0.01, 1.0, 0.15, step=0.01),
), _jiggle, lambda p: False, "follower"))

EFFECT_ORDER = ("contrast", "timing_bias", "noise", "gaussian", "sharpen", "spring", "lag",
                "delay", "jiggle")
