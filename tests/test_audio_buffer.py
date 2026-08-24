from array import array

from ron.voice.audio import (
    SampleRingBuffer,
    StreamingLinearResampler,
    linear_resample,
    root_mean_square,
)


def test_ring_buffer_keeps_only_newest_samples() -> None:
    buffer = SampleRingBuffer(5)
    buffer.append([1.0, 2.0, 3.0])
    buffer.append([4.0, 5.0, 6.0])

    assert list(buffer.snapshot()) == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert buffer.sample_count == 5


def test_resampler_is_bounded_and_preserves_endpoints() -> None:
    result = linear_resample(array("f", [0.0, 0.5, 1.0, 0.5]), 8_000, 16_000)

    assert len(result) == 8
    assert result[0] == 0.0
    assert result[-1] == 0.5
    assert 0.0 < root_mean_square(result) < 1.0


def test_streaming_resampler_matches_one_continuous_timeline() -> None:
    resampler = StreamingLinearResampler(48_000, 16_000)

    first = resampler.process(array("f", range(48)))
    second = resampler.process(array("f", range(48, 96)))

    combined = list((*first, *second))
    assert combined == [float(value) for value in range(0, 96, 3)]
