# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Preview transport: a looping playback phase driven by an injected clock.

Pure state machine; the UI polls :meth:`PreviewTransport.phase` from a timer while playing.
Playback never changes the curve or the scene.
"""

from __future__ import annotations

from collections.abc import Callable

from motion_atelier.curve.evaluate import value_at
from motion_atelier.curve.model import Curve

MIN_DURATION = 0.1
MAX_DURATION = 60.0


def marker_at(curve: Curve, phase: float) -> tuple[float, float]:
    """The point of the curve at playback ``phase`` in ``[0, 1]`` (time-linear)."""
    phase = min(1.0, max(0.0, phase))
    x = curve.x_start + (curve.x_end - curve.x_start) * phase
    return x, value_at(curve, x)


class PreviewTransport:
    """Play / pause / restart with a looping phase. ``clock()`` returns monotonic seconds."""

    def __init__(self, clock: Callable[[], float], duration: float = 1.5) -> None:
        self._clock = clock
        self._duration = self._clamp(duration)
        self._playing = False
        self._offset = 0.0  # elapsed seconds accumulated while paused
        self._started = 0.0

    @staticmethod
    def _clamp(duration: float) -> float:
        return min(MAX_DURATION, max(MIN_DURATION, float(duration)))

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def duration(self) -> float:
        return self._duration

    def elapsed(self) -> float:
        if self._playing:
            return self._offset + (self._clock() - self._started)
        return self._offset

    def phase(self) -> float:
        return (self.elapsed() % self._duration) / self._duration

    def play(self) -> None:
        if not self._playing:
            self._started = self._clock()
            self._playing = True

    def pause(self) -> None:
        if self._playing:
            self._offset = self.elapsed()
            self._playing = False

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def restart(self) -> None:
        self._offset = 0.0
        self._started = self._clock()

    def set_duration(self, seconds: float) -> None:
        """Change the duration keeping the current phase (no jump of the marker)."""
        phase = self.phase()
        self._duration = self._clamp(seconds)
        self._offset = phase * self._duration
        self._started = self._clock()
