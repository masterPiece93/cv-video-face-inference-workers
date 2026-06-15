"""Load profiles — define how load is shaped over time.

Each profile implements get_delay_for_message() to control the publish rate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from tests.load.core.types import LoadProfile, LoadType


@dataclass
class RampProfile(LoadProfile):
    """Gradual ramp-up from 0 to target throughput.

    Publishes messages with decreasing delay — starts slow, ends fast.
    Models gradual traffic increase (e.g., morning login surge).
    """

    def __post_init__(self):
        self.load_type = LoadType.RAMP
        if not self.description:
            self.description = "Gradual ramp-up to target throughput"

    def get_delay_for_message(self, message_index: int) -> float:
        """Linearly decreasing delay (slower start, faster end)."""
        if self.total_messages <= 1:
            return 0.0
        progress = message_index / (self.total_messages - 1)
        avg_delay = self.duration_seconds / self.total_messages
        return avg_delay * 2 * (1 - progress)


@dataclass
class SpikeProfile(LoadProfile):
    """Sudden spike — publishes a burst with minimal delay.

    Models flash-sale or viral event traffic patterns.
    """

    burst_pct: float = 0.8  # 80% of messages in first 20% of time

    def __post_init__(self):
        self.load_type = LoadType.SPIKE
        if not self.description:
            self.description = "Sudden burst of messages with minimal delay"

    def get_delay_for_message(self, message_index: int) -> float:
        """Most messages sent in the initial burst window."""
        if self.total_messages <= 1:
            return 0.0
        burst_count = int(self.total_messages * self.burst_pct)
        burst_window = self.duration_seconds * 0.2

        if message_index < burst_count:
            return burst_window / burst_count
        else:
            remaining = self.total_messages - burst_count
            remaining_time = self.duration_seconds - burst_window
            return remaining_time / remaining if remaining > 0 else 0.0


@dataclass
class SoakProfile(LoadProfile):
    """Sustained constant load over an extended period.

    Models typical business-hours traffic. Useful for detecting memory leaks,
    connection pool exhaustion, or gradual degradation.
    """

    def __post_init__(self):
        self.load_type = LoadType.SOAK
        if not self.description:
            self.description = "Constant sustained load over extended duration"

    def get_delay_for_message(self, message_index: int) -> float:
        """Uniform delay between messages."""
        if self.total_messages <= 1:
            return 0.0
        return self.duration_seconds / self.total_messages


@dataclass
class StressProfile(LoadProfile):
    """Incrementally increasing load until breaking point.

    Publishes in waves — each wave has more messages per second than the last.
    Useful for finding throughput ceiling and saturation point.
    """

    waves: int = 5  # Number of increasing-intensity waves

    def __post_init__(self):
        self.load_type = LoadType.STRESS
        if not self.description:
            self.description = "Incrementally increasing load to find breaking point"

    def get_delay_for_message(self, message_index: int) -> float:
        """Decreasing delay in steps (waves)."""
        if self.total_messages <= 1:
            return 0.0
        msgs_per_wave = self.total_messages // self.waves
        time_per_wave = self.duration_seconds / self.waves

        if msgs_per_wave == 0:
            return self.duration_seconds / self.total_messages

        current_wave = min(message_index // msgs_per_wave, self.waves - 1)
        intensity = 1.5 ** current_wave
        base_delay = time_per_wave / msgs_per_wave
        return base_delay / intensity
