import json
import os
import tempfile
from difflib import unified_diff
from pathlib import Path

import pytest

from helpers import (
    SNAPSHOTS_DIR,
    extract_chart_values,
    merge_patch,
    normalize_unified_diff,
    patch_top_level_keys,
    run,
    sha256_file,
    patch_keys_diff,
    version_tag,
    yq_bin,
)


def test_patches(version, rendered_patches, charts_cache):
    charts_dir = charts_cache[version]
    tag = version_tag(version)
    snapshot_dir = SNAPSHOTS_DIR / tag
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    meta_file = snapshot_dir / "meta.json"
    meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
    meta.setdefault("rke2_version", version)
    meta.setdefault("charts", {})

    for chart, patch_file in rendered_patches.items():
        chart_file = charts_dir / f"{chart}.yaml"
        snapshot_file = snapshot_dir / f"{chart}.patch"

        assert chart_file.is_file(), f"Missing chart {chart_file} for {version}"
        assert patch_file.is_file(), f"Missing rendered patch {patch_file}"

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            values_path, chart_version = extract_chart_values(chart_file, chart, workdir)
            patched_path = workdir / "patched.yaml"
            merge_patch(values_path, patch_file, patched_path)

            run([yq_bin(), "e", ".", str(patched_path)])

            for key in patch_top_level_keys(patch_file):
                run([yq_bin(), "-e", f".{key}", str(patched_path)])

            meta["charts"][chart] = {
                "chart_version": chart_version,
                "values_sha256": sha256_file(values_path),
                "merge_status": "ok",
            }

            diff_content = patch_keys_diff(values_path, patched_path, patch_file, chart)
            assert diff_content, f"Patch produced no diff for {chart}@{version}"

            if os.environ.get("UPDATE_SNAPSHOTS") == "1":
                snapshot_file.write_text(diff_content)
                continue

            assert snapshot_file.is_file(), (
                f"Missing snapshot {snapshot_file}; run UPDATE_SNAPSHOTS=1 pytest tests/patches"
            )
            expected = normalize_unified_diff(snapshot_file.read_text())
            actual = normalize_unified_diff(diff_content)
            if actual != expected:
                delta = unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=str(snapshot_file),
                    tofile=f"{chart}@{version}",
                )
                pytest.fail("values diff drift:\n" + "".join(delta))

    meta_file.write_text(json.dumps(meta, indent=2) + "\n")
