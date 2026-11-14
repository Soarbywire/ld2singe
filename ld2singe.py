#!/usr/bin/env python3
"""
ld2singe 1.0 - put original laserdisc video into a Hypseus Singe game.

Given
  * the laserdisc video extracted from a MAME CHD  (chdman extractld -> .avi)
  * a working Singe game folder (framefile .txt + its .m2v/.ogg video + scripts or Zip ROM)
it works out, scene by scene, which laserdisc frames show the same picture as each
frame of the game's video, then writes a new video (one .m2v + one .ogg) and framefile
from the laserdisc. Frame numbers stay the same, so the game's scripts are not touched.

Steps (results are cached, so re-running is quick):
  scan       small greyscale thumbnail of every frame of both videos
  calibrate  measure how the game video is flipped and framed relative to the laserdisc
  match      find the laserdisc frames for every scene; write report.csv + check sheets
  build      write the new game folder (work/game)

Usage
  python ld2singe.py --ld timetrv.avi --game "C:\\Hypseus Singe\\singe\\Hologram" --out work --step match
  python ld2singe.py --ld timetrv.avi --game "C:\\Hypseus Singe\\singe\\Hologram" --out work

Needs Python 3.9+, numpy, Pillow, and ffmpeg + ffprobe (on the PATH or via --ffmpeg).
Your original files are only read, never changed.
"""
__version__ = "1.0"

import argparse, csv, json, os, shutil, subprocess, sys, time
from fractions import Fraction
import numpy as np
from PIL import Image, ImageDraw

TW, TH = 64, 48            # stored thumbnail size (whole frame)
FW, FH = 32, 24            # feature grid compared between the videos
BLANK_STD = 6.0            # features flatter than this are "blank" (black frames)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

# ============================================================== ffmpeg helpers
class Tools:
    def __init__(self, ffmpeg):
        if not ffmpeg:                               # an ffmpeg next to ld2singe(.exe) wins over the PATH
            here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
            for name in ("ffmpeg.exe", "ffmpeg"):
                if os.path.isfile(os.path.join(here, name)):
                    ffmpeg = os.path.join(here, name)
                    break
        self.ffmpeg = ffmpeg or "ffmpeg"
        d = os.path.dirname(self.ffmpeg)
        exe = ".exe" if self.ffmpeg.lower().endswith(".exe") else ""
        self.ffprobe = os.path.join(d, "ffprobe" + exe) if d else "ffprobe"
        for prog in (self.ffmpeg, self.ffprobe):
            try:
                subprocess.run([prog, "-version"], capture_output=True)
            except OSError:
                sys.exit(f"Can't run {prog}. Install ffmpeg (Windows: winget install Gyan.FFmpeg, then open a new "
                         "window), put ffmpeg.exe and ffprobe.exe next to ld2singe, or use --ffmpeg PATH.")

    def probe(self, path):
        r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", path],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"ffprobe could not read {path}:\n{r.stderr}")
        s = json.loads(r.stdout)["streams"][0]
        a = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.split()
        return {"w": int(s["width"]), "h": int(s["height"]), "fps": Fraction(s["r_frame_rate"]), "has_audio": bool(a)}

    def frames(self, path, w, h, vf, count=None, pix="gray", vf_file=None):
        """Yield decoded frames as numpy arrays, one per coded frame, in display order."""
        cmd = [self.ffmpeg, "-v", "error", "-nostdin", "-i", path, "-an", "-sn"]
        cmd += [self.script_opt(), vf_file] if vf_file else ["-vf", vf]
        cmd += ["-fps_mode", "passthrough"]
        if count is not None:
            cmd += ["-frames:v", str(count)]
        cmd += ["-pix_fmt", pix, "-f", "rawvideo", "-"]
        ch = 1 if pix == "gray" else 3
        size = w * h * ch
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=size * 8)
        stopped_early = False
        try:
            while True:
                buf = p.stdout.read(size)
                if len(buf) < size:
                    break
                a = np.frombuffer(buf, np.uint8)
                yield a.reshape(h, w) if ch == 1 else a.reshape(h, w, 3)
        except GeneratorExit:
            stopped_early = True        # we had all the frames we needed; ffmpeg's complaint about the closed pipe is expected
            raise
        finally:
            if stopped_early:
                p.kill()
            p.stdout.close()
            err = p.stderr.read().decode(errors="replace")
            p.wait()
            if not stopped_early and p.returncode not in (0, None) and "Broken pipe" not in err:
                sys.exit(f"ffmpeg failed on {path}:\n{err}")

    def script_opt(self):
        """Option that reads a filter graph from a file. ffmpeg 7+ uses '-/filter:v FILE';
        ffmpeg 8 removed the older '-filter_script:v FILE'. Test which one this ffmpeg takes."""
        if getattr(self, "_script_opt", None):
            return self._script_opt
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".txt"); os.write(fd, b"null"); os.close(fd)
        try:
            for opt in ("-/filter:v", "-filter_script:v"):
                r = subprocess.run([self.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "nullsrc=d=0.1",
                                    opt, tmp, "-f", "null", "-"], capture_output=True, text=True)
                if r.returncode == 0:
                    self._script_opt = opt
                    return opt
        finally:
            os.remove(tmp)
        sys.exit("This ffmpeg accepts neither -/filter:v nor -filter_script:v; please send me 'ffmpeg -version'.")

    def start_time(self, path):
        """Timestamp of the first video frame (so frame n is at start + n / fps)."""
        r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                            "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "json", path],
                           capture_output=True, text=True)
        try:
            f = json.loads(r.stdout)["frames"][0]
            return float(f.get("pts_time") or f.get("best_effort_timestamp_time") or 0)
        except (ValueError, KeyError, IndexError):
            return 0.0

    def frame_times(self, path):
        """Timestamp of every video frame (from the container; no decoding)."""
        r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "packet=pts_time,dts_time", "-of", "csv=p=0", path], capture_output=True, text=True)
        t = []
        for line in r.stdout.split():
            parts = [x for x in line.split(",") if x and x != "N/A"]
            if parts:
                t.append(float(parts[0]))
        return np.sort(np.array(t, dtype=np.float64))

    def run(self, args):
        r = subprocess.run([self.ffmpeg, "-v", "error", "-nostdin", "-y"] + args, capture_output=True, text=True)
        if r.returncode:
            sys.exit("ffmpeg failed:\n" + " ".join(args) + "\n" + r.stderr)

# ============================================================== framefile
def read_framefile(path):
    """Hypseus framefile: first line = video folder, then '<frame> <file>' lines."""
    lines = [l.strip() for l in open(path, encoding="utf-8", errors="replace")]
    lines = [l for l in lines if l and not l.startswith(("#", ";"))]
    vdir = lines[0]
    base = os.path.dirname(os.path.abspath(path))
    vabs = vdir if os.path.isabs(vdir) else os.path.normpath(os.path.join(base, vdir))
    entries = []
    for l in lines[1:]:
        parts = l.split(None, 1)
        if len(parts) == 2 and parts[0].lstrip("-").isdigit():
            entries.append((int(parts[0]), parts[1].strip()))
    if not entries:
        sys.exit(f"No '<frame> <file>' lines found in {path}")
    return vdir, vabs, entries

def find_framefile(game):
    good = []
    for f in os.listdir(game):
        if not f.lower().endswith(".txt"):
            continue
        try:
            _, vabs, ent = read_framefile(os.path.join(game, f))
            if os.path.isdir(vabs) and any(os.path.exists(os.path.join(vabs, e[1])) for e in ent):
                good.append(f)
        except (SystemExit, UnicodeError, IndexError):
            pass
    if len(good) != 1:
        sys.exit(f"Could not pick the framefile in {game} (found: {good}). Use --framefile.")
    return os.path.join(game, good[0])

