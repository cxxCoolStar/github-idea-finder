#!/usr/bin/env python3
"""Search GitHub repositories from product-oriented queries.

The script deliberately keeps semantic ranking in the calling Skill. It handles
repeatable API retrieval, enrichment, de-duplication, and evidence formatting.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

API_ROOT = "https://api.github.com"
USER_AGENT = "github-idea-finder/0.1"


def dotenv_github_token(paths: Iterable[Path]) -> str | None:
    """Read only GITHUB_TOKEN from simple .env files without mutating the process."""
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
    """Prefer the process environment, then local .env files, without logging secrets."""
    configured = os.getenv("GITHUB_TOKEN", "").strip()
    if configured:
        return configured
    return dotenv_github_token(
        (
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[1] / ".env",
        )
    )


def request_json(path: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(API_ROOT + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("message", body)
        except json.JSONDecodeError:
            message = body
        if exc.code in (403, 429):
            raise RuntimeError(
                "GitHub API rate limit reached. Set GITHUB_TOKEN and retry."
            ) from exc
        raise RuntimeError(f"GitHub API error {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub: {exc.reason}") from exc
    except TimeoutError as exc:
        # A single slow README/detail response should be handled by the
        # enrichment caller instead of leaking a traceback to the user.
        raise RuntimeError("GitHub request timed out.") from exc


def build_search_query(
    query: str,
    language: str | None = None,
    min_stars: int | None = None,
    updated_within: int | None = None,
    search_scope: str = "name,description",
) -> str:
    terms = query.strip()
    if not terms:
        raise ValueError("query must not be empty")
    # Allow callers to provide a narrower GitHub qualifier such as in:name or
    # topic:ai-agent. A README route is added separately by the search planner.
    has_scope = re.search(r"(?:^|\s)(?:in|topic):[^\s]+", terms)
    filters = [] if has_scope else ([f"in:{search_scope}"] if search_scope else [])
    if language:
        filters.append(f"language:{language}")
    if min_stars is not None:
        filters.append(f"stars:>={min_stars}")
    if updated_within is not None:
        since = dt.date.today() - dt.timedelta(days=updated_within)
        filters.append(f"pushed:>={since.isoformat()}")
    return " ".join([terms, *filters])


def search_request_variants(query: str) -> list[str]:
    """Plan separate GitHub routes so broad metadata search cannot bury README hits."""
    terms = query.strip()
    if not terms:
        raise ValueError("query must not be empty")
    variants = [terms]
    if not re.search(r"(?:^|\s)(?:in|topic):[^\s]+", terms):
        variants.append(f"{terms} in:readme")
    return variants


def entity_query_variants(query: str) -> list[str]:
    """Create narrow name queries for explicit product-like names in a query."""
    variants: list[str] = []
    quoted_phrases = re.findall(r'"([^"\n]{2,})"', query)
    entity_tokens = re.findall(r"(?<![a-z0-9])([a-z0-9]+(?:[-_][a-z0-9]+)+)(?![a-z0-9])", query.lower())
    for phrase in [*quoted_phrases, *entity_tokens]:
        variant = f'"{phrase}" in:name'
        if variant not in variants:
            variants.append(variant)
    return variants


def build_search_params(search_query: str, args: argparse.Namespace) -> dict[str, str | int]:
    """Build API parameters while preserving GitHub's best-match default."""
    params: dict[str, str | int] = {
        "q": search_query,
        "per_page": args.fetch_limit,
    }
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
    now = dt.datetime.now(dt.timezone.utc)
    return max(0, (now - parsed).days)


def health_score(repo: dict[str, Any]) -> int:
    """Estimate repository health, never semantic product relevance."""
    if repo.get("archived"):
        return 5
    score = 25
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)
    score += min(25, round(math.log10(stars + 1) * 8))
    score += min(12, round(math.log10(forks + 1) * 4))
    age = days_since(repo.get("pushed_at"))
    if age is not None:
        score += 20 if age <= 30 else 14 if age <= 90 else 7 if age <= 365 else 0
    license_info = repo.get("license") or {}
    if license_info.get("spdx_id") not in (None, "NOASSERTION"):
        score += 8
    if repo.get("has_wiki") or repo.get("has_issues"):
        score += 5
    return min(100, score)


