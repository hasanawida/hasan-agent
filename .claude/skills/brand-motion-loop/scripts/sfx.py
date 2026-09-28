#!/usr/bin/env python3
"""Shared UI sound palette + loop mixer for the one-shape motion loops.

Every UI sound is synthesised (nothing to license). Mixer.place() measures each sound's peak
and puts that peak exactly on its cue; tails that run past the loop end wrap to the start.
Mixer.music() crossfades what follows bar 7 into bar 1 so the music loop point is seamless.
"""
import subprocess
import wave

import imageio_ffmpeg
import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000
rng = np.random.default_rng(7)


def band(x, lo, hi, order=2):
    return sosfilt(butter(order, [lo, hi], btype="band", fs=SR, output="sos"), x)


def hp(x, f, order=2):
    return sosfilt(butter(order, f, btype="high", fs=SR, output="sos"), x)


def lp(x, f, order=2):
    return sosfilt(butter(order, f, btype="low", fs=SR, output="sos"), x)


def ramp_in(x, ms=0.6):
    n = max(1, int(ms / 1000 * SR))
    x[:n] *= np.linspace(0, 1, n)
    return x


def db(v):
    return 10 ** (v / 20)


def t_(dur):
    return np.arange(int(dur * SR)) / SR












# ---------------------------------------------------------------- sound palette
def click(freq=1900, gain=-15, body=160, dur=0.09, bright=1.0):
    t = t_(dur)
    s = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.011)
    s += 0.45 * np.sin(2 * np.pi * freq * 1.62 * t) * np.exp(-t / 0.005) * bright
    s += 0.5 * np.sin(2 * np.pi * body * t) * np.exp(-t / 0.018)
    s += 0.35 * hp(rng.standard_normal(len(t)), 3500) * np.exp(-t / 0.0018) * bright
    s = ramp_in(s)
    return s / np.abs(s).max() * db(gain)


def tick(freq=4200, gain=-24, dur=0.03):
    t = t_(dur)
    s = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.0025)
    s += 0.6 * band(rng.standard_normal(len(t)), 3000, 7000) * np.exp(-t / 0.0015)
    s = ramp_in(s, 0.3)
    return s / np.abs(s).max() * db(gain)


def key(gain=-17, pitch=1.0, dur=0.07):
    t = t_(dur)
    s = band(rng.standard_normal(len(t)), 1200 * pitch, 4200 * pitch) * np.exp(-t / 0.006)
    s += 0.7 * np.sin(2 * np.pi * 420 * pitch * t) * np.exp(-t / 0.009)
    s += 0.25 * np.sin(2 * np.pi * 140 * t) * np.exp(-t / 0.02)
    s = ramp_in(s, 0.4)
    return s / np.abs(s).max() * db(gain)


def whoosh(gain=-27, dur=0.34, peak_at=0.11, lo=450, hi=1500):
    t = t_(dur)
    n = rng.standard_normal(len(t))
    a, b = band(n, lo * 0.7, lo * 1.4), band(n, hi * 0.7, hi * 1.4)
    k = np.clip(t / dur, 0, 1)
    s = a * (1 - k) + b * k                                 # rising colour
    env = np.where(t < peak_at, (t / peak_at) ** 2, np.exp(-(t - peak_at) / 0.07))
    s = s * env
    return s / np.abs(s).max() * db(gain)


def chime(freqs, gain=-21, stagger=0.045, decay=0.32):
    dur = stagger * len(freqs) + decay * 4
    t = t_(dur)
    s = np.zeros(len(t))
    for i, f in enumerate(freqs):
        o = int(i * stagger * SR)
        tt = t[: len(t) - o]
        v = np.sin(2 * np.pi * f * tt) + 0.18 * np.sin(2 * np.pi * 2 * f * tt) * np.exp(-tt / 0.08)
        v *= np.exp(-tt / decay) * (1 - np.exp(-tt / 0.002))
        s[o:] += v * (1.0 if i == 0 else 0.8)
    return s / np.abs(s).max() * db(gain)


def toggle(gain=-15):
    a, b = click(2300, gain, 190, 0.07), click(1650, gain - 5, 140, 0.07)
    o = int(0.028 * SR)
    s = np.zeros(o + len(b))
    s[: len(a)] += a
    s[o:] += b
    return s


