"""The template sync, as one command, for people who are not driving it from Claude.

Every other module here replaces a make recipe. This one replaces a *README section*:
jebel-quant/rhiza#1654 logs an upgrade that took eight documented steps, a clone into
``~/.local/share``, three scripts run in sequence and four commits, and the guide that came
out of it is wrong in one place that matters -- it says ``git add -A`` where the upstream
document says never to, because the lock records exactly which paths the sync materialised
and staging everything sweeps unrelated work into the sync commit.

**This is a wrapper, and that is the whole design.** ``rhiza-claude``'s ``docs/headless.md``
settles the question this module would otherwise re-open:

    Nothing here is a re-implementation. ``/rhiza:update`` shells out to the very
    ``sync.py`` invocation shown below [...] A separate CLI would be a second
    implementation of sync to keep in step with the first, and that is the one thing this
    project has decided not to maintain. One operation, one entry point.

So nothing below parses ``template.yml``, resolves a bundle, walks the lock or merges a
file. Those live in ``sync.py``, ``resolve_conflicts.py`` and ``stage_synced.py``, which are
stdlib-only, type-checked and covered in the repository that owns them. This module
provisions them, runs them in the documented order, reads the documented exit codes, and
stops. A third caller of one entry point is not a second implementation of it.

**Why it lives here** rather than beside the scripts: ``rhiza-task`` is the only thing in
this ecosystem published to PyPI, so ``uvx rhiza-task update`` needs no install at all --
and the install was the step the issue's users tripped over. The scripts stay where they
are; only the front door moves to where a consumer already is.

**Where it stops, and why it stops there.** After ``stage_synced.py`` the change is on disk
and staged, and this task prints the commit command instead of running it. Two reasons, and
neither is timidity. The commit needs ``SKIP=check-managed-files`` -- rhiza-hooks refuses a
commit touching any path in the lock's ``files:`` list, and this commit is by construction
exactly that list -- which is a thing a person should see once rather than have hidden. And
a sync that reported conflicts has just resolved them by taking the template's side, which
is right for a managed file and is still a diff worth reading before it is history.

**This is the one task in the package that this repository cannot dogfood.** ``ci.yml``'s
``gates`` job runs ``rhiza-task all`` against this tree, and there is no ``.rhiza/`` here to
sync -- CLAUDE.md's first section says why, at length. So the guard below skips, and the
vectors are asserted the way every other module's are, plus a scratch-repo job in CI. That
is a real gap and worth naming rather than leaving for someone to discover: nothing here
proves ``sync.py`` still takes the arguments this module passes it. What would prove it is a
job in *rhiza-claude* running its scripts against this front door, which is that
repository's call to make.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Config
from ..spec import Failed, Guard, task
from ..uv import tool, uv_run

SCRIPTS_REPO = "https://github.com/Jebel-Quant/rhiza-claude"
"""Where the sync scripts come from. Not a setting: which implementation of the template
sync is authoritative is not a per-repository choice, and a repo pointing this at a fork
would be running a second implementation -- the thing the module docstring rules out."""

SCRIPTS_SUBDIR = "plugin/scripts"
"""Where the scripts sit inside that clone. ``headless.md`` spells the same path into its
``$RHIZA`` export, so a reader following either document lands in the same directory."""

SCRIPTS_PYTHON = "3.12"
"""The interpreter the scripts are run under.

``headless.md`` is unambiguous: ``tomllib`` and ``datetime.UTC`` put their floor at 3.11,
but 3.12 is what every command pins and the only version their CI exercises -- and a bare
``python3`` on macOS is 3.9, which crashes ``sync.py``. Pinning it here is what stops this
front door being the one that reintroduces that crash. It is deliberately *not* tied to
:attr:`Config.python_version`, which is the target repository's interpreter and has nothing
to say about a different project's scripts."""

TEMPLATE_YML = ".rhiza/template.yml"
"""The pointer file. Its presence is what makes a repository rhiza-managed, so it is also
the guard: a repo without one has no template to sync and skips rather than fails."""

REF_KEYS = ("template-branch", "ref")
"""The keys that can hold the template ref, in the precedence the sync itself applies.

Not a preference: ``_validate_fields.py`` picks ``template-branch`` over ``ref`` when a file
has both, so this order is a mirror of that one. Reversing it would produce a pointer file
that disagrees with the version actually synced, with nothing non-zero anywhere to say so."""

