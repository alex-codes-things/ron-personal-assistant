"""List or safely measure microphone input without recognition or tool execution."""

from __future__ import annotations

import argparse
import math
import time

from ron.voice.audio import MicrophoneStream, root_mean_square


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List input microphones and exit")
    parser.add_argument("--seconds", type=float, default=5.0, help="Measurement duration")
    parser.add_argument("--device", help="Numeric device index or unique name fragment")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    devices = MicrophoneStream.list_input_devices()
    if not devices:
        print("No input microphones were found.")
        return 1
    print("Available input microphones:")
    for device in devices:
        print(
            f"  {device['index']}: {device['name']} "
            f"(native {device['sample_rate']} Hz)"
        )
    if args.list:
        return 0
    if not 1.0 <= args.seconds <= 60.0:
        raise SystemExit("--seconds must be between 1 and 60")
    selected: str | int | None = args.device
    if isinstance(selected, str) and selected.isdecimal():
        selected = int(selected)
    stream = MicrophoneStream(device=selected)
    rms_values: list[float] = []
    peak = 0.0
    stream.start()
    print(f"Measuring {stream.device_label} for {args.seconds:.1f} seconds...")
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            samples = stream.read(0.5)
            if samples is None:
                continue
            rms_values.append(root_mean_square(samples))
            peak = max(peak, max((abs(float(value)) for value in samples), default=0.0))
    finally:
        stream.stop()
    if not rms_values:
        print("The microphone opened but returned no audio frames.")
        return 1
    average = sum(rms_values) / len(rms_values)
    dbfs = 20 * math.log10(max(average, 1e-8))
    print(f"Average level: {dbfs:.1f} dBFS; peak: {peak:.3f}; overflows: {stream.overflow_count}")
    if peak < 0.003:
        print("Warning: the microphone is extremely quiet. Check Windows input level.")
    elif peak >= 0.99:
        print("Warning: the microphone is clipping. Lower its Windows input level.")
    else:
        print("Microphone capture looks usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
