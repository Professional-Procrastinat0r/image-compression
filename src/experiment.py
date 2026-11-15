"""Codec measurements and tangible reports for the ExpOps compression demo."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import platform
import statistics
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import PIL
from PIL import Image, ImageDraw, ImageFont, features
from skimage.metrics import peak_signal_noise_ratio, structural_similarity
from expops.core import process, log_metric
from expops.reporting import chart
from quality_reports import render_difference_map, render_quality_reports


def encode(image, codec, quality):
    buffer = io.BytesIO()
    options = {"quality": int(quality)}
    if codec == "JPEG":
        options.update(subsampling=2, optimize=False, progressive=False)
    elif codec == "WEBP":
        options.update(lossless=False, method=4)
    else:
        raise ValueError(f"Unsupported codec {codec}")
    image.save(buffer, format=codec, **options)
    return buffer.getvalue()


def decode(payload):
    with Image.open(io.BytesIO(payload)) as image:
        return image.convert("RGB")


@process()
def prepare_dataset(context=None):
    """Validate the declared references once and pass their identities downstream."""
    pinned = json.loads(context.fs.read_text("code/configs/dataset_manifest.json"))
    records = []
    for index, item in enumerate(pinned["images"], start=1):
        name = Path(item["name"]).stem
        with context.inputs[name].open("rb") as stream:
            payload = stream.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != item["sha256"] or len(payload) != item["bytes"]:
            raise ValueError(f"Reference identity mismatch for {name}")
        image = decode(payload)
        if sorted(image.size) != [512, 768]:
            raise ValueError(f"Unexpected reference dimensions for {name}")
        records.append({"image": name, "sha256": digest, "bytes": len(payload),
                        "width": image.width, "height": image.height})
        log_metric("images_validated", index, step=index)
    if len(records) != 24 or len({r["image"] for r in records}) != 24:
        raise ValueError("Expected 24 unique reference images")
    return {"dataset_manifest": records}


def read_reference(context, record):
    with context.inputs[record["image"]].open("rb") as stream:
        payload = stream.read()
    if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ValueError(f"Reference identity mismatch for {record['image']}")
    return decode(payload)


@process()
def compress_images(dataset_manifest, codec, quality, repeats=5, context=None):
    if codec not in {"JPEG", "WEBP"} or not 1 <= int(quality) <= 95:
        raise ValueError("Choose JPEG/WEBP and quality 1..95")
    if int(repeats) < 2:
        raise ValueError("At least two timing repetitions are required")
    if not features.check("webp"):
        raise RuntimeError("Pillow needs WebP support")
    rows = []
    for index, record in enumerate(dataset_manifest, start=1):
        name = record["image"]
        reference = read_reference(context, record)
        # Warm the encoder before timing. Storage is outside the timer.
        payload = encode(reference, codec, quality)
        encode_ms = []
        for _ in range(int(repeats)):
            start = time.perf_counter_ns()
            payload = encode(reference, codec, quality)
            encode_ms.append((time.perf_counter_ns() - start) / 1e6)
        digest = hashlib.sha256(payload).hexdigest()
        extension = "jpg" if codec == "JPEG" else "webp"
        address = f"artefact/encoded/{digest}/{name}.{extension}"
        context.fs.write_bytes(address, payload)
        row = {
            "image": name, "codec": codec, "quality": int(quality),
            "bytes": len(payload), "reference_png_bytes": record["bytes"],
            "bits_per_pixel": len(payload) * 8 / (reference.width * reference.height),
            "encode_ms": statistics.median(encode_ms),
            "encode_samples_ms": encode_ms,
            "sha256": digest, "vfs_path": address,
            "reference": record,
        }
        rows.append(row)
        for metric in ("bytes", "bits_per_pixel", "encode_ms"):
            log_metric(metric, row[metric], step=index)
        log_metric("images_completed", index, step=index)
    versions = {"python": platform.python_version(), "pillow": PIL.__version__,
                "jpeg": features.version("jpg"), "webp": features.version("webp")}
    return {"compression_batch": {"rows": rows, "versions": versions, "repeats": int(repeats)}}


@process()
def evaluate_images(compression_batch, context=None):
    """Consume published encodings from the upstream process through the VFS."""
    rows = []
    for index, encoding in enumerate(compression_batch["rows"], start=1):
        payload = context.fs.read_bytes(encoding["vfs_path"])
        if len(payload) != encoding["bytes"] or hashlib.sha256(payload).hexdigest() != encoding["sha256"]:
            raise ValueError(f"Published encoding checksum mismatch for {encoding['image']}")
        reference = read_reference(context, encoding["reference"])
        reconstructed = decode(payload)
        decode_ms = []
        for _ in range(compression_batch["repeats"]):
            start = time.perf_counter_ns()
            reconstructed = decode(payload)
            decode_ms.append((time.perf_counter_ns() - start) / 1e6)
        if reconstructed.size != reference.size:
            raise ValueError("Compressed image dimensions differ from reference")
        source_array, decoded_array = np.asarray(reference), np.asarray(reconstructed)
        row = {key: value for key, value in encoding.items() if key != "reference"}
        row.update({
            "psnr_db": float(peak_signal_noise_ratio(source_array, decoded_array, data_range=255)),
            "ssim": float(structural_similarity(source_array, decoded_array, channel_axis=2, data_range=255)),
            "decode_ms": statistics.median(decode_ms), "decode_samples_ms": decode_ms,
            "size_reduction_pct": 100 * (1 - len(payload) / encoding["reference_png_bytes"]),
        })
        rows.append(row)
        for metric in ("bytes", "bits_per_pixel", "psnr_db", "ssim", "encode_ms", "decode_ms", "size_reduction_pct"):
            log_metric(metric, row[metric], step=index)
        log_metric("images_evaluated", index, step=index)
    log_metric("results", rows)
    log_metric("versions", {"encoder": compression_batch["versions"],
                            "decoder": {"python": platform.python_version(), "pillow": PIL.__version__,
                                        "jpeg": features.version("jpg"), "webp": features.version("webp")}})
    return {"rows": rows}


def snapshot(block, name):
    value = block.get(name + "__last", block.get(name))
    if isinstance(value, dict) and value and all(str(k).isdigit() for k in value):
        return value[max(value, key=lambda k: int(k))]
    if value is None:
        raise ValueError(f"Missing report snapshot {name}")
    return value


def comparison_panel(reference, variants, labels, title, destination, crop=False):
    images = [reference, *variants]
    if crop:
        x, y = reference.width // 2, reference.height // 2
        images = [image.crop((x-96, y-96, x+96, y+96)) for image in images]
    panel_width, image_height, label_height = 300, 250, 85
    canvas = Image.new("RGB", (panel_width * len(images), image_height + label_height + 60), "#f4f6fa")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=16)
    draw.text((18, 18), title, fill="#14263e", font=font)
    for i, (image, label) in enumerate(zip(images, labels)):
        preview = image.copy()
        if crop:
            preview = preview.resize((image_height-10, image_height-10), Image.Resampling.NEAREST)
        else:
            preview.thumbnail((panel_width-20, image_height-10), Image.Resampling.LANCZOS)
        left = i * panel_width + (panel_width - preview.width)//2
        canvas.paste(preview, (left, 60 + (image_height-preview.height)//2))
        draw.multiline_text((i*panel_width+12, image_height+65), label, fill="#14263e", font=font, spacing=5)
    canvas.save(destination)


@chart()
def compression_report(metrics, ctx):
    rows = []
    versions = {}
    for branch, block in metrics.items():
        rows.extend(snapshot(block, "results"))
        versions[branch] = snapshot(block, "versions")
    rows.sort(key=lambda r: (r["image"], r["codec"], r["quality"]))
    if len(rows) != 144:
        raise ValueError(f"Expected 144 measurements, received {len(rows)}")
    output = ctx.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "measurements.json").write_text(json.dumps({"rows": rows, "versions": versions}, indent=2), encoding="utf-8")
    fields = [k for k in rows[0] if not k.endswith("samples_ms")]
    with (output / "measurements.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    selected = {"kodim01", "kodim05", "kodim13", "kodim23"}
    encoded = {}
    # Fetch the selected intermediates for comparison panels.
    for row in rows:
        if row["image"] not in selected:
            continue
        payload = ctx.fs.read_bytes(row["vfs_path"])
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError("Published encoding checksum mismatch")
        encoded[(row["image"], row["codec"], row["quality"])] = decode(payload)
    for name in sorted(selected):
        reference = decode(ctx.fs.read_bytes(f"input/{name}.png"))
        for quality in (30, 60, 90):
            pair = [next(r for r in rows if r["image"] == name and r["codec"] == codec and r["quality"] == quality)
                    for codec in ("JPEG", "WEBP")]
            labels = ["Reference PNG\n" + f"{len(ctx.fs.read_bytes(f'input/{name}.png'))/1024:.1f} KiB"]
            labels += [f"{r['codec']} setting {quality}\n{r['bytes']/1024:.1f} KiB | SSIM {r['ssim']:.3f}" for r in pair]
            variants = [encoded[(name, r["codec"], quality)] for r in pair]
            if quality == 30:
                render_difference_map(reference, variants, name, output / f"pixel_differences_{name}.png")
            for crop in (False, True):
                suffix = "detail" if crop else "full"
                comparison_panel(reference, variants, labels,
                                 f"{name} - {'detail crop' if crop else 'full image'} - encoder setting {quality}",
                                 output / f"compare_{name}_q{quality}_{suffix}.png", crop)
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    for codec, color in (("JPEG", "#176dbe"), ("WEBP", "#d46524")):
        groups = [[r for r in rows if r["codec"] == codec and r["quality"] == q] for q in (30,60,90)]
        bpp = [statistics.mean(r["bits_per_pixel"] for r in group) for group in groups]
        for ax, metric, ylabel in zip(axes, ("ssim", "psnr_db", "encode_ms"),
                                     ("Mean SSIM", "Mean PSNR (dB)", "Mean median encode time (ms)")):
            values = [statistics.mean(r[metric] for r in group) for group in groups]
            ax.plot(bpp, values, "o-", color=color, label=codec)
            ax.set(xlabel="Mean bits per pixel", ylabel=ylabel)
            ax.grid(alpha=.2)
            ax.legend()
    fig.suptitle("Kodak 24 images - size, reconstruction quality and encoding cost")
    fig.savefig(output / "compression_overview.png", dpi=150)
    plt.close(fig)
    render_quality_reports(rows, output)
    fig, ax = plt.subplots(figsize=(9,4), constrained_layout=True)
    labels, enc, dec = [], [], []
    for codec in ("JPEG", "WEBP"):
        for quality in (30,60,90):
            group = [r for r in rows if r["codec"] == codec and r["quality"] == quality]
            labels.append(f"{codec} Q{quality}")
            enc.append(statistics.mean(r["encode_ms"] for r in group))
            dec.append(statistics.mean(r["decode_ms"] for r in group))
    x = np.arange(len(labels))
    ax.bar(x-.2, enc, .4, label="Encode")
    ax.bar(x+.2, dec, .4, label="Decode")
    ax.set(xticks=x, xticklabels=labels, ylabel="Mean per-image median time (ms)", title="Codec operation time - five repetitions per image")
    ax.legend()
    fig.savefig(output / "codec_timings.png", dpi=150)
    plt.close(fig)