RESOURCE_MARKERS = (
    "awesome",
    "tutorial",
    "guide",
    "handbook",
    "curated",
    "collection",
    "examples",
    "prompts",
    "resources",
    "list",
)
COMPONENT_MARKERS = (
    "skill",
    "workflow",
    "plugin",
    "mcp",
    "router",
    "toolkit",
    "sdk",
    "library",
    "framework",
    "integration",
    "tips",
    "prompts",
    "resources",
)

# User requirements are often written as product language while repositories
# use implementation or deployment language. Keep these aliases generic: they
# describe observable capabilities and do not encode any particular domain.
CAPABILITY_ALIASES = {
    "gui": (
        "gui",
        "graphical user interface",
        "visual interface",
        "web ui",
        "webui",
        "web console",
        "desktop app",
        "desktop application",
        "graphical",
        "browser interface",
    ),
    "standalone application": (
        "standalone",
        "desktop app",
        "desktop application",
        "independently runnable",
        "single binary",
        "no installation needed",
        "pre-built binaries",
        "cross-platform",
        "download latest release",
        "runs locally",
        "run locally",
        "local-first",
        "local application",
        "personal computer",
    ),
    "tool calling": (
        "tool calling",
        "tool-calling",
        "function calling",
        "tool use",
        "tool usage",
        "uses tools",
        "run tools",
        "runs tools",
        "invoke tools",
        "invokes tools",
        "built-in tools",
        "tool invocation",
        "tool invocations",
        "tool management",
        "mcp tools",
        "mcp-compatible tool",
        "use tools",
        "model context protocol",
        "mcp",
    ),
    "multi-step tasks": (
        "multi-step",
        "multi step",
        "step by step",
        "plans tasks",
        "task planning",
        "decomposes tasks",
        "autonomous execution",
    ),
}


def normalize_capability_specs(specs: Any) -> dict[str, dict[str, list[str]]]:
    """Normalize AI-generated capability evidence without trusting its shape."""
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        raise ValueError("capability specs must be a JSON object or array")
    normalized: dict[str, dict[str, list[str]]] = {}
    for item in specs:
        if not isinstance(item, dict):
            raise ValueError("each capability spec must be a JSON object")
        requirement = str(
            item.get("canonical_capability")
            or item.get("canonical")
            or item.get("requirement")
            or ""
        ).strip()
        if not requirement:
            raise ValueError("each capability spec needs canonical_capability or requirement")

        def values(key: str, *, lower: bool = True) -> list[str]:
            value = item.get(key, [])
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise ValueError(f"capability spec field {key!r} must be a string or array")
            cleaned = [str(entry).strip() for entry in value if str(entry).strip()]
            if lower:
                cleaned = [entry.lower() for entry in cleaned]
            return list(dict.fromkeys(cleaned))

        aliases = values("aliases")
        evidence_phrases = values("evidence_phrases")
        ambiguity_notes = values("ambiguity_notes", lower=False)
        key = re.sub(r"\s+", " ", requirement.lower())
        normalized[key] = {
            "aliases": aliases,
            "evidence_phrases": evidence_phrases,
            "ambiguity_notes": ambiguity_notes,
        }
    return normalized


def capability_terms(
    requirement: str,
    capability_specs: dict[str, dict[str, list[str]]] | None = None,
) -> tuple[str, ...]:
    """Return dynamic evidence terms first, with generic aliases as a fallback."""
    normalized = re.sub(r"\s+", " ", requirement.strip().lower())
    dynamic = (capability_specs or {}).get(requirement) or (capability_specs or {}).get(normalized)
    if dynamic:
        terms = [*dynamic.get("aliases", []), *dynamic.get("evidence_phrases", [])]
        if terms:
            return tuple(dict.fromkeys(term for term in terms if term))
    return CAPABILITY_ALIASES.get(normalized, (normalized,) if normalized else ())


