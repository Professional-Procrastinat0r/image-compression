"""Check the contracts between the new independently scheduled processes."""
import io
import sys
import tempfile
import unittest
import yaml
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import experiment
from quality_reports import summarize_quality


class LocalFiles:
    def __init__(self, artefacts):
        self.artefacts = Path(artefacts)

    def path(self, address):
        alias, key = address.split("/", 1)
        return {"code": ROOT, "artefact": self.artefacts}[alias] / key

    def read_text(self, address):
        return self.path(address).read_text(encoding="utf-8")

    def read_bytes(self, address):
        return self.path(address).read_bytes()

    def write_bytes(self, address, payload):
        path = self.path(address)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


class StageHandoffs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = SimpleNamespace(
            fs=LocalFiles(self.tmp.name),
            inputs={f"kodim{i:02d}": ROOT / "data" / f"kodim{i:02d}.png" for i in range(1, 25)},
        )
        metric = patch.object(experiment, "log_metric")
        metric.start()
        self.addCleanup(metric.stop)
        self.manifest = experiment.prepare_dataset(context=self.ctx)["dataset_manifest"]

    def test_separate_encoder_evaluator_file_handoff(self):
        for codec in ("JPEG", "WEBP"):
            with self.subTest(codec=codec):
                batch = experiment.compress_images(self.manifest[:1], codec, 60, repeats=2, context=self.ctx)
                result = experiment.evaluate_images(batch["compression_batch"], context=self.ctx)
                row = result["rows"][0]
                self.assertEqual(row["image"], "kodim01")
                self.assertEqual(row["codec"], codec)
                self.assertEqual(len(row["encode_samples_ms"]), 2)
                self.assertEqual(len(row["decode_samples_ms"]), 2)
                self.assertGreater(row["ssim"], 0)
                self.assertLessEqual(row["ssim"], 1)
                self.assertNotIn("reference", row)
                self.assertAlmostEqual(row["size_reduction_pct"],
                                       100 * (1 - row["bytes"] / row["reference_png_bytes"]))

    def test_corrupt_intermediate_is_rejected(self):
        batch = experiment.compress_images(self.manifest[:1], "JPEG", 30, repeats=2, context=self.ctx)
        address = batch["compression_batch"]["rows"][0]["vfs_path"]
        self.ctx.fs.write_bytes(address, b"corrupted")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            experiment.evaluate_images(batch["compression_batch"], context=self.ctx)

    def test_reference_change_is_rejected(self):
        self.ctx.inputs["kodim01"] = SimpleNamespace(open=lambda *args: io.BytesIO(b"changed"))
        with self.assertRaisesRegex(ValueError, "Reference identity mismatch"):
            experiment.prepare_dataset(context=self.ctx)

    def test_configuration_has_isolated_branch_handoffs(self):
        config = yaml.safe_load((ROOT / "configs/project_config.yaml").read_text())
        pipeline = config["experiment"]["pipeline"]
        self.assertEqual(len(pipeline["processes"]), 14)
        predecessors = {}
        for edge in pipeline["process_adjlist"].splitlines():
            source, target = edge.split()
            predecessors.setdefault(target, set()).add(source)
        for codec in ("jpeg", "webp"):
            for quality in (30, 60, 90):
                suffix = f"{codec}_q{quality}"
                self.assertEqual(predecessors[f"encode_{suffix}"], {"prepare_dataset"})
                self.assertEqual(predecessors[f"evaluate_{suffix}"], {f"encode_{suffix}"})
        self.assertEqual(len(predecessors["compression_report"]), 6)

    def test_quality_summary_weights_images_equally(self):
        rows = [{"image": f"image{i}", "codec": codec, "quality": quality,
                 "ssim": .5 + .01*i, "psnr_db": 20+i, "bits_per_pixel": 1+i,
                 "size_reduction_pct": 50+i}
                for codec in ("JPEG", "WEBP") for quality in (30, 60, 90) for i in range(24)]
        summary = summarize_quality(rows)
        self.assertEqual(len(summary), 6)
        self.assertAlmostEqual(summary[0]["mean_ssim"], .615)
        self.assertAlmostEqual(summary[0]["mean_size_reduction_pct"], 61.5)
        self.assertAlmostEqual(summary[0]["std_psnr_db"], 7.0710678118654755)
        rows[-1]["image"] = rows[-2]["image"]
        with self.assertRaisesRegex(ValueError, "unique images"):
            summarize_quality(rows)


if __name__ == "__main__":
    unittest.main()