# ============================================================== thumbnails and features
def degrid_vf(passes):
    """Morphological opening: erases thin lines (grid, scanlines) a few pixels wide, keeps larger shapes."""
    return "format=gray," + "erosion," * passes + "dilation," * passes if passes else ""

def scan(tools, path, label, vbi_rows=0, degrid=0):
    info = tools.probe(path)
    pre = f"drawbox=x=0:y=0:w=iw:h={vbi_rows}:color=black:t=fill," if vbi_rows else ""
    vf = f"{pre}{degrid_vf(degrid)}scale={TW}:{TH}:flags=area,format=gray"
    out, t0 = [], time.time()
    for i, g in enumerate(tools.frames(path, TW, TH, vf)):
        out.append(g)
        if i and i % 10000 == 0:
            log(f"  {label}: {i} frames ({i / (time.time() - t0):.0f} per second)")
    if not out:
        sys.exit(f"No frames decoded from {path}")
    return np.stack(out), info

def make_grid(top, margin, T=(1.0, 1.0, 0.0, 0.0)):
    """Sample points (in thumbnail pixels) for the feature grid. The grid covers the
    laserdisc picture area; T = (sx, sy, dx, dy) maps it into the other video's frame."""
    sx, sy, dx, dy = T
    u = np.linspace(margin, 1 - margin, FW)
    v = np.linspace(max(top, margin), 1 - margin, FH)
    U, V = np.meshgrid(u, v)
    X = ((U - 0.5) * sx + 0.5 + dx) * TW - 0.5
    Y = ((V - 0.5) * sy + 0.5 + dy) * TH - 0.5
    return np.clip(Y, 0, TH - 1.001), np.clip(X, 0, TW - 1.001)

def sample(thumbs, grid):
    Y, X = grid
    y0 = np.floor(Y).astype(int); x0 = np.floor(X).astype(int)
    fy = (Y - y0).astype(np.float32); fx = (X - x0).astype(np.float32)
    out = np.empty((len(thumbs),) + Y.shape, np.float32)
    for i in range(0, len(thumbs), 20000):                  # in chunks to keep memory modest
        t = thumbs[i:i + 20000].astype(np.float32)
        a = t[:, y0, x0]; b = t[:, y0, x0 + 1]; c = t[:, y0 + 1, x0]; d = t[:, y0 + 1, x0 + 1]
        out[i:i + 20000] = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
    return out

def norm_rows(f):
    a = f.reshape(len(f), -1).astype(np.float32)
    a = a - a.mean(1, keepdims=True)
    sd = a.std(1, keepdims=True)
    blank = sd[:, 0] < BLANK_STD
    a /= np.maximum(sd, 1e-3)
    return a, blank

def norm_diffs(f):
    """Frame-to-frame change (motion), normalised. Static overlays (drawn-in props) cancel out.
    Row i = change from frame i to i+1 (last row zero)."""
    a = f.reshape(len(f), -1).astype(np.float32)
    d = a[1:] - a[:-1]
    d = d - d.mean(1, keepdims=True)
    sd = d.std(1, keepdims=True)
    still = sd[:, 0] < 0.4
    d /= np.maximum(sd, 1e-3)
    d[still] = 0
    return np.vstack([d, np.zeros((1, d.shape[1]), np.float32)])

FLIPS = {"none": (1, 1), "vflip": (1, -1), "hflip": (-1, 1), "rot180": (-1, -1)}
FLIP_VF = {"none": "", "vflip": "vflip,", "hflip": "hflip,", "rot180": "vflip,hflip,"}

