#!/usr/bin/env python3
"""Generate the terminal-style dashboard in the GitHub profile README.

Test private aggregates locally without exposing the token in shell history:
    python scripts/generate_readme.py --prompt-token --dry-run
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

import requests


ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
CACHE_PATH = Path(os.getenv("PROFILE_STATS_CACHE", ROOT / ".cache/profile_stats.json"))
LIGHT_SVG_PATH = ROOT / "assets/profile-light.svg"
DARK_SVG_PATH = ROOT / "assets/profile-dark.svg"

USERNAME = "MrMajumder"
PANEL_WIDTH = 83
START_MARKER = "<!-- profile-dashboard:start -->"
END_MARKER = "<!-- profile-dashboard:end -->"
PLACEHOLDER = "—"

THEMES = {
    "light": {
        "background": "#f6f8fa",
        "border": "#d0d7de",
        "text": "#24292f",
        "muted": "#afb8c1",
        "prompt": "#1a7f37",
        "command": "#0969da",
        "section": "#8250df",
        "key": "#953800",
        "value": "#0a3069",
        "add": "#1a7f37",
        "delete": "#cf222e",
    },
    "dark": {
        "background": "#0d1117",
        "border": "#30363d",
        "text": "#c9d1d9",
        "muted": "#484f58",
        "prompt": "#7ee787",
        "command": "#79c0ff",
        "section": "#d2a8ff",
        "key": "#ffa657",
        "value": "#a5d6ff",
        "add": "#3fb950",
        "delete": "#f85149",
    },
}

StyledToken = tuple[str, str]
StyledLine = list[StyledToken]


@dataclass
class Stats:
    repositories: int | str = PLACEHOLDER
    contributed: int | str = PLACEHOLDER
    stars: int | str = PLACEHOLDER
    followers: int | str = PLACEHOLDER
    contributions_year: int | str = PLACEHOLDER
    commits: int | str = PLACEHOLDER
    additions: int | str = PLACEHOLDER
    deletions: int | str = PLACEHOLDER
    github_since: int | str = PLACEHOLDER
    private_aggregates: bool = False


@dataclass
class RepositoryTotals:
    branch_oid: str | None
    latest_authored_oid: str | None
    commits: int
    additions: int
    deletions: int


class GitHubClient:
    """Small GitHub API client that never logs request variables or response bodies."""

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "MrMajumder-profile-readme",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def rest(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(
            f"https://api.github.com{path}", params=params, timeout=30
        )
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub REST request failed ({response.status_code})")
        return response.json()

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub GraphQL request failed ({response.status_code})")
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError("GitHub GraphQL returned an error")
        return payload["data"]


def visible_width(value: str) -> int:
    width = 0
    regional_indicator_pending = False
    for character in value:
        codepoint = ord(character)
        if character == "\u200d" or unicodedata.combining(character):
            continue
        if 0xFE00 <= codepoint <= 0xFE0F:
            continue
        if 0x1F1E6 <= codepoint <= 0x1F1FF:
            if not regional_indicator_pending:
                width += 2
            regional_indicator_pending = not regional_indicator_pending
            continue
        regional_indicator_pending = False
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def token_text(tokens: StyledLine) -> str:
    return "".join(text for text, _class_name in tokens)


def value_tokens(value: str | StyledLine) -> StyledLine:
    return value if isinstance(value, list) else [(value, "value")]


def dotted_tokens(
    label: str,
    value: str | StyledLine,
    width: int = PANEL_WIDTH,
    leading_marker: bool = True,
) -> StyledLine:
    styled_value = value_tokens(value)
    plain_value = token_text(styled_value)
    marker_width = 2 if leading_marker else 0
    prefix_width = marker_width + visible_width(label) + 2
    remaining = width - prefix_width - visible_width(plain_value)
    tokens: StyledLine = []
    if leading_marker:
        tokens.append((". ", "muted"))
    tokens.append((f"{label}:", "key"))
    if remaining < 3:
        tokens.append((" ", "text"))
    else:
        tokens.append((" " + "." * (remaining - 1) + " ", "muted"))
    tokens.extend(styled_value)
    padding = width - visible_width(token_text(tokens))
    if padding > 0:
        tokens.append((" " * padding, "muted"))
    return tokens


def dotted_row(label: str, value: str, width: int = PANEL_WIDTH) -> str:
    return token_text(dotted_tokens(label, value, width)).rstrip()


def dotted_segment(label: str, value: str, width: int) -> str:
    return token_text(dotted_tokens(label, value, width, leading_marker=False))


def paired_tokens(
    left_label: str,
    left_value: str | StyledLine,
    right_label: str,
    right_value: str | StyledLine,
    left_width: int | None = None,
) -> StyledLine:
    if left_width is None:
        left_width = (PANEL_WIDTH - 5) // 2
    right_width = PANEL_WIDTH - 5 - left_width
    return [
        (". ", "muted"),
        *dotted_tokens(left_label, left_value, left_width, leading_marker=False),
        (" | ", "muted"),
        *dotted_tokens(right_label, right_value, right_width, leading_marker=False),
    ]


def paired_row(
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    left_width: int | None = None,
) -> str:
    return token_text(
        paired_tokens(
            left_label, left_value, right_label, right_value, left_width=left_width
        )
    ).rstrip()


def section_tokens(title: str, width: int = PANEL_WIDTH) -> StyledLine:
    prefix = f"- {title} "
    return [
        ("- ", "muted"),
        (title, "section"),
        (" " + "-" * max(0, width - visible_width(prefix)), "muted"),
    ]


def section_heading(title: str, width: int = PANEL_WIDTH) -> str:
    return token_text(section_tokens(title, width))


def number(value: int | str) -> str:
    return f"{value:,}" if isinstance(value, int) else value


def build_panel_tokens(stats: Stats) -> list[StyledLine]:
    if isinstance(stats.additions, int) and isinstance(stats.deletions, int):
        loc: StyledLine = [
            (number(stats.additions - stats.deletions), "value"),
            (" (", "text"),
            (f"{number(stats.additions)}++", "add"),
            (" | ", "muted"),
            (f"{number(stats.deletions)}--", "delete"),
            (")", "text"),
        ]
    else:
        loc = [(PLACEHOLDER, "value")]

    return [
        [
            ("mrmajumder@github", "prompt"),
            (":~$ ", "text"),
            ("basic_info", "command"),
        ],
        section_tokens("About Me"),
        dotted_tokens("Name", "Shafayat Hossain Majumder (mrmajumder)"),
        dotted_tokens("Current", "Associate Security Engineer @Canonical"),
        dotted_tokens(
            "Education.MASc", "Information Systems Security, Concordia University"
        ),
        dotted_tokens("Education.BSc", "Computer Science & Engineering, BUET"),
        dotted_tokens("Location", "Montreal, Quebec, Canada"),
        dotted_tokens("Origin", "Bangladesh"),
        [],
        section_tokens("Skills & Interests"),
        dotted_tokens(
            "Skills", "Vulnerability management, source code analysis, LLM workflows"
        ),
        dotted_tokens(
            "Interests", "Offensive security, reverse engineering, bug bounty"
        ),
        dotted_tokens("Languages", "Python, Bash, C/C++, JavaScript, Java"),
        dotted_tokens("Hobbies", "Food, chess, photography, gym"),
        dotted_tokens("Certifications", "ISC2 CC, BSCP (In progress)"),
        [],
        section_tokens("GitHub Stats"),
        paired_tokens(
            "Repositories",
            number(stats.repositories),
            "Stars earned",
            number(stats.stars),
        ),
        paired_tokens(
            "Contributions (1y)",
            number(stats.contributions_year),
            "Commits",
            number(stats.commits),
        ),
        paired_tokens(
            "LOC on GitHub", loc, "Since", str(stats.github_since), left_width=67
        ),
    ]


def build_panel(stats: Stats) -> list[str]:
    return [token_text(line).rstrip() for line in build_panel_tokens(stats)]


def render_svg(
    stats: Stats,
    theme_name: str,
) -> str:
    if theme_name not in THEMES:
        raise ValueError(f"Unknown SVG theme: {theme_name}")

    theme = THEMES[theme_name]
    panel = build_panel_tokens(stats)

    panel_font_size = 17
    panel_line_height = 22
    padding_x = 12
    padding_y = 29
    panel_x = padding_x
    panel_width = round(PANEL_WIDTH * 9.65)
    canvas_width = panel_x + panel_width + padding_x
    canvas_height = padding_y * 2 + (len(panel) - 1) * panel_line_height

    style_rules = "\n".join(
        [
            "text { white-space: pre; }",
            f".text {{ fill: {theme['text']}; }}",
            f".muted {{ fill: {theme['muted']}; }}",
            f".prompt {{ fill: {theme['prompt']}; font-weight: 700; }}",
            f".command {{ fill: {theme['command']}; font-weight: 700; }}",
            f".section {{ fill: {theme['section']}; font-weight: 700; }}",
            f".key {{ fill: {theme['key']}; }}",
            f".value {{ fill: {theme['value']}; }}",
            f".add {{ fill: {theme['add']}; font-weight: 700; }}",
            f".delete {{ fill: {theme['delete']}; font-weight: 700; }}",
        ]
    )

    panel_rows: list[str] = []
    for index, panel_row in enumerate(panel):
        y = padding_y + index * panel_line_height
        row_width = visible_width(token_text(panel_row))
        padded_panel_row = [*panel_row]
        if row_width < PANEL_WIDTH:
            padded_panel_row.append((" " * (PANEL_WIDTH - row_width), "muted"))
        spans = [
            f'<tspan class="{class_name}">{escape(text)}</tspan>'
            for text, class_name in padded_panel_row
        ]
        panel_rows.append(
            f'<text x="{panel_x}" y="{y}" class="text" '
            f'textLength="{panel_width}" lengthAdjust="spacing">'
            f'{"".join(spans)}</text>'
        )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{canvas_width}" height="{canvas_height}" '
                f'viewBox="0 0 {canvas_width} {canvas_height}" role="img" '
                f'aria-labelledby="title description">'
            ),
            "<title id=\"title\">Shafayat Hossain Majumder's GitHub profile</title>",
            (
                '<desc id="description">Terminal-style profile summary with skills, '
                "interests, and GitHub statistics.</desc>"
            ),
            "<style>",
            style_rules,
            "</style>",
            (
                f'<rect x="1" y="1" width="{canvas_width - 2}" '
                f'height="{canvas_height - 2}" rx="16" '
                f'fill="{theme["background"]}" stroke="{theme["border"]}"/>'
            ),
            (
                f'<g font-family="Consolas, \'Liberation Mono\', Menlo, monospace" '
                f'font-size="{panel_font_size}" xml:space="preserve">'
            ),
            *panel_rows,
            "</g>",
            "</svg>",
            "",
        ]
    )


def dashboard_picture() -> str:
    asset_base = "./assets"
    return "\n".join(
        [
            "<picture>",
            (
                '  <source media="(prefers-color-scheme: dark)" '
                f'srcset="{asset_base}/profile-dark.svg">'
            ),
            (
                f'  <img src="{asset_base}/profile-light.svg" width="100%" '
                'alt="Terminal-style profile for Shafayat Hossain Majumder">'
            ),
            "</picture>",
        ]
    )


def write_if_changed(path: Path, content: str) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def replace_dashboard(readme: str, dashboard: str) -> str:
    if START_MARKER not in readme or END_MARKER not in readme:
        raise ValueError("README dashboard markers are missing")
    before, remainder = readme.split(START_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    return f"{before}{START_MARKER}\n{dashboard}\n{END_MARKER}{after}"


def public_stats(client: GitHubClient, username: str) -> Stats:
    user = client.rest(f"/users/{username}")
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = client.rest(
            f"/users/{username}/repos",
            {"per_page": 100, "page": page, "type": "owner"},
        )
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return Stats(
        repositories=int(user["public_repos"]),
        stars=sum(int(repo["stargazers_count"]) for repo in repos),
        followers=int(user["followers"]),
        github_since=int(str(user["created_at"])[:4]),
    )


REPOSITORY_QUERY = """
query($login: String!, $cursor: String, $contributed: Boolean!) {
  user(login: $login) {
    id
    createdAt
    followers { totalCount }
    contributionsCollection {
      contributionCalendar { totalContributions }
    }
    repositories(
      first: 100
      after: $cursor
      ownerAffiliations: OWNER
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) @skip(if: $contributed) {
      nodes {
        id
        isPrivate
        stargazerCount
        defaultBranchRef { target { ... on Commit { oid } } }
      }
      pageInfo { hasNextPage endCursor }
    }
    repositoriesContributedTo(
      first: 100
      after: $cursor
      includeUserRepositories: false
      contributionTypes: [COMMIT]
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) @include(if: $contributed) {
      nodes {
        id
        isPrivate
        stargazerCount
        defaultBranchRef { target { ... on Commit { oid } } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


HISTORY_QUERY = """
query($repositoryId: ID!, $userId: ID!, $cursor: String) {
  node(id: $repositoryId) {
    ... on Repository {
      defaultBranchRef {
        target {
          ... on Commit {
            history(first: 100, after: $cursor, author: {id: $userId}) {
              nodes { oid additions deletions }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    }
  }
}
"""


def fetch_repository_connection(
    client: GitHubClient, username: str, contributed: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cursor: str | None = None
    repositories: list[dict[str, Any]] = []
    user_data: dict[str, Any] = {}
    while True:
        data = client.graphql(
            REPOSITORY_QUERY,
            {"login": username, "cursor": cursor, "contributed": contributed},
        )
        user_data = data["user"]
        connection_name = "repositoriesContributedTo" if contributed else "repositories"
        connection = user_data[connection_name]
        # GitHub may return null placeholders for deleted repositories or private
        # contributions that the token cannot inspect. They must not become part
        # of the published aggregate or abort the entire update.
        repositories.extend(node for node in connection["nodes"] if node is not None)
        if not connection["pageInfo"]["hasNextPage"]:
            return user_data, repositories
        cursor = connection["pageInfo"]["endCursor"]


def load_cache(path: Path) -> dict[str, RepositoryTotals]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    entries: dict[str, RepositoryTotals] = {}
    for repository_id, values in payload.get("repositories", {}).items():
        try:
            entries[repository_id] = RepositoryTotals(**values)
        except (TypeError, ValueError):
            continue
    return entries


def save_cache(path: Path, cache: dict[str, RepositoryTotals]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "repositories": {key: asdict(value) for key, value in cache.items()},
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def repository_totals(
    client: GitHubClient,
    repository: dict[str, Any],
    user_id: str,
    cached: RepositoryTotals | None,
) -> RepositoryTotals:
    repository_id = repository["id"]
    branch = repository.get("defaultBranchRef")
    branch_oid = branch["target"]["oid"] if branch else None
    if cached and cached.branch_oid == branch_oid:
        return cached
    if not branch_oid:
        return RepositoryTotals(None, None, 0, 0, 0)

    cursor: str | None = None
    new_commits: list[dict[str, Any]] = []
    found_cached_commit = False
    while True:
        data = client.graphql(
            HISTORY_QUERY,
            {"repositoryId": repository_id, "userId": user_id, "cursor": cursor},
        )
        history = data["node"]["defaultBranchRef"]["target"]["history"]
        for commit in history["nodes"]:
            if cached and cached.latest_authored_oid == commit["oid"]:
                found_cached_commit = True
                break
            new_commits.append(commit)
        if found_cached_commit or not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]
        time.sleep(0.05)

    if cached and found_cached_commit:
        commits = cached.commits + len(new_commits)
        additions = cached.additions + sum(int(item["additions"]) for item in new_commits)
        deletions = cached.deletions + sum(int(item["deletions"]) for item in new_commits)
        latest = new_commits[0]["oid"] if new_commits else cached.latest_authored_oid
    else:
        commits = len(new_commits)
        additions = sum(int(item["additions"]) for item in new_commits)
        deletions = sum(int(item["deletions"]) for item in new_commits)
        latest = new_commits[0]["oid"] if new_commits else None

    return RepositoryTotals(branch_oid, latest, commits, additions, deletions)


def private_stats(client: GitHubClient, username: str, cache_path: Path) -> Stats:
    user, owned = fetch_repository_connection(client, username, contributed=False)
    _, contributed = fetch_repository_connection(client, username, contributed=True)
    repositories = {repo["id"]: repo for repo in [*owned, *contributed]}
    old_cache = load_cache(cache_path)
    new_cache: dict[str, RepositoryTotals] = {}

    for repository_id, repository in repositories.items():
        new_cache[repository_id] = repository_totals(
            client, repository, user["id"], old_cache.get(repository_id)
        )
        # Persist partial progress so a first full scan can resume after rate limits.
        save_cache(cache_path, new_cache)

    return Stats(
        repositories=len(owned),
        contributed=len({repo["id"] for repo in contributed}),
        stars=sum(
            int(repo["stargazerCount"]) for repo in owned if not repo["isPrivate"]
        ),
        followers=int(user["followers"]["totalCount"]),
        contributions_year=int(
            user["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        ),
        commits=sum(item.commits for item in new_cache.values()),
        additions=sum(item.additions for item in new_cache.values()),
        deletions=sum(item.deletions for item in new_cache.values()),
        github_since=int(user["createdAt"][:4]),
        private_aggregates=True,
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--readme", type=Path, default=README_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--light-svg", type=Path, default=LIGHT_SVG_PATH)
    parser.add_argument("--dark-svg", type=Path, default=DARK_SVG_PATH)
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="securely prompt for a PAT instead of reading PROFILE_STATS_TOKEN",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and print aggregate statistics without changing README.md",
    )
    parser.add_argument(
        "--require-private-token",
        action="store_true",
        help="fail instead of generating public-only statistics when no token is set",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    token = (
        getpass.getpass("Fine-grained GitHub PAT: ").strip()
        if args.prompt_token
        else os.getenv("PROFILE_STATS_TOKEN")
    )
    if args.prompt_token and not token:
        print("A token was not provided", file=sys.stderr)
        return 2
    if args.require_private_token and not token:
        print("PROFILE_STATS_TOKEN is required", file=sys.stderr)
        return 2

    client = GitHubClient(token)
    try:
        stats = (
            private_stats(client, args.username, args.cache)
            if token
            else public_stats(client, args.username)
        )
        if args.dry_run:
            print(json.dumps(asdict(stats), indent=2))
            return 0
        changed = write_if_changed(
            args.light_svg,
            render_svg(stats, "light"),
        )
        changed = (
            write_if_changed(
                args.dark_svg,
                render_svg(stats, "dark"),
            )
            or changed
        )
        current = args.readme.read_text(encoding="utf-8")
        updated = replace_dashboard(current, dashboard_picture())
        if updated != current:
            args.readme.write_text(updated, encoding="utf-8", newline="\n")
            changed = True
        if changed:
            print("README dashboard updated")
        else:
            print("README dashboard is already current")
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        print(f"Generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
