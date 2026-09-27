# Demo footage — manual pull needed

Scripted download of Pixabay/Pexels was attempted and blocked: both sites return
`HTTP 403` (Cloudflare bot-protection) to a plain `curl`/`WebFetch` request, even just
hitting the search page (no API key call was attempted — that would need a paid/free-tier
key and isn't worth doing for 3-5 clips). This matches the "MAYBE, needs an account /
JS-rendered" caveat already flagged in docs/DATASETS.md.

**What the team should do manually** (5 minutes, any laptop with a browser):

1. Open one of:
   - https://pixabay.com/videos/search/labrador%20retriever/
   - https://pixabay.com/videos/search/dog%20walking/
   - https://www.pexels.com/search/videos/labrador/
   - https://www.pexels.com/search/videos/dog%20sitting/
2. Pick 3-5 short (10-30s) clips that show a Labrador (or any dog) doing some mix of:
   walking, sitting, standing, lying down, sniffing, barking (bonus if audible).
3. Click the clip's own download button (no login needed on either site for standard
   resolution) and save the `.mp4` files into `data/demo_videos/`, e.g.:
   ```
   data/demo_videos/clip1.mp4
   data/demo_videos/clip2.mp4
   data/demo_videos/clip3.mp4
   ```
4. No renaming/format conversion needed — `app/io/inputs.py::VideoSource` opens any path
   via OpenCV's `VideoCapture`, so `.mp4` just works. Test one with:
   ```
   .venv/bin/python -m services.detection.run --video data/demo_videos/clip1.mp4 --server http://localhost:8000
   ```

**Fallback if nobody gets to this before the demo:** the whole pipeline also runs directly
off a live webcam — set `adapters.video_source: "0"` in `config.yaml` (already the default)
and just point the laptop's camera at the dog (or at a phone screen playing a dog video, in
a pinch). No code path is different between "webcam" and "file" — `VideoSource` treats both
the same way, so this isn't a blocker for the demo, just a nice-to-have for a more polished
video source than a live camera pointed around a noisy hackathon venue.
