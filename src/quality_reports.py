"""Quality comparisons across configurations and individual reference images."""
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


def summarize_quality(rows):
    """Keep each photograph equally weighted within its configuration."""
    summaries = []
    for codec in ("JPEG", "WEBP"):
        for quality in (30, 60, 90):
            group = [r for r in rows if r["codec"] == codec and r["quality"] == quality]
            if len(group) != 24 or len({r["image"] for r in group}) != 24:
                raise ValueError(f"Expected 24 unique images for {codec} Q{quality}")
            summary = {"codec": codec, "quality": quality, "images": len(group)}
            for metric in ("ssim", "psnr_db", "bits_per_pixel", "size_reduction_pct"):
                values = np.asarray([r[metric] for r in group], dtype=float)
                summary[f"mean_{metric}"] = float(values.mean())
                summary[f"std_{metric}"] = float(values.std(ddof=1))
            summaries.append(summary)
    return summaries


def render_quality_reports(rows, output):
    output = Path(output)
    summaries = summarize_quality(rows)
    with (output / "quality_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    for codec, color, marker in (("JPEG", "#176dbe", "o"), ("WEBP", "#d46524", "s")):
        group = [s for s in summaries if s["codec"] == codec]
        for ax, metric, label in zip(axes, ("ssim", "psnr_db"), ("SSIM (higher is better)", "PSNR (dB, higher is better)")):
            ax.errorbar([s["quality"] for s in group], [s[f"mean_{metric}"] for s in group],
                        yerr=[s[f"std_{metric}"] for s in group], color=color, marker=marker,
                        capsize=4, label=codec)
            ax.set(xlabel="Encoder quality setting (codec-specific)", ylabel=label, xticks=[30, 60, 90])
            ax.grid(alpha=.2)
            ax.legend()
    fig.suptitle("Reconstruction quality - mean and +/- 1 sample SD across 24 images")
    fig.savefig(output / "image_quality_comparison.png", dpi=150)
    plt.close(fig)

    images = sorted({r["image"] for r in rows})
    configurations = [(codec, q) for codec in ("JPEG", "WEBP") for q in (30, 60, 90)]
    lookup = {(r["image"], r["codec"], r["quality"]): r for r in rows}
    values = np.array([[lookup[(image, codec, q)]["ssim"] for codec, q in configurations] for image in images])
    fig, ax = plt.subplots(figsize=(9, 10), constrained_layout=True)
    matrix = ax.imshow(values, vmin=0, vmax=1, cmap="viridis", aspect="auto")
    for y in range(len(images)):
        for x in range(len(configurations)):
            ax.text(x, y, f"{values[y,x]:.3f}", ha="center", va="center",
                    color="black" if values[y,x] > .55 else "white", fontsize=9)
    ax.set(xticks=range(6), xticklabels=[f"{c}\nQ{q}" for c,q in configurations],
           yticks=range(len(images)), yticklabels=images,
           title="SSIM for every image and configuration (higher is better)")
    fig.colorbar(matrix, ax=ax, label="SSIM", shrink=.6)
    fig.savefig(output / "per_image_ssim.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    distributions = [[lookup[(image, codec, q)]["ssim"] for image in images] for codec,q in configurations]
    ax.boxplot(distributions, tick_labels=[f"{c} Q{q}" for c,q in configurations], showmeans=True)
    ax.set(ylabel="SSIM", title="Quality variation across photographs - 24 observations per configuration")
    ax.legend(handles=[Line2D([], [], color="tab:orange", label="Median"),
                       Line2D([], [], color="tab:green", marker="^", linestyle="none", label="Mean")])
    ax.grid(axis="y", alpha=.2)
    fig.savefig(output / "quality_distribution.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for codec, color, marker in (("JPEG", "#176dbe", "o"), ("WEBP", "#d46524", "s")):
        group = [s for s in summaries if s["codec"] == codec]
        x = [s["mean_size_reduction_pct"] for s in group]
        y = [s["mean_ssim"] for s in group]
        ax.plot(x, y, marker=marker, color=color, label=codec)
        for a,b,s in zip(x,y,group):
            ax.annotate(f"Q{s['quality']}", (a,b), xytext=(5,6), textcoords="offset points")
    ax.set(xlabel="Mean file-size reduction relative to reference PNG (%)",
           ylabel="Mean SSIM", title="Storage savings versus reconstructed image quality")
    ax.grid(alpha=.2)
    ax.margins(x=.12, y=.12)
    ax.legend()
    fig.savefig(output / "quality_vs_storage_savings.png", dpi=150)
    plt.close(fig)


def render_difference_map(reference, variants, name, destination):
    reference_array = np.asarray(reference, dtype=np.float32)
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5), constrained_layout=True)
    axes[0].imshow(reference)
    axes[0].set_title("Reference")
    for ax, variant, codec in zip(axes[1:], variants, ("JPEG", "WebP")):
        error = np.abs(reference_array - np.asarray(variant, dtype=np.float32)).mean(axis=2)
        image = ax.imshow(error, cmap="inferno", vmin=0, vmax=64)
        ax.set_title(f"{codec} Q30 pixel differences")
    for ax in axes:
        ax.axis("off")
    fig.colorbar(image, ax=list(axes[1:]), label="Mean absolute RGB error (0-255); colour saturates at 64",
                 orientation="horizontal", shrink=.8, pad=.04)
    fig.suptitle(f"{name} - brighter regions have larger pixel differences")
    fig.savefig(destination, dpi=150)
    plt.close(fig)