def _repo_text(repo: dict[str, Any]) -> tuple[str, str, str]:
    name = " ".join([repo.get("full_name") or "", repo.get("name") or ""]).lower()
    description = (repo.get("description") or "").lower()
    topics = " ".join(repo.get("topics") or []).lower()
    return " ".join([name, description, topics]), name, description


def candidate_kind(repo: dict[str, Any]) -> str:
    """Classify repository shape without claiming semantic product fit."""
    text, name, _ = _repo_text(repo)
    name_adjacent_hits = sum(
        1 for marker in COMPONENT_MARKERS
        if re.search(rf"(?:^|[ /_.-]){re.escape(marker)}s?(?:$|[ /_.-])", name)
    )
    if any(re.search(rf"\b{re.escape(marker)}s?\b", text) for marker in RESOURCE_MARKERS):
        return "resource-or-list"
    description = (repo.get("description") or "").lower()
    explicit_component_language = any(
        re.search(rf"\b{re.escape(marker)}s?\b", description)
        for marker in ("library", "framework", "sdk", "plugin", "extension", "skill collection")
    )
    if name_adjacent_hits or explicit_component_language:
        return "component-or-extension"
    return "uncertain"


def relevance_flags(repo: dict[str, Any]) -> list[str]:
    """Return compact, explainable signals used by the local reranker."""
    text, name, _ = _repo_text(repo)
    flags: list[str] = []
    if any(
        re.search(rf"(?:^|[ /_.-]){re.escape(marker)}s?(?:$|[ /_.-])", name)
        for marker in COMPONENT_MARKERS
    ):
        flags.append("component-name-or-topic")
    if any(
        re.search(rf"\b{re.escape(marker)}s?\b", text) for marker in RESOURCE_MARKERS
    ):
        flags.append("resource-or-list-mention")
    return flags


def _requirement_terms(requirement: str) -> tuple[str, ...]:
    """Return generic evidence aliases for a user-facing requirement."""
    return capability_terms(requirement)


