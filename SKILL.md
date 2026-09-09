---
name: github-idea-finder
description: Discover open-source GitHub alternatives from a product idea through bounded, multi-round search, README inspection, evidence verification, and project comparison. Use for finding reusable open-source products or competitors; do not use for ordinary GitHub issue, PR, or code operations.
---

# Github Idea Finder

Find a defensible shortlist of GitHub repositories from a product idea. Search is an iterative discovery process, not a single keyword query. Do not claim exhaustive coverage.

## Agent Loop

1. Clarify the Search Brief before searching when the request is broad:
   - complete product, library/SDK, component, or infrastructure;
   - target user and job to be done;
   - 3-8 observable must-have capabilities;
   - deployment, platform, model, license, and maintenance constraints.
2. Create a session file outside the Skill directory, for example `work/github-idea-session.json`:

   ```powershell
   python scripts/github_discovery.py session --state-file work/github-idea-session.json --idea "<original idea>" --max-rounds 4 --max-searches 30 --max-inspections 50
   ```

3. Generate a Search Plan, not just aliases. Include several short query families:
   - category: `open source AI agent`, `open source AI assistant`;
   - product form: `desktop AI agent`, `self-hosted autonomous agent`;
   - observable behavior: `AI agent tool execution`, `AI agent task planning`;
   - community vocabulary discovered from the idea or later README evidence, such as `agent harness`, `agent OS`, or `computer-use agent`;
   - relevant GitHub topics.
4. Run round one for broad recall. Use no semantic hard gates at this stage:

   ```powershell
   python scripts/github_discovery.py search --state-file work/github-idea-session.json --query "<query 1>" --query "<query 2>" --query "<query 3>" --topic "<topic>" --fetch-limit 30
   ```

   The command returns new repositories and their discovery routes. Do not discard a candidate only because its type is uncertain.
5. Select a diverse inspection batch. Include candidates from different routes, not only the highest-starred repositories:

   ```powershell
   python scripts/github_discovery.py inspect --state-file work/github-idea-session.json --repo owner/repo --repo another/repo
   ```

   Inspection reads the repository metadata and README, records license/health/deployment signals, and extracts GitHub repositories linked from the README. Read the returned README evidence yourself and identify:
   - the project's own product vocabulary;
   - unresolved mandatory capabilities;
   - alternatives, integrations, and related repositories;
   - new query terms that may discover different projects.
6. Run the next round using only genuinely new queries and repositories. Search both newly discovered vocabulary and repositories linked from inspected READMEs. Keep a ledger in the session file; never repeat a query just to increase result volume.
7. Inspect the strongest new candidates and any high-value linked repositories. For each mandatory capability, record `supported`, `not-supported`, or `uncertain`, a confidence, and a direct README or release quote. A phrase match is not sufficient when the ambiguity note says a weaker interpretation is possible.
8. Stop when one condition is met:
   - the session reaches its round, search, or inspection budget;
   - two consecutive rounds produce no new high-value repositories;
   - the leading candidates have sufficient evidence for comparison;
   - remaining results are duplicates, forks, resource lists, or clearly outside the Search Brief.

## Search Plan And Evidence

Let the AI generate capability aliases, evidence phrases, ambiguity notes, and query terms from the current Search Brief and from inspected README evidence. Do not maintain a growing product-specific alias table or hard-code competitor names.

Keep discovery and judgment separate:

- discovery favors recall and route diversity;
- inspection collects evidence;
- the Agent judges semantic fit;
- deterministic code handles API errors, rate limits, state, deduplication, and budgets.

Use repository shape (`complete product`, `component`, `resource list`, or `uncertain`) as evidence, not an automatic blacklist. A repository can be a valid candidate even when its README uses unexpected terminology.

## Final Report

Return:

- the interpreted product brief;
- rounds and query families used;
- a table of repository, product form, capability evidence, gaps, license, deployment signal, maintenance signal, and links;
- a decision of `adopt`, `pilot`, or `watch` for each serious candidate;
- evidence quotes for material claims;
- a short `not a match` section for tempting but misclassified results;
- a coverage note listing known candidates that were not found or were only found through a seeded exact-name query.

Never use Stars as a proxy for product fit, and never claim that the search found every relevant project.

## Implementation

The Agent-facing entry point is `scripts/github_discovery.py`:

- `session` creates or updates the bounded search ledger;
- `search` performs one new query round and records discovery provenance;
- `inspect` fetches repository evidence and extracts README-linked repositories.

The script is intentionally not an autonomous LLM. The calling Agent chooses the next action while the script provides deterministic GitHub retrieval and state management.
