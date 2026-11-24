# clip-ai

Turn a long YouTube live stream into vertical Shorts (9:16), on a CPU and without downloading the full video.

## How it works

1. **Captions**: fetches YouTube auto-captions with `yt-dlp`, so it doesn't transcribe anything locally. It tries `hi-orig`, `en-orig`, `en`, then `hi`.
2. **Scoring**: splits the stream into 5-second buckets and scores each one for "hype" using speech rate, `!` marks, bleeps (`[ __ ]`) and a weighted list of Hindi hype words.
3. **Picking**: chooses the top N non-overlapping windows and skips the first and last 2 minutes (intro and outro).
4. **Rendering**: streams only those segments with `ffmpeg` and renders 720x1280 or 1080x1920 video. The gameplay is center-cropped (dropping the facecam and chat), placed over a blurred background, with captions burned in.
5. **Voice removal (optional)**: strips the streamer's voice with Demucs and keeps the game audio.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) on your `PATH`
- [Node.js](https://nodejs.org/), which `yt-dlp` uses as its JS runtime
- Python packages:

```bash
pip install -r requirements.txt
```

`demucs` (and its PyTorch dependency) is only needed for `--no-voice`. To skip it, run `pip install yt-dlp` alone.

Burned-in captions use the `Nirmala UI` font (Windows) so Devanagari text renders. On other platforms, change `FontName` in `render()`.

## Usage

```bash
python clip_ai.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

| Option | Default | Description |
|---|---|---|
| `--clips` | `5` | Number of clips to make |
| `--len` | `45` | Clip length in seconds |
| `--out` | `clips` | Output folder |
| `--height` | `720` | Source height; `720` gives 720x1280 output, higher gives 1080x1920 |
| `--zoom` | `0.55` | Fraction of the source width kept; lower means more zoom (`1.0` = no crop) |
| `--no-captions` | off | Don't burn in captions |
| `--no-voice` | off | Remove the streamer's voice and keep game sound (slow on CPU, about 4-5 min per 45 s clip). Also turns off captions |

Example:

```bash
python clip_ai.py "https://youtu.be/VIDEO_ID" --clips 10 --len 30 --no-voice --out clips_novoice
```

## Output

Each run writes to the output folder:

- `clip_HH-MM-SS.mp4`: one Short per moment, named by its start time in the stream
- `clip_HH-MM-SS.srt`: the caption file for that clip
- `clips.json`: for each clip, its start time, score, a timestamped YouTube link, a transcript preview and render time

Clips are named by timestamp, so running again with a larger `--clips` skips the ones already rendered. Intermediate files (captions, audio stems) go in `work/`.
