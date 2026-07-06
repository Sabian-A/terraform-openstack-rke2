import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATCHES_DIR = ROOT / "patches"
RKE2_YAML = ROOT / "RKE2.yaml"
SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"
YQ_VERSION = "4.40.5"

# Stable render fixtures for patches/rke2-*.yaml.tpl; prod binds the same vars in main.tf (cilium ~L109, coredns ~L118). Every fixture value is a typed placeholder token (<value:int|string|bool>) so every value that appears in the snapshot diff is obviously a voluntary test value, never a real prod default.
PATCH_VARS = {
    "operator_replica": "<value:int>",
    "cluster_name": "<value:string>",
    "cluster_id": "<value:int>",
    "ff_with_kubeproxy": "<value:bool>",
    "enable_encryption": "<value:bool>",
    "encryption_type": "<value:string>",
    "enable_node_encryption": "<value:bool>",
}


def version_tag(version: str) -> str:
    return version.replace("+", "-")


def load_rke2_versions():
    with RKE2_YAML.open() as f:
        data = yaml.safe_load(f)
    versions = data.get("versions", [])
    if not versions:
        raise ValueError(f"No versions found in {RKE2_YAML}")
    return versions


def run(cmd: list, *, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def yq_bin() -> str:
    path = os.environ.get("YQ_BIN")
    if path and Path(path).is_file():
        return path
    found = shutil.which("yq")
    if found:
        return found
    raise RuntimeError("yq not found; install mikefarah/yq v4.40.5 or set YQ_BIN")


def render_patch_tpl(tpl_path: Path) -> str:
    content = tpl_path.read_text()

    def replace_ternary(match: re.Match) -> str:
        var = match.group(1).strip()
        false_val = match.group(2).strip()
        true_val = match.group(3).strip()
        val = PATCH_VARS[var]
        if isinstance(val, str):
            return val
        return false_val if val else true_val

    content = re.sub(
        r"\$\{(\w+)\s*\?\s*([^:]+)\s*:\s*([^}]+)\}",
        replace_ternary,
        content,
    )
    for key, value in PATCH_VARS.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        content = content.replace(f"${{{key}}}", rendered)
    return content


def render_patches() -> Dict[str, str]:
    patches = {}
    for tpl_path in sorted(PATCHES_DIR.glob("rke2-*.yaml.tpl")):
        chart = tpl_path.name.removesuffix(".tpl")
        patches[chart] = render_patch_tpl(tpl_path)
    if not patches:
        raise RuntimeError(f"No patch templates found in {PATCHES_DIR}")
    return patches


def patch_top_level_keys(patch_path: Path) -> List[str]:
    data = yaml.safe_load(patch_path.read_text())
    if not isinstance(data, dict):
        return []
    return list(data.keys())


def pick_expr(keys: List[str]) -> str:
    inner = ", ".join(f'"{key}": .["{key}"]' for key in keys)
    return "{" + inner + "}"


def extract_patch_subset(values_path: Path, keys: List[str]) -> str:
    if not keys:
        return "{}\n"
    return run([yq_bin(), "-o=yaml", pick_expr(keys), str(values_path)]).stdout


def patch_keys_diff(
    values_path: Path,
    patched_path: Path,
    patch_path: Path,
    chart: str,
) -> str:
    keys = patch_top_level_keys(patch_path)
    original = extract_patch_subset(values_path, keys)
    patched = extract_patch_subset(patched_path, keys)
    if original == patched:
        return ""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"{chart}/values.yaml",
            tofile=f"{chart}/values.patched.yaml",
        )
    )


def extract_chart_values(chart_file: Path, chart_name: str, workdir: Path) -> Tuple[Path, str]:
    tar_path = workdir / "chart.tar"
    values_path = workdir / "values.yaml"
    chart_content = run([yq_bin(), "-r", ".spec.chartContent", str(chart_file)]).stdout
    tar_path.write_bytes(__import__("base64").b64decode(chart_content))
    with tarfile.open(tar_path, "r:gz") as tar:
        values_path.write_bytes(tar.extractfile(f"{chart_name}/values.yaml").read())
    chart_yaml_path = workdir / "Chart.yaml"
    with tarfile.open(tar_path, "r:gz") as tar:
        chart_yaml_path.write_bytes(tar.extractfile(f"{chart_name}/Chart.yaml").read())
    chart_version = run([yq_bin(), "-r", ".version", str(chart_yaml_path)]).stdout.strip()
    return values_path, chart_version


def merge_patch(values_path: Path, patch_path: Path, output_path: Path) -> None:
    shutil.copy(values_path, output_path)
    run([yq_bin(), "-i", "e", f'. *= load("{patch_path}")', str(output_path)])


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
