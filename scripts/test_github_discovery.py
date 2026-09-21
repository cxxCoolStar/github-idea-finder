import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_discovery import (
    build_search_query,
    empty_state,
    experience_digest,
    experience_file_entries,
    learn_experience,
    load_state,
    normalize_experience_key,
    repo_links,
    route_queries,
    save_state,
    search,
)


class DiscoveryStateTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            state = empty_state("find an agent")
            state["round"] = 2
            state["seen_repositories"] = ["example/agent"]
            save_state(path, state)
            self.assertEqual(load_state(path), state)

    def test_missing_state_is_initialized(self):
        with tempfile.TemporaryDirectory() as directory:
            state = load_state(Path(directory) / "missing.json", "new idea")
            self.assertEqual(state["version"], 4)
            self.assertEqual(state["idea"], "new idea")
            self.assertEqual(state["round"], 0)

    def test_readme_links_are_deduplicated(self):
        readme = """
        See https://github.com/example/agent and https://github.com/example/agent.
        Also compare https://github.com/other/project, not an issue link.
        Ignore https://github.com/other/project/issues/42 and
        https://github.com/user-attachments/assets/abc.
        """
        self.assertEqual(
            repo_links(readme),
            ["example/agent", "other/project"],
        )

    def test_readme_links_require_a_repository_boundary(self):
        self.assertEqual(repo_links("https://github.com/example/project/issues/42"), [])

    def test_clone_urls_are_normalized_to_repository_names(self):
        self.assertEqual(repo_links("git clone https://github.com/example/project.git"), ["example/project"])

    def test_empty_readme_has_no_links(self):
        self.assertEqual(repo_links(None), [])

    def test_unscoped_query_gets_a_separate_readme_route(self):
        self.assertEqual(
            route_queries(["desktop AI agent", "topic:ai-agent"]),
            ["desktop AI agent", "desktop AI agent in:readme", "topic:ai-agent"],
        )

    def test_search_query_adds_metadata_scope_once(self):
        self.assertEqual(build_search_query("desktop AI agent"), "desktop AI agent in:name,description")
        self.assertEqual(build_search_query("desktop AI agent in:readme"), "desktop AI agent in:readme")

    def test_used_queries_are_rejected_before_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            state = empty_state("idea")
            state["used_queries"] = [
                "open source agent",
                "open source agent in:readme",
            ]
            save_state(path, state)
            args = argparse.Namespace(
                state_file=str(path),
                idea=None,
                query=["open source agent"],
                topic=[],
                fetch_limit=5,
                sort="best-match",
            )
            with patch("github_discovery.github_token") as token, patch("github_discovery.request_json") as request:
                with self.assertRaises(ValueError):
                    search(args)
                token.assert_not_called()
                request.assert_not_called()

    def test_inspection_budget_is_cumulative(self):
        from github_discovery import inspect

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            state = empty_state("idea")
            state["budget"]["max_inspections"] = 1
            state["inspected_repositories"] = ["example/one"]
            save_state(path, state)
            args = argparse.Namespace(state_file=str(path), repo=["example/two"])
            with patch("github_discovery.github_token"), patch("github_discovery.enrich") as enrich:
                with self.assertRaises(ValueError):
                    inspect(args)
                enrich.assert_not_called()


class ExperienceTests(unittest.TestCase):
    def test_normalize_key_collapses_case_and_punctuation(self):
        self.assertEqual(
            normalize_experience_key("  Which Queries Work Well?  "),
            "which queries work well",
        )

    def test_learn_appends_then_replaces_same_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experience.md"
            first = learn_experience(path, "ai-agent-desktop", "which queries work well", "Use the query families ...")
            self.assertEqual(first["changed"], "added")
            second = learn_experience(path, "ai-agent-desktop", "which queries work well!", "Updated lesson body")
            self.assertEqual(second["changed"], "replaced")
            entries = experience_file_entries(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["key_norm"], "which queries work well")
            self.assertIn("Updated lesson body", entries[0]["text"])
            self.assertNotIn("Use the query families", entries[0]["text"])

    def test_learn_preserves_distinct_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experience.md"
            learn_experience(path, "ai-agent-desktop", "query families", "family A")
            learn_experience(path, "ai-agent-desktop", "judgment standards", "standard B")
            entries = experience_file_entries(path)
            self.assertEqual(len(entries), 2)

    def test_digest_reports_domain_counts_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experience.md"
            learn_experience(path, "ai-agent-desktop", "query families", "family A")
            learn_experience(path, "ai-agent-desktop", "pitfalls", "pitfall B")
            learn_experience(path, "server", "mcp cwd", "lesson C")
            digest = experience_digest(path)
            self.assertEqual(digest["entry_count"], 3)
            self.assertEqual(digest["domains"]["ai-agent-desktop"], 2)
            self.assertEqual(digest["domains"]["server"], 1)
            self.assertIn("family A", digest["content"])

    def test_digest_of_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            digest = experience_digest(Path(directory) / "missing.md")
            self.assertEqual(digest["entry_count"], 0)
            self.assertEqual(digest["domains"], {})
            self.assertEqual(digest["content"], "")


if __name__ == "__main__":
    unittest.main()