CONFLICTS = 1
"""``sync.py``'s exit code for "synced, with conflicts" -- an expected outcome, not an error.

The three codes are a contract that document states: 0 synced cleanly or already current,
1 synced with conflicts and the merged files are on disk, 2 refused and applied nothing.
Only 1 is named here because 0 needs no branch and anything else is a failure; treating 1 as
an error is the mistake this constant exists to make hard."""


def _cache_root() -> Path:
    """Return the directory this module keeps its own clone of the scripts in.

    ``XDG_CACHE_HOME`` is honoured because it is the standard answer and because it is what
    makes the clone path testable without writing to a real home directory.

    Returns:
        The clone directory, which may not exist yet.
    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "rhiza-task" / "rhiza-claude"


def _ensure_scripts(cfg: Config) -> Path:
    """Return the scripts directory, cloning it first when this module owns the copy.

    Two cases, and the difference between them is who the clone belongs to. With
    ``RHIZA_CLAUDE_DIR`` set the directory is *someone else's* -- most likely the
    ``~/.local/share/rhiza-claude`` that ``headless.md`` tells people to make -- so it is
    used exactly as found and never written to. A ``git reset --hard`` into a working clone
    would discard whatever the person had in it, which is not a thing a sync command should
    be able to do to you.

    The cache copy is ours, so it is refreshed: a stale ``sync.py`` is a correctness risk,
    and here there is nothing to lose by moving it.

    Args:
        cfg: The resolved config, for the working directory git is invoked from.

    Returns:
        The directory holding ``sync.py`` and its siblings.

    Raises:
        Failed: When ``RHIZA_CLAUDE_DIR`` names a directory with no scripts in it. Silently
            falling back to the cache would run a different copy than the one asked for.
    """
    override = os.environ.get("RHIZA_CLAUDE_DIR")
    if override:
        scripts = Path(override).expanduser() / SCRIPTS_SUBDIR
        if not scripts.is_dir():
            msg = f"RHIZA_CLAUDE_DIR is set to {override}, which has no {SCRIPTS_SUBDIR}/"
            raise Failed(2, msg)
        return scripts

    cache = _cache_root()
    if (cache / ".git").is_dir():
        tool("git", "-C", str(cache), "fetch", "--depth", "1", "origin", "HEAD", cwd=cfg.root)
        tool("git", "-C", str(cache), "reset", "--hard", "FETCH_HEAD", cwd=cfg.root)
    else:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tool("git", "clone", "--depth", "1", SCRIPTS_REPO, str(cache), cwd=cfg.root)
    return cache / SCRIPTS_SUBDIR


def _bump_ref(path: Path, ref: str) -> None:
    r"""Rewrite the pointer's ref line, and nothing else in the file.

    **Which key holds the ref is not a fixed answer**, and getting it wrong fails silently
    rather than loudly. ``_validate_fields.py`` resolves it as ``"template-branch" if
    "template-branch" in config else "ref"`` -- so in a file carrying both, ``template-branch``
    is what the sync obeys. Rewriting ``ref:`` there would leave the file *claiming* the new
    version while the sync fetched the old one, and every exit code would be 0. The same
    precedence is applied here for exactly that reason.

    The anchor is column zero, copied from ``headless.md``'s own
    ``sed -i "s/^ref: .*/ref: \"$TARGET\"/"``: ``profiles:``, ``templates:``, ``exclude:`` and
    ``language:`` are a separate decision a version bump must not carry, and matching at any
    indentation would reach a nested key inside them.

    Args:
        path: The ``.rhiza/template.yml`` to edit.
        ref: The target ref, e.g. ``v1.8.0``.

    Raises:
        Failed: When the file holds neither key at top level. Appending one would be a guess
            about a file this module has already said it does not parse.
    """
    lines = path.read_text().splitlines(keepends=True)
    for key in REF_KEYS:
        if not any(line.startswith(f"{key}:") for line in lines):
            continue
        rewritten = [f'{key}: "{ref}"\n' if line.startswith(f"{key}:") else line for line in lines]
        path.write_text("".join(rewritten))
        return
    msg = f"{TEMPLATE_YML} has no top-level {' or '.join(REF_KEYS)} line to bump"
    raise Failed(2, msg)


def _commit_bump(cfg: Config) -> None:
    """Commit the pointer bump, because ``sync.py`` refuses to run with it uncommitted.

    This is the one commit the task makes, and it is not a relaxation of the rule that the
    *sync* commit is the caller's -- it is a precondition of getting a sync at all.
    ``headless.md`` has it as step 2 for the same reason, and a real run is what proved it
    load-bearing: bump, then sync, and sync reports ``Working tree is not clean`` naming the
    file this task just wrote. Vector tests cannot find that, since the refusal is a fact
    about ``sync.py`` rather than about the arguments handed to it.

    The pathspec is not decoration. ``headless.md`` says ``git commit -am``, which would
    sweep every other modified tracked file into a commit whose message claims to be a
    version bump; naming the path commits that path and nothing else. A tree dirty for other
    reasons is then still refused by ``sync.py``, which is upstream's call to make and not
    this module's to pre-empt.

    Args:
        cfg: The resolved config.
    """
    tool("git", "commit", "-m", f"chore: bump rhiza to {cfg.template_ref}", "--", TEMPLATE_YML, cwd=cfg.root)


def _script(cfg: Config, scripts: Path, name: str, *args: str, check: bool = True) -> int:
    """Run one headless script against the target repository.

    ``--no-project`` is not optional: without it uv resolves the *target* repo's environment
    for a script that imports nothing outside the standard library, which at best is a slow
    no-op and at worst fails in a repo whose own dependencies do not resolve.

    Args:
        cfg: The resolved config.
        scripts: The directory holding the scripts.
        name: The script's filename, e.g. ``sync.py``.
        *args: Arguments after the repository path.
        check: Raise on a non-zero exit rather than returning it.

    Returns:
        The script's exit status.
    """
    return uv_run(
        "python",
        str(scripts / name),
        str(cfg.root),
        *args,
        cwd=cfg.root,
        no_project=True,
        python=SCRIPTS_PYTHON,
        check=check,
    )


@task(
    "update",
    "sync the rhiza template into this repository",
    section="Template",
    # No `Guard(tool="git")`, and that is a rule rather than an omission: `doctor` names uv
    # and git as the two prerequisites a process running `uvx rhiza-task` cannot do without,
    # and fails on a miss. Guarding a task on one of them would demote that hard requirement
    # to a per-task skip, so `test_probes_nothing_a_guard_already_owns` asserts the two sets
    # are disjoint -- and caught this guard on the first run. A machine with no git gets the
    # OS's own message from the clone, which is what `uv.py`'s `_bin` already settles for uv.
    guards=(Guard(file=TEMPLATE_YML, reason=f"no {TEMPLATE_YML}; this repository is not rhiza-managed"),),
)
def update(cfg: Config) -> None:
    """Bump the template pointer if asked, then sync, resolve and stage.

    The four steps ``headless.md`` documents, in its order and reading its exit codes. What
    is left to the caller is the commit, for the reasons the module docstring gives.

    Args:
        cfg: The resolved config. :attr:`Config.template_ref` is the ref to move to; empty
            re-syncs at whatever ``template.yml`` already names.

    Raises:
        Failed: When ``sync.py`` refused -- a dirty tree, an invalid ``template.yml`` or a
            git failure -- or when a later step failed. A refusal applies nothing, so there
            is no half-done state to unpick.
    """
    scripts = _ensure_scripts(cfg)
    if cfg.template_ref:
        _bump_ref(cfg.root / TEMPLATE_YML, cfg.template_ref)
        _commit_bump(cfg)
        print(f"[INFO] {TEMPLATE_YML} now points at {cfg.template_ref}")

    code = _script(cfg, scripts, "sync.py", check=False)
    if code == CONFLICTS:
        # Taking the template's side of every marker is what resolve_conflicts.py does, and
        # it is right rather than merely convenient: a rhiza-managed file is the template's
        # to own, so local divergence in one is drift to undo, not work to preserve.
        print("[INFO] sync reported conflicts; taking the template's side")
        _script(cfg, scripts, "resolve_conflicts.py")
    elif code:
        # Naming the bump matters when there was one: the pointer is committed by then, so
        # "applied nothing" is true of the sync and not of the run, and a reader with a dirty
        # tree needs to know there is a commit to drop.
        detail = "sync refused and applied nothing: dirty tree, invalid template.yml, or git failure"
        if cfg.template_ref:
            detail += f" (the bump to {cfg.template_ref} is already committed)"
        raise Failed(code, detail)

    _script(cfg, scripts, "stage_synced.py", "--json")
    target = cfg.template_ref or "the pinned ref"
    print("[INFO] staged the synced files. To commit exactly that set:")
    print(f'[INFO]   SKIP=check-managed-files git commit -m "chore: apply rhiza sync {target}"')
