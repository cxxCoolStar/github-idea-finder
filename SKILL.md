---
name: github-idea-finder
description: Find open-source GitHub alternatives from a product idea, then compare feature fit, project health, license, and deployment evidence. Use when a user wants to discover reusable open-source products or competitors from a natural-language idea; do not use for ordinary GitHub issue, PR, or code operations.
---

# Github Idea Finder

Turn an idea into a short, evidence-backed shortlist of GitHub repositories. Treat the result as a discovery aid, not proof that an exact competitor exists.

## Workflow

1. Check whether the request is specific enough to search. If the user only names a broad product or a competitor, ask up to three focused questions before searching:
   - Is the target a complete end-user product, a library/SDK, a plugin/skill, or an infrastructure component?
   - Which 3-5 capabilities are mandatory? Ask for concrete user-visible behaviors rather than implementation labels.
   - What deployment, platform, model, license, and maintenance constraints matter?
   Offer a default interpretation when helpful, but label it and wait for confirmation if the distinction changes the search boundary.
2. Turn the answers into a short Search Brief:
   - target user and job to be done;
   - must-have capabilities (3-8 concrete features);
   - constraints such as self-hosting, platform, language, data source, and license;
   - terms that describe the product category and terms that describe its implementation.
3. Generate a capability evidence spec from the Search Brief. Let the AI produce one object per mandatory capability:
   ```json
   {
     "canonical_capability": "tool calling",
     "aliases": ["function calling", "tool invocation"],
     "evidence_phrases": ["runs tools", "invokes external tools"],
     "ambiguity_notes": ["A tool SDK alone does not prove an end-user product can call tools."]
   }
   ```
   Keep aliases and evidence phrases grounded in the user's meaning. Do not invent product names or domain-specific exclusions. Pass these objects to the helper with repeated `--capability-spec` flags, or save them in JSON and use `--capability-spec-file`. The fixed aliases in the helper are only a generic fallback for older calls.
4. Generate a query matrix from the Search Brief rather than one long natural-language query. Use 3-6 short English queries spanning:
   - one broad product category anchor with no more than 2-3 key terms (for example, `open source AI agent` or `open source AI assistant`);
   - product form and deployment (for example, `desktop AI agent`, `self-hosted autonomous agent`);
   - core capabilities (for example, `AI agent tool use`, `AI agent task planning`);
   - one or more relevant GitHub topics via `--topic`.
   If the idea names a product, company, protocol, or distinctive brand, add an exact-name query and known aliases (for example, `"Acme Notes" in:name` and `acme-notes in:name`). Do not assume a category query will discover every named competitor. Pass each mandatory capability as a repeated `--must-have` flag.
5. Run the bundled search helper for all query routes:

   ```powershell
   python scripts/search_repos.py --idea "<original idea>" --target-form complete-product --must-have "<capability 1>" --must-have "<capability 2>" --capability-spec '{"canonical_capability":"<capability 1>","aliases":["..."],"evidence_phrases":["..."],"ambiguity_notes":["..."]}' --query "<query 1>" --query "<query 2>" --topic ai-agent --topic ai-assistant --limit 10 --fetch-limit 100 --readme --output json
   ```

   The helper runs each short query through GitHub's default `best-match` route, then separately searches README text and merges duplicate repositories before local evidence analysis. Use `--sort stars` only when the user explicitly wants a popularity-oriented scan. Set `GITHUB_TOKEN` in the process environment or in a local `.env` file. The helper reads only that key and never prints it. Without a token, GitHub's unauthenticated request limit is low. Keep `.env` out of distributable Skill packages; use `.env.example` as a template. The helper is read-only and uses GitHub's public REST API. Omit `--target-form` when the user wants all repository shapes; use the matching value from the Search Brief when they specify one.
6. Inspect the returned `selection` evidence. Treat `component-or-extension`, `resource-or-list`, and `uncertain` as evidence labels, not automatic exclusions. A repository is a candidate only when its evidence matches the Search Brief. Use `ambiguity_notes` to explain why a phrase match is insufficient when applicable.
7. Use the selection decision as a recommendation, not a fact: `adopt` means the collected evidence supports direct evaluation, `pilot` means run a small proof of concept, and `watch` means promising but incomplete evidence or a form/health gap. Never use Stars as a proxy for product fit.
8. Return a concise report with:
   - a one-sentence interpretation of the idea;
   - a table containing repository, fit summary, matching capabilities, gaps, license, deployment signal, last update, and links;
   - decision (`adopt`, `pilot`, or `watch`) and its hard-gate gaps for each candidate;
   - 3-5 evidence-backed recommendations for what to reuse or investigate next;
   - a "not a match" note for tempting but misclassified results when useful.

## Evidence rules

- Link directly to the repository and cite README, topics, directory, release, or license evidence for material claims.
- Separate `feature_fit` from `health_score`; the script's health score only estimates maintenance and usability signals.
- Treat missing license, stale updates, archived status, and generated or placeholder READMEs as risks, not assumptions.
- Do not claim exhaustive coverage or repeat the premise that 99% of products exist on GitHub.

## Output modes

- Use `--output markdown` for a human-readable first pass.
- Use `--output json` when another agent or later step will perform semantic reranking.
- Use `--no-details` only when rate limits require a fast, shallow scan; disclose that README and license evidence were not collected.

The helper implementation and its offline tests live in `scripts/search_repos.py` and `scripts/test_search_repos.py`.
