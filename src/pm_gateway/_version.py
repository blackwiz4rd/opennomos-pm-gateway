"""Package version resolved from the installed distribution metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opennomos-pm-gateway")
except PackageNotFoundError:  # pragma: no cover - vendored or uninstalled tree
    # The monorepo vendors pm_gateway under the `opennomos` distribution, so
    # there is no `opennomos-pm-gateway` metadata to read there.
    __version__ = "0.1.0a1"
