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

import os

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import tool

SECTION = "Docker"

HAVE_DOCKER = Guard(tool="docker", reason="docker not found; install from https://docs.docker.com/get-docker/")

BUILD_SECRETS = (("gh_pat", "GH_PAT"), ("uv_extra_index_url", "UV_EXTRA_INDEX_URL"))
"""The BuildKit secrets ``docker-build`` forwards, as ``(secret id, environment variable)``.

These are the two mounts the bundle's Dockerfile declares so that ``uv sync`` can resolve a
private Git dependency or a private index *inside* the builder stage, and the ids and
variable names are the ones ``rhiza_docker.yml`` passes -- so a project that builds in CI
builds the same way here, with the same ``export``. Two rules about how they are passed:

* **Only when set.** ``--secret id=x,env=X`` for an unset ``X`` makes buildx fail with
  ``failed to read secret X from env``, whereas the Dockerfile's ``[ -s /run/secrets/x ]``
  guard already treats a *missing* mount as a no-op. So the conditional lives here rather
  than in the Dockerfile. A variable set to the empty string counts as unset for the same
  reason: an empty mount is exactly what that guard skips.
* **Never ``--build-arg``.** A build argument is recorded in the image and readable with
  ``docker history``; a secret exists only for the instruction that mounts it.

A Dockerfile that declares no such mount gets a warning from docker about an unused secret
and nothing else, the same shape as the unconditional ``PYTHON_VERSION`` build arg.
"""


def image_name(cfg: Config) -> str:
    """Return the tag to build and run.

    Args:
        cfg: The resolved config.

    Returns:
        The configured image name, or the repository directory's name.
    """
    return cfg.docker_image or cfg.root.name


def secret_flags() -> list[str]:
    """Return the ``--secret`` flags for every :data:`BUILD_SECRETS` variable that is set.

    Returns:
        ``["--secret", "id=<id>,env=<VAR>", ...]`` in :data:`BUILD_SECRETS` order, empty
        when none of the variables is set.
    """
    flags: list[str] = []
    for secret_id, var in BUILD_SECRETS:
        if os.environ.get(var):
            flags.extend(("--secret", f"id={secret_id},env={var}"))
    return flags


@task("docker-build", "build the Docker image", section=SECTION, guards=(HAVE_DOCKER,))
def docker_build(cfg: Config) -> None:
    """Build ``<docker_folder>/Dockerfile`` with the repository root as the context.

    ``PYTHON_VERSION`` is passed as a build argument whatever the layer, as docker.mk
    does. A Dockerfile that declares no such ``ARG`` gets a warning from docker and
    nothing else, which is cheaper than making the flag conditional on a language.

    ``GH_PAT`` and ``UV_EXTRA_INDEX_URL`` are forwarded as BuildKit secrets when set, so a
    private dependency the CI build resolves is resolved here too; :data:`BUILD_SECRETS`
    says why they are conditional and why they are not build arguments.

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
        *secret_flags(),
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