def analyze_candidate(
    repo: dict[str, Any],
    must_have: list[str] | None = None,
    target_form: str = "any",
    capability_specs: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Evaluate generic selection evidence without domain-specific assumptions."""
    kind = candidate_kind(repo)
    _, _, description = _repo_text(repo)
    readme = (repo.get("readme_excerpt") or "").lower()
    # Capability evidence must come from prose, not repository topics or names.
    evidence_text = f"{description} {readme}"
    requirements = must_have or []
    shallow = "readme_excerpt" not in repo and "has_readme" not in repo
    requirement_evidence = {}
    requirement_matches = {}
    ambiguity_notes = {}
    for requirement in requirements:
        terms = capability_terms(requirement, capability_specs)
        matches = [term for term in terms if term and term in evidence_text]
        requirement_matches[requirement] = matches
        spec = (capability_specs or {}).get(requirement) or (capability_specs or {}).get(requirement.strip().lower()) or {}
        ambiguity_notes[requirement] = spec.get("ambiguity_notes", [])
        requirement_evidence[requirement] = (
            "unknown" if shallow else "found" if matches else "not-found"
        )
    hard_gates = {
        "not_archived": not bool(repo.get("archived")),
        "description_present": bool(repo.get("description")),
        "readme_evidence_collected": not shallow,
        "license_present": bool(repo.get("license") and repo.get("license") not in {"NOASSERTION", "Other"}),
        "target_form_match": not (
            target_form == "complete-product"
            and kind in {"resource-or-list", "component-or-extension"}
        ),
    }
    found = sum(value == "found" for value in requirement_evidence.values())
    feature_fit = round(found / len(requirements) * 100) if requirements else None
    maintenance = int(repo.get("health_score") or 0)
    gaps = [name for name, passed in hard_gates.items() if not passed]
    gaps.extend(requirement for requirement, status in requirement_evidence.items() if status != "found")
    if shallow or not hard_gates["license_present"] or not hard_gates["target_form_match"]:
        decision = "watch"
    elif requirements and found == len(requirements):
        decision = "adopt"
    elif requirements and found * 2 >= len(requirements):
        decision = "pilot"
    elif requirements:
        decision = "watch"
    else:
        decision = "pilot"
    return {
        "decision": decision,
        "candidate_kind": kind,
        "hard_gates": hard_gates,
        "requirement_evidence": requirement_evidence,
        "requirement_matches": requirement_matches,
        "ambiguity_notes": ambiguity_notes,
        "scores": {"feature_fit": feature_fit, "maintenance": maintenance},
        "gaps": gaps,
    }


def retrieval_score(entry: dict[str, Any], queries: list[str]) -> int:
    """Rank discovery candidates before semantic reranking by the Skill."""
    repo = entry["raw"]
    text, _, _ = _repo_text(repo)
    query_words = {
        word
        for query in queries
        for word in re.findall(r"[a-z0-9][a-z0-9_-]+", query.lower())
        if word not in {"open", "source", "the", "and", "for", "with", "an", "a"}
    }
    normalized_name = re.sub(r"[^a-z0-9]+", " ", (repo.get("full_name") or "").lower()).strip()
    overlap = sum(1 for word in query_words if word in text)
    score = overlap * 8
    for query in queries:
        phrase = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
        if phrase and phrase in normalized_name:
            score += 55
    name_words = set(re.findall(r"[a-z0-9]+", normalized_name))
    score += min(24, sum(8 for word in query_words if word in name_words))
    score += min(15, len(set(entry["matched_queries"])))
    route_ranks = entry.get("route_ranks") or []
    if route_ranks:
        # Preserve a small amount of route diversity. A repository near the
        # front of a dedicated GitHub route should not disappear just because
        # another broad route returned hundreds of similar projects first.
        score += max(0, 50 - min(route_ranks))
    topic_terms = {
        match.group(1).lower()
        for query in queries
        for match in re.finditer(r"(?:^|\s)topic:([^\s]+)", query.lower())
    }
    repo_topics = {str(topic).lower() for topic in repo.get("topics") or []}
    score += 35 * len(topic_terms & repo_topics)
    # Shape is a weak prior only: components and resource lists remain visible
    # and are never excluded by the generic retriever.
    score -= {"resource-or-list": 24, "component-or-extension": 12}.get(candidate_kind(repo), 0)
    list_markers = ("awesome", "list", "free-for-dev", "public-apis", "resources")
    if any(marker in text for marker in list_markers):
        score -= 45
    if repo.get("archived"):
        score -= 40
    return score


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
    result = {
        "full_name": detail.get("full_name", full_name),
        "html_url": detail.get("html_url"),
        "description": detail.get("description"),
        "topics": detail.get("topics", []),
        "language": detail.get("language"),
        "stars": detail.get("stargazers_count", 0),
        "forks": detail.get("forks_count", 0),
        "open_issues": detail.get("open_issues_count", 0),
        "license": (detail.get("license") or {}).get("spdx_id"),
        "license_name": (detail.get("license") or {}).get("name"),
        "archived": bool(detail.get("archived")),
        "default_branch": detail.get("default_branch"),
        "pushed_at": detail.get("pushed_at"),
        "created_at": detail.get("created_at"),
        "has_readme": None,
        "readme_excerpt": None,
        "health_score": health_score(detail),
    }
    if include_readme:
        try:
            readme = decode_readme(request_json(f"/repos/{urllib.parse.quote(full_name, safe='/')}/readme", token))
            result["has_readme"] = bool(readme)
            result["readme_excerpt"] = readme[:6000] if readme else None
        except RuntimeError:
            result["has_readme"] = False
    return result


def selection_sort_key(result: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Prefer evidence-backed product fit, then retrieval and health signals."""
    selection = result.get("selection") or {}
    gates = selection.get("hard_gates") or {}
    scores = selection.get("scores") or {}
    decisions = {"adopt": 3, "pilot": 2, "watch": 1}
    feature_fit = scores.get("feature_fit")
    return (
        int(bool(gates.get("target_form_match", True))),
        decisions.get(selection.get("decision"), 0),
        int(feature_fit if feature_fit is not None else -1),
        int(result.get("retrieval_score") or 0),
        int(result.get("health_score") or 0),
    )


def search(
    queries: list[str],
    args: argparse.Namespace,
    capability_specs: dict[str, dict[str, list[str]]] | None = None,
) -> list[dict[str, Any]]:
    token = github_token()
    by_name: dict[str, dict[str, Any]] = {}
    for raw_query in queries:
        planned_queries = [
            variant
            for request_query in [raw_query, *entity_query_variants(raw_query)]
            for variant in search_request_variants(request_query)
        ]
        for request_query in dict.fromkeys(planned_queries):
            search_query = build_search_query(
                request_query, args.language, args.min_stars, args.updated_within
            )
            params = urllib.parse.urlencode(build_search_params(search_query, args))
            payload = request_json(f"/search/repositories?{params}", token)
            for route_rank, item in enumerate(payload.get("items", [])):
                name = item.get("full_name")
                if not name:
                    continue
                if name not in by_name:
                    by_name[name] = {"raw": item, "matched_queries": [], "route_ranks": []}
                by_name[name]["matched_queries"].append(raw_query)
                by_name[name]["route_ranks"].append(route_rank)

    ranked = sorted(
        by_name.values(),
        key=lambda entry: retrieval_score(entry, queries),
        reverse=True,
    )
    # Keep a wider evidence pool before making the final shortlist. README and
    # license evidence can distinguish a real product from a high-ranked list
    # or component, but only if those candidates survive initial retrieval.
    candidate_budget = args.limit if args.no_details else min(100, max(args.limit, args.limit * 5))
    candidates = ranked[:candidate_budget]
    results = []
    for entry in candidates:
        raw = entry["raw"]
        if args.no_details:
            result = {
                "full_name": raw.get("full_name"),
                "html_url": raw.get("html_url"),
                "description": raw.get("description"),
                "language": raw.get("language"),
                "stars": raw.get("stargazers_count", 0),
                "forks": raw.get("forks_count", 0),
                "pushed_at": raw.get("pushed_at"),
                "archived": bool(raw.get("archived")),
                "health_score": health_score(raw),
            }
        else:
            result = enrich(raw, token, args.readme)
        result["candidate_kind"] = candidate_kind(raw)
        result["relevance_flags"] = relevance_flags(raw)
        result["selection"] = analyze_candidate(
            result, args.must_have, args.target_form, capability_specs
        )
        result["matched_queries"] = sorted(set(entry["matched_queries"]))
        result["retrieval_score"] = retrieval_score(entry, queries)
        results.append(result)
    if not args.no_details:
        results.sort(key=selection_sort_key, reverse=True)
        results = results[: args.limit]
    return results


def markdown(results: list[dict[str, Any]], queries: list[str], shallow: bool) -> str:
    lines = ["# GitHub Open-Source Candidates", "", f"Queries: {', '.join(queries)}", ""]
    if shallow:
        lines.append("> Shallow scan: details, license, and README evidence were not collected.")
        lines.append("")
    lines.extend([
        "| Repository | Kind | Stars | Language | Updated | License | Health | Description |",
        "|---|---|---:|---|---|---|---:|---|",
    ])
    for item in results:
        updated = (item.get("pushed_at") or "")[:10] or "-"
        description = (item.get("description") or "").replace("|", "\\|").replace("\n", " ")
        repo = f"[{item.get('full_name')}]({item.get('html_url')})"
        lines.append(
            f"| {repo} | {item.get('candidate_kind', '-')} | {item.get('stars', 0):,} | {item.get('language') or '-'} | "
            f"{updated} | {item.get('license') or '-'} | {item.get('health_score', 0)}/100 | {description} |"
        )
    if results:
        lines.extend(["", "## Evidence", ""])
        for item in results:
            matched = ", ".join(item.get("matched_queries", []))
            lines.append(f"- **{item.get('full_name')}**: matched `{matched}`; health score is an engineering signal, not feature fit.")
            excerpt = (item.get("readme_excerpt") or "").strip().splitlines()
            if excerpt:
                snippet = " ".join(line.strip() for line in excerpt[:3])[:500]
                lines.append(f"  README: {snippet}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idea", help="Original product idea, retained as report context")
    parser.add_argument("--query", action="append", help="GitHub query; repeat for query expansion")
    parser.add_argument("--topic", action="append", default=[], help="GitHub topic to search; repeat for multiple topic routes")
    parser.add_argument("--must-have", action="append", default=[], help="Required capability or constraint to verify; repeat for multiple requirements")
    parser.add_argument(
        "--capability-spec",
        action="append",
        default=[],
        help="AI-generated capability JSON object; repeat for multiple objects or pass an array",
    )
    parser.add_argument(
        "--capability-spec-file",
        action="append",
        default=[],
        help="Path to a JSON file containing an AI-generated capability object or array",
    )
    parser.add_argument(
        "--target-form",
        choices=("any", "complete-product", "library-sdk", "component-extension", "infrastructure"),
        default="any",
        help="Preferred repository shape; complete-product only gates known lists/components",
    )
    parser.add_argument("--language")
    parser.add_argument("--min-stars", type=int)
    parser.add_argument("--updated-within", type=int, metavar="DAYS")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=None,
        help="Number of GitHub candidates fetched per query before local reranking (default: max(10*limit, limit), capped at 100)",
    )
    parser.add_argument(
        "--sort",
        choices=("best-match", "stars", "forks", "help-wanted-issues", "updated"),
        default="best-match",
    )
    parser.add_argument("--readme", action="store_true", help="Fetch and include a README excerpt")
    parser.add_argument("--no-details", action="store_true", help="Skip per-repository API enrichment")
    parser.add_argument("--output", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")
    if args.fetch_limit is None:
        args.fetch_limit = min(100, max(args.limit, args.limit * 10))
    if args.fetch_limit < args.limit or args.fetch_limit > 100:
        parser.error("--fetch-limit must be between --limit and 100")
    if not args.query and not args.idea and not args.topic:
        parser.error("provide --idea, --query, or --topic")
    if args.updated_within is not None and args.updated_within < 1:
        parser.error("--updated-within must be positive")
    return args


def main() -> int:
    # GitHub descriptions and README excerpts may contain Unicode on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    queries = [q.strip() for q in (args.query or []) if q.strip()]
    if not queries:
        queries = [args.idea.strip()] if args.idea else []
    queries.extend(f"topic:{topic.strip()}" for topic in args.topic if topic.strip())
    if not queries:
        print("error: provide --idea, --query, or --topic", file=sys.stderr)
        return 2
    raw_specs: list[Any] = []
    try:
        for raw_spec in args.capability_spec:
            parsed = json.loads(raw_spec)
            raw_specs.extend(parsed if isinstance(parsed, list) else [parsed])
        for spec_file in args.capability_spec_file:
            parsed = json.loads(Path(spec_file).read_text(encoding="utf-8"))
            raw_specs.extend(parsed if isinstance(parsed, list) else [parsed])
        capability_specs = normalize_capability_specs(raw_specs) if raw_specs else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: invalid capability spec: {exc}", file=sys.stderr)
        return 2
    try:
        results = search(queries, args, capability_specs)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output == "json":
        print(json.dumps({"idea": args.idea, "queries": queries, "results": results}, ensure_ascii=False, indent=2))
    else:
        print(markdown(results, queries, args.no_details))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
