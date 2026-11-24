"""
clip-ai: lightweight long-stream -> Shorts pipeline (CPU friendly).

Steps
  1. Fetch YouTube auto-captions (no local transcription needed).
  2. Score every 5s bucket for "hype" (speech rate, exclamations, bleeps).
  3. Pick the top N non-overlapping windows.
  4. Stream ONLY those segments from YouTube with ffmpeg (no full download),
     render 9:16 (blurred background + full frame) with burned-in captions.

Usage
  python clip_ai.py URL [--clips 5] [--len 45] [--out clips] [--height 720]
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
YTDLP = [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--no-warnings"]

HYPE_WORDS = {
    "अरे": 2, "ओ": 2, "ओह": 2, "अबे": 3, "भागो": 4, "भाग": 2, "बचाओ": 4, "मर": 2,
    "मार": 2, "मारो": 3, "मरा": 3, "गया": 1, "क्या": 1, "वाह": 3, "हाहा": 4,
    "यस": 3, "नो": 2, "चलो": 1, "पकड़": 2, "भाई": 0.5, "बाप": 3, "पागल": 3,
}
BLEEP = re.compile(r"\[\s*__\s*\]")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def fetch_captions(url, work):
    out = work / "stream"
    for lang in ("hi-orig", "en-orig", "en", "hi"):
        f = work / f"stream.{lang}.json3"
        if f.exists():
            return f
        subprocess.run(YTDLP + ["--skip-download", "--write-auto-subs", "--write-subs",
                                "--sub-langs", lang, "--sub-format", "json3",
                                "-o", str(out) + ".%(ext)s", url], capture_output=True)
        if f.exists():
            return f
    sys.exit("No captions found for this video.")


def load_words(path):
    """Return list of (start_sec, end_sec, text) caption lines."""
    d = json.load(open(path, encoding="utf-8"))
    lines = []
    for e in d["events"]:
        if "segs" not in e:
            continue
        t = "".join(s.get("utf8", "") for s in e["segs"]).strip()
        if not t:
            continue
        st = e["tStartMs"] / 1000
        lines.append([st, st + e.get("dDurationMs", 2000) / 1000, t])
    # clip overlapping durations so captions don't stack
    for a, b in zip(lines, lines[1:]):
        a[1] = min(a[1], b[0])
    return lines


def score(lines, total, bucket=5):
    n = int(total // bucket) + 1
    s = [0.0] * n
    for st, en, t in lines:
        i = int(st // bucket)
        if i >= n:
            continue
        words = t.split()
        s[i] += len(words) * 0.15                      # speech rate
        s[i] += 3 * len(BLEEP.findall(t))               # bleeps = rage/hype
        s[i] += 1.5 * t.count("!")
        for w in words:
            s[i] += HYPE_WORDS.get(w.strip("।!?,."), 0)
    return s


def pick(s, total, clips, length, bucket=5):
    k = max(1, length // bucket)
    win = [sum(s[i:i + k]) for i in range(len(s) - k)]
    order = sorted(range(len(win)), key=lambda i: -win[i])
    chosen = []
    for i in order:
        st = i * bucket
        if st < 120 or st > total - 120:               # skip intro/outro
            continue
        if all(abs(st - c) > length * 3 for c, _ in chosen):
            chosen.append((st, win[i]))
        if len(chosen) == clips:
            break
    return sorted(chosen)


def srt_time(t):
    t = max(t, 0)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h:02}:{m:02}:{t % 60:06.3f}".replace(".", ",")


def write_srt(lines, st, length, path):
    out, n = [], 0
    for a, b, t in lines:
        if b <= st or a >= st + length:
            continue
        n += 1
        out.append(f"{n}\n{srt_time(a - st)} --> {srt_time(min(b, st + length) - st)}\n{t}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def stream_urls(url, height):
    r = run(YTDLP + ["-g", "-f", f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/b[height<={height}]", url],
            capture_output=True, text=True)
    urls = r.stdout.strip().splitlines()
    return urls if len(urls) == 2 else (urls[0], urls[0])


def render(vurl, aurl, st, length, srt_name, out_name, cwd, height, zoom=0.55):
    """zoom = fraction of the source width kept (centered). 0.55 drops the
    facecam (left ~20%) and chat box (right ~20%) and enlarges the gameplay."""
    W, H = (720, 1280) if height <= 720 else (1080, 1920)
    x0 = (1 - zoom) / 2
    subs = (f",subtitles={srt_name}:force_style='FontName=Nirmala UI,FontSize=14,Bold=1,"
            f"Outline=2,MarginV=60,Alignment=2'") if srt_name else ""
    vf = (f"[0:v]crop=iw*{zoom}:ih:iw*{x0}:0,split[a][b];"
          f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"scale={W // 8}:-2,boxblur=4,scale={W}:{H}[bg];"
          f"[b]scale={W}:-2[fg];"
          f"[bg][fg]overlay=0:(H-h)/2{subs}[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats",
           "-ss", str(st), "-t", str(length), "-i", vurl,
           "-ss", str(st), "-t", str(length), "-i", aurl,
           "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_name]
    run(cmd, cwd=cwd)


def remove_voice(mp4, work):
    """Strip the streamer's voice with Demucs, keep game audio (slow on CPU: ~4-5 min / 45s)."""
    wav = work / "voice_in.wav"
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-vn", "-ac", "2", "-ar", "44100", str(wav)])
    run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs",
         "-o", str(work / "sep"), str(wav)], capture_output=True)
    game = work / "sep" / "htdemucs" / "voice_in" / "no_vocals.wav"
    tmp = mp4.with_suffix(".tmp.mp4")
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-i", str(game),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5",
         "-ar", "48000", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, mp4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--len", type=int, default=45)
    ap.add_argument("--out", default="clips")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--zoom", type=float, default=0.55,
                    help="fraction of source width kept; lower = more zoom (1.0 = no crop)")
    ap.add_argument("--no-voice", action="store_true",
                    help="remove streamer voice, keep game sound (slow on CPU); also disables captions")
    ap.add_argument("--no-captions", action="store_true")
    a = ap.parse_args()
    captions = not (a.no_captions or a.no_voice)

    work, out = ROOT / "work", ROOT / a.out
    work.mkdir(exist_ok=True); out.mkdir(exist_ok=True)
    T = time.time()

    info = json.loads(run(YTDLP + ["--skip-download", "-J", a.url], capture_output=True, text=True).stdout)
    total, title = info["duration"], info["title"]
    print(f"[1/4] {title}  ({total / 3600:.1f} h)")

    cap = fetch_captions(a.url, work)
    lines = load_words(cap)
    print(f"[2/4] captions: {len(lines)} lines  ({time.time() - T:.0f}s)")

    chosen = pick(score(lines, total), total, a.clips, a.len)
    print(f"[3/4] picked {len(chosen)} moments")

    vurl, aurl = stream_urls(a.url, a.height)
    report = []
    for i, (st, sc) in enumerate(chosen, 1):
        t0 = time.time()
        stamp = time.strftime("%H-%M-%S", time.gmtime(st))
        name = f"clip_{stamp}"   # name by timestamp so re-runs with more clips skip existing ones
        preview = " ".join(t for x, _, t in lines if st <= x < st + a.len)[:400]
        entry = {"file": f"{name}.mp4", "start": stamp, "score": round(sc),
                 "link": f"https://youtu.be/{info['id']}?t={int(st)}", "transcript": preview}
        if (out / f"{name}.mp4").exists():
            print(f"  -> {name}  already done, skipping")
            report.append(entry)
            continue
        srt = None
        if captions:
            srt = f"{name}.srt"
            write_srt(lines, st, a.len, out / srt)
        print(f"  -> {name}  score={sc:.0f}")
        render(vurl, aurl, st, a.len, srt, f"{name}.mp4", out, a.height, a.zoom)
        if a.no_voice:
            print("     removing voice (slow)...")
            remove_voice(out / f"{name}.mp4", work)
        entry["render_sec"] = round(time.time() - t0)
        report.append(entry)
        print(f"     done in {time.time() - t0:.0f}s")

    (out / "clips.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[4/4] {len(report)} clips in {out}  — total {time.time() - T:.0f}s")


if __name__ == "__main__":
    main()
