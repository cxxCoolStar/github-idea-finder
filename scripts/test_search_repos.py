import datetime as dt
from argparse import Namespace
import tempfile
import unittest
from pathlib import Path

from search_repos import (
    build_search_query,
    analyze_candidate,
    candidate_kind,
    build_search_params,
    capability_terms,
    dotenv_github_token,
    entity_query_variants,
    health_score,
    relevance_flags,
    retrieval_score,
    selection_sort_key,
    search_request_variants,
    normalize_capability_specs,
)


class SearchRepoTests(unittest.TestCase):
    def test_query_contains_supported_filters(self):
        query = build_search_query("self hosted notes", "python", 100, 30)
        self.assertIn("in:name,description", query)
        self.assertIn("language:python", query)
        self.assertIn("stars:>=100", query)
        self.assertIn("pushed:>=", query)

    def test_empty_query_is_rejected(self):
        with self.assertRaises(ValueError):
            build_search_query("  ")

    def test_explicit_github_qualifier_is_not_broadened(self):
        query = build_search_query('"kimi-code" in:name')
        self.assertEqual(query, '"kimi-code" in:name')

    def test_readme_route_is_planned_separately(self):
        self.assertEqual(
            search_request_variants("desktop AI agent"),
            ["desktop AI agent", "desktop AI agent in:readme"],
        )

    def test_scoped_query_is_not_repeated(self):
        self.assertEqual(search_request_variants("hermes-agent in:name"), ["hermes-agent in:name"])
        self.assertEqual(search_request_variants("topic:ai-agent"), ["topic:ai-agent"])

    def test_best_match_omits_sort_parameter(self):
        args = Namespace(sort="best-match", fetch_limit=100)
        self.assertEqual(build_search_params("agent in:name,description", args), {
            "q": "agent in:name,description",
            "per_page": 100,
        })

    def test_explicit_sort_keeps_descending_order(self):
        args = Namespace(sort="stars", fetch_limit=20)
        self.assertEqual(build_search_params("agent in:name,description", args), {
            "q": "agent in:name,description",
            "sort": "stars",
            "order": "desc",
            "per_page": 20,
        })

    def test_product_like_names_get_exact_name_variants(self):
        self.assertEqual(entity_query_variants("kimi-code"), ['"kimi-code" in:name'])

    def test_archived_repository_scores_low(self):
        self.assertEqual(health_score({"archived": True}), 5)

    def test_active_licensed_repository_scores_above_baseline(self):
        recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()
        score = health_score({
            "archived": False,
            "stargazers_count": 1000,
            "forks_count": 100,
            "pushed_at": recent,
            "license": {"spdx_id": "MIT"},
            "has_issues": True,
        })
        self.assertGreaterEqual(score, 70)

    def test_resource_lists_are_downranked_against_product_repositories(self):
        queries = ["open source AI coding agent"]
        list_entry = {
            "raw": {
                "full_name": "awesome-selfhosted/awesome-selfhosted",
                "name": "awesome-selfhosted",
                "description": "A list of self-hosted software",
                "topics": ["awesome-list"],
                "stargazers_count": 300000,
            },
            "matched_queries": queries,
        }
        product_entry = {
            "raw": {
                "full_name": "example/coding-agent",
                "name": "coding-agent",
                "description": "Open source AI coding agent for developers",
                "topics": ["coding", "agent", "cli"],
                "stargazers_count": 1000,
            },
            "matched_queries": queries,
        }
        self.assertGreater(retrieval_score(product_entry, queries), retrieval_score(list_entry, queries))

    def test_repository_shape_is_labeled_without_domain_specific_exclusion(self):
        agent = {
            "full_name": "gptme/gptme",
            "name": "gptme",
            "description": "Your agent in your terminal writes code and runs shell commands",
            "topics": [],
        }
        skills = {
            "full_name": "addyosmani/agent-skills",
            "name": "agent-skills",
            "description": "Production-grade engineering skills for AI coding agents",
            "topics": ["skills"],
        }
        self.assertEqual(candidate_kind(agent), "uncertain")
        self.assertEqual(candidate_kind(skills), "component-or-extension")
        queries = ["open source AI coding agent"]
        self.assertIsInstance(retrieval_score({"raw": agent, "matched_queries": queries}, queries), int)
        self.assertIn("component-name-or-topic", relevance_flags(skills))

    def test_mention_of_coding_assistant_does_not_make_a_product_a_competitor(self):
        workflow = {
            "full_name": "example/video-workflow",
            "name": "video-workflow",
            "description": "A video production workflow that turns your AI coding assistant into a studio",
            "topics": [],
        }
        self.assertEqual(candidate_kind(workflow), "component-or-extension")

    def test_adjacent_markers_are_not_global_negative_terms(self):
        workflow = {
            "full_name": "example/workflow",
            "name": "workflow",
            "description": "A workflow automation product",
            "topics": ["workflow"],
        }
        self.assertEqual(candidate_kind(workflow), "component-or-extension")
        self.assertIn("component-name-or-topic", relevance_flags(workflow))

    def test_missing_required_test_evidence_prevents_adopt_decision(self):
        repo = {
            "full_name": "example/agent",
            "name": "agent",
            "description": "Open source coding agent that writes code and runs shell commands",
            "topics": [],
            "license": "MIT",
            "archived": False,
            "health_score": 90,
            "stars": 1000,
            "readme_excerpt": "Install the coding agent and edit files from your terminal.",
        }
        analysis = analyze_candidate(repo, ["GUI", "tool calling"])
        self.assertEqual(analysis["decision"], "watch")
        self.assertIn("GUI", analysis["gaps"])

    def test_capability_aliases_use_product_language_and_keep_domain_generic(self):
        repo = {
            "full_name": "example/agent",
            "name": "agent",
            "description": "A local desktop application with a web console",
            "topics": [],
            "license": "MIT",
            "archived": False,
            "health_score": 90,
            "readme_excerpt": (
                "The assistant plans tasks step by step, runs tools, and is independently runnable."
            ),
        }
        analysis = analyze_candidate(
            repo, ["GUI", "standalone application", "tool calling", "multi-step tasks"]
        )
        self.assertEqual(analysis["decision"], "adopt")
        self.assertTrue(all(value == "found" for value in analysis["requirement_evidence"].values()))
        self.assertIn("runs tools", analysis["requirement_matches"]["tool calling"])

    def test_common_readme_variants_are_detected(self):
        repo = {
            "full_name": "example/open-agent",
            "name": "open-agent",
            "description": "Personal AI assistant",
            "topics": [],
            "license": "Apache-2.0",
            "archived": False,
            "health_score": 80,
            "readme_excerpt": (
                "Ships as a single binary with a WebUI. Supports tool invocation "
                "and transparent step by step execution."
            ),
        }
        analysis = analyze_candidate(
            repo, ["GUI", "standalone application", "tool calling", "multi-step tasks"]
        )
        self.assertEqual(analysis["decision"], "adopt")

    def test_ai_generated_capability_specs_override_static_aliases(self):
        specs = normalize_capability_specs([
            {
                "canonical_capability": "structured collaboration",
                "aliases": ["team workspace"],
                "evidence_phrases": ["shared task board"],
                "ambiguity_notes": ["A chat room alone is insufficient."],
            }
        ])
        self.assertEqual(
            capability_terms("structured collaboration", specs),
            ("team workspace", "shared task board"),
        )
        repo = {
            "full_name": "example/collab-agent",
            "name": "collab-agent",
            "description": "An agent product with a team workspace",
            "topics": [],
            "license": "MIT",
            "archived": False,
            "health_score": 80,
            "readme_excerpt": "Includes a shared task board.",
        }
        analysis = analyze_candidate(repo, ["structured collaboration"], capability_specs=specs)
        self.assertEqual(analysis["requirement_evidence"]["structured collaboration"], "found")
        self.assertEqual(
            analysis["ambiguity_notes"]["structured collaboration"],
            ["A chat room alone is insufficient."],
        )

    def test_invalid_capability_spec_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_capability_specs({"aliases": ["missing canonical"]})

    def test_topics_alone_do_not_count_as_capability_evidence(self):
        repo = {
            "full_name": "example/agent",
            "name": "agent",
            "description": "A repository",
            "topics": ["gui", "mcp", "multi-agent"],
            "license": "MIT",
            "archived": False,
            "health_score": 80,
            "readme_excerpt": "A repository with source code.",
        }
        analysis = analyze_candidate(repo, ["GUI", "tool calling"])
        self.assertEqual(analysis["requirement_evidence"]["GUI"], "not-found")
        self.assertEqual(analysis["requirement_evidence"]["tool calling"], "not-found")

    def test_low_feature_fit_is_watch_not_pilot(self):
        repo = {
            "full_name": "example/unrelated",
            "name": "unrelated",
            "description": "A licensed data tool",
            "topics": [],
            "license": "MIT",
            "archived": False,
            "health_score": 80,
            "readme_excerpt": "A web console for viewing data.",
        }
        analysis = analyze_candidate(repo, ["GUI", "tool calling", "multi-step tasks"])
        self.assertEqual(analysis["decision"], "watch")

    def test_complete_product_form_marks_component_as_watch_without_filtering_it(self):
        repo = {
            "full_name": "example/skills",
            "name": "skills",
            "description": "A skill collection",
            "topics": [],
            "license": "MIT",
            "archived": False,
            "health_score": 80,
            "readme_excerpt": "A desktop app with tools and step by step execution.",
        }
        analysis = analyze_candidate(repo, ["GUI"], target_form="complete-product")
        self.assertEqual(analysis["decision"], "watch")
        self.assertFalse(analysis["hard_gates"]["target_form_match"])

    def test_repository_name_match_beats_same_text_in_unrelated_project(self):
        queries = ["kimi-code"]
        exact_entry = {
            "raw": {
                "full_name": "MoonshotAI/kimi-code",
                "name": "kimi-code",
                "description": "Kimi Code CLI",
                "topics": [],
                "stargazers_count": 7000,
            },
            "matched_queries": queries,
        }
        mention_entry = {
            "raw": {
                "full_name": "example/agent-tools",
                "name": "agent-tools",
                "description": "Tools that mention kimi-code integrations",
                "topics": [],
                "stargazers_count": 100000,
            },
            "matched_queries": queries,
        }
        self.assertGreater(retrieval_score(exact_entry, queries), retrieval_score(mention_entry, queries))

    def test_topic_route_and_route_position_preserve_specialized_candidates(self):
        queries = ["topic:ai-assistant"]
        entry = {
            "raw": {
                "full_name": "makecindy/cindy",
                "name": "cindy",
                "description": "Open-source AI agent",
                "topics": ["ai-assistant"],
                "stargazers_count": 1000,
            },
            "matched_queries": queries,
            "route_ranks": [20],
        }
        baseline = {
            "raw": {
                "full_name": "example/agent",
                "name": "agent",
                "description": "Open-source AI agent",
                "topics": [],
                "stargazers_count": 1000,
            },
            "matched_queries": queries,
            "route_ranks": [20],
        }
        self.assertGreater(retrieval_score(entry, queries), retrieval_score(baseline, queries))

    def test_evidence_backed_fit_beats_raw_retrieval_score(self):
        evidence_backed = {
            "selection": {
                "decision": "adopt",
                "hard_gates": {"target_form_match": True},
                "scores": {"feature_fit": 100},
            },
            "retrieval_score": 40,
            "health_score": 70,
        }
        popular_but_shallow = {
            "selection": {
                "decision": "watch",
                "hard_gates": {"target_form_match": True},
                "scores": {"feature_fit": None},
            },
            "retrieval_score": 180,
            "health_score": 95,
        }
        self.assertGreater(selection_sort_key(evidence_backed), selection_sort_key(popular_but_shallow))

    def test_dotenv_loader_reads_only_github_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OTHER_SECRET=do-not-read\nGITHUB_TOKEN=ghp_test\n", encoding="utf-8")
            self.assertEqual(dotenv_github_token([path]), "ghp_test")


if __name__ == "__main__":
    unittest.main()
