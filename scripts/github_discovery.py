#!/usr/bin/env python3
"""Stateful GitHub discovery primitives for an autonomous search agent.

The model owns the search strategy. This module owns GitHub retrieval,
repository inspection, deduplication, and the bounded session ledger.

Experience persistence: `learn` appends or merges a lesson into a Markdown
file that survives across sessions, so a fresh session can inherit prior
search vocabulary, judgment standards, and pitfalls instead of starting from
scratch. `experience` prints the digest of that file. The file is optional;
a session only reads/writes it when an `--experience-file` is configured.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
USER_AGENT = "github-idea-finder/0.2"
STATE_VERSION = 4
EXPERIENCE_VERSION = 1
# Experience entries that share the same normalized key are considered the
# same lesson; learn() merges by replacing the previous entry so the file does
# not grow without bound.
EXPERIENCE_ENTRY_SEPARATOR = "\n---\n"
# Only accept the two path segments that identify a repository. This avoids
# treating /issues, /pull, /blob, and GitHub asset URLs as repositories.
GITHUB_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"(?=$|[\s<>'\"`),.;!?\]])",
    re.IGNORECASE,
)
NON_REPOSITORY_OWNERS = {"user-attachments", "sponsors", "topics", "features", "marketplace"}


def dotenv_github_token(paths: tuple[Path, ...]) -> str | None:
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() != "GITHUB_TOKEN":
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if value:
                return value
    return None


def github_token() -> str | None:
    configured = os.getenv("GITHUB_TOKEN", "").strip()
    if configured:
        return configured
    return dotenv_github_token((Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"))


def request_json(path: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(API_ROOT + path, headers=headers), timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("message", body)
        except json.JSONDecodeError:
            message = body
        if exc.code in (403, 429):
            raise RuntimeError("GitHub API rate limit reached. Set GITHUB_TOKEN and retry.") from exc
        raise RuntimeError(f"GitHub API error {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("GitHub request timed out.") from exc


def build_search_query(query: str) -> str:
    terms = query.strip()
    if not terms:
        raise ValueError("query must not be empty")
    if re.search(r"(?:^|\s)(?:in|topic):[^\s]+", terms):
        return terms
    return f"{terms} in:name,description"


def build_search_params(search_query: str, args: argparse.Namespace) -> dict[str, str | int]:
    params: dict[str, str | int] = {"q": search_query, "per_page": args.fetch_limit}
    if args.sort != "best-match":
        params["sort"] = args.sort
        params["order"] = "desc"
    return params


def days_since(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (dt.datetime.now(dt.timezone.utc) - parsed).days)


def health_score(repo: dict[str, Any]) -> int:
    if repo.get("archived"):
        return 5
    score = 25
    score += min(25, round(math.log10(int(repo.get("stargazers_count") or repo.get("stars") or 0) + 1) * 8))
    score += min(12, round(math.log10(int(repo.get("forks_count") or repo.get("forks") or 0) + 1) * 4))
    age = days_since(repo.get("pushed_at"))
    if age is not None:
        score += 20 if age <= 30 else 14 if age <= 90 else 7 if age <= 365 else 0
    license_info = repo.get("license") or {}
    if isinstance(license_info, dict) and license_info.get("spdx_id") not in (None, "NOASSERTION"):
        score += 8
    if repo.get("has_wiki") or repo.get("has_issues"):
        score += 5
    return min(100, score)


RESOURCE_MARKERS = ("awesome", "tutorial", "guide", "handbook", "curated", "collection", "examples", "prompts", "resources", "list")
COMPONENT_MARKERS = ("skill", "workflow", "plugin", "mcp", "router", "toolkit", "sdk", "library", "framework", "integration", "tips", "prompts", "resources")


def candidate_kind(repo: dict[str, Any]) -> str:
    text = " ".join([repo.get("full_name") or "", repo.get("name") or "", repo.get("description") or "", " ".join(repo.get("topics") or [])]).lower()
    name = " ".join([repo.get("full_name") or "", repo.get("name") or ""]).lower()
    if any(re.search(rf"\b{re.escape(marker)}s?\b", text) for marker in RESOURCE_MARKERS):
        return "resource-or-list"
    if any(re.search(rf"(?:^|[ /_.-]){re.escape(marker)}s?(?:$|[ /_.-])", name) for marker in COMPONENT_MARKERS):
        return "component-or-extension"
    if any(re.search(rf"\b{re.escape(marker)}s?\b", (repo.get("description") or "").lower()) for marker in ("library", "framework", "sdk", "plugin", "extension", "skill collection")):
        return "component-or-extension"
    return "uncertain"


def decode_readme(payload: dict[str, Any]) -> str:
    content = payload.get("content", "")
    if not content:
        return ""
    try:
        return base64.b64decode(content).decode("utf-8", errors="replace")
    except (ValueError, binascii.Error):
        return ""


def enrich(repo: dict[str, Any], token: str | None, include_readme: bool) -> dict[str, Any]:
    full_name = repo["full_name"]
    detail = request_json(f"/repos/{urllib.parse.quote(full_name, safe='/')}", token)
    license_info = detail.get("license") or {}
    result = {
        "full_name": detail.get("full_name", full_name), "html_url": detail.get("html_url"),
        "description": detail.get("description"), "topics": detail.get("topics", []),
        "language": detail.get("language"), "stars": detail.get("stargazers_count", 0),
        "forks": detail.get("forks_count", 0), "open_issues": detail.get("open_issues_count", 0),
        "license": license_info.get("spdx_id"), "license_name": license_info.get("name"),
        "archived": bool(detail.get("archived")), "default_branch": detail.get("default_branch"),
        "pushed_at": detail.get("pushed_at"), "created_at": detail.get("created_at"),
        "has_readme": None, "readme_excerpt": None, "health_score": health_score(detail),
    }
    if include_readme:
        try:
            readme = decode_readme(request_json(f"/repos/{urllib.parse.quote(full_name, safe='/')}/readme", token))
            result["has_readme"] = bool(readme)
            result["readme_excerpt"] = readme[:6000] if readme else None
        except RuntimeError:
            result["has_readme"] = False
    return result


def empty_state(idea: str = "") -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "idea": idea,
        "round": 0,
        "used_queries": [],
        "seen_repositories": [],
        "inspected_repositories": [],
        "candidates": {},
        "discovered_terms": [],
        "related_repositories": [],
        "experience_file": None,
        "budget": {"max_rounds": 4, "max_searches": 30, "max_inspections": 50},
    }


def load_state(path: Path, idea: str = "") -> dict[str, Any]:
    if not path.exists():
        return empty_state(idea)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported session version: {state.get('version')}")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def default_experience_file() -> Path:
    return Path.cwd() / "work" / "github-idea-experience.md"


def normalize_experience_key(text: str) -> str:
    """Normalize a lesson key so learn() can detect duplicates regardless of
    quoting or casing. 'which queries work well' and 'which queries work well!'
    collapse to the same entry; a very short or non-tagged lesson is not
    considered a heading and is kept as-is (matched by its text)."""
    return re.sub(r"[^a-z0-9]+", " ", text.strip().lower()).strip()


def experience_file_entries(path: Path) -> list[dict[str, Any]]:
    """Parse an experience file back into a list of entries.

    The file is a Markdown document. An entry is:
    - a heading line ("## domain") that becomes `domain`, plus
    - a "Key: value" line under it that becomes the entry key, and
    - a body of remaining lines.
    Headings are used as grouping labels; the actual dedup key is the
    normalized `key`. `text` is always the body only (no heading or Key
    line), so it round-trips cleanly through write_experience_file. A block
    that only has a heading (no key line) is treated as a section header and
    is preserved as `header_only`.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = text.split(EXPERIENCE_ENTRY_SEPARATOR)
    entries: list[dict[str, Any]] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        domain = None
        for line in lines:
            m = re.match(r"^#{1,6}\s+(.+)$", line)
            if m:
                domain = m.group(1).strip()
            else:
                break
        key_line = next((line for line in lines if line.lower().startswith("key:")), None)
        key = key_line.split(":", 1)[1].strip() if key_line else None
        # Body is everything after the heading and key lines.
        body_lines: list[str] = []
        for line in lines:
            if re.match(r"^#{1,6}\s+", line) or line.lower().startswith("key:"):
                continue
            body_lines.append(line)
        if not key and not domain:
            # A stray block without a heading or key is not a valid entry.
            continue
        entries.append({
            "domain": domain,
            "key": key,
            "key_norm": normalize_experience_key(key) if key else "",
            "text": "\n".join(body_lines),
            "header_only": not key,
        })
    return entries