def thwip(gain=-22, dur=0.12):
    t = t_(dur)
    f = 620 * np.exp(-t / 0.035) + 170
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.04)
    return ramp_in(s, 1.0) / np.abs(s).max() * db(gain)


def draw_sweep(gain=-30, dur=0.6):
    t = t_(dur)
    n = rng.standard_normal(len(t))
    k = t / dur
    s = band(n, 2500, 5000) * k + band(n, 900, 1800) * (1 - k)
    env = np.sin(np.pi * np.clip(k, 0, 1)) ** 2
    return s * env / np.abs(s * env).max() * db(gain)


def stretch_tone(curve):
    """Rubber-band tension: a soft low tone whose level and pitch follow the stretch the page computed."""
    ts = np.array([c[0] for c in curve]); ss = np.array([c[1] for c in curve])
    t0 = ts[0]
    t = t0 + np.arange(int((ts[-1] - t0) * SR)) / SR
    s = np.interp(t, ts, ss)
    amp = np.clip(s / 50.0, 0, 1) ** 1.3
    f = 92 + 1.1 * s
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = (np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)) * amp
    y = lp(y, 900)
    return t0, y / (np.abs(y).max() + 1e-9) * db(-25)


def peak_index(s):
    e = np.convolve(np.abs(s), np.ones(48) / 48, mode="same")   # 1 ms envelope
    return int(np.argmax(e))


class Mixer:
    def __init__(self, loop_s, beat_s):
        self.N = int(round(loop_s * SR))
        self.B = beat_s
        self.sfx = np.zeros(self.N)
        self.report = []
        self.loop = np.zeros((self.N, 2))

    def music(self, wav_path, rms_db=-17.0, xfade_s=0.12, loop_s=None):
        """Seamless music loop of loop_s seconds (default: the whole mix), tiled to fill the mix."""
        raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(wav_path), "-f", "f32le",
                              "-ac", "2", "-ar", str(SR), "-"], check=True, capture_output=True).stdout
        m = np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)
        N = int(round((loop_s or self.N / SR) * SR))
        X = int(xfade_s * SR)
        loop = m[:N].copy()
        g = np.linspace(0, 1, X)[:, None]
        loop[:X] = m[:X] * np.sin(g * np.pi / 2) + m[N:N + X] * np.cos(g * np.pi / 2)   # equal power
        loop *= db(rms_db) / np.sqrt(np.mean(loop ** 2))
        reps = -(-self.N // N)
        self.loop = np.tile(loop, (reps, 1))[:self.N]                                  # a seamless loop tiles seamlessly

    def place(self, sound, at, name, first_ms=None):
        """Put the sound's measured peak on `at` (for multi-note sounds: the first note's peak)."""
        k = peak_index(sound if first_ms is None else sound[:int(first_ms / 1000 * SR)])
        start = int(round(at * SR)) - k
        idx = (start + np.arange(len(sound))) % self.N
        np.add.at(self.sfx, idx, sound)
        self.report.append({"t": round(at, 4), "beat": round(at / self.B, 3), "sound": name,
                            "peak_ms_into_sound": round(k / SR * 1000, 2)})

    def add_at(self, t0, sound, name):
        idx = (int(round(t0 * SR)) + np.arange(len(sound))) % self.N
        np.add.at(self.sfx, idx, sound)
        self.report.append({"t": round(t0, 4), "beat": round(t0 / self.B, 3), "sound": name, "peak_ms_into_sound": None})

    def save(self, out_wav):
        mix = self.loop + self.sfx[:, None]
        ceiling, knee = db(-1.0), db(-4.0)                                # soft-knee limiter: unity below the knee,
        a = np.abs(mix)                                                   # smoothly approaches the ceiling above it
        over = a > knee
        mix[over] = np.sign(mix[over]) * (knee + (ceiling - knee) * np.tanh((a[over] - knee) / (ceiling - knee)))
        pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
        return {"seconds": round(len(mix) / SR, 3), "peak_dbfs": round(20 * np.log10(np.abs(mix).max()), 2),
                "sounds": len(self.report), "seam_step": round(float(np.abs(mix[-1] - mix[0]).max()), 4)}
