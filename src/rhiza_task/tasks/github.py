"""The GitHub helpers: github.mk, as tasks.

Six thin wrappers over ``gh``, and the reason the fragment could not retire with the
other ten: ``github`` is in the ``github-project`` profile, so a consumer on the flagship
profile would have lost ``make view-prs``.

Nothing here is a gate. No aggregate names them, no workflow invokes them, and they
produce a table for a human at a prompt -- which is why the gh templates are carried over
character for character rather than reimplemented against ``--json``. Reproducing
``timeago`` and gh's colour handling in Python would be a worse table and a new thing to
maintain.

Two shapes from the fragment disappear:

``require-gh`` and ``gh-install`` were both "is gh installed?", spelled twice because make
has no way to say it once -- one hard-failing as a prerequisite, one warning as a target a
human runs. :class:`~rhiza_task.spec.Guard`'s ``tool`` field says it once, and the
outcome is a skip with the install URL attached. ``gh-install`` as a *task* goes: it never
installed anything, and ``rhiza-task doctor`` is where "what is missing on this machine"
belongs.

``FORGE_TYPE`` goes too. github.mk computes it at parse time from the presence of
``.github/workflows`` or ``.gitlab-ci.yml`` and then no target in the fragment -- or in
any other fragment -- ever reads it.
"""

from __future__ import annotations

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import capture, tool

SECTION = "GitHub Helpers"

HAVE_GH = Guard(
    tool="gh",
    reason="gh not found; install from https://github.com/cli/cli#installation",
)
"""The single spelling of ``require-gh``, shared by every task in this module."""

RELEASE_WORKFLOW_JQ = '.[] | select(.name | test("release";"i")) | .name'
"""github.mk's own filter: the first workflow whose name mentions "release", any case."""


def _header(*labels: str) -> str:
    """Build the bold header row of a gh table template.

    Args:
        *labels: Column headings, in order.

    Returns:
        A ``{{tablerow ...}}`` action rendering them in bold.
    """
    cells = " ".join(f'(printf "{label}" | color "bold")' for label in labels)
    return "{{tablerow " + cells + "}}"


PR_TEMPLATE = _header("NUM", "TITLE", "AUTHOR", "BRANCH", "UPDATED") + (
    "{{range .}}{{tablerow "
    '(printf "#%v" .number | color "green") '
    ".title "
    '(.author.login | color "cyan") '
    '(.headRefName | color "yellow") '
    '(timeago .updatedAt | color "white")'
    "}}{{end}}"
)

ISSUE_TEMPLATE = _header("NUM", "TITLE", "AUTHOR", "LABELS", "UPDATED") + (
    "{{range .}}{{tablerow "
    '(printf "#%v" .number | color "green") '
    ".title "
    '(.author.login | color "cyan") '
    '(pluck "name" .labels | join ", " | color "yellow") '
    '(timeago .updatedAt | color "white")'
    "}}{{end}}"
)

FAILED_RUN_TEMPLATE = _header("STATUS", "NAME", "BRANCH", "EVENT", "TIME") + (
    "{{range .}}{{tablerow "
    '(printf "%s" .conclusion | color "red") '
    ".name "
    '(.headBranch | color "cyan") '
    '(.event | color "yellow") '
    '(timeago .createdAt | color "white")'
    "}}{{end}}"
)

WORKFLOW_RUN_TEMPLATE = _header("STATUS", "CONCLUSION", "TITLE", "EVENT", "TIME") + (
    "{{range .}}{{tablerow "
    '(printf "%s" .status | color "cyan") '
    '(printf "%s" (or .conclusion "—") | color '
    '(or (and (eq .conclusion "success") "green") (and (eq .conclusion "failure") "red") "yellow")) '
    ".displayTitle "
    '(.event | color "yellow") '
    '(timeago .createdAt | color "white")'
    "}}{{end}}"
)

WHOAMI_TEMPLATE = (
    "{{range $host, $accounts := .hosts}}{{range $accounts}}{{if .active}}"
    r'  {{printf "✓" | color "green"}} Logged in to {{$host}} account '
    r'{{.login | color "bold"}} ({{.tokenSource}}){{"\n"}}'
    r'  Active account: {{printf "true" | color "green"}}{{"\n"}}'
    r'  Git operations protocol: {{.gitProtocol | color "yellow"}}{{"\n"}}'
    r'  Token scopes: {{.scopes | color "yellow"}}{{"\n"}}'
    "{{end}}{{end}}{{end}}"
)

