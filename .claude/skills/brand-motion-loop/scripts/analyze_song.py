#!/usr/bin/env python3
"""Beat grid for the loop.

1. Measure tempo (comb over the onset envelope), beat phase and downbeat phase with numpy.
2. Pick a 7-bar window that starts on a downbeat, sits in the full groove and loops cleanly.
3. Conform it to exactly 120 BPM (ffmpeg atempo, pitch preserved) so 28 beats = 14.000 s.
4. Re-measure the conformed audio and report how far its beats sit from the k * 0.5 s grid.

Usage: python3 tools/analyze_song.py <project>/audio   (reads track.json there)
Writes grid.json and music_120.wav (loop + tail for the seam crossfade) next to it.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import imageio_ffmpeg
import numpy as np

HERE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
SRC = HERE / "src"
FF = imageio_ffmpeg.get_ffmpeg_exe()
TARGET_BPM = 120.0
BEATS_PER_BAR = 4


def decode(path, sr, channels=1):
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(channels), "-ar", str(sr), "-"],
                         check=True, capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def stft_mag(x, nfft=2048, hop=128):
    pad = np.pad(x, (nfft // 2, nfft // 2))
    n = 1 + (len(pad) - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(pad[idx] * np.hanning(nfft), axis=1))


def onset_env(mag, sr, nfft, lo_hz=0, hi_hz=None):
    f = np.fft.rfftfreq(nfft, 1 / sr)
    band = (f >= lo_hz) & (f < (hi_hz or sr / 2))
    lm = np.log1p(1000 * mag[:, band])
    flux = np.maximum(0, np.diff(lm, axis=0, prepend=lm[:1])).sum(1)
    k = np.ones(9) / 9                                   # remove slow trend
    return np.maximum(0, flux - np.convolve(flux, k, mode="same"))


def comb_salience(env, fps, bpms):
    n = np.arange(len(env))
    e = env - env.mean()
    out = np.zeros(len(bpms))
    for h, wgt in ((1, 1.0), (2, 0.6), (4, 0.3)):       # beat + 8th + 16th pulse
        for i in range(0, len(bpms), 64):
            f = bpms[i:i + 64, None] / 60 * h
            out[i:i + 64] += wgt * np.abs(np.exp(-2j * np.pi * f * n[None, :] / fps) @ e)
    return out


def measure_tempo(env, fps, lo=100, hi=140):
    coarse = np.arange(lo, hi, 0.05)
    c = comb_salience(env, fps, coarse)
    b0 = coarse[np.argmax(c)]
    fine = np.arange(b0 - 0.1, b0 + 0.1, 0.001)
    return float(fine[np.argmax(comb_salience(env, fps, fine))])


def interp(env, fps, t):
    x = t * fps
    i = np.clip(np.floor(x).astype(int), 0, len(env) - 2)
    a = x - i
    return env[i] * (1 - a) + env[i + 1] * a


def beat_phase(env, fps, period, t_end):
    phis = np.arange(0, period, 0.0005)
    k = np.arange(int(t_end / period) - 1)
    score = [interp(env, fps, p + k * period).mean() for p in phis]
    return float(phis[int(np.argmax(score))])


def per_beat_features(mag, sr, nfft, hop, beats):
    f = np.fft.rfftfreq(nfft, 1 / sr)
    edges = np.geomspace(40, 11000, 25)                  # 24 log bands
    bands = np.stack([mag[:, (f >= a) & (f < b)].mean(1) for a, b in zip(edges[:-1], edges[1:])], 1)
    lb = np.log1p(100 * bands)
    fps = sr / hop
    feats = []
    for a, b in zip(beats[:-1], beats[1:]):
        fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        feats.append(lb[fa:fb].mean(0))
    return np.array(feats)


def main():
    track = json.loads((HERE / "track.json").read_text())
    global BARS
    BARS = int(track.get("bars", 7))                     # length of the musical loop in bars
    SRC.mkdir(exist_ok=True)
    mp3 = SRC / f"{track['id']}.mp3"
    if not mp3.exists():
        urllib.request.urlretrieve(track["mp3_url"], mp3)

    sr, nfft, hop = 22050, 2048, 128
    fps = sr / hop
    x = decode(mp3, sr)
    dur = len(x) / sr
    mag = stft_mag(x, nfft, hop)
    env = onset_env(mag, sr, nfft)

    bpm = measure_tempo(env, fps)
    period = 60 / bpm
    # phase from the kick. A kick is the one hit with a low thump AND a broadband click at the same instant;
    # offbeat bass has the thump without the click, offbeat hats/claps the click without the thump.
    # Geometric mean of the two onset envelopes keeps only kicks. Short window (512) so peaks are not pulled early.
    tfps = sr / 32
    m512 = stft_mag(x, 512, 32)
    low = onset_env(m512, sr, 512, 30, 150)
    click = onset_env(m512, sr, 512, 160, 2500)
    kick = np.sqrt((low / (low.mean() + 1e-9)) * (click / (click.mean() + 1e-9)))
    phi = beat_phase(kick, tfps, period, dur)
    beats = phi + period * np.arange(-int(phi / period) - 1, int((dur - phi) / period))
    beats = beats[beats > -0.05]

    # downbeat. Primary evidence: DAW-made tracks start on bar 1, so if the first sound lands on a grid
    # beat, that beat is a downbeat. Secondary (reported): phrase novelty across 4-beat blocks.
    feats = per_beat_features(mag, sr, nfft, hop, np.maximum(beats, 0))
    nov = np.zeros(len(feats))
    for i in range(4, len(feats) - 4):
        nov[i] = np.linalg.norm(feats[i:i + 4].mean(0) - feats[i - 4:i].mean(0))
    nn = nov / (nov.mean() + 1e-9)
    novelty_score = [float(np.mean(nn[j::4])) for j in range(4)]
    t_first = float(np.argmax(np.abs(x) > 0.01 * np.abs(x).max()) / sr)
    b_first = int(np.argmin(np.abs(beats - t_first)))
    if abs(beats[b_first] - t_first) < 0.06:
        dphase, dsource = b_first % 4, f"first sound at {t_first:.3f}s sits on beat {b_first}"
    else:
        dphase, dsource = int(np.argmax(novelty_score)), "phrase novelty"
    downbeats = beats[dphase::4]
    phase_score = novelty_score

    # window: in the full groove, steady energy, and the bar after the loop sounds like its first bar
    rms_beat = np.array([np.sqrt(np.mean(x[max(0, int(a * sr)):int(b * sr)] ** 2)) for a, b in zip(beats[:-1], beats[1:])])
    groove = np.median(rms_beat[len(rms_beat) // 4: 3 * len(rms_beat) // 4])
    cands = []
    for i, d in enumerate(downbeats):
        bi = dphase + 4 * i                              # beat index of this downbeat
        if bi + 4 * (BARS + 1) >= len(feats):
            break
        bars = [feats[bi + 4 * k: bi + 4 * k + 4].mean(0) for k in range(BARS + 1)]
        e = rms_beat[bi: bi + 4 * BARS]
        if e.min() < 0.6 * groove:
            continue
        seam = np.linalg.norm(bars[BARS] - bars[0])      # what follows the loop vs how it starts
        steady = np.std(np.log(e + 1e-9))
        cands.append((seam + 3 * steady, float(d), float(seam), float(steady)))
    cands.sort()
    forced = track.get("window_start_s")
    if forced is not None:
        start = float(downbeats[np.argmin(np.abs(downbeats - forced))])
    else:
        start = cands[0][1]

    # conform: cut with a tail for the loop crossfade, stretch to 120 BPM, keep 48 kHz stereo
    loop_src = BARS * BEATS_PER_BAR * period
    tail = 1.0
    tempo = TARGET_BPM / bpm
    out = HERE / "music_120.wav"
    grid = np.arange(BARS * BEATS_PER_BAR) * 0.5

    def cut(src_start):
        pre = 0.25                                       # keep a little before the downbeat, trimmed after stretch
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{src_start - pre:.6f}", "-t", f"{loop_src + pre + tail / tempo:.6f}",
                        "-i", str(mp3), "-af", f"atempo={tempo:.8f},atrim=start={pre / tempo:.6f},asetpts=PTS-STARTPTS",
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", str(out)], check=True)

    def attack_offset():
        # average the (rectified, 1 ms smoothed) waveform over all 28 grid beats; find where the kick attack rises
        y48 = decode(out, 48000)
        env48 = np.convolve(np.abs(y48), np.ones(48) / 48, mode="same")
        w = np.arange(-int(0.04 * 48000), int(0.06 * 48000))
        segs = [env48[int(g * 48000) + w] for g in grid[1:] if int(g * 48000) + w[-1] < len(env48)]
        m = np.mean(segs, 0)
        base, peak = np.median(m[:int(0.03 * 48000)]), m.max()
        k = int(np.argmax(m > base + 0.3 * (peak - base)))
        return (w[k] / 48000) * 1000, m

    cut(start)
    att_ms, _ = attack_offset()
    start = start + (att_ms / 1000) * tempo              # move the cut so the attack sits on the grid
    cut(start)
    att_ms, _ = attack_offset()

    # verify every beat: the kick is one repeated sample, so cross-correlate each beat's low band with the
    # average kick (template) and read the lag. Sub-ms precise, and bass notes average out of the template.
    from scipy.signal import butter, sosfiltfilt
    y48 = decode(out, 48000)
    yl = sosfiltfilt(butter(4, 200, btype="low", fs=48000, output="sos"), y48)
    pre, post, search = int(0.02 * 48000), int(0.08 * 48000), int(0.04 * 48000)
    idx = [int(round(g * 48000)) for g in grid]
    tmpl = np.mean([yl[i - pre:i + post] for i in idx[1:] if i + post < len(yl)], 0)
    errs = []
    for i in idx:
        best, lag = -np.inf, 0
        for L in range(-search, search + 1, 4):
            a = i + L - pre
            if a < 0 or a + pre + post > len(yl):
                continue
            c = float(np.dot(yl[a:a + pre + post], tmpl))
            if c > best:
                best, lag = c, L
        errs.append(lag / 48000)
    errs = np.array(errs) * 1000
    errs = errs[1:]                                      # beat 0 sits on the file edge (no pre-roll to measure against)
    offset_ms = float(np.median(errs))
    y = decode(out, sr)
    ym = stft_mag(y, nfft, hop)
    y_bpm = measure_tempo(onset_env(ym, sr, nfft), fps, 110, 130)

    res = {
        "track": track, "measured_bpm": round(bpm, 3), "beat_phase_s": round(phi, 4),
        "downbeat_phase": dphase, "downbeat_source": dsource, "novelty_by_phase": [round(v, 3) for v in phase_score],
        "kick_attack_vs_grid_ms": round(att_ms, 2),
        "window_start_s": round(start, 4), "window_len_src_s": round(loop_src, 4),
        "atempo": round(tempo, 6), "bpm": TARGET_BPM, "beats": BARS * BEATS_PER_BAR, "loop_s": BARS * BEATS_PER_BAR * 60 / TARGET_BPM,
        "top_windows": [{"start_s": round(c[1], 3), "seam": round(c[2], 3), "steady": round(c[3], 3)} for c in cands[:5]],
        "conformed_bpm": round(y_bpm, 3),
        "kick_xcorr_vs_grid_ms": {"median": round(offset_ms, 2), "max_abs_dev": round(float(np.max(np.abs(errs - offset_ms))), 2),
                          "per_beat": [round(float(e), 1) for e in errs]},
    }
    (HERE / "grid.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k not in ("track",)}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
