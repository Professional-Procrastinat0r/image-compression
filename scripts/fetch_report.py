"""Download published reports through the independent ExpOps reader APIs."""
import argparse
from pathlib import Path
from expops.storage import FileRef, open_metadata_reader, read_managed_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--all", action="store_true", help="Include every encoded image and comparison")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "output" / args.run_id
    selected = {"compression_overview.png", "codec_timings.png", "measurements.csv", "measurements.json",
                "compare_kodim05_q30_full.png", "compare_kodim05_q30_detail.png"}
    with open_metadata_reader("image-compression", {"type": "firestore", "gcp_project": "exp-ops"}) as reader:
        status = reader.get_run_status(args.run_id)
        if status != "completed":
            raise RuntimeError(f"Run is {status!r}; wait for successful completion")
        charts = reader.list_run_charts(args.run_id)
    items = [item for chart in charts.values() for item in chart["items"]]
    if not items:
        raise RuntimeError("Run has no published report artefacts")
    output.mkdir(parents=True, exist_ok=True)
    for item in items:
        name = item["title"]
        if not args.all and name not in selected:
            continue
        if Path(name).name != name:
            raise ValueError("Unexpected artefact filename")
        payload = read_managed_file(FileRef.from_mapping(item["published_file"]))
        (output / name).write_bytes(payload)
        print(output / name)


if __name__ == "__main__":
    main()
