"""Download and verify Kodak references, optionally provision/upload cloud inputs."""
import argparse
import hashlib
import io
import json
import urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "exp-ops-image-compression-inputs"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "data"
    folder.mkdir(exist_ok=True)
    pinned = json.loads((ROOT / "configs/dataset_manifest.json").read_text(encoding="utf-8"))
    expected = {item["name"]: item["sha256"] for item in pinned["images"]}
    records = []
    for index in range(1, 25):
        name = f"kodim{index:02d}.png"
        path = folder / name
        url = f"https://r0k.us/graphics/kodak/kodak/{name}"
        if path.exists():
            payload = path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG" or sorted(image.size) != [512, 768] or image.mode != "RGB":
                raise ValueError(f"Unexpected Kodak image {name}")
        if hashlib.sha256(payload).hexdigest() != expected[name]:
            raise ValueError(f"Source checksum differs from the pinned dataset for {name}")
        path.write_bytes(payload)
        records.append({"name": name, "url": url, "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()})
        print(f"Verified {name}", flush=True)
    manifest = {"source": "https://r0k.us/graphics/kodak/", "images": records}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.upload:
        import s3fs
        import google.auth
        from google.cloud import storage, firestore
        credentials, _ = google.auth.default()
        credentials = credentials.with_quota_project("exp-ops")
        client = storage.Client(project="exp-ops", credentials=credentials)
        from google.api_core.exceptions import NotFound
        from google.auth.transport.requests import AuthorizedSession
        with AuthorizedSession(credentials) as session:
            response = session.get("https://cloudresourcemanager.googleapis.com/v1/projects/exp-ops", timeout=30)
            response.raise_for_status()
            project_number = int(response.json()["projectNumber"])
        try:
            bucket = client.get_bucket(BUCKET)
            if int(bucket.project_number) != project_number:
                raise RuntimeError("Existing input bucket belongs to a different project")
        except NotFound:
            bucket = client.bucket(BUCKET)
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            bucket.iam_configuration.public_access_prevention = "enforced"
            bucket = client.create_bucket(bucket, location="ASIA-SOUTHEAST1")
        for record in records:
            blob = bucket.blob("kodak/" + record["name"])
            blob.upload_from_filename(folder / record["name"], content_type="image/png")
        bucket.blob("kodak/manifest.json").upload_from_filename(folder / "manifest.json", content_type="application/json")
        s3fs.S3FileSystem(profile="expops").info("expops-test-bucket-755933694771-ap-southeast-1-an")
        # This reads only this experiment's document and verifies database access.
        firestore.Client(project="exp-ops", credentials=credentials).collection("mlops_projects").document("image-compression-preflight").get()
        print(f"Uploaded 24 images and manifest to gs://{BUCKET}/kodak/")


if __name__ == "__main__":
    main()
