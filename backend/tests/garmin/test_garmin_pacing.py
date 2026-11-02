"""The two request pacers every Garmin caller uses (apps/fitness/garmin/pacing.py)."""

from apps.fitness.garmin.pacing import FixedPause, MinInterval


def test_fixed_pause_sleeps_the_same_before_every_request():
    slept: list[float] = []
    pacer = FixedPause(1.5, slept.append)
    pacer.wait()
    pacer.wait()
    assert slept == [1.5, 1.5]


def test_min_interval_sleeps_only_the_rest_of_the_interval():
    now = [100.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    pacer = MinInterval(2.0, sleep, lambda: now[0])
    pacer.wait()  # first request: nothing to wait for
    now[0] += 0.5
    pacer.wait()  # 0.5 s after the first: 1.5 s left
    now[0] += 5.0
    pacer.wait()  # long after: no wait
    assert slept == [1.5]