RELEASE_TEMPLATE = (
    r'  Tag:          {{.tagName | color "green"}}{{"\n"}}'
    r'  Name:         {{.name}}{{"\n"}}'
    r'  Author:       {{.author.login}}{{"\n"}}'
    r'  Published:    {{timeago .publishedAt}}{{"\n"}}'
    r"  Status:       {{if .isDraft}}"
    r'{{printf "Draft" | color "yellow"}}'
    r"{{else if .isPrerelease}}"
    r'{{printf "Pre-release" | color "yellow"}}'
    r"{{else}}"
    r'{{printf "Published" | color "green"}}'
    r'{{end}}{{"\n"}}'
    r'  URL:          {{.url}}{{"\n"}}'
)


@task("view-prs", "list open pull requests", section=SECTION, guards=(HAVE_GH,))
def view_prs(cfg: Config) -> None:
    """List the repository's open pull requests as a table.

    Args:
        cfg: The resolved config.
    """
    print("[INFO] Open Pull Requests:")
    tool(
        "gh",
        "pr",
        "list",
        "--json",
        "number,title,author,headRefName,updatedAt",
        "--template",
        PR_TEMPLATE,
        cwd=cfg.root,
    )


@task("view-issues", "list open issues", section=SECTION, guards=(HAVE_GH,))
def view_issues(cfg: Config) -> None:
    """List the repository's open issues as a table.

    Args:
        cfg: The resolved config.
    """
    print("[INFO] Open Issues:")
    tool(
        "gh",
        "issue",
        "list",
        "--json",
        "number,title,author,labels,updatedAt",
        "--template",
        ISSUE_TEMPLATE,
        cwd=cfg.root,
    )


@task("failed-workflows", "list recent failing workflow runs", section=SECTION, guards=(HAVE_GH,))
def failed_workflows(cfg: Config) -> None:
    """Show the ten most recent runs that concluded in failure.

    Args:
        cfg: The resolved config.
    """
    print("[INFO] Recent Failing Workflow Runs:")
    tool(
        "gh",
        "run",
        "list",
        "--limit",
        "10",
        "--status",
        "failure",
        "--json",
        "conclusion,name,headBranch,event,createdAt",
        "--template",
        FAILED_RUN_TEMPLATE,
        cwd=cfg.root,
    )


@task("workflow-status", "show recent runs for the release workflow", section=SECTION, guards=(HAVE_GH,))
def workflow_status(cfg: Config) -> None:
    """Find the release workflow by name, then show its five most recent runs.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When no workflow's name mentions "release".
    """
    listing = capture("gh", "workflow", "list", "--json", "name,id", "--jq", RELEASE_WORKFLOW_JQ, cwd=cfg.root)
    workflow = next((line.strip() for line in listing.splitlines() if line.strip()), "")
    if not workflow:
        raise Skip("no release workflow in this repository")

    print(f"[INFO] Release workflow: {workflow}")
    tool(
        "gh",
        "run",
        "list",
        "--workflow",
        workflow,
        "--limit",
        "5",
        "--json",
        "status,conclusion,headBranch,event,createdAt,displayTitle,url",
        "--template",
        WORKFLOW_RUN_TEMPLATE,
        cwd=cfg.root,
    )


@task("latest-release", "show information about the latest GitHub release", section=SECTION, guards=(HAVE_GH,))
def latest_release(cfg: Config) -> None:
    """Print tag, author, publication time and status for the newest release.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the repository has published no release.
    """
    # The probe is `capture`, not a second `gh release view`: it is the one call whose
    # *value* matters rather than its output, and capture returns "" for a non-zero exit,
    # which is exactly github.mk's `if gh release view ... >/dev/null 2>&1` branch.
    if not capture("gh", "release", "view", "--json", "tagName", "--jq", ".tagName", cwd=cfg.root):
        raise Skip("no releases in this repository")

    print("[INFO] Latest release:")
    tool(
        "gh",
        "release",
        "view",
        "--json",
        "tagName,name,publishedAt,url,isDraft,isPrerelease,author",
        "--template",
        RELEASE_TEMPLATE,
        cwd=cfg.root,
    )


@task("whoami", "check github auth status", section=SECTION, guards=(HAVE_GH,))
def whoami(cfg: Config) -> None:
    """Report which account gh is authenticated as, and with what scopes.

    Args:
        cfg: The resolved config.
    """
    print("[INFO] GitHub Authentication Status:")
    tool(
        "gh",
        "auth",
        "status",
        "--hostname",
        "github.com",
        "--json",
        "hosts",
        "--template",
        WHOAMI_TEMPLATE,
        cwd=cfg.root,
    )
