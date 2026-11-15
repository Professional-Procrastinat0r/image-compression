# Image compression with ExpOps

Compare JPEG and WebP on all 24 Kodak reference images. Six parallel configuration branches use encoder quality settings 30, 60 and 90, followed by a reporting process. Every image has a warm-up and five measured encoding/decoding repetitions. Timings use memory buffers and exclude source downloads, quality scoring and output publication.

## Storage and execution

| Role | Cloud resource |
| --- | --- |
| Metadata | Firestore default database in GCP project `exp-ops` |
| Inputs | `gs://exp-ops-image-compression-inputs/kodak/` in `exp-ops` |
| Cache | `s3://expops-test-bucket-755933694771-ap-southeast-1-an/image-compression/cache/` |
| Artefacts | Same S3 bucket under `image-compression/artefact/` |
| Compute | Three local workers or the supplied Slurm profile |

Google Application Default Credentials and the existing AWS `expops` profile supply authentication. Credentials stay outside this repository. Each Slurm worker needs access to the same provider credentials through the platform's existing worker credential configuration. The repository uses the adjacent ExpOps checkout, initially verified at `65db1526cd37dee69480d3068971c10e277989c0`.

## Setup

From this directory on Windows:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '../expops-platform[gcp,aws,slurm]' -r requirements.txt google-cloud-storage
.venv/Scripts/python.exe scripts/prepare_data.py --upload
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run.ps1 -Profile cloud
```

The input preparation verifies RGB PNG dimensions and records source URLs, byte counts and SHA-256 hashes in `data/manifest.json`. It creates a private input bucket in `asia-southeast1` if needed. Re-running uploads the same verified local inputs. The 24 PNGs occupy about 15.4 MB. To prepare offline inputs only, omit `--upload`, then use `-Profile local`.

The checked-in `configs/dataset_manifest.json` pins all 24 source hashes. Preparation rejects changed source bytes.

On a prepared Linux Slurm driver:

```bash
python -m pip install -e '../expops-platform[gcp,aws,slurm]' -r requirements.txt
expops run configs/compute.slurm.yaml
```

Adapt account/QOS/queue and worker interpreter/base paths to the cluster. The code mount points at this checkout and uses ExpOps source preparation. The Slurm profile requires provider access from the worker nodes and should be rehearsed on the target cluster.

## Tangible outputs

The static report publishes all 144 compressed originals, 24 labelled before/after PNG panels, a size-quality-time overview, codec timing chart, CSV and JSON measurements. Panels show the reference alongside JPEG and WebP at each setting for images 01, 05, 13 and 23. Full-image panels scale to fit. Detail panels enlarge a fixed centre crop with nearest-neighbour scaling so interpolation does not hide codec artefacts.

The report verifies S3 output checksums before rendering and publishing its managed outputs. Intermediate encodings are content-addressed under the artefact mount and reused by reporting. The platform retains benchmark outputs in its S3 process cache and replays their metrics on cached runs.

To retrieve an overview, sample comparison panels and measurements independently of execution, use the run ID printed by ExpOps:

```powershell
.venv/Scripts/python.exe scripts/fetch_report.py <run-id>
```

Add `--all` to retrieve all published artefacts. This uses the read-only metadata API and verifies each published file's size and checksum. Downloads appear under `output/<run-id>/`.

Numerical metrics are SSIM and PSNR on RGB arrays with a fixed range of 255, file bytes, bits per pixel, and median encode/decode milliseconds. Quality numbers belong to each encoder. Compare the measured size-quality curves and visual samples. SSIM is an objective similarity metric, and the display panels provide qualitative examples of visual quality. Compression relative to the reference PNG measures the supplied file representation.

Per-image progress, timing and reconstruction metrics are logged during each branch. Connect the independent Run Monitor to the Firestore metadata and the configured mounts. A repeated identical command exercises cache reuse. Actual queue, startup and transfer time depend on the execution environment.

## Dataset credit

Kodak Lossless True Color Image Suite, hosted and converted to PNG by Rich Franzen, https://r0k.us/graphics/kodak/. Photographer credits are linked from that site. Keep this acknowledgement with demonstrations and redistributed samples.

Dependencies and encoder versions are recorded with each report. Local dataset copies, generated results, credentials and environments are ignored by Git.

## Verified execution

On 15 November 2026, the complete experiment ran with three local CPU workers and live Firestore, GCS and S3 storage. Run `project-image-compression-20261115112459-e6bf2803` completed all seven processes and published 172 indexed artefacts. Runtime after environment preparation was 202 seconds, including input identity checks, computation and output publication. All 144 measurement rows and their five encode/decode samples were checked. The enlarged comparison panels were visually inspected after downloading through checksum-verifying published references.

An earlier unchanged run reused all seven processes, retained all 172 artefacts, and completed in 16 seconds after environment preparation. The supplied Slurm configuration awaits execution on the target cluster.
