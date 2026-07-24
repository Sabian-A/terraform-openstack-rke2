import os

import pytest

from helpers import load_rke2_versions, render_patches, run


@pytest.fixture(scope="session")
def rendered_patches(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("rendered-patches")
    patches = {}
    for chart, content in render_patches().items():
        path = out / chart
        path.write_text(content)
        patches[chart.removesuffix(".yaml")] = path
    return patches


@pytest.fixture(scope="session")
def charts_cache(tmp_path_factory) -> dict:
    cache = {}
    versions = load_rke2_versions()
    only = os.environ.get("RKE2_VERSION")
    if only:
        versions = [v for v in versions if v == only]
    for version in versions:
        tag = version.replace("+", "-")
        dest = tmp_path_factory.mktemp(f"charts-{tag}")
        name = f"rt-{tag}"
        run(["docker", "rm", "-f", name], check=False)
        run(["docker", "create", "--entrypoint", "/bin/sh", "--name", name, f"rancher/rke2-runtime:{tag}"])
        run(["docker", "cp", f"{name}:/charts", str(dest)])
        run(["docker", "rm", name])
        cache[version] = dest / "charts"
    return cache


def pytest_generate_tests(metafunc):
    if "version" in metafunc.fixturenames:
        versions = load_rke2_versions()
        only = os.environ.get("RKE2_VERSION")
        if only:
            versions = [v for v in versions if v == only]
        metafunc.parametrize("version", versions)
