# 4. Load Profiles

A **profile** defines *how* the messages are spread over the test `--duration`.
Same number of messages, very different stress on the system.

All profiles share two inputs:

- `--messages` / `total_messages` — how many messages in total.
- `--duration` / `duration_seconds` — the target time window to spread them over.

Each profile implements `get_delay_for_message(i)` which returns how long to wait
before publishing message `i`.

> Quick view in your terminal: `python tests/load/cli.py profiles`

---

## 4.1 Ramp (`-p ramp`) — Gradual increase

**Shape:** starts slow, accelerates, ends fast (delay decreases linearly).

**Formula:**
```
avg_delay = duration / total_messages
delay(i)  = avg_delay * 2 * (1 - i/(total_messages - 1))
```
So the first message waits ~`2 × avg_delay`, the last waits ~`0`.

**Visual (throughput over time):**
```
msgs/sec │              ▁▂▃▄▅▆▇█
         │         ▁▂▃▄▅
         │    ▁▂▃▄
         │ ▁▂▃
         └─────────────────────────► time
```

**Use it for:**
- Morning login surge / gradual traffic build-up.
- Verifying autoscaling reacts smoothly.
- A safe "default" profile for general latency measurement.

**Example:**
```bash
python tests/load/cli.py managed -p ramp -n 50 -d 120 --seed-dir candidate_data
```

---

## 4.2 Spike (`-p spike`) — Sudden burst

**Shape:** 80% of all messages are fired in the first 20% of the time window,
then the remaining 20% trickle out over the rest.

**Parameters:** `burst_pct = 0.8` (configurable in code).

**Formula:**
```
burst_count  = total_messages * 0.8
burst_window = duration * 0.2
during burst: delay = burst_window / burst_count       (very small)
after  burst: delay = (duration - burst_window) / remaining
```

**Visual:**
```
msgs/sec │ █
         │ █
         │ █▇
         │ ██▅▃▂▁▁▁▁▁▁▁▁▁
         └─────────────────────────► time
```

**Use it for:**
- Flash events, viral spikes, a batch job dumping a backlog at once.
- Testing flow control, `max_messages`, and back-pressure handling.
- Finding cold-start / queue-buildup behavior.

**Example:**
```bash
python tests/load/cli.py managed -p spike -n 200 -d 60 --seed-dir candidate_data
```

---

## 4.3 Soak (`-p soak`) — Sustained constant load

**Shape:** uniform, steady rate for the whole duration.

**Formula:**
```
delay(i) = duration / total_messages   (constant)
```

**Visual:**
```
msgs/sec │ ▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆▆
         └─────────────────────────► time
```

**Use it for:**
- Long-running stability tests (memory leaks, connection-pool exhaustion,
  gradual degradation, FD leaks).
- Establishing a steady-state baseline.

**Tip:** soak tests are usually **long**. Pair a long `--duration` with a
generous `--timeout`:
```bash
python tests/load/cli.py managed -p soak -n 600 -d 1800 --timeout 2400 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

---

## 4.4 Stress (`-p stress`) — Escalating waves

**Shape:** 5 waves; each wave publishes faster than the previous (intensity
multiplies by `1.5×` per wave). Designed to find the breaking point.

**Parameters:** `waves = 5`, intensity = `1.5 ** wave_index`.

**Formula:**
```
msgs_per_wave = total_messages / 5
time_per_wave = duration / 5
base_delay    = time_per_wave / msgs_per_wave
delay(i)      = base_delay / (1.5 ** current_wave)
```

**Visual:**
```
msgs/sec │                      ████
         │                ▆▆▆▆
         │           ▅▅▅
         │       ▄▄
         │   ▃▃
         └─────────────────────────► time
            w1   w2   w3   w4   w5
```

**Use it for:**
- Finding the throughput ceiling / saturation point.
- Observing where latency starts to climb and errors appear.
- Capacity planning.

**Example:**
```bash
python tests/load/cli.py managed -p stress -n 250 -d 100 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

---

## 4.5 Choosing the Right Profile

| Goal | Profile |
|------|---------|
| General latency measurement / default | **ramp** |
| Burst resilience, flow control, backlog drain | **spike** |
| Stability over time, leak detection | **soak** |
| Find max throughput / breaking point | **stress** |

---

## 4.6 Picking `--messages` and `--duration`

- **Effective average rate** ≈ `messages / duration` msgs/sec (exact distribution
  depends on the profile).
- For `spike`, the *peak* rate is much higher than the average — size your
  `--messages` accordingly.
- For `soak`, choose a low rate but a long duration.
- For `stress`, the final wave is ~`1.5⁴ ≈ 5×` the first wave's rate.

**Worked examples:**

| Command | Avg rate | Notes |
|---------|----------|-------|
| `-p soak -n 60 -d 60` | 1 msg/s | Gentle steady-state |
| `-p ramp -n 120 -d 60` | ramps 0→~4 msg/s | Builds up |
| `-p spike -n 100 -d 60` | 80 msgs in first 12s | ~6.6 msg/s peak |
| `-p stress -n 250 -d 100` | escalating | last wave ~5× first |

---

## 4.7 Customizing Profiles (Advanced)

Profiles live in `tests/load/core/profiles.py`. To tweak:

- **Spike burst fraction:** change `burst_pct` on `SpikeProfile` (default `0.8`).
- **Stress waves / intensity:** change `waves` (default `5`) or the `1.5`
  multiplier in `StressProfile.get_delay_for_message`.
- **New profile:** subclass `LoadProfile`, set `self.load_type` in
  `__post_init__`, implement `get_delay_for_message`, and register it in the
  `LOAD_PROFILES` dict in `cli.py`.

```python
@dataclass
class SawtoothProfile(LoadProfile):
    def __post_init__(self):
        self.load_type = LoadType.RAMP  # or a new enum value
        self.description = "Repeating sawtooth bursts"

    def get_delay_for_message(self, i: int) -> float:
        ...
```

---

## Next Steps

- See these profiles applied to real situations → [Scenarios & Recipes](./05-scenarios.md)
- Understand the output → [Metrics & Reports](./06-metrics-and-reports.md)
</content>
