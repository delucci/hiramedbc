"""Checking for and pulling MySQL images, and deciding the run platform (arm64 / amd64)."""

from rich.markup import escape

from hiramedbc.docker.runner import DockerCli, DockerCommandError
from hiramedbc.utils import console as out
from hiramedbc.utils.platform import host_arch

IMAGE_REPOSITORY = "mysql"
AMD64 = "linux/amd64"

# pull error messages indicating there is no arm64 variant (they differ by runtime and image store)
_NO_PLATFORM_MARKERS = (
    "no matching manifest",
    "does not provide the specified platform",
    "no match for platform",
)


def image_ref(version: str) -> str:
    return f"{IMAGE_REPOSITORY}:{version}"


def is_known_amd64_only(version: str) -> bool:
    """Whether the official image is known to have no arm64 variant for this version (5.x)."""
    return version.split(".")[0] == "5"


def is_platform_missing(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _NO_PLATFORM_MARKERS)


def resolve_arch(docker: DockerCli, arch: str | None = None) -> str:
    """CPU architecture to run on. Prefers the Docker daemon's, falling back to the Python process's."""
    return arch or docker.server_arch() or host_arch()


class ImageManager:
    def __init__(self, docker: DockerCli, arch: str | None = None) -> None:
        self.docker = docker
        self._arch = arch

    @property
    def arch(self) -> str:
        if self._arch is None:
            self._arch = resolve_arch(self.docker)
        return self._arch

    def initial_platform(self, version: str) -> str | None:
        if self.arch == "arm64" and is_known_amd64_only(version):
            return AMD64
        return None

    def pull(self, version: str) -> str | None:
        """Pull the image and return the platform to specify at run time.

        Even for versions not in the known list, retry with amd64 if pulling on ARM64 fails because there
        is no arm64 variant.
        """
        image = image_ref(version)
        platform = self.initial_platform(version)
        out.info(f"Pulling image {escape(image)}{' (' + platform + ')' if platform else ''}...")
        try:
            self.docker.pull(image, platform)
        except DockerCommandError as e:
            if self.arch != "arm64" or platform is not None or not is_platform_missing(e.output):
                raise
            out.info(f"{escape(image)} has no arm64 variant. Pulling {AMD64} instead...")
            platform = AMD64
            self.docker.pull(image, platform)
        return platform

    def ensure(self, version: str) -> str | None:
        """Pull the image if it is missing, and return the platform to specify at run time."""
        image = image_ref(version)
        local_arch = self.docker.image_arch(image)
        platform: str | None = None
        if local_arch is None:
            platform = self.pull(version)
            local_arch = self.docker.image_arch(image)
        # Check arch (docker info) only when the image might be amd64
        if (platform == AMD64 or local_arch == "amd64") and self.arch == "arm64":
            out.info(f"{escape(image)} is amd64 only. Running it under emulation.")
            return AMD64
        return platform
