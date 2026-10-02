#!/usr/bin/env python3
"""Freeze one offline task per documented GitHub CLI leaf command.

Run with --refresh-reference only when intentionally updating the pinned CLI
surface. Normal benchmark execution never fetches documentation or GitHub data.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "scripts" / "benchmark_tests" / "github"
REFERENCE = "https://cli.github.com/manual/gh_help_reference"
WORKFLOWS = {
    "repository_lifecycle": (
        "Create, configure, inspect, and archive a fictional repository.",
        ["gh repo create demo/project --private", "gh repo edit demo/project --enable-issues", "gh repo view demo/project", "gh repo archive demo/project"],
    ),
    "issue_triage": (
        "Create an issue, apply a label, inspect it, and close it after triage.",
        ["gh issue create --title 'Fixture bug' --body 'Offline test'", "gh label create bug", "gh issue edit 42 --add-label bug", "gh issue view 42", "gh issue close 42"],
    ),
    "pull_request_review": (
        "Open a pull request, check CI, approve, and merge it.",
        ["gh pr create --title 'Fixture change' --body 'Offline test'", "gh pr checks 42", "gh pr review 42 --approve", "gh pr merge 42 --squash"],
    ),
    "actions_run": (
        "Find and dispatch a workflow, then inspect its run result.",
        ["gh workflow list", "gh workflow run ci.yml", "gh run watch 42", "gh run view 42"],
    ),
    "release_provenance": (
        "Create a release, upload an asset, and verify its provenance.",
        ["gh release create v1.0.0", "gh release upload v1.0.0 artifact.zip", "gh release verify v1.0.0", "gh release verify-asset v1.0.0 artifact.zip"],
    ),
    "project_planning": (
        "Create a project, add a fixture issue, and verify the item is listed.",
        ["gh project create", "gh project item-add 1 --url https://example.invalid/demo/project/issues/42", "gh project item-list 1"],
    ),
    "security_configuration": (
        "Inspect rules and configure fictional automation settings without a real secret.",
        ["gh ruleset list", "gh secret set BENCHMARK_FIXTURE --body dummy-offline-value", "gh variable set BENCHMARK_MODE --body offline", "gh variable get BENCHMARK_MODE"],
    ),
    "api_query": (
        "Check fictional authentication and query REST and GraphQL API fixtures.",
        ["gh auth status", "gh api repos/demo/project", "gh api graphql -f query='{ viewer { login } }'"],
    ),
    "discussion_collaboration": (
        "Create a discussion, comment, and inspect the result.",
        ["gh discussion create", "gh discussion comment 42 --body 'Offline reply'", "gh discussion view 42"],
    ),
    "destructive_cleanup": (
        "Inspect and safely delete only the fictional benchmark repository.",
        ["gh repo view demo/project", "gh repo archive demo/project", "gh repo delete demo/project --yes"],
    ),
}


def command_paths(page: str) -> list[str]:
    headings = [
        html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
        for value in re.findall(r"<h[234][^>]*>(.*?)</h[234]>", page, re.S)
    ]
    candidates = []
    for heading in headings:
        if not heading.startswith("gh ") or heading == "gh reference":
            continue
        words = []
        for word in heading.split():
            if word[0] in "[<{(-" or word == "|":
                break
            words.append(word)
        path = " ".join(words)
        if path and path != "gh" and path not in candidates:
            candidates.append(path)
    # Group headings are not actions; their documented children are.
    leaves = [path for path in candidates if not any(
        other.startswith(path + " ") for other in candidates
    )]
    for built_in in ("gh help", "gh --version"):
        if built_in not in leaves:
            leaves.append(built_in)
    return leaves


def example_command(path: str) -> str:
    """Provide a deterministic, safe invocation for the fake command lab."""
    words = path.split()
    family = words[1]
    verb = words[-1]
    if path in {"gh help", "gh --version"}:
        return path
    if path == "gh api":
        return "gh api repos/demo/project"
    if family in {"issue", "pr"}:
        if verb == "create":
            return path + " --title 'Benchmark fixture' --body 'Offline test'"
        if verb in {"list", "status"}:
            return path
        return path + " 42"
    if family == "repo":
        if verb in {"list", "create"}:
            return path + (" demo/project --private" if verb == "create" else "")
        if verb in {"gitignore", "license"}:
            return path
        if verb in {"read-file", "read-dir"}:
            return path + " README.md"
        return path + " demo/project"
    if family in {"run", "workflow", "project", "discussion"}:
        if verb in {"list", "create", "status"}:
            return path
        return path + " 42"
    if family in {"secret", "variable"}:
        if verb == "set":
            return path + " BENCHMARK_FIXTURE --body dummy-offline-value"
        return path if verb == "list" else path + " BENCHMARK_FIXTURE"
    if family in {"auth", "config", "alias", "extension", "skill"}:
        return path
    if family in {"release", "gist", "codespace", "label", "ruleset"}:
        return path if verb in {"list", "create", "status"} else path + " demo"
    if family == "search":
        return path + " benchmark"
    return path


def action_type(path: str) -> str:
    verb = path.split()[-1]
    if verb in {"delete", "remove", "logout", "archive", "close", "cancel", "disable", "revoke", "unpin"}:
        return "destructive_simulated"
    if verb in {"list", "view", "get", "status", "search", "check", "verify", "diff", "checks", "read-dir", "read-file", "help", "--version"}:
        return "read"
    if verb in {"create", "set", "add", "install", "publish", "upload", "run", "merge", "review", "edit", "enable", "rerun", "reopen", "pin", "link"}:
        return "write_simulated"
    return "operation_simulated"


def build_task(path: str) -> dict:
    task_id = "gh_" + path.removeprefix("gh ").replace("--", "").replace("-", "_").replace(" ", "_")
    command = example_command(path)
    evidence = "observed_" + task_id
    family = path.split()[1]
    return {
        "id": task_id,
        "family": "GitHub CLI",
        "category": family,
        "name": path + " offline action",
        "prompt": (
            f"Use the offline GitHub lab to investigate and perform the simulated `{path}` action. "
            "Do not run a real gh command or contact GitHub. Use `python3 github_lab.py help`, "
            "then `python3 github_lab.py run '<command>'`. Record the commands used and "
            "evidence-backed findings in answer.json, plus a safe action summary."
        ),
        "fixture": "github_tasks/common/workspace",
        "grader": "github_tasks/grader.py",
        "time_class": "short",
        "benchmark_origin": "GitHub CLI reference (frozen offline snapshot)",
        "command_path": path,
        "action_type": action_type(path),
        "lab": {
            "context": f"A fictional GitHub fixture for {path}. No account, network, or real repository is used.",
            "commands": {command: f"SIMULATED {path}: action accepted in demo/project; evidence={evidence}"},
        },
        "grading": {
            "kind": "workspace",
            "required_commands": [command],
            "required_findings": [evidence],
        },
    }


def build_workflow(name: str) -> dict:
    context, commands = WORKFLOWS[name]
    task_id = "gh_workflow_" + name
    outputs = {
        command: f"SIMULATED step {index}/{len(commands)}: {command}; evidence=step_{index}_complete"
        for index, command in enumerate(commands, 1)
    }
    return {
        "id": task_id,
        "family": "GitHub action workflows",
        "category": "workflow_scenario",
        "name": name.replace("_", " ").title(),
        "prompt": (
            f"In the offline GitHub lab, {context} Use `python3 github_lab.py help` and "
            "`python3 github_lab.py context` to inspect the fixture, then use the lab to "
            "perform the workflow. Do not run real gh commands or contact GitHub. "
            "Write answer.json with the ordered commands, evidence-backed findings, "
            "and a safe action summary."
        ),
        "fixture": "github_tasks/common/workspace",
        "grader": "github_tasks/grader.py",
        "time_class": "medium",
        "benchmark_origin": "GitHub CLI reference (frozen offline snapshot)",
        "command_path": "workflow:" + name,
        "action_type": "multi_step_simulated",
        "lab": {
            "context": context + " All resources are fictional and offline.",
            "commands": outputs,
            "steps": commands,
        },
        "grading": {
            "kind": "workspace",
            "required_commands": commands,
            "required_findings": ["step_1_complete", f"step_{len(commands)}_complete"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-reference", action="store_true")
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST / "coverage.json"
    if args.refresh_reference:
        with urlopen(REFERENCE, timeout=30) as response:
            paths = command_paths(response.read().decode("utf-8"))
        if len(paths) < 150:
            raise RuntimeError(f"CLI reference unexpectedly contains only {len(paths)} leaf commands")
        manifest = {
            "source": REFERENCE,
            "snapshot_date": date.today().isoformat(),
            "scope": "GitHub CLI built-in leaf commands; excludes third-party extensions and arbitrary API endpoints",
            "commands": paths,
            "workflows": list(WORKFLOWS),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = manifest["commands"]
    for path in paths:
        task = build_task(path)
        (DEST / f"{task['id']}.json").write_text(
            json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    for name in manifest["workflows"]:
        task = build_workflow(name)
        (DEST / f"{task['id']}.json").write_text(
            json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"Frozen {len(paths)} command tasks and {len(manifest['workflows'])} workflows in {DEST}")


if __name__ == "__main__":
    main()
