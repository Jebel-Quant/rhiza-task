"""The container tasks: docker.mk, as tasks.

Three wrappers over the docker CLI, and the shortest of the five fragments. The only
thing worth stating is what the image is called: docker.mk defaults it to
``$(shell basename $(CURDIR))``, so an unset :attr:`~rhiza_task.config.Config.docker_image`
resolves to the repository directory's name here too -- moving a checkout would rename the
image, which is surprising but is the behaviour consumers already have.

``docker-build`` skips rather than fails on a missing Dockerfile, as the fragment does.
That is not the same judgement as the tool guard's: a repository with no ``docker/``
folder has adopted the bundle and not used it yet, whereas a machine with no docker
cannot answer the question at all. Both are a skip, and ``--strict`` fails both.
"""

from __future__ import annotations

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import tool

SECTION = "Docker"

HAVE_DOCKER = Guard(tool="docker", reason="docker not found; install from https://docs.docker.com/get-docker/")


def image_name(cfg: Config) -> str:
    """Return the tag to build and run.

    Args:
        cfg: The resolved config.

    Returns:
        The configured image name, or the repository directory's name.
    """
    return cfg.docker_image or cfg.root.name


@task("docker-build", "build the Docker image", section=SECTION, guards=(HAVE_DOCKER,))
def docker_build(cfg: Config) -> None:
    """Build ``<docker_folder>/Dockerfile`` with the repository root as the context.

    ``PYTHON_VERSION`` is passed as a build argument whatever the layer, as docker.mk
    does. A Dockerfile that declares no such ``ARG`` gets a warning from docker and
    nothing else, which is cheaper than making the flag conditional on a language.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the folder holds no Dockerfile.
    """
    dockerfile = cfg.root / cfg.docker_folder / "Dockerfile"
    if not dockerfile.is_file():
        raise Skip(f"no {cfg.docker_folder}/Dockerfile")

    tag = f"{image_name(cfg)}:latest"
    print(f"[INFO] building {tag} with Python {cfg.python_version}")
    tool(
        "docker",
        "buildx",
        "build",
        "--file",
        f"{cfg.docker_folder}/Dockerfile",
        "--build-arg",
        f"PYTHON_VERSION={cfg.python_version}",
        "--tag",
        tag,
        "--load",
        ".",
        cwd=cfg.root,
    )


@task("docker-run", "run the Docker container", section=SECTION, needs=("docker-build",), guards=(HAVE_DOCKER,))
def docker_run(cfg: Config) -> None:
    """Run the built image interactively, removing the container on exit.

    Args:
        cfg: The resolved config.
    """
    tag = f"{image_name(cfg)}:latest"
    print(f"[INFO] running {tag}")
    tool("docker", "run", "--rm", "-it", tag, cwd=cfg.root)


@task("docker-clean", "remove the Docker image", section=SECTION, guards=(HAVE_DOCKER,))
def docker_clean(cfg: Config) -> None:
    """Delete the image, tolerating its absence.

    ``check=False`` is docker.mk's ``2>/dev/null || true``: removing an image that was
    never built is the expected state of a clean target, not a failure.

    Args:
        cfg: The resolved config.
    """
    tag = f"{image_name(cfg)}:latest"
    print(f"[INFO] removing {tag}")
    tool("docker", "rmi", tag, cwd=cfg.root, check=False)