# ============================================================== calibration
def calibrate(ld_thumbs, dvd_thumbs, top, margin):
    """Find flip + scale + shift that make the game video's frames line up with the laserdisc's."""
    ldf, ldb = norm_rows(sample(ld_thumbs, make_grid(top, margin)))
    lnT = np.ascontiguousarray(ldf.T); D = lnT.shape[0]
    _, db = norm_rows(sample(dvd_thumbs, make_grid(top, margin)))
    idx = np.where(~db)[0]
    if len(idx) == 0:
        sys.exit("The game video looks completely black.")
    idx = idx[np.linspace(0, len(idx) - 1, min(len(idx), 80)).round().astype(int)]
    sub = dvd_thumbs[idx]

    def feats(T):
        return norm_rows(sample(sub, make_grid(top, margin, T)))[0]

    def q_full(T):
        C = (feats(T) @ lnT) / D
        C[:, ldb] = -1
        m = np.sort(C.max(1))
        return float(m[len(m) // 4:].mean()), C              # ignore the worst quarter (frames not on the disc)

    def q_cand(T, cand):
        f = feats(T)
        m = np.sort(np.array([(f[i] @ lnT[:, c]).max() / D for i, c in enumerate(cand)]))
        return float(m[len(m) // 4:].mean())

    def refine(fx, fy, C):
        T = [1.0, 1.0, 0.0, 0.0]
        qf = None
        for rnd in range(2):
            k = min(60, C.shape[1] - 1)
            cand = [np.argpartition(-C[i], k)[:k] for i in range(len(C))]   # likely laserdisc frames per sample
            qbest = q_cand((fx * T[0], fy * T[1], T[2], T[3]), cand)
            for step in (0.04, 0.02, 0.01, 0.005, 0.0025):
                improved = True
                while improved:
                    improved = False
                    for i in range(4):
                        for sgn in (-1, 1):
                            t = list(T); t[i] += sgn * step
                            if not (0.75 <= t[0] <= 1.33 and 0.75 <= t[1] <= 1.33 and abs(t[2]) <= 0.15 and abs(t[3]) <= 0.15):
                                continue
                            q = q_cand((fx * t[0], fy * t[1], t[2], t[3]), cand)
                            if q > qbest + 1e-4:
                                qbest, T, improved = q, t, True
            qf, C = q_full((fx * T[0], fy * T[1], T[2], T[3]))   # refresh candidates with the better framing
        return qf, T

    # Every orientation gets the full framing search: footage with a mirror reflection is
    # nearly symmetric top-to-bottom, so the first rough scores can be very close.
    best = None
    for name, (fx, fy) in FLIPS.items():
        q0, C = q_full((fx, fy, 0.0, 0.0))
        qf, T = refine(fx, fy, C)
        log(f"  {name:6s}: match quality {q0:.3f} -> {qf:.3f} after framing "
            f"(scale x {T[0]:.3f}, y {T[1]:.3f}, shift x {T[2] * 100:+.1f}%, y {T[3] * 100:+.1f}%)")
        if best is None or qf > best[0]:
            best = (qf, name, T)
    qf, flip, T = best
    fx, fy = FLIPS[flip]
    log(f"  using {flip} (the game's video is {'not flipped' if flip == 'none' else flip} compared with the laserdisc)")
    return {"flip": flip, "T": [fx * T[0], fy * T[1], T[2], T[3]], "quality": round(qf, 4)}

def refine_on_pairs(ld_thumbs, dvd_thumbs, pairs, top, margin, cal):
    """Second calibration pass on frame pairs that are already pinned down in time
    (scenes with strong motion), so zooms or pans can't be mistaken for a framing difference."""
    di = np.array([p[0] for p in pairs]); li = np.array([p[1] for p in pairs])
    lf, _ = norm_rows(sample(ld_thumbs[li], make_grid(top, margin)))
    sub = dvd_thumbs[di]
    fx, fy = FLIPS[cal["flip"]]
    T = [abs(cal["T"][0]), abs(cal["T"][1]), cal["T"][2], cal["T"][3]]
    def q(t):
        f, _ = norm_rows(sample(sub, make_grid(top, margin, (fx * t[0], fy * t[1], t[2], t[3]))))
        return float((f * lf).sum(1).mean() / f.shape[1])
    # Search on the four EDGES of the picture rather than on scale and shift: a scale error and a
    # shift error can hide each other, edges cannot.
    def to_T(e):
        l, r_, t_, b = e
        return [r_ - l, b - t_, (l + r_) / 2 - 0.5, (t_ + b) / 2 - 0.5]
    E = [0.5 + T[2] - T[0] / 2, 0.5 + T[2] + T[0] / 2, 0.5 + T[3] - T[1] / 2, 0.5 + T[3] + T[1] / 2]
    qb = q(to_T(E))
    for step in (0.02, 0.01, 0.005, 0.0025, 0.00125, 0.0006):
        improved = True
        while improved:
            improved = False
            for i in range(4):
                for sgn in (-1, 1):
                    e = list(E); e[i] += sgn * step
                    t = to_T(e)
                    if not (0.6 <= t[0] <= 1.6 and 0.6 <= t[1] <= 1.6 and abs(t[2]) <= 0.25 and abs(t[3]) <= 0.25):
                        continue
                    v = q(t)
                    if v > qb + 1e-6:
                        qb, E, improved = v, e, True
    T = to_T(E)
    log(f"  refined framing on {len(pairs)} pinned frames: scale x {T[0]:.3f}, y {T[1]:.3f}, "
        f"shift x {T[2] * 100:+.1f}%, y {T[3] * 100:+.1f}%")
    return {"flip": cal["flip"], "T": [fx * T[0], fy * T[1], T[2], T[3]], "quality": cal["quality"],
            "refined_on": len(pairs)}

def refine_hires(tools, rows, ld_path, dvd_paths, file_starts, cal, vbi_rows, top, margin, degrid, npairs=30):
    """Final, precise framing: compare real frames (not thumbnails) of matched moments and nudge the
    four edges of the picture until they sit exactly on top of each other."""
    good = [r for r in rows if r["status"] == "ok" and (r["motion"] or 0) >= 0.5 and r["frames"] >= 20]
    picks = [(r, int(r["frames"] * f)) for r in good for f in (0.25, 0.5, 0.75)]
    if len(picks) < 6:
        return cal
    picks = [picks[i] for i in np.unique(np.linspace(0, len(picks) - 1, npairs).round().astype(int))]
    lw, lh, gw, gh = 360, 262, 360, 240
    want_ld, want_g = [], {}
    for r, k in picks:
        want_ld.append(r["ld_start"] + k)
        want_g.setdefault(r["file"], []).append(r["script_start"] - file_starts[r["file"]] + k)
    pre = f"drawbox=x=0:y=0:w=iw:h={vbi_rows}:color=black:t=fill," if vbi_rows else ""
    op = "erosion," * degrid + "dilation," * degrid
    L = grab(tools, ld_path, want_ld, lw, lh, pre, label="framing check, laserdisc ")
    G = {name: grab(tools, dvd_paths[name], idx, gw, gh, op, label="framing check, game video ") for name, idx in want_g.items()}
    pairs = []
    for r, k in picks:
        a = L.get(r["ld_start"] + k); b = G[r["file"]].get(r["script_start"] - file_starts[r["file"]] + k)
        if a is not None and b is not None:
            pairs.append((a.astype(np.float32).mean(2), b.astype(np.float32).mean(2)))
    if len(pairs) < 6:
        return cal
    def bil(img, X, Y):
        h, w = img.shape
        X = np.clip(X, 0, w - 1.001); Y = np.clip(Y, 0, h - 1.001)
        x0 = np.floor(X).astype(int); y0 = np.floor(Y).astype(int); fx = X - x0; fy = Y - y0
        return (img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) + (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy
    def nrm(a):
        a = a - a.mean(); n = np.linalg.norm(a)
        return a / n if n > 1e-3 else a * 0
    def shrink(img, f):                                  # box-average by f (coarse levels see big misplacements)
        h, w = img.shape
        return img[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f).mean((1, 3))
    fx_, fy_ = FLIPS[cal["flip"]]
    def to_T(e):
        return [e[1] - e[0], e[3] - e[2], (e[0] + e[1]) / 2 - 0.5, (e[2] + e[3]) / 2 - 0.5]
    T = [abs(cal["T"][0]), abs(cal["T"][1]), cal["T"][2], cal["T"][3]]
    E = [0.5 + T[2] - T[0] / 2, 0.5 + T[2] + T[0] / 2, 0.5 + T[3] - T[1] / 2, 0.5 + T[3] + T[1] / 2]
    moves = [(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1),      # one edge
             (1, 1, 0, 0), (0, 0, 1, 1), (1, -1, 0, 0), (0, 0, 1, -1)]    # slide / stretch a pair of edges
    q0 = None
    for f, steps in ((8, (0.03, 0.015, 0.008)), (4, (0.008, 0.004)), (2, (0.004, 0.002)), (1, (0.002, 0.001, 0.0005, 0.00025))):
        Ls = [shrink(a, f) for a, _ in pairs]; Gs = [shrink(b, f) for _, b in pairs]
        nx, ny = max(24, 300 // f), max(18, 220 // f)
        U, V = np.meshgrid(np.linspace(margin, 1 - margin, nx), np.linspace(max(top, margin), 1 - margin, ny))
        Lf = [nrm(bil(a, U * a.shape[1] - 0.5, V * a.shape[0] - 0.5)) for a in Ls]
        def q(t):
            Xn = (U - 0.5) * fx_ * t[0] + 0.5 + t[2]; Yn = (V - 0.5) * fy_ * t[1] + 0.5 + t[3]
            return float(np.mean([(nrm(bil(b, Xn * b.shape[1] - 0.5, Yn * b.shape[0] - 0.5)) * lf).sum()
                                  for b, lf in zip(Gs, Lf)]))
        qb = q(to_T(E))
        if q0 is None:
            q0 = qb
        for step in steps:
            improved = True
            while improved:
                improved = False
                for mv in moves:
                    for sgn in (-1, 1):
                        e = [E[i] + sgn * step * mv[i] for i in range(4)]
                        t = to_T(e)
                        if not (0.6 <= t[0] <= 1.6 and 0.6 <= t[1] <= 1.6 and abs(t[2]) <= 0.25 and abs(t[3]) <= 0.25):
                            continue
                        v = q(t)
                        if v > qb + 1e-6:
                            qb, E, improved = v, e, True
    T = to_T(E)
    log(f"  precise framing from {len(pairs)} frame pairs: scale x {T[0]:.4f}, y {T[1]:.4f}, "
        f"shift x {T[2] * 100:+.2f}%, y {T[3] * 100:+.2f}%  (fit {q0:.3f} -> {qb:.3f})")
    out = dict(cal); out["T"] = [fx_ * T[0], fy_ * T[1], T[2], T[3]]; out["precise"] = True
    return out

# ============================================================== matching
def segment_scenes(f, blank, cut=0.45):
    n = len(f)
    starts = [0]
    for i in range(1, n):
        if blank[i] != blank[i - 1]:
            starts.append(i); continue
        if not blank[i] and float(f[i] @ f[i - 1]) / f.shape[1] < cut:
            starts.append(i)
    starts.append(n)
    return [(starts[k], starts[k + 1]) for k in range(len(starts) - 1) if starts[k + 1] > starts[k]]

def pic_score(dn, ln, d0, l0, n):
    if l0 < 0 or l0 + n > len(ln):
        return -1.0
    return float((dn[d0:d0 + n] * ln[l0:l0 + n]).sum(1).mean() / dn.shape[1])

def motion_score(dd, ldd, d0, l0, n):
    if n < 2 or l0 < 0 or l0 + n > len(ldd):
        return 0.0
    a = dd[d0:d0 + n - 1]; b = ldd[l0:l0 + n - 1]
    live = (np.abs(a).sum(1) > 0) & (np.abs(b).sum(1) > 0)
    if not live.any():
        return 0.0
    return float((a[live] * b[live]).sum(1).mean() / a.shape[1])

def total_score(dn, ln, dd, ldd, d0, l0, n):
    p = pic_score(dn, ln, d0, l0, n)
    return p + motion_score(dd, ldd, d0, l0, n) if p > -1 else -9.0

def match_all(dn, dblank, dd, ln, lblank, ldd, files_frames, args, dark):
    lnT = np.ascontiguousarray(ln.T); lddT = np.ascontiguousarray(ldd.T); D = ln.shape[1]
    rows, sid = [], 0
    def match_piece(row, ga, n, depth=0):
        """Match one stretch of the game video. A long stretch whose start and end disagree
        (DRIFT) is usually several laserdisc clips edited together: split it and match each part."""
        nb = np.where(~dblank[ga:ga + n])[0]
        if len(nb) == 0:
            row["status"] = "blank"; return [row]
        samp = np.unique(nb[np.linspace(0, len(nb) - 1, min(len(nb), args.samples)).round().astype(int)])
        C = (dn[ga + samp] @ lnT) / D
        C[:, lblank] = -1
        k = min(args.candidates, C.shape[1] - 1)
        votes = {}
        for r_, kk in enumerate(samp):
            for c in np.argpartition(-C[r_], k)[:k]:
                if C[r_, c] >= args.min_score - 0.15:
                    votes[int(c) - int(kk)] = votes.get(int(c) - int(kk), 0) + 1
        via = ""
        if not votes:                                  # second chance: match on movement alone
            ms = samp[dd[ga + samp].any(1)]
            if len(ms):
                Cm = (dd[ga + ms] @ lddT) / D
                for r_, kk in enumerate(ms):
                    for c in np.argpartition(-Cm[r_], k)[:k]:
                        if Cm[r_, c] >= 0.5:
                            votes[int(c) - int(kk)] = votes.get(int(c) - int(kk), 0) + 1
                via = " (by motion)"
        if not votes:
            row["status"] = "NO MATCH"; return [row]
        tried = {}
        for off, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:8]:
            for o in range(off - 4, off + 5):
                if o not in tried:
                    tried[o] = total_score(dn, ln, dd, ldd, ga, o, n)
        o = max(tried, key=tried.get)
        sc = pic_score(dn, ln, ga, o, n)
        row.update(ld_start=o, offset=o - row["script_start"], score=round(sc, 3),
                   motion=round(motion_score(dd, ldd, ga, o, n), 3))
        if n >= (90 if depth == 0 else 30):           # long stretch: do its start and end agree?
            q = n // 3
            s1 = max(range(o - 3, o + 4), key=lambda x: total_score(dn, ln, dd, ldd, ga, x, q))
            s2 = max(range(o - 3, o + 4), key=lambda x: total_score(dn, ln, dd, ldd, ga + n - q, x + n - q, q))
            if s1 != s2 or sc < args.min_score:
                if depth < 6:                         # split in two and match each half on its own
                    h = n // 2
                    out = []
                    for (pa, pn) in ((0, h), (h, n - h)):
                        sub = dict(row, script_start=row["script_start"] + pa,
                                   script_end=row["script_start"] + pa + pn - 1, frames=pn,
                                   ld_start=None, offset=None, score=None, motion=None, status="",
                                   _g=ga + pa, scene=f"{row['scene']}{'a' if pa == 0 else 'b'}")
                        out += match_piece(sub, ga + pa, pn, depth + 1)
                    return out
                if s1 != s2:
                    row["status"] = f"DRIFT {s1 - o:+d}/{s2 - o:+d}"
        ms_ = row["motion"]
        if row["status"].startswith("DRIFT"):
            pass
        elif sc >= args.min_score or (via and ms_ >= 0.6):
            row["status"] = "ok" + via
        elif depth == 0 and ((sc >= args.min_score - 0.2 and (ms_ >= 0.1 or n < 3)) or (via and ms_ >= 0.45)):
            row["status"] = "weak" + via
        elif depth > 0 and sc >= args.min_score - 0.07 and ms_ >= 0.1:
            # a part of a split scene must earn its place: a near-still part with a middling
            # score is usually the port's own footage (title, attract mode) lined up by chance
            row["status"] = "weak" + via
        else:
            row["status"] = "NO MATCH"
        if row["status"] == "NO MATCH" or row["status"].startswith("DRIFT"):
            row["ld_start"] = row["offset"] = None    # keep the game's own video here
        return [row]

    for (sstart, cnt, g0, name) in files_frames:
        for (a, b) in segment_scenes(dn[g0:g0 + cnt], dblank[g0:g0 + cnt]):
            n = b - a; ga = g0 + a
            row = {"scene": sid, "file": name, "script_start": sstart + a, "script_end": sstart + b - 1,
                   "frames": n, "ld_start": None, "offset": None, "score": None, "motion": None, "status": "",
                   "_g": ga}
            sid += 1
            rows += match_piece(row, ga, n)
    # Black or nearly black scenes (fades, gaps; may still show drawn-in props) follow the
    # next/previous scene if the laserdisc is dark there too.
    for i, r in enumerate(rows):
        if r["status"] not in ("blank", "NO MATCH"):
            continue
        g = r["_g"]
        if r["status"] == "NO MATCH" and float(np.median(dark[g:g + r["frames"]])) > 40:
            continue
        for j in (i + 1, i - 1):
            if 0 <= j < len(rows) and rows[j]["file"] == r["file"] and rows[j]["status"].startswith("ok"):
                ls = r["script_start"] + rows[j]["offset"]
                if ls >= 0 and ls + r["frames"] <= len(lblank) and lblank[ls:ls + r["frames"]].mean() > 0.8:
                    r.update(offset=rows[j]["offset"], ld_start=ls, status="dark (follows neighbour)")
                    break
    # Bridge gaps: unmatched stretches that sit between two laserdisc stretches with the same
    # offset are the same clip carrying on (the port drew something over it, or it was too dim
    # to score). Without this the picture flips between laserdisc and game video and back.
    i = 0
    while i < len(rows):
        if rows[i]["ld_start"] is not None:
            i += 1; continue
        j = i
        while j + 1 < len(rows) and rows[j + 1]["ld_start"] is None and rows[j + 1]["file"] == rows[i]["file"]:
            j += 1
        P = rows[i - 1] if i > 0 else None
        N = rows[j + 1] if j + 1 < len(rows) else None
        span = rows[j]["script_end"] - rows[i]["script_start"] + 1
        if P and N and P["ld_start"] is not None and N["ld_start"] is not None \
                and P["file"] == rows[i]["file"] == N["file"] and abs(P["offset"] - N["offset"]) <= 2 \
                and span <= args.bridge:
            for r in rows[i:j + 1]:
                ls = r["script_start"] + P["offset"]
                if 0 <= ls and ls + r["frames"] <= len(lblank):
                    r.update(offset=P["offset"], ld_start=ls, status="bridged (same clip both sides)")
        i = j + 1
    return rows

def merge_segments(rows, ld_len):
    segs = []
    for r in rows:
        if r["ld_start"] is None or r["ld_start"] + r["frames"] > ld_len:
            segs.append({"script_start": r["script_start"], "frames": r["frames"], "source": "dvd",
                         "file": r["file"], "scenes": [r["scene"]], "blank": r["status"] == "blank"})
            continue
        last = segs[-1] if segs else None
        if last and last["source"] == "ld" and last["script_start"] + last["frames"] == r["script_start"] \
                and last["ld_start"] + last["frames"] == r["ld_start"]:
            last["frames"] += r["frames"]; last["scenes"].append(r["scene"])
        else:
            segs.append({"script_start": r["script_start"], "frames": r["frames"], "source": "ld",
                         "ld_start": r["ld_start"], "scenes": [r["scene"]], "file": r["file"]})
    return segs

# ============================================================== VBI
def detect_vbi_rows(tools, path, info, sample_n=200):
    w, h = info["w"], info["h"]
    rows = [g.mean(1) for g in tools.frames(path, w, h, "select='not(mod(n\\,30))',format=gray", count=sample_n)]
    if not rows:
        return 0
    m = np.median(np.stack(rows), 0)
    base = float(np.median(m[int(h * 0.10):int(h * 0.15)]))
    bright = np.where(m[:int(h * 0.08)] > base + 40)[0]
    return int(bright.max() + 3) if len(bright) else 0

def read_vbi(path, nframes):
    """Optional. chdman dumpmeta -i x.chd -o vbi.bin -tag AVLD : 16 bytes per field
    (white, line16, line17, line18, line1718 as 24-bit big-endian). Returns picture number per frame."""
    data = open(path, "rb").read()
    n = len(data) // 16
    per_field = n >= 2 * nframes - 2
    pics = np.full(nframes, -1, np.int64)
    for k in range(n):
        fr = k // 2 if per_field else k
        if fr >= nframes or pics[fr] >= 0:
            continue
        rec = data[16 * k:16 * k + 16]
        for o in (4, 7, 10):
            c = int.from_bytes(rec[o:o + 3], "big")
            if c >> 20 != 0xF:
                continue
            d = [(c >> s) & 0xF for s in (16, 12, 8, 4, 0)]
            if d[0] <= 7 and all(x <= 9 for x in d):
                pics[fr] = int("".join(map(str, d)))
                break
    return pics

# ============================================================== check sheets
def grab(tools, path, idx, w, h, vf_pre="", workdir=".", label=""):
    """Decode the video once at thumbnail size and keep only the listed frame numbers.
    (A select='eq(n,..)+..' expression with thousands of frames is too big for ffmpeg to parse.)"""
    want = sorted(set(int(i) for i in idx if i is not None and i >= 0))
    out = {}
    if not want:
        return out
    wanted, last = set(want), want[-1]
    step = max(1, last // 10)
    for n, g in enumerate(tools.frames(path, w, h, f"{vf_pre}scale={w}:{h}:flags=fast_bilinear", pix="rgb24")):
        if n in wanted:
            out[n] = g.copy()
        if n and n % step == 0:
            log(f"    {label}{min(100, 100 * n // last)}%")
        if n >= last:
            break
    return out

def check_sheets(tools, rows, dvd_paths, ld_path, vbi_rows, flip, outdir):
    tw, th = 160, 120
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        if f.startswith("check_"):
            os.remove(os.path.join(outdir, f))
    picks, want_ld, want_dvd = [], [], {}
    for r in rows:
        if r["ld_start"] is None:
            continue
        ks = sorted({0, r["frames"] // 2, r["frames"] - 1})
        picks.append((r, ks))
        for k in ks:
            want_ld.append(r["ld_start"] + k)
            want_dvd.setdefault(r["file"], []).append(r["script_start"] - r["file_start"] + k)
    log(f"  grabbing {len(want_ld)} frame pairs for the check sheets")
    pre = f"drawbox=x=0:y=0:w=iw:h={vbi_rows}:color=black:t=fill," if vbi_rows else ""
    ld_imgs = grab(tools, ld_path, want_ld, tw, th, pre, outdir, "laserdisc ")
    dvd_imgs = {}
    for name, idx in want_dvd.items():
        for i, g in grab(tools, dvd_paths[name], idx, tw, th, FLIP_VF[flip], outdir, "game video ").items():
            dvd_imgs[(name, i)] = g
    pages = 0
    for p in range(0, len(picks), 12):
        chunk = picks[p:p + 12]
        sheet = Image.new("RGB", (6 * tw + 40 + 230, len(chunk) * (th + 14) + 34), (28, 28, 28))
        dr = ImageDraw.Draw(sheet)
        dr.text((8, 8), "Each pair: left = game's own video, right = laserdisc. Same pose in every pair = aligned.",
                fill=(235, 235, 235))
        y = 34
        for r, ks in chunk:
            col = (120, 220, 120) if r["status"].startswith(("ok", "dark")) else \
                (120, 190, 245) if r["status"].startswith("bridged") else (245, 200, 80)
            dr.text((8, y + 4), f"scene {r['scene']}", fill=col)
            dr.text((8, y + 20), f"frames {r['script_start']}-{r['script_end']}", fill=col)
            dr.text((8, y + 36), f"laserdisc {r['ld_start']}", fill=col)
            dr.text((8, y + 52), f"score {r['score']}  motion {r['motion']}", fill=col)
            dr.text((8, y + 68), r["status"], fill=col)
            x = 230
            for k in ks:
                for im in (dvd_imgs.get((r["file"], r["script_start"] - r["file_start"] + k)),
                           ld_imgs.get(r["ld_start"] + k)):
                    if im is not None:
                        sheet.paste(Image.fromarray(im), (x, y))
                    x += tw + 2
                x += 10
            y += th + 14
        pages += 1
        sheet.save(os.path.join(outdir, f"check_{pages:03d}.png"))
    return pages

# ============================================================== build
class RawReader:
    """Decoded frames from ffmpeg as yuv420p bytes, read on demand."""
    def __init__(self, tools, args, w, h):
        self.size = w * h * 3 // 2
        self.p = subprocess.Popen([tools.ffmpeg, "-v", "error", "-nostdin"] + args +
                                  ["-an", "-sn", "-fps_mode", "passthrough", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.size * 4)
        self.n = 0                                       # index of the next frame this reader will return
    def read(self):
        b = self.p.stdout.read(self.size)
        if len(b) < self.size:
            return None
        self.n += 1
        return b
    def close(self):
        try:
            self.p.kill(); self.p.stdout.close(); self.p.wait()
        except Exception:
            pass

def place_vf(W, H, src_w, src_h, rx, ry, ox, oy, flip_vf="", pre="", interl=True):
    """ffmpeg filters that put a source picture into a W x H black frame: scaled by (rx, ry) of the
    frame size and centred at (0.5 + ox, 0.5 + oy). Scaling is done field by field (interlaced video)."""
    w = max(2, int(round(W * abs(rx) / 2)) * 2)
    h = max(2, int(round(H * abs(ry) / 2)) * 2)
    x0 = int(round((0.5 + ox) * W - w / 2))
    y0 = int(round(((0.5 + oy) * H - h / 2) / 2)) * 2      # even: keeps the two fields where they were
    M = 480
    vf = pre
    if (w, h) != (src_w, src_h):
        vf += f"scale={w}:{h}:flags=lanczos" + (":interl=1," if interl else ",")
    vf += flip_vf
    if (w, h, x0, y0) != (W, H, 0, 0):
        vf += f"pad={W + 2 * M}:{H + 2 * M}:{M + x0}:{M + y0}:black,crop={W}:{H}:{M}:{M},"
    return vf + "setsar=1"

def build_stream(tools, segs, ld_path, ld_info, ld_times, ld_th, dvd_paths, file_starts, vbi_rows,
                 out_game, size, cal, stem, first_frame, total_frames, clean=0, framing="game", degrid=2):
    """Write the whole new video as ONE .m2v and ONE .ogg, frame by frame.
    Laserdisc stretches: seek near the start, then line the decoded frames up with the scanned
    thumbnails so the first frame is exactly right; take exactly the number of frames needed.
    Game-video stretches: read the game video straight through, so frame numbers are exact."""
    vid = os.path.join(out_game, "ld")
    os.makedirs(vid, exist_ok=True)
    W, H = size
    fps = ld_info["fps"]; fr = float(1 / fps)
    t0 = tools.start_time(ld_path)
    exact = len(ld_times) > 0
    def when(i):
        if exact and 0 <= i < len(ld_times):
            return float(ld_times[i])
        if exact and i >= len(ld_times):
            return float(ld_times[-1]) + (i - len(ld_times) + 1) * fr
        return t0 + i * fr

    # ---- where the pictures go.  A point (U, V) of the laserdisc frame (0..1) lands in the output at
    #      ((U - .5) * sx + .5 + dx, (V - .5) * sy + .5 + dy); negative sx / sy = mirrored / upside-down.
    gsx, gsy, gdx, gdy = cal["T"]                       # where the game's own video has the laserdisc picture
    if framing == "disc":                               # full-size picture: the 480 lines below the data lines
        Hl = ld_info["h"]
        c0 = Hl - 480 if Hl > 480 else 0
        sx = 1.0 if gsx > 0 else -1.0
        ay = Hl / (Hl - c0)
        sy = ay if gsy > 0 else -ay
        dx = 0.0
        dy = -(0.5 * ay - 0.5) if gsy > 0 else (0.5 * ay - 0.5)
    else:
        sx, sy, dx, dy = gsx, gsy, gdx, gdy
    box = f"drawbox=x=0:y=0:w=iw:h={vbi_rows}:color=black:t=fill," if vbi_rows else ""
    # Flips are done here in Python on the finished frame (some ffmpeg builds crash on flip + pad),
    # so ffmpeg places the picture where it must be BEFORE the whole frame is flipped.
    ld_vf = place_vf(W, H, ld_info["w"], ld_info["h"], sx, sy, -dx if sx < 0 else dx, -dy if sy < 0 else dy, "", box)
    def flipped(b):
        if sx > 0 and sy > 0:
            return b
        a = np.frombuffer(b, np.uint8)
        out = []
        for pl, (ph, pw) in ((a[:W * H], (H, W)), (a[W * H:W * H * 5 // 4], (H // 2, W // 2)), (a[W * H * 5 // 4:], (H // 2, W // 2))):
            pl = pl.reshape(ph, pw)
            if sy < 0:
                pl = pl[::-1]
            if sx < 0:
                pl = pl[:, ::-1]
            out.append(np.ascontiguousarray(pl).tobytes())
        return b"".join(out)
    # the game's own video, moved to the same place (no change in "game" framing)
    krx, kry = sx / gsx, sy / gsy
    kept_pos = (krx, kry, dx - gdx * krx, dy - gdy * kry)
    log(f"  picture placement ({framing} framing): laserdisc x{abs(sx):.3f} wide, x{abs(sy) * (480 / ld_info['h'] if ld_info['h'] > 480 else 1):.3f} tall"
        f" of the screen, centre moved {dx * 100:+.1f}% across, {dy * 100:+.1f}% down")

    rate = 44100
    venc = [tools.ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}",
            "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-",
            "-c:v", "mpeg2video", "-pix_fmt", "yuv420p", "-b:v", "8M", "-maxrate", "9.8M", "-bufsize", "3M",
            "-g", "15", "-bf", "2", "-aspect", "4:3", "-flags", "+ildct+ilme", "-f", "mpeg2video",
            os.path.join(vid, stem + ".m2v")]
    aenc = [tools.ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "2", "-i", "-",
            "-c:a", "libvorbis", "-q:a", "6", os.path.join(vid, stem + ".ogg")]
    black = bytes([16]) * (W * H) + bytes([128]) * (W * H // 2)
    def samples_upto(fcount):
        return round(Fraction(fcount) * rate / fps)

    # ---- lining frames up: decode a few thumbnails exactly the way the scan did, from the same seek
    #      point, and find which scanned laserdisc frame each one is. The real decode then starts there.
    scan_vf = f"{box}{degrid_vf(degrid)}scale={TW}:{TH}:flags=area,format=gray"
    def probe_thumbs(t, count):
        r = subprocess.run([tools.ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t:.6f}", "-i", ld_path, "-an", "-sn",
                            "-vf", scan_vf, "-fps_mode", "passthrough", "-frames:v", str(count),
                            "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        k = len(r) // (TW * TH)
        return np.frombuffer(r[:k * TW * TH], np.uint8).reshape(k, TH, TW).astype(np.int16)
    lref = ld_th.astype(np.int16)

    # ---- the game's own video and sound, read straight through
    game_rd, game_au = {}, {}
    def game_frame_reader(name):
        r = game_rd.get(name)
        if r is None:
            op = "erosion," * clean + "dilation," * clean   # optional: erase thin grid lines
            gi = tools.probe(dvd_paths[name])
            r = game_rd[name] = RawReader(tools, ["-i", dvd_paths[name], "-vf",
                                                  place_vf(W, H, gi["w"], gi["h"], *kept_pos, "", op)], W, H)
        return r
    def game_ogg(name):
        ogg = os.path.splitext(dvd_paths[name])[0] + ".ogg"
        return ogg if os.path.exists(ogg) else None
    def game_audio(name, a, n):                        # sound for game frames a..a+n of file `name`
        ogg = game_ogg(name)
        if not ogg:
            return b""
        st = game_au.get(name)
        if st is None:
            st = game_au[name] = {"p": subprocess.Popen([tools.ffmpeg, "-v", "error", "-nostdin", "-i", ogg, "-f", "s16le",
                                                         "-ar", str(rate), "-ac", "2", "-"], stdout=subprocess.PIPE), "pos": 0}
        s0 = round(Fraction(a) * rate / fps) * 4; s1 = round(Fraction(a + n) * rate / fps) * 4
        if s0 < st["pos"]:
            return b""
        if s0 > st["pos"]:
            st["p"].stdout.read(s0 - st["pos"]); st["pos"] = s0
        b = st["p"].stdout.read(s1 - s0); st["pos"] += len(b)
        return b
    def pcm_at(path, t, dur):
        return subprocess.run([tools.ffmpeg, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.6f}", "-i", path, "-vn",
                               "-t", f"{dur:.6f}", "-f", "s16le", "-ar", str(rate), "-ac", "2", "-"],
                              capture_output=True).stdout

    # ---- match the laserdisc's loudness to the game's sound, so the level doesn't jump between them
    gain = 1.0
    if ld_info["has_audio"]:
        ratios = []
        cands = [s_ for s_ in segs if s_["source"] == "ld" and s_["frames"] >= 90 and s_.get("file")]
        for s_ in cands[::max(1, len(cands) // 40)][:40]:
            ogg = game_ogg(s_["file"])
            if not ogg:
                continue
            dur = min(4.0, s_["frames"] * fr - 0.5)
            ga = s_["script_start"] - file_starts[s_["file"]] + 8
            a_ = np.frombuffer(pcm_at(ld_path, when(s_["ld_start"] + 8), dur), np.int16).astype(np.float32)
            b_ = np.frombuffer(pcm_at(ogg, ga * fr, dur), np.int16).astype(np.float32)
            if len(a_) > rate and len(b_) > rate:
                ra, rb = float(np.sqrt((a_ ** 2).mean())), float(np.sqrt((b_ ** 2).mean()))
                if ra > 60 and rb > 60:
                    ratios.append(rb / ra)
        if len(ratios) >= 3:
            gain = float(np.clip(np.median(ratios), 0.2, 5.0))
            log(f"  sound: laserdisc level changed by {20 * np.log10(gain):+.1f} dB to match the game's own sound "
                f"(measured on {len(ratios)} stretches)")
    def ld_audio(a, n):
        if not ld_info["has_audio"]:
            return b""
        pcm = pcm_at(ld_path, when(a), n * fr + 0.1)
        if abs(gain - 1.0) > 0.02:
            x = np.frombuffer(pcm[:len(pcm) // 4 * 4], np.int16).astype(np.float32) * gain
            pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
        return pcm

    V = subprocess.Popen(venc, stdin=subprocess.PIPE)
    A = subprocess.Popen(aenc, stdin=subprocess.PIPE)
    written = 0                                          # frames written so far (= next output frame - first_frame)
    def put_audio(pcm, nframes):
        need = (samples_upto(written + nframes) - samples_upto(written)) * 4
        A.stdin.write(pcm[:need].ljust(need, b"\0"))

    shifted, t_start = {}, time.time()
    total_out = total_frames - first_frame
    for k, sgm in enumerate(segs):
        gap = sgm["script_start"] - (first_frame + written)
        if gap > 0:                                     # frames the game never uses: black and silence
            for _ in range(gap):
                V.stdin.write(black)
            put_audio(b"", gap); written += gap
        n = sgm["frames"]
        if sgm["source"] == "ld":
            a = sgm["ld_start"]
            pad = 6
            st = max(0, a - pad)
            seek = max(0.0, when(st) - fr / 2)
            m = min(n, 40)
            pt = probe_thumbs(seek, m + 2 * pad)
            exp_o = a - st                              # where frame `a` should be if the seek is exact
            best_o, best_d, exp_d = exp_o, 1e9, 1e9
            for o in range(max(0, len(pt) - 1)):
                k = min(m, len(pt) - o, len(lref) - a)
                if k < max(1, m // 2):
                    continue
                dd_ = float(np.abs(pt[o:o + k] - lref[a:a + k]).mean())
                if o == exp_o:
                    exp_d = dd_
                if dd_ < best_d - 1e-6:
                    best_d, best_o = dd_, o
            if exp_d - best_d < (0.01 if best_d < 0.02 else 0.25):   # no real difference (still picture): trust the seek
                best_o = exp_o
            d = best_o - exp_o
            if d:
                shifted[d] = shifted.get(d, 0) + 1
            rd = RawReader(tools, ["-ss", f"{seek:.6f}", "-i", ld_path, "-vf", ld_vf], W, H)
            for _ in range(best_o):
                rd.read()
            last = None
            for j in range(n):
                b = rd.read()
                b = flipped(b) if b is not None else (last if last is not None else black)
                V.stdin.write(b); last = b
            rd.close()
            put_audio(ld_audio(a, n), n)                # sound: exactly the stretch of the disc these frames came from
        else:
            name = sgm["file"]
            a = sgm["script_start"] - file_starts[name]
            rd = game_frame_reader(name)
            if rd.n > a:                                # (never expected) restart the reader
                rd.close(); game_rd.pop(name); rd = game_frame_reader(name)
            while rd.n < a:
                if rd.read() is None:
                    break
            last = None
            for j in range(n):
                b = rd.read()
                if b is None:
                    b = last if last is not None else black
                if sgm.get("blank"):                    # nothing but the grid/background here: black, like the disc
                    b = black
                V.stdin.write(b); last = b
            put_audio(game_audio(name, a, n), n)
        written += n
        if (k + 1) % 50 == 0 or k + 1 == len(segs):
            el = time.time() - t_start
            log(f"  {first_frame + written}/{total_frames} frames written ({100 * written / max(1, total_out):.0f}%, "
                f"{written / max(el, 1e-3):.0f} per second)")
    rest = total_frames - (first_frame + written)
    for _ in range(max(0, rest)):
        V.stdin.write(black)
    if rest > 0:
        put_audio(b"", rest); written += rest
    for r in game_rd.values():
        r.close()
    for st in game_au.values():
        try:
            st["p"].kill()
        except Exception:
            pass
    V.stdin.close(); A.stdin.close()
    if V.wait() or A.wait():
        sys.exit("ffmpeg failed while writing the new video")
    if shifted:
        log("  laserdisc pieces whose start was corrected by lining up the pictures: " +
            ", ".join(f"{c} by {d:+d} frame(s)" for d, c in sorted(shifted.items())))
    return written

# ============================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ld", required=True, help="laserdisc .avi made by  chdman extractld")
    ap.add_argument("--game", required=True, help="the working Singe game folder (with its framefile .txt)")
    ap.add_argument("--framefile", help="framefile .txt (found automatically if omitted)")
    ap.add_argument("--out", default="ld2singe_work", help="work folder for caches, report and the new game folder")
    ap.add_argument("--vbi", help="optional: chdman dumpmeta -i x.chd -o vbi.bin -tag AVLD")
    ap.add_argument("--step", choices=["scan", "calibrate", "match", "build"], default="build",
                    help="stop after this step (default: build, i.e. do everything)")
    ap.add_argument("--version", action="version", version=f"ld2singe {__version__}")
    ap.add_argument("--vbi-rows", type=int, default=-1, help="rows at the top of the laserdisc to black out (auto)")
    ap.add_argument("--margin", type=float, default=0.08, help="edge of the picture to ignore when comparing (0-0.2)")
    ap.add_argument("--samples", type=int, default=12, help="frames sampled per scene when searching")
    ap.add_argument("--candidates", type=int, default=25, help="laserdisc candidates kept per sampled frame")
    ap.add_argument("--min-score", type=float, default=0.70, help="picture match needed for a scene to count as 'ok'")
    ap.add_argument("--size", default="dvd", help="output size e.g. 720x480, or 'dvd' = same as the game's video")
    ap.add_argument("--ffmpeg", help="full path to ffmpeg.exe if it is not on the PATH")
    ap.add_argument("--bridge", type=int, default=900,
                    help="longest unmatched gap (frames) to fill from the laserdisc when both sides agree")
    ap.add_argument("--framing", choices=["game", "disc"], default="game",
                    help="game = picture sits exactly where the game's video had it; disc = full-size laserdisc picture")
    ap.add_argument("--clean-kept", type=int, default=0,
                    help="grid-removal strength for stretches that keep the game's own video (0 = off)")
    ap.add_argument("--rescan", action="store_true", help="ignore cached thumbnails")
    ap.add_argument("--degrid", type=int, default=2,
                    help="erase thin lines (e.g. the DVD's grid) before comparing: 0 = off, 1-3 = stronger")
    args = ap.parse_args()

    tools = Tools(args.ffmpeg)
    os.makedirs(args.out, exist_ok=True)
    framefile = args.framefile or find_framefile(args.game)
    _, vabs, entries = read_framefile(framefile)
    files = sorted((s, n, os.path.join(vabs, n)) for s, n in entries if os.path.exists(os.path.join(vabs, n)))
    missing = [n for s, n in entries if not os.path.exists(os.path.join(vabs, n))]
    log(f"framefile {framefile}: {len(files)} video file(s)" + (f", missing: {missing}" if missing else ""))
    dvd_paths = {n: p for _, n, p in files}
    file_starts = {n: s for s, n, _ in files}

    # ---------- scan
    ld_info = tools.probe(args.ld)
    log(f"laserdisc: {ld_info['w']}x{ld_info['h']} at {float(ld_info['fps']):.3f} fps, sound: {ld_info['has_audio']}")
    cache = os.path.join(args.out, "thumbs.npz")
    if os.path.exists(cache) and not args.rescan and \
            json.loads(str(np.load(cache, allow_pickle=True)["meta"])).get("degrid") == args.degrid:
        z = np.load(cache, allow_pickle=True)
        meta = json.loads(str(z["meta"]))
        ld_th = z["ld"]; ld_times = z["ld_times"]; dvd_list = list(z["dvd"]); vbi_rows = meta["vbi_rows"]
        log("using cached thumbnails (add --rescan to redo)")
    else:
        vbi_rows = args.vbi_rows if args.vbi_rows >= 0 else detect_vbi_rows(tools, args.ld, ld_info)
        log(f"laserdisc data lines at the top: {vbi_rows} rows (blacked out)")
        log("scanning the laserdisc (the slow part)")
        ld_th, _ = scan(tools, args.ld, "laserdisc", vbi_rows, args.degrid)
        ld_times = tools.frame_times(args.ld)
        if len(ld_times) != len(ld_th):
            log(f"  note: {len(ld_times)} timestamps for {len(ld_th)} frames; cutting will fall back to frame rate")
        dvd_list, meta = [], {"files": [], "vbi_rows": vbi_rows, "degrid": args.degrid}
        for s, n, p in files:
            log(f"scanning game video {n}")
            th, inf = scan(tools, p, n, 0, args.degrid)
            dvd_list.append(th)
            meta["files"].append({"name": n, "start": s, "frames": len(th), "w": inf["w"], "h": inf["h"]})
        np.savez_compressed(cache, ld=ld_th, ld_times=ld_times, dvd=np.array(dvd_list, dtype=object), meta=json.dumps(meta))
    log(f"laserdisc frames: {len(ld_th)};  game video frames: {sum(len(t) for t in dvd_list)}")
    d = np.diff(ld_times) if len(ld_times) == len(ld_th) else np.array([])
    if len(d) and (np.abs(d - float(1 / ld_info["fps"])) > 0.25 / float(ld_info["fps"])).any():
        log(f"  note: laserdisc timestamps are uneven in {(np.abs(d - float(1 / ld_info['fps'])) > 0.25 / float(ld_info['fps'])).sum()} place(s); cutting uses the real timestamps")

    if args.step == "scan":
        return

    # ---------- calibrate
    top = (vbi_rows + 2) / ld_info["h"]
    dvd_all = np.concatenate(dvd_list)
    log("calibrating framing")
    cal = calibrate(ld_th, dvd_all, top, args.margin)
    json.dump(cal, open(os.path.join(args.out, "calibration.json"), "w"), indent=1)
    if args.step == "calibrate":
        return

    # ---------- match (twice: the first pass pins down frames used to refine the framing)
    lfeat = sample(ld_th, make_grid(top, args.margin))
    ln, lblank = norm_rows(lfeat)
    ldd = norm_diffs(lfeat)
    files_frames, g = [], 0
    for m, t in zip(meta["files"], dvd_list):
        files_frames.append((m["start"], len(t), g, m["name"])); g += len(t)
    ends = np.cumsum([len(t) for t in dvd_list]) - 1
    dark = np.median(dvd_all.reshape(len(dvd_all), -1), axis=1)       # typical brightness per frame

    def match_with(c):
        dfeat = sample(dvd_all, make_grid(top, args.margin, tuple(c["T"])))
        dn, dblank = norm_rows(dfeat)
        dd = norm_diffs(dfeat)
        dd[ends] = 0                                     # no motion across file boundaries
        return match_all(dn, dblank, dd, ln, lblank, ldd, files_frames, args, dark)

    log("matching scenes")
    rows = match_with(cal)
    strong = [r for r in rows if r["status"] == "ok" and (r["motion"] or 0) >= 0.7 and r["frames"] >= 10]
    pairs = []
    for r in strong:
        for k in np.linspace(0, r["frames"] - 1, min(r["frames"], 25)).round().astype(int):
            pairs.append((r["_g"] + int(k), r["ld_start"] + int(k)))
    if len(pairs) >= 20:
        cal = refine_on_pairs(ld_th, dvd_all, pairs, top, args.margin, cal)
        json.dump(cal, open(os.path.join(args.out, "calibration.json"), "w"), indent=1)
        log("matching scenes again with the refined framing")
        rows = match_with(cal)
    for r in rows:
        r["file_start"] = file_starts[r["file"]]
    for _ in range(2):                                   # precise framing from real frames, then match on it
        cal2 = refine_hires(tools, rows, args.ld, dvd_paths, file_starts, cal, vbi_rows, top, args.margin, args.degrid)
        moved = max(abs(abs(a) - abs(b)) for a, b in zip(cal2["T"], cal["T"]))
        cal = cal2
        json.dump(cal, open(os.path.join(args.out, "calibration.json"), "w"), indent=1)
        if moved < 0.004:
            break
        rows = match_with(cal)
        for r in rows:
            r["file_start"] = file_starts[r["file"]]
    for r in rows:
        r["file_start"] = file_starts[r["file"]]
    pics = read_vbi(args.vbi, len(ld_th)) if args.vbi and os.path.exists(args.vbi) else None
    for r in rows:
        r["ld_picture"] = int(pics[r["ld_start"]]) if pics is not None and r["ld_start"] is not None else ""
    rep = os.path.join(args.out, "report.csv")
    with open(rep, "w", newline="") as fh:
        w = csv.DictWriter(fh, ["scene", "file", "script_start", "script_end", "frames", "ld_start", "ld_picture",
                                "offset", "score", "motion", "status"], extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    tot = sum(r["frames"] for r in rows)
    stat = {}
    for r in rows:
        k = (r["status"] or "?").split(" (")[0].split()[0]
        stat[k] = stat.get(k, 0) + r["frames"]
    log("summary: " + ", ".join(f"{k} {v} frames ({100 * v / tot:.1f}%)" for k, v in sorted(stat.items())))
    pages = check_sheets(tools, rows, dvd_paths, args.ld, vbi_rows, cal["flip"], os.path.join(args.out, "check"))
    log(f"report: {rep};  check sheets: {pages} page(s) in {os.path.join(args.out, 'check')}")
    if args.step == "match":
        return

    # ---------- build: one new video file, written frame by frame
    segs = merge_segments(rows, len(ld_th))
    size = (meta["files"][0]["w"], meta["files"][0]["h"]) if args.size == "dvd" else \
        tuple(int(v) for v in args.size.lower().split("x"))
    out_game = os.path.join(args.out, "game")
    if os.path.exists(out_game):
        shutil.rmtree(out_game)
    old_vid = os.path.normcase(os.path.abspath(vabs))
    shutil.copytree(args.game, out_game,          # everything except the old video folder
                    ignore=lambda d, names: [n for n in names
                                             if os.path.normcase(os.path.abspath(os.path.join(d, n))) == old_vid])
    first = min(m["start"] for m in meta["files"])
    total = max(m["start"] + m["frames"] for m in meta["files"])
    stem = os.path.splitext(os.path.basename(framefile))[0]
    log(f"building ld/{stem}.m2v: {sum(s['frames'] for s in segs if s['source'] == 'ld')} frames from the laserdisc, "
        f"{sum(s['frames'] for s in segs if s['source'] != 'ld')} kept from the game's own video")
    n = build_stream(tools, segs, args.ld, ld_info, ld_times, ld_th, dvd_paths, file_starts, vbi_rows,
                     out_game, size, cal, stem, first, total, clean=args.clean_kept, framing=args.framing, degrid=args.degrid)
    got = subprocess.run([tools.ffprobe, "-v", "error", "-count_packets", "-show_entries", "stream=nb_read_packets",
                          "-of", "csv=p=0", os.path.join(out_game, "ld", stem + ".m2v")],
                         capture_output=True, text=True).stdout.strip().strip(",")
    ff_out = os.path.join(out_game, os.path.basename(framefile))
    with open(ff_out, "w", newline="\r\n") as fh:
        fh.write(f"ld/\n{first}\t{stem}.m2v\n")
    json.dump(segs, open(os.path.join(args.out, "segments.json"), "w"), indent=1)
    log(f"check: the new video has {got} frames, the game's video has {total - first}"
        + ("  - OK" if got == str(total - first) else "  <-- DIFFERENT: please report this"))
    log(f"new game folder: {out_game}")

if __name__ == "__main__":
    main()
