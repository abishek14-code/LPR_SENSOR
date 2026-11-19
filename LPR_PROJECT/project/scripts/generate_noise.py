"""Create reproducible, unit-RMS PWL stimulus; not measured or simulated data."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def generate(destination, seed=42, sample_hz=200, duration=56):
    destination = Path(destination)
    if sample_hz <= 0 or duration <= 0:
        raise ValueError('sample_hz and duration must be positive')
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration * sample_hz) + 1) / sample_hz
    y = rng.normal(size=len(t))
    y -= y.mean()
    # Normalize the CONTINUOUS piecewise-linear waveform's time-domain RMS.
    # Integral over each segment is dt*(a*a+a*b+b*b)/3.
    rms = np.sqrt(np.mean((y[:-1]**2 + y[:-1]*y[1:] + y[1:]**2)/3))
    y /= rms
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(destination, np.column_stack([t, y]), fmt='%.12e')
    metadata = dict(seed=seed, sample_hz=sample_hz, duration_s=duration,
                    rms_continuous_before_scaling=1.0, interpolation='linear',
                    provenance='GENERATED_STIMULUS_NOT_SIMULATOR_OUTPUT',
                    sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
    destination.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
    return metadata


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT/'ltspice/stimuli/noise_seed42.txt')
    args = ap.parse_args()
    print(json.dumps(generate(args.output), indent=2))
