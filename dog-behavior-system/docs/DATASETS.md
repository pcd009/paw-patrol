# Dog Behaviour Recognition — Dataset Research

Compiled 2026-09-26 for the Labrador Retriever behaviour-recognition hackathon prototype (demo: 2026-09-27 afternoon).
Every entry below was checked against the live source page or API where possible. Anything I could not
directly confirm (blocked by login/JS/paywall/time) is explicitly marked **UNVERIFIED**.

---

## PART A — Recommended stack (ranked, per pipeline stage)

| Stage | Recommendation | Why | Usable by tomorrow? |
|---|---|---|---|
| **1. Dog detection/tracking** | **Ultralytics YOLOv8/v11 `yolo*.pt` COCO-pretrained "dog" class**, run as-is (COCO class 16 = dog). No fine-tuning needed for detection — COCO already detects dogs well. | Zero setup, ships with `ultralytics` pip package. | **YES** |
| **2. Posture/behaviour per frame** (sit/stand/lie/sniff) | Small Roboflow Universe sets (`dogs-behavior-pawqf`, `dog-pose-e4xee`) as a *seed* fine-tune set for a lightweight classifier head on top of YOLO crops, **supplemented by Claude Sonnet vision zero-shot on keyframes** as the fallback/primary for anything the tiny fine-tune set doesn't cover well. | No large labelled posture dataset with our exact classes + bbox exists at hackathon scale; Roboflow sets are tiny (~350 img) and behind a login. | Roboflow: MAYBE (needs account); Claude zero-shot: **YES**, no dataset needed |
| **3. Locomotion gait (walk/trot/gallop)** | **No usable video dataset with these 3 exact labels.** Fallback: heuristic on YOLO bbox-centroid speed/step-frequency (optical flow or track velocity), calibrated using the **Vehkaoja IMU dataset** labels (walking/trotting/galloping) as a cross-check/ground truth reference for what those gaits look like kinematically. | Video datasets either don't distinguish trot vs gallop (most only have "walking"/"running") or aren't dog-specific. | Heuristic: **YES** (no dataset needed, just motion math) |
| **4. Sniffing** | **No dedicated visual sniffing dataset.** Use Claude Sonnet vision zero-shot on keyframes (head-down-near-ground + nose motion cue), or treat as a posture sub-state. | Not found anywhere as a labelled visual class at usable scale/access. | Zero-shot only: **YES** |
| **5. Audio bark/whine** | **AudioSet** "Bark", "Whimper (dog)" classes via a pretrained **AST model (`MIT/ast-finetuned-audioset-10-10-0.4593`, Hugging Face)** or **PANNs** — run zero-shot, no fine-tuning needed. Optionally supplement with **ESC-50** (`dog_bark` class, tiny, direct GitHub download) for a quick sanity-check clip. | AudioSet itself is not directly downloadable as audio (only YouTube-ID + timestamp CSVs — most linked videos are stale/removed); using the *pretrained model* is far faster than trying to rebuild the raw audio corpus in a day. | **YES** (pretrained model, zero download of AudioSet itself needed) |
| **6. Howl / growl (optional)** | Same AST/PANNs model — AudioSet ontology already includes "Howl", "Growling", "Bow-wow", "Yip" as sibling classes under Dog, so the same zero-shot pass gives you these for free. | — | **YES** |
| **7. IMU collar (MPU6050)** | **Vehkaoja et al. 2022, "Movement Sensor Dataset for Dog Behavior Classification"** (Mendeley Data, DOI 10.17632/vxhx934tbn.4; CC BY 4.0). Labels: galloping, lying on chest, sitting, sniffing, standing, trotting, walking — **near-perfect 1:1 match** to our video label set. Train a small classifier (RF/1D-CNN) on windowed accel+gyro features, or use it purely as a reference for feature engineering if the real MPU6050 arrives late. | Best-matching dataset found in the entire search — literally has "sniffing" and "lying on chest" as explicit classes, sampled at 100Hz like a typical IMU. | **YES** |
| **8. Labrador filtering/fine-tuning (breed)** | **Stanford Dogs** (120 breeds, 20,580 images, ImageNet-derived; synset `n02099712` = Labrador retriever — high-confidence from ImageNet/Stanford-Dogs literature and mirrored repo folder naming conventions, but I could not re-open the live Stanford page to re-count images per class today — **UNVERIFIED exact Labrador image count**, expect ~100–150 based on the dataset's ~171 avg images/class). Use **StanfordExtra** (MIT license, same source images) if keypoints/pose needed with breed filtering. | Only breed-labelled set found that (a) is free, (b) includes Labrador as an explicit class, (c) has a companion keypoint dataset (StanfordExtra). Oxford-IIIT Pet does **NOT** include Labrador/Golden Retriever (confirmed — its 25 dog breeds list was fully retrieved and neither appears). Tsinghua Dogs likely includes Labrador (130 breeds, very common in China) but breed list not retrievable from the page content fetched — **UNVERIFIED**. | **YES** for Stanford Dogs (plain HTTP download); StanfordExtra keypoints need a Google-Form email round-trip (probably same-day). |
| **9. Demo footage** | Pixabay / Pexels search pages (CC0-style Pixabay License / Pexels License, no attribution required, direct MP4 download from the video page). See concrete search URLs below. | Confirmed via search that both sites have 40–3,700+ Labrador-tagged clips, free, no login. Exact individual clip URLs not enumerated (search API doesn't deep-link into specific video IDs) — grab a handful manually from the search pages below. | **YES** |

### Honest gaps
- **No dataset anywhere maps directly to "walk vs trot vs gallop" from video.** This is the single biggest gap. Fallback = bbox/keypoint velocity heuristic, or Claude Sonnet vision on short clips with a carefully engineered prompt describing gait cadence.
- **No dataset for visual "sniffing."** Fallback = Claude Sonnet zero-shot / heuristic (head lowered + small back-and-forth motion).
- **Posture (sit/stand/lie) video datasets with bbox exist only as tiny (~100s of images), login-gated Roboflow projects.** Fine-tuning a classifier head on ~350 images across 6 classes is possible but risky in scope for one day; treat Claude zero-shot as the reliable primary and any fine-tuned classifier as a bonus if time allows.
- **AudioSet raw audio is not realistically downloadable in a day** (relies on scraping thousands of individual YouTube videos, many now dead). Solved by using the pretrained AST/PANNs/YAMNet model directly instead of the raw dataset — this is actually *better* for a hackathon (zero training).
- **No IMU dataset confirmed to include Labradors specifically** — Vehkaoja et al. paper's breed list not verified (I could not open the full paper text within tool budget). Treat the IMU dataset as breed-agnostic reference data for feature engineering/label semantics, not as breed-matched training data.

---

## PART B — Exact download commands / access notes

### IMU — Vehkaoja et al. 2022 (recommended primary IMU dataset)
```bash
# Mendeley Data page (no login required for direct file download):
# https://data.mendeley.com/datasets/vxhx934tbn/4
# Metadata confirmed via public API:
curl -s "https://data.mendeley.com/public-api/datasets/vxhx934tbn?version=4"
# Kaggle mirror (needs free Kaggle account + API token ~/.kaggle/kaggle.json):
kaggle datasets download -d benjamingray44/inertial-data-for-dog-behaviour-classification
```
License: CC BY 4.0. Main file on Kaggle mirror: `DogMoveData.csv` (raw IMU) — cite Vehkaoja et al., *Data in Brief* 40 (2022) 107822 and Kumpulainen et al., *Applied Animal Behaviour Science* 241 (2021) 105393.

### Dog detection — COCO-pretrained YOLO (no dataset download needed)
```bash
pip install ultralytics
python -c "from ultralytics import YOLO; m=YOLO('yolo11n.pt'); print(m.names[16])"  # 'dog'
```

### Pose (optional/stretch) — Ultralytics Dog-Pose (StanfordExtra repackaged)
```bash
# ~337 MB — do NOT put in _samples/, only fetch on the training machine if time allows
wget https://github.com/ultralytics/assets/releases/download/v0.0.0/dog-pose.zip
# or let Ultralytics auto-download via:
python -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt').train(data='dog-pose.yaml', epochs=20)"
```
License: AGPL-3.0. StanfordExtra annotations (source of this dataset) require a Google-Form request: https://github.com/benjiebob/StanfordExtra (MIT license for the annotation/code repo itself).

### Breed filtering — Stanford Dogs
```bash
wget http://vision.stanford.edu/aditya86/ImageNetDogs/images.tar
wget http://vision.stanford.edu/aditya86/ImageNetDogs/annotation.tar
# Labrador retriever folder (per standard Stanford-Dogs/ImageNet synset naming used across all mirrors,
# e.g. Kaggle "jessicali9530/stanford-dogs-dataset"):
#   Images/n02099712-Labrador_retriever/
```
(Direct re-verification of the breed folder listing on the official page was not possible today — page returned only a stub/frameset over plain HTTP; treat folder name as high-confidence but re-check on the actual extracted tarball before relying on it.)

### Audio — pretrained models (no dataset download needed)
```bash
pip install transformers torch torchaudio
python - <<'PY'
from transformers import pipeline
clf = pipeline("audio-classification", model="MIT/ast-finetuned-audioset-10-10-0.4593")
print(clf("sample.wav"))  # returns AudioSet labels incl. Bark, Whimper (dog), Howl, Growling, Bow-wow, Yip
PY
```
Alt: PANNs (`pip install panns-inference`) or YAMNet (`tensorflow_hub` model `https://tfhub.dev/google/yamnet/1`) — both give the same AudioSet 527-class output including the same dog subclasses.

### Audio — tiny sanity-check clips (optional)
```bash
git clone --depth 1 https://github.com/karolpiczak/ESC-50.git   # ~600MB total repo but dog_bark clips are a small subset; MIT-style license (see repo)
# UrbanSound8K: form-gated download at https://urbansounddataset.weebly.com/urbansound8k.html, or:
pip install soundata && python -c "import soundata; soundata.initialize('urbansound8k').download()"
```

### Audio — Barkopedia (Hugging Face, 2025)
```bash
pip install datasets
python -c "from datasets import load_dataset; ds=load_dataset('ArlingtonCL2/Barkopedia-Dog-Vocal-Detection'); print(ds)"
```
Vocal-detection subset: labels are `dog` (continuous bark) / `dog_noise` (bark + background noise); breed metadata for this subset reportedly spans Chihuahua/German Shepherd/Husky/**Labrador**/Pitbull/Shiba Inu (per dataset card text — **UNVERIFIED** clip-level breakdown by breed). Note the separate breed-classification subset (`PuneettArora/Barkopedia_DOG_BREED_CLASSIFICATION_DATASET`, MIT license, direct load) explicitly has **no Labrador** (only Husky/Shiba Inu/Chihuahua/German Shepherd/Pitbull, 29,347 clips) — these two Barkopedia sub-datasets disagree on whether Labrador is present, flagged for the team to double check on load.

### Demo footage (CC0-style, no login)
- https://pixabay.com/videos/search/labrador%20retriever/ (68+ clips)
- https://pixabay.com/videos/search/labrador/ (45+ clips)
- https://pixabay.com/videos/search/dog%20walking/ (2,413+ clips)
- https://www.pexels.com/search/videos/labrador/ (3,700+ clips)
- https://www.pexels.com/search/videos/dog%20sitting/
Both licenses (Pixabay License, Pexels License) permit free download and reuse without attribution — grab 5–10 clips manually via browser, each page has a direct MP4 download button (no API key needed for manual/browser download; API key only needed for programmatic bulk download).

### Blockers requiring API keys / accounts
- **Roboflow** (posture datasets `dogs-behavior-pawqf`, `dog-pose-e4xee`, `posture-detection`): free account + API key needed; page is Cloudflare-protected against anonymous scraping (confirmed — direct curl returned a JS challenge page, HTTP 403 via WebFetch).
- **Kaggle** (IMU mirror, Stanford Dogs mirror): free account + `kaggle.json` API token needed for CLI download (browser download works without CLI).
- **StanfordExtra** keypoint JSON: Google Form + email link (not instant, budget 10–30 min turnaround).

---

## PART C — Label-mapping table (dataset label → our label)

| Dataset | Dataset label | Our label |
|---|---|---|
| Vehkaoja et al. 2022 (IMU) | galloping | galloping |
| Vehkaoja et al. 2022 (IMU) | trotting | trotting |
| Vehkaoja et al. 2022 (IMU) | walking | walking |
| Vehkaoja et al. 2022 (IMU) | sitting | sitting |
| Vehkaoja et al. 2022 (IMU) | standing | standing |
| Vehkaoja et al. 2022 (IMU) | lying on chest | lying_on_chest |
| Vehkaoja et al. 2022 (IMU) | sniffing | sniffing |
| Roboflow `dogs-behavior-pawqf` | lying | lying_on_chest (approx — not confirmed sternal-only) |
| Roboflow `dogs-behavior-pawqf` | sitting | sitting |
| Roboflow `dogs-behavior-pawqf` | standing | standing |
| Roboflow `dogs-behavior-pawqf` | running | galloping/trotting (ambiguous, needs manual review) |
| Roboflow `dog-pose-e4xee` | stand / lie / sit | standing / lying_on_chest / sitting |
| AudioSet / AST / PANNs / YAMNet | Bark | barking |
| AudioSet / AST / PANNs / YAMNet | Whimper (dog) | whining |
| AudioSet / AST / PANNs / YAMNet | Howl | howling (optional) |
| AudioSet / AST / PANNs / YAMNet | Growling | growling (optional) |
| ESC-50 | dog_bark | barking |
| UrbanSound8K | dog_bark | barking |
| Barkopedia vocal-detection | dog | barking (binary presence only, no whine/howl/growl distinction) |
| Stanford Dogs / StanfordExtra | Labrador_retriever (breed folder) | breed filter for fine-tuning/oversampling |

---

## PART D — Full candidate table (including rejects)

| Name | Year/Paper | URL | Labels relevant to us | Granularity | Size | Format | Breed labels? | License | Access | Realistic size | Usable tomorrow? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Vehkaoja et al. Movement Sensor Dataset | Data in Brief 2022 | data.mendeley.com/datasets/vxhx934tbn/4 | walk/trot/gallop/sit/stand/lie/sniff | per-timestep (100Hz) | not stated on API (CSV, dozens of MB expected) | CSV | Breed list UNVERIFIED | CC BY 4.0 | Direct download | small | **YES** |
| StanfordExtra | ECCV 2020 (Biggs et al.) | github.com/benjiebob/StanfordExtra | pose keypoints, breed | per-image, 20 keypoints | 12k images | JSON + images | YES (breed field) | MIT | Google Form for annotations | ~750MB (images) | Partial (form delay) |
| Ultralytics Dog-Pose | repackaged StanfordExtra | docs.ultralytics.com/datasets/pose/dog-pose | pose keypoints | per-image, 24 keypoints | 8,476 images | YOLO pose txt | Inherited from StanfordExtra | AGPL-3.0 | Direct wget | 337 MB | Stretch only |
| Roboflow `dogs-behavior-pawqf` | community, undated | universe.roboflow.com/custom-yolo-dataset-f1bxb/dogs-behavior-pawqf | sit/stand/lying/running/barking/eating | per-image bbox | ~350 images | YOLO export | No | Roboflow default (varies, often CC BY 4.0) — UNVERIFIED (page blocked) | Needs Roboflow account/API key | tiny | Maybe |
| Roboflow `dog-pose-e4xee` | community | universe.roboflow.com/cudo-mvurt/dog-pose-e4xee | stand/lie/sit | per-image class | small (UNVERIFIED count) | classification export | No | UNVERIFIED | Needs API key | tiny | Maybe |
| Stanford Dogs | Khosla et al. 2011 | vision.stanford.edu/aditya86/ImageNetDogs | breed | per-image class + bbox | 20,580 images | folders + .mat annotations | YES, 120 breeds incl. Labrador (high-confidence, not re-verified live today) | Research use (ImageNet-derived; no explicit commercial license — treat as research-only) | Direct HTTP download | ~750MB | **YES** |
| Tsinghua Dogs | 2020 (Zou et al.) | cg.cs.tsinghua.edu.cn/ThuDogs | breed | per-image class + bbox (whole body + head) | 70,428 images, 130 breeds | images + xml | Likely yes, Labrador presence UNVERIFIED | Not stated on page | Direct (cloud.tsinghua.edu.cn) | 2.5GB (low-res) / 38.8GB (high-res) | Low-res maybe |
| Oxford-IIIT Pet | Parkhi et al. 2012 | robots.ox.ac.uk/~vgg/data/pets | breed (25 dog breeds) | per-image class + head bbox + trimap | ~7,400 images | tar.gz | YES but **no Labrador/Golden Retriever** (confirmed) | CC BY-SA 4.0 | Direct tar.gz | ~800MB | REJECT (no Labrador) |
| Open Images V6/V7 | Google | storage.googleapis.com/openimages | "Dog" superclass only | bbox | huge | CSV + image URLs | No breed granularity found (UNVERIFIED complete negative) | CC BY 4.0 | Direct/ CSV + gsutil | huge, filter needed | REJECT (no breed granularity, huge) |
| AudioSet | Gemmeke et al. 2017 | research.google.com/audioset | Bark/Whimper/Howl/Growling/Bow-wow/Yip | per-clip (10s), weak labels | 5000+ hrs total; Dog class = 13,705 videos/37.9 hrs | YouTube ID + timestamp CSV (no audio bundled) | No | CC BY 4.0 (labels); underlying video licenses vary | Needs re-scraping YouTube (impractical in 1 day) | N/A | REJECT raw data — use pretrained model instead |
| AST (MIT/ast-finetuned-audioset-10-10-0.4593) | Gong et al. 2021 | huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593 | all AudioSet 527 classes incl. dog sounds | per-clip zero-shot inference | model ~350MB | HF `transformers` | N/A (model, not dataset) | HF model card (check for research-only terms) | Direct HF download | ~350MB | **YES** |
| PANNs | Kong et al. 2020 | github.com/qiuqiangkong/panns_inference | same AudioSet classes | per-clip inference | model, tens of MB | pip package | N/A | MIT (code); model weights via Zenodo | Direct | small-medium | **YES** |
| YAMNet | Google | tfhub.dev/google/yamnet/1 | same AudioSet classes (521) | per-frame (0.96s) inference | small model | TF Hub | N/A | Apache 2.0 | Direct | small | **YES** |
| ESC-50 | Piczak 2015 | github.com/karolpiczak/ESC-50 | dog_bark (1 of 50 classes, 40 clips) | per-clip | ~600MB full repo (audio ~830MB total dataset, but git repo includes it) | WAV + CSV | No | Attribution/CC (see repo) | git clone | full repo large; dog_bark subset small | Partial (clone whole repo, take subset) |
| UrbanSound8K | Salamon et al. 2014 | urbansounddataset.weebly.com/urbansound8k.html | dog_bark (1 of 10 classes) | per-clip | 8732 clips, ~6GB (10 folds) | WAV + CSV | No | Non-commercial research use only (custom license, confirmed on page) | Form or `soundata` package | ~6GB total, dog_bark subset small | Partial (use soundata to fetch just what's needed) |
| Barkopedia Dog Vocal Detection | ArlingtonCL2, 2025, IJCAI'25 challenge | huggingface.co/datasets/ArlingtonCL2/Barkopedia-Dog-Vocal-Detection | dog / dog_noise (bark presence) | per-clip, strong+weak labels | 6.5GB, ~547+ rows across splits | HF datasets (audio) | Breeds incl. Labrador reported in card text (UNVERIFIED per-clip breakdown) | Not specified on page — UNVERIFIED | Direct HF `load_dataset`, no gating | 6.5GB (can subsample) | Maybe (large; subsample) |
| Barkopedia Dog Breed Classification | ArlingtonCL2/PuneettArora, 2025 | huggingface.co/datasets/PuneettArora/Barkopedia_DOG_BREED_CLASSIFICATION_DATASET | breed (audio, not visual) | per-clip | 29,347 clips, 5 breeds | HF datasets (audio) | YES — Husky/Shiba Inu/Chihuahua/German Shepherd/Pitbull; **no Labrador** | MIT | Direct HF `load_dataset` | large but subsample | REJECT for Labrador (breed absent) |
| AP-10K | NeurIPS 2022 | github.com/AlexTheBad/AP-10K | generic pose (17 kp), family/species only | per-image | 10,015 images, 54 species | COCO-style JSON | No breed labels | CC BY 4.0 | Google Drive/Baidu | medium | Low priority (no dog behaviour classes, no breed) |
| APT-36K | NeurIPS 2022 | github.com/pandorgan/APT-36K | pose + tracking, species only | per-frame, video clips | 36,000 frames, 30 species | COCO-style | No | UNVERIFIED | UNVERIFIED | UNVERIFIED | Low priority |
| Animal Pose Dataset | Cao et al. ICCV 2019 | kaggle.com/datasets/bloodaxe/animal-pose-dataset | pose, dog category only (5 species total) | per-image, 20 keypoints | 6,000+ instances, 4,000+ images | JSON/COCO-like | No breed labels | UNVERIFIED (Kaggle mirror) | Kaggle (needs account) | small-medium | Maybe (stretch pose only) |
| Animal Kingdom | CVPR 2022 | github.com/sutdcv/Animal-Kingdom | 140 generic ethogram actions, 850 species (dog is a minor fraction) | per-clip multi-label + 33K pose frames | 50 hrs video | video + annotation files | No | Custom (non-commercial research, request form implied) | Request-based (UNVERIFIED exact process) | large | REJECT (too broad/generic, hard to filter to dog+our labels in 1 day) |
| MammalNet | CVPR 2023 | mammal-net.github.io | 12 generic behaviors, 173 mammal species | per-clip | 18K videos, 539 hrs | video + annotation | No | UNVERIFIED | UNVERIFIED | huge | REJECT (not dog-specific, generic behaviors) |
| DogCentric Activity Dataset | Iwashita et al. ICPR 2014 | robotics.ait.kyushu-u.ac.jp (paper) | walking/drinking/interacting etc. | per-clip | 209 videos | video | No | UNVERIFIED | UNVERIFIED | small | REJECT (egocentric camera on dog's back — wrong framing for fixed webcam) |
| DECADE (a.k.a. dogTorch) | Ehsani et al. 2018 | github.com/ehsanik/dogTorch | ego-video + IMU joint data | per-frame (5fps), 24,500 frames | 380 clips | video + IMU (Google Drive) | Not specified | MIT (repo) | Google Drive (may need access request) | medium | REJECT for our framing (head-mounted ego camera + no behaviour class labels, it's joint-position regression) but interesting precedent for combining video+IMU |
| K9-Bench | 2026, arXiv 2607.02680 | arxiv.org/abs/2607.02680 | posture/action-sequence/context/cause-effect/interaction, as QA pairs | per-video multiple-choice QA (907 videos, 4744 Q&A) | UNVERIFIED download availability | QA JSON (assumed) | No | UNVERIFIED | UNVERIFIED | UNVERIFIED | Possible eval-only use for the Claude interpretation step, not for classifier training |
| Animal Kingdom / MammalNet / DogCentric / K9-Bench (summary) | — | — | — | — | — | — | — | — | — | — | All REJECTED as primary training data; noted only as prior art / potential eval sets |

---

## PART E — Notes on verification limits

- Roboflow Universe pages could not be scraped directly (Cloudflare JS challenge, HTTP 403 confirmed via curl); class lists for the two Roboflow posture projects are taken from search-engine snippets only and are **UNVERIFIED** at the exact-count level — re-check after creating a free Roboflow account.
- Stanford Dogs' live breed-listing page returned only a stub/frameset during this session (plain HTTP 200 but ~8KB of boilerplate, no breed table) — Labrador presence is stated with high confidence from general knowledge of the ImageNet/Stanford-Dogs synset list (`n02099712`), not from a freshly re-opened breed table.
- Vehkaoja et al. dataset's dog-breed roster (are any of the 45 dogs Labradors?) was **not verified** — the Data in Brief paper text itself was not opened within the capped tool budget. Recommend a quick manual check of the paper (open access via ScienceDirect/PMC, both surfaced in search) before the demo if breed-matching the IMU data matters.
- UrbanSound8K license is non-commercial/research-only per the dataset's own terms — fine for a hackathon demo, not for any commercial use.
- Barkopedia's two sub-datasets (vocal-detection vs. breed-classification) gave conflicting info on whether Labrador is represented — flagged for the team, not resolved.