def write_experience_file(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for entry in entries:
        block: list[str] = []
        if entry.get("domain"):
            block.append(f"## {entry['domain']}")
        if entry.get("key"):
            block.append(f"Key: {entry['key']}")
        block.append(entry.get("text", ""))
        lines.append("\n".join(line for line in block if line))
    path.write_text(("\n\n" + EXPERIENCE_ENTRY_SEPARATOR + "\n\n").join(lines).strip() + "\n", encoding="utf-8")


def learn_experience(path: Path, domain: str | None, key: str, body: str) -> dict[str, Any]:
    """Merge a new lesson into the experience file.

    Deduplication is by the normalized key: a later entry with the same key
    replaces the previous one (so the file converges instead of growing).
    Header-only blocks (sections) are preserved in place. The returned dict
    describes what changed and the entry's normalized key so the caller (or
    the Agent) can reason about the write."""
    entries = experience_file_entries(path)
    key_norm = normalize_experience_key(key)
    existing_idx = None
    for idx, entry in enumerate(entries):
        if not entry["header_only"] and entry["key_norm"] == key_norm:
            existing_idx = idx
            break
    entry = {"domain": domain, "key": key, "key_norm": key_norm, "text": body}
    if existing_idx is not None:
        entries[existing_idx] = entry
        changed = "replaced"
    else:
        entries.append(entry)
        changed = "added"
    write_experience_file(path, entries)
    return {"key": key, "key_norm": key_norm, "changed": changed, "path": str(path)}


def experience_digest(path: Path) -> dict[str, Any]:
    """Return a compact summary of the experience file for the Agent to read
    before starting a new search session. Includes per-domain counts and the
    raw Markdown (bounded) so a fresh session can inherit prior lessons."""
    entries = experience_file_entries(path)
    domains: dict[str, int] = {}
    for entry in entries:
        if not entry["header_only"] and entry["domain"]:
            domains[entry["domain"]] = domains.get(entry["domain"], 0) + 1
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
    return {
        "path": str(path),
        "entry_count": sum(1 for e in entries if not e["header_only"]),
        "domains": domains,
        "content": text,
    }


def repo_links(readme: str | None) -> list[str]:
    if not readme:
        return []
    found: list[str] = []
    for match in GITHUB_REPO_RE.finditer(readme):
        name = match.group(1).rstrip("`'\"),.;!?]")
        if name.lower().endswith(".git"):
            name = name[:-4]
        owner = name.split("/", 1)[0].lower()
        if owner in NON_REPOSITORY_OWNERS:
            continue
        if name not in found:
            found.append(name)
    return found


def add_unique(values: list[str], additions: list[str]) -> None:
    for value in additions:
        if value and value not in values:
            values.append(value)


def route_queries(queries: list[str]) -> list[str]:
    planned: list[str] = []
    for query in queries:
        planned.append(query)
        if not re.search(r"(?:^|\s)(?:in|topic):[^\s]+", query):
            planned.append(f"{query} in:readme")
    return list(dict.fromkeys(planned))


def search(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state_file)
    state = load_state(state_path, args.idea or "")
    queries = [query.strip() for query in args.query if query.strip()]
    queries.extend(f"topic:{topic.strip()}" for topic in args.topic if topic.strip())
    if not queries:
        raise ValueError("search requires at least one --query or --topic")
    queries = route_queries(list(dict.fromkeys(queries)))
    queries = [query for query in queries if query not in state["used_queries"]]
    if not queries:
        raise ValueError("all requested queries were already used in this session")
    if state["round"] >= state["budget"]["max_rounds"]:
        raise ValueError("search session reached max_rounds")
    if len(state["used_queries"]) + len(queries) > state["budget"]["max_searches"]:
        raise ValueError("search batch exceeds session search budget")
    token = github_token()

    seen = set(state["seen_repositories"])
    new_candidates: dict[str, dict[str, Any]] = {}
    route_stats: list[dict[str, Any]] = []
    for query in queries:
        search_query = build_search_query(query)
        params = build_search_params(search_query, args)
        payload = request_json(
            "/search/repositories?" + urllib.parse.urlencode(params),
            token,
        )
        route_new = 0
        for rank, item in enumerate(payload.get("items", [])):
            full_name = item.get("full_name")
            if not full_name:
                continue
            if full_name in state["candidates"]:
                candidate = state["candidates"][full_name]
                add_unique(candidate.setdefault("discovered_by", []), [query])
                continue
            if full_name in new_candidates:
                add_unique(new_candidates[full_name].setdefault("discovered_by", []), [query])
                continue
            new_candidates[full_name] = {
                "full_name": full_name,
                "html_url": item.get("html_url"),
                "description": item.get("description"),
                "language": item.get("language"),
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "topics": item.get("topics", []),
                "candidate_kind": candidate_kind(item),
                "discovered_by": [query],
                "route_rank": rank,
            }
            route_new += 1
        route_stats.append({"query": query, "returned": len(payload.get("items", [])), "new": route_new})
        state["used_queries"].append(query)

    for full_name, candidate in new_candidates.items():
        state["candidates"][full_name] = candidate
    add_unique(state["seen_repositories"], list(new_candidates))
    state["round"] += 1
    save_state(state_path, state)
    return {
        "action": "search",
        "round": state["round"],
        "new_repositories": list(new_candidates),
        "candidates": list(new_candidates.values()),
        "route_stats": route_stats,
        "session_file": str(state_path),
        "next_action": "inspect selected repositories, then derive the next query batch from their evidence",
    }


def inspect(args: argparse.Namespace) -> dict[str, Any]:
    token = github_token()
    state_path = Path(args.state_file)
    state = load_state(state_path)
    names = list(dict.fromkeys(name.strip() for name in args.repo if name.strip()))
    if not names:
        raise ValueError("inspect requires at least one --repo")
    already_inspected = set(state.get("inspected_repositories", []))
    pending = [name for name in names if name not in already_inspected]
    remaining = state["budget"]["max_inspections"] - len(already_inspected)
    if len(pending) > remaining:
        raise ValueError("inspection batch exceeds session inspection budget")

    inspected: list[dict[str, Any]] = []
    related: list[str] = []
    for full_name in names:
        if full_name in already_inspected:
            inspected.append({"full_name": full_name, "inspection_skipped": "already inspected"})
            continue
        try:
            result = enrich({"full_name": full_name}, token, include_readme=True)
            result["candidate_kind"] = candidate_kind(result)
            result["related_repositories"] = repo_links(result.get("readme_excerpt"))
            related.extend(result["related_repositories"])
            state["candidates"][full_name] = result
            state["inspected_repositories"].append(full_name)
            inspected.append(result)
        except RuntimeError as exc:
            inspected.append({"full_name": full_name, "inspection_error": str(exc)})

    add_unique(state["related_repositories"], related)
    add_unique(state["seen_repositories"], names)
    save_state(state_path, state)
    return {
        "action": "inspect",
        "inspected": inspected,
        "related_repositories": list(dict.fromkeys(related)),
        "session_file": str(state_path),
        "next_action": "search related repositories or newly discovered terminology before final ranking",
    }


def session(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state_file)
    state = load_state(state_path, args.idea or "")
    if args.idea:
        state["idea"] = args.idea
    if args.experience_file:
        state["experience_file"] = str(Path(args.experience_file).resolve())
    if args.max_rounds:
        state["budget"]["max_rounds"] = args.max_rounds
    if args.max_searches:
        state["budget"]["max_searches"] = args.max_searches
    if args.max_inspections:
        state["budget"]["max_inspections"] = args.max_inspections
    save_state(state_path, state)
    result: dict[str, Any] = {"action": "session", "state": state, "session_file": str(state_path)}
    if state.get("experience_file"):
        result["experience"] = experience_digest(Path(state["experience_file"]))
    return result


def experience(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.experience_file) if args.experience_file else default_experience_file()
    return {"action": "experience", "digest": experience_digest(path)}


def learn(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.experience_file) if args.experience_file else default_experience_file()
    result = learn_experience(path, args.domain or None, args.key, args.body)
    return {"action": "learn", "learned": result, "digest": experience_digest(path)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="action", required=True)

    session_parser = sub.add_parser("session")
    session_parser.add_argument("--state-file", required=True)
    session_parser.add_argument("--idea")
    session_parser.add_argument("--experience-file", help="Markdown file that accumulates cross-session lessons (default: work/github-idea-experience.md)")
    session_parser.add_argument("--max-rounds", type=int)
    session_parser.add_argument("--max-searches", type=int)
    session_parser.add_argument("--max-inspections", type=int)

    experience_parser = sub.add_parser("experience", help="Show the accumulated experience digest before starting a session")
    experience_parser.add_argument("--experience-file")

    learn_parser = sub.add_parser("learn", help="Append or merge one lesson into the experience file")
    learn_parser.add_argument("--experience-file")
    learn_parser.add_argument("--domain", help="Section heading, e.g. 'ai-agent-desktop'")
    learn_parser.add_argument("--key", required=True, help="Short stable label for the lesson, used for dedup")
    learn_parser.add_argument("--body", required=True, help="The lesson body the Agent writes")

    search_parser = sub.add_parser("search")
    search_parser.add_argument("--state-file", required=True)
    search_parser.add_argument("--idea")
    search_parser.add_argument("--query", action="append", default=[])
    search_parser.add_argument("--topic", action="append", default=[])
    search_parser.add_argument("--fetch-limit", type=int, default=30)
    search_parser.add_argument("--sort", choices=("best-match", "stars", "updated"), default="best-match")

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--state-file", required=True)
    inspect_parser.add_argument("--repo", action="append", default=[])
    return root


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = {"ok": True, **globals()[args.action](args)}
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        result = {"ok": False, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
