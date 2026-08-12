"""Every module the runtime imports must exist in the deployment image.

This guard exists because the same mistake shipped twice in one afternoon: code
that imported Pillow, and code that imported httpx, both of which are present in
the development environment and absent from the container. Both passed the whole
test suite and failed on the first real request.

Deferred imports are the trap — an import inside a function body is invisible
until that line runs, which for an admin-only feature can be long after deploy.
"""

import ast
import pathlib
import sys

import pytest

API = pathlib.Path(__file__).resolve().parent.parent
ROOT = API.parents[1]
REQUIREMENTS = ROOT / "requirements-deploy.txt"

def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").split("[")[0]


def deploy_closure() -> set[str]:
    """Every distribution pip would install from requirements-deploy.txt.

    Direct requirements plus their transitive dependencies, so a module that
    arrives via FastAPI (pydantic, starlette) counts as present without being
    listed. Extras are excluded on purpose: starlette declares httpx under an
    extra, and httpx is exactly the sort of thing this test exists to catch.
    """
    import importlib.metadata as md
    direct = set()
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            direct.add(_norm(line.split("==")[0].split(">=")[0].split("<")[0]))
    closure, queue = set(), list(direct)
    while queue:
        dist = queue.pop()
        if dist in closure:
            continue
        closure.add(dist)
        try:
            requires = md.requires(dist) or []
        except md.PackageNotFoundError:
            continue                       # not installed here; take it on trust
        for req in requires:
            if "extra ==" in req:
                continue
            name = _norm(req.split(";")[0].split("==")[0].split(">=")[0]
                            .split("<")[0].split("!=")[0].split("~=")[0]
                            .split("(")[0].split(" ")[0])
            if name:
                queue.append(name)
    return closure


def available_modules() -> set[str]:
    import importlib.metadata as md
    closure = deploy_closure()
    mods = set(sys.stdlib_module_names)
    provided = md.packages_distributions()
    for module, dists in provided.items():
        if any(_norm(d) in closure for d in dists):
            mods.add(module)
    # Distribution names that match their module name but are not installed
    # here, so packages_distributions() cannot see them.
    mods.update(d.replace("-", "_") for d in closure)
    # Local modules and the workspace packages, which ship inside the image.
    mods.update(p.stem for p in API.glob("*.py"))
    for pkg in (ROOT / "packages").glob("*/"):
        mods.add(pkg.name)
        for inner in pkg.glob("*/"):
            if (inner / "__init__.py").exists():
                mods.add(inner.name)
    return mods


def imported_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


RUNTIME_MODULES = sorted(p for p in API.glob("*.py"))


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda p: p.name)
def test_runtime_imports_are_in_the_deployment_image(module):
    have = available_modules()
    missing = sorted(n for n in imported_names(module) if n not in have)
    assert not missing, (
        f"{module.name} imports {missing}, which are not in "
        f"requirements-deploy.txt. Either add them or use the standard library "
        f"— this fails in production on the first request that runs the import.")
