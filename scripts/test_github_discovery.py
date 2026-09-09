import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_discovery import build_search_query, empty_state, load_state, repo_links, route_queries, save_state, search


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
            self.assertEqual(state["version"], 3)
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


if __name__ == "__main__":
    unittest.main()
