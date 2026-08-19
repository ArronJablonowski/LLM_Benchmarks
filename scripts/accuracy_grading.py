#!/usr/bin/env python3
"""Accuracy-first grading helpers for the local LLM benchmark suite.

Generated Python is evaluated in a short-lived, isolated-mode interpreter
after strict syntax screening and with a small builtin/import allowlist.  This
is restricted execution for trusted local benchmark output, not a general
security boundary for hostile code.
"""
from __future__ import annotations

import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


GRADING_PROFILE = "behavioral-v1"
DEFAULT_PYTHON_GRADER_TIMEOUT_SECONDS = 5.0
MAX_GRADER_DETAIL_CHARS = 2000

PRIVATE_IPV4_GRADER = {
    "function": "is_private_ipv4",
    "allowed_imports": ["ipaddress", "re"],
    "cases": [
        {"args": ["10.0.0.0"], "expected": True},
        {"args": ["10.255.255.255"], "expected": True},
        {"args": ["172.16.0.0"], "expected": True},
        {"args": ["172.31.255.255"], "expected": True},
        {"args": ["192.168.0.0"], "expected": True},
        {"args": ["192.168.255.255"], "expected": True},
        {"args": ["9.255.255.255"], "expected": False},
        {"args": ["11.0.0.0"], "expected": False},
        {"args": ["172.15.255.255"], "expected": False},
        {"args": ["172.32.0.0"], "expected": False},
        {"args": ["192.167.255.255"], "expected": False},
        {"args": ["192.169.0.0"], "expected": False},
        {"args": ["127.0.0.1"], "expected": False},
        {"args": ["169.254.1.1"], "expected": False},
        {"args": ["100.64.0.1"], "expected": False},
        {"args": ["224.0.0.1"], "expected": False},
        {"args": ["8.8.8.8"], "expected": False},
        {"args": ["0.0.0.0"], "expected": False},
        {"args": [""], "expected": False},
        {"args": ["10.0.0"], "expected": False},
        {"args": ["10.0.0.1.5"], "expected": False},
        {"args": ["10.0.0.256"], "expected": False},
        {"args": ["10.999.0.1"], "expected": False},
        {"args": ["-1.2.3.4"], "expected": False},
        {"args": ["10.0.0.a"], "expected": False},
    ],
}

COUNT_UNIQUE_IPS_GRADER = {
    "function": "count_unique_ips",
    "allowed_imports": ["ipaddress", "re"],
    "cases": [
        {"args": [[]], "expected": 0},
        {"args": [["no addresses"]], "expected": 0},
        {"args": [["src=192.0.2.1 dst=198.51.100.2"]], "expected": 2},
        {"args": [["src=192.0.2.1", "again 192.0.2.1 and 203.0.113.7", "203.0.113.7"]], "expected": 2},
        {"args": [["invalid 999.1.2.3 10.0.0.256 valid 10.0.0.25"]], "expected": 1},
        {"args": [["peer=0.0.0.0 broadcast=255.255.255.255"]], "expected": 2},
        {"args": [["ipv6=2001:db8::1 ipv4=8.8.8.8"]], "expected": 1},
        {"args": [["same 8.8.8.8 twice 8.8.8.8"]], "expected": 1},
        {"args": [["invalid 1.2.3.4.5 valid 8.8.8.8"]], "expected": 1},
    ],
}


def normalize_text(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def extract_python_code(text):
    """Accept plain Python or one complete fenced Python block, with no prose."""
    candidate = (text or "").strip()
    fenced = re.fullmatch(
        r"```(?:python|py)?[ \t]*\r?\n(?P<code>.*?)\r?\n```",
        candidate,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        return fenced.group("code").strip()
    if "```" in candidate:
        raise ValueError("response must be plain Python or one complete Python code fence")
    return candidate


def _base_result(verdict, grader_type, error="", tests_passed=0, tests_total=0, failures=None):
    return {
        "verdict": verdict,
        "grader_type": grader_type,
        "grader_version": GRADING_PROFILE,
        "tests_passed": int(tests_passed or 0),
        "tests_total": int(tests_total or 0),
        "error": str(error or "")[:MAX_GRADER_DETAIL_CHARS],
        "failures": list(failures or []),
    }


def _strict_json(text):
    candidate = (text or "").strip()
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(candidate, object_pairs_hook=reject_duplicate_keys), ""
    except Exception as exc:
        return None, f"strict JSON parse failed: {exc}"


def _compact_json(text):
    """Return True when JSON has no insignificant whitespace outside strings."""
    candidate = (text or "").strip()
    in_string = False
    escaped = False
    for character in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character.isspace():
            return False
    return True


def grade_task(task, status, text, skipped=False):
    """Return a detailed grading record for one completed model response."""
    if skipped:
        return _base_result("skip", "capability")
    if status != "ok":
        return _base_result("fail", "transport", f"generation status was {status!r}")

    if task.get("python_grader"):
        return grade_python_function(text, task["python_grader"])

    normalized = normalize_text(text)
    lowered = normalized.lower()
    if "final_answer" in task or "final_answer_any" in task:
        accepted = [str(task["final_answer"])] if "final_answer" in task else [str(value) for value in task["final_answer_any"]]
        marker_count = len(re.findall(r"\bFINAL\s*:", text or "", flags=re.IGNORECASE))
        passed = marker_count == 1 and any(
            re.search(r"\bFINAL\s*:\s*" + re.escape(value) + r"\s*[.!]?\s*$", text or "", flags=re.IGNORECASE)
            for value in accepted
        )
        error = "" if passed else f"response must end with exactly one FINAL marker containing one of {accepted!r}"
        return _base_result("pass" if passed else "content_mismatch", "final_answer", error, int(passed), 1)
    if "expected_exact" in task:
        passed = normalized == task["expected_exact"]
        return _base_result(
            "pass" if passed else "content_mismatch",
            "exact_text",
            "" if passed else "response did not exactly match the required text",
            int(passed),
            1,
        )
    if "expected_contains" in task:
        missing = [value for value in task["expected_contains"] if value.lower() not in lowered]
        return _base_result(
            "pass" if not missing else "content_mismatch",
            "required_text",
            "" if not missing else "missing required text: " + ", ".join(missing),
            len(task["expected_contains"]) - len(missing),
            len(task["expected_contains"]),
            missing,
        )
    if "expected_groups" in task:
        failures = []
        passed = 0
        for index, group in enumerate(task["expected_groups"], 1):
            if any(value.lower() in lowered for value in group):
                passed += 1
            else:
                failures.append(f"group {index}: one of {group!r}")
        return _base_result(
            "pass" if not failures else "content_mismatch",
            "required_text_groups",
            "" if not failures else "missing " + "; ".join(failures),
            passed,
            len(task["expected_groups"]),
            failures,
        )
    if "expected_contains_any" in task:
        passed = any(value.lower() in lowered for value in task["expected_contains_any"])
        return _base_result(
            "pass" if passed else "content_mismatch",
            "required_text_any",
            "" if passed else "none of the accepted answer markers were present",
            int(passed),
            1,
        )
    if "json_expected" in task:
        obj, error = _strict_json(text) if task.get("strict_json") else (_permissive_json(text), "")
        if not error and task.get("compact_json") and not _compact_json(text):
            error = "JSON response contains whitespace outside strings and is not compact"
        expected = task["json_expected"]
        exact_keys = set(obj) == set(expected) if isinstance(obj, dict) and task.get("exact_json_keys") else True
        passed = (
            isinstance(obj, dict)
            and not error
            and exact_keys
            and all(type(obj.get(key)) is type(value) and obj.get(key) == value for key, value in expected.items())
        )
        if not passed and not error:
            error = "JSON object did not exactly match required keys and values"
        return _base_result("pass" if passed else "content_mismatch", "json_exact", error, int(passed), 1)
    if "json_tool" in task:
        obj, error = _strict_json(text) if task.get("strict_json") else (_permissive_json(text), "")
        if not error and task.get("compact_json") and not _compact_json(text):
            error = "JSON response contains whitespace outside strings and is not compact"
        spec = task["json_tool"]
        failures = []
        if not isinstance(obj, dict):
            failures.append("response is not a JSON object")
        else:
            if task.get("exact_json_keys") and set(obj) != {"tool", "arguments"}:
                failures.append("top-level keys must be exactly tool and arguments")
            if obj.get("tool") != spec["tool"]:
                failures.append(f"tool must be {spec['tool']!r}")
            arguments = obj.get("arguments")
            if not isinstance(arguments, dict):
                failures.append("arguments must be a JSON object")
                arguments = {}
            exact_argument_keys = set(spec.get("exact_argument_keys") or [])
            if exact_argument_keys and set(arguments) != exact_argument_keys:
                failures.append(f"argument keys must be exactly {sorted(exact_argument_keys)!r}")
            for key, value in spec.get("argument_contains", {}).items():
                if str(arguments.get(key)) != str(value):
                    failures.append(f"argument {key!r} must equal {value!r}")
            for key, accepted in spec.get("argument_contains_any", {}).items():
                value = str(arguments.get(key) or "").lower()
                if not any(str(marker).lower() in value for marker in accepted):
                    failures.append(f"argument {key!r} must contain one of {accepted!r}")
            for key in spec.get("argument_required", []):
                if key not in arguments or arguments.get(key) in (None, ""):
                    failures.append(f"argument {key!r} is required")
        detail = error or ("; ".join(failures) if failures else "")
        return _base_result("pass" if not failures and not error else "content_mismatch", "json_tool", detail, int(not failures and not error), 1, failures)
    return _base_result("pass", "unscored", tests_passed=1, tests_total=1)


def _permissive_json(text):
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except Exception:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def grade_python_function(text, spec, timeout_seconds=DEFAULT_PYTHON_GRADER_TIMEOUT_SECONDS):
    tests = list(spec.get("cases") or [])
    try:
        code = extract_python_code(text)
    except ValueError as exc:
        return _base_result("content_mismatch", "python_behavior", str(exc), tests_total=len(tests))
    if not code:
        return _base_result("content_mismatch", "python_behavior", "empty Python response", tests_total=len(tests))
    line_limit = int(spec.get("line_limit") or 0)
    if line_limit and len(code.splitlines()) > line_limit:
        return _base_result(
            "content_mismatch",
            "python_behavior",
            f"code has {len(code.splitlines())} lines; limit is {line_limit}",
            tests_total=len(tests),
        )

    payload = {"code": code, "spec": spec}
    command = [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()), "--worker"]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    with tempfile.TemporaryDirectory(prefix="llm-benchmark-grader-") as temp_dir:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=temp_dir,
            env=env,
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, stderr = proc.communicate(json.dumps(payload), timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.communicate()
            return _base_result(
                "content_mismatch",
                "python_behavior",
                f"candidate exceeded {float(timeout_seconds):g}s grader timeout",
                tests_total=len(tests),
            )
    if proc.returncode < 0:
        signal_number = -proc.returncode
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"signal {signal_number}"
        return _base_result(
            "content_mismatch",
            "python_behavior",
            f"candidate terminated by resource/safety limit ({signal_name})",
            tests_total=len(tests),
        )
    if proc.returncode != 0:
        return _base_result(
            "grader_error",
            "python_behavior",
            f"grader worker exited {proc.returncode}: {(stderr or stdout).strip()[:1000]}",
            tests_total=len(tests),
        )
    try:
        result = json.loads(stdout)
    except Exception as exc:
        return _base_result(
            "grader_error",
            "python_behavior",
            f"invalid grader worker response: {exc}; output={stdout[:500]!r}",
            tests_total=len(tests),
        )
    result.setdefault("grader_type", "python_behavior")
    result.setdefault("grader_version", GRADING_PROFILE)
    result.setdefault("tests_total", len(tests))
    result.setdefault("tests_passed", 0)
    result.setdefault("failures", [])
    result["error"] = str(result.get("error") or "")[:MAX_GRADER_DETAIL_CHARS]
    return result


FORBIDDEN_AST_NODES = (
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Lambda,
    ast.Nonlocal,
    ast.Raise,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)

FORBIDDEN_NAMES = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "exit",
    "getattr", "globals", "help", "input", "locals", "memoryview", "open",
    "quit", "setattr", "type", "vars",
}

ALLOWED_ATTRIBUTES = {
    "ASCII", "I", "IGNORECASE", "MULTILINE", "S", "VERBOSE",
    "IPv4Address", "IPv4Network", "add", "append", "compile", "count",
    "discard", "endswith", "extend", "find", "findall", "finditer", "fullmatch",
    "group", "groups", "ip_address", "ip_network", "is_private", "isdigit", "join",
    "lower", "match", "replace", "search", "split", "splitlines", "startswith",
    "strip", "update", "upper", "version",
}

ALLOWED_IMPORT_SYMBOLS = {
    "ipaddress": {"IPv4Address", "IPv4Network", "ip_address", "ip_network"},
    "re": {"compile", "findall", "finditer", "fullmatch", "match", "search"},
}

SAFE_BUILTIN_NAMES = (
    "Exception", "IndexError", "KeyError", "OverflowError", "TypeError", "ValueError",
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
    "isinstance", "len", "list", "map", "max", "min", "range", "reversed", "round",
    "set", "sorted", "str", "sum", "tuple", "zip",
)


def _validate_candidate(tree, allowed_imports):
    allowed = set(allowed_imports)
    allowed_top_level = (ast.AnnAssign, ast.Assign, ast.FunctionDef, ast.Import, ast.ImportFrom)
    if len(list(ast.walk(tree))) > 2000:
        raise ValueError("candidate is too structurally large")

    def safe_name(name):
        return bool(name) and not name.startswith("_") and name not in FORBIDDEN_NAMES

    def safe_constant_expression(node):
        if isinstance(node, ast.Constant):
            return not isinstance(node.value, (str, bytes)) or len(node.value) <= 10000
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            return len(node.elts) <= 100 and all(safe_constant_expression(value) for value in node.elts)
        if isinstance(node, ast.Dict):
            return len(node.keys) <= 100 and all(
                key is None or safe_constant_expression(key) for key in node.keys
            ) and all(safe_constant_expression(value) for value in node.values)
        if isinstance(node, ast.Attribute):
            return isinstance(node.value, ast.Name) and node.value.id == "re" and node.attr in {
                "ASCII", "I", "IGNORECASE", "MULTILINE", "S", "VERBOSE"
            }
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return safe_constant_expression(node.left) and safe_constant_expression(node.right)
        if isinstance(node, ast.Call):
            return (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr == "compile"
                and len(node.args) in (1, 2)
                and all(safe_constant_expression(argument) for argument in node.args)
                and not node.keywords
            )
        return False

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, allowed_top_level):
            raise ValueError(f"unsafe top-level statement: {type(node).__name__}")
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or not safe_name(node.targets[0].id):
                raise ValueError("top-level assignment target is not allowed")
            if not safe_constant_expression(node.value):
                raise ValueError("top-level assignment value is not allowed")
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or not safe_name(node.target.id):
                raise ValueError("top-level annotated assignment target is not allowed")
            if node.value is None or not safe_constant_expression(node.value):
                raise ValueError("top-level annotated assignment value is not allowed")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_AST_NODES):
            raise ValueError(f"unsupported Python construct: {type(node).__name__}")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in FORBIDDEN_NAMES):
            raise ValueError(f"unsafe name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr not in ALLOWED_ATTRIBUTES:
            raise ValueError(f"unsafe attribute: {node.attr}")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError("function decorators are not allowed")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed:
                    raise ValueError(f"import is not allowed: {alias.name}")
                if alias.asname and alias.asname.startswith("_"):
                    raise ValueError(f"unsafe import alias: {alias.asname}")
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or node.module != root or root not in allowed:
                raise ValueError(f"import is not allowed: {node.module or '<relative>'}")
            approved_symbols = ALLOWED_IMPORT_SYMBOLS.get(root, set())
            for alias in node.names:
                if alias.name not in approved_symbols:
                    raise ValueError(f"imported symbol is not allowed: {root}.{alias.name}")
                if alias.asname and alias.asname.startswith("_"):
                    raise ValueError(f"unsafe import alias: {alias.asname}")


def _apply_worker_limits():
    try:
        import resource
    except ImportError:
        return
    limits = [
        ("RLIMIT_CPU", 2),
        ("RLIMIT_AS", 512 * 1024 * 1024),
        ("RLIMIT_FSIZE", 0),
        ("RLIMIT_NOFILE", 16),
        ("RLIMIT_NPROC", 1),
        ("RLIMIT_CORE", 0),
    ]
    for name, value in limits:
        kind = getattr(resource, name, None)
        if kind is None:
            continue
        try:
            soft, hard = resource.getrlimit(kind)
            new_hard = value if hard < 0 else min(hard, value)
            resource.setrlimit(kind, (min(value, new_hard), new_hard))
        except (OSError, ValueError):
            continue


def _strict_equal(actual, expected):
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, int) and not isinstance(expected, bool):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    return actual == expected


def _worker_main():
    payload = json.loads(sys.stdin.read())
    code = str(payload.get("code") or "")
    spec = payload.get("spec") or {}
    tests = list(spec.get("cases") or [])
    allowed_imports = list(spec.get("allowed_imports") or [])
    try:
        tree = ast.parse(code, mode="exec")
        _validate_candidate(tree, allowed_imports)
    except (SyntaxError, ValueError) as exc:
        print(json.dumps(_base_result("content_mismatch", "python_behavior", f"candidate rejected: {exc}", tests_total=len(tests))))
        return 0

    _apply_worker_limits()
    real_import = __import__

    def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".", 1)[0]
        if level or root not in set(allowed_imports):
            raise ImportError(f"import is not allowed: {name}")
        return real_import(name, globals, locals, fromlist, level)

    import builtins
    safe_builtins = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    safe_builtins["__import__"] = restricted_import
    namespace = {"__builtins__": safe_builtins, "__name__": "__benchmark_candidate__"}
    try:
        compiled = compile(tree, "<model-response>", "exec")
        exec(compiled, namespace, namespace)
    except BaseException as exc:
        print(json.dumps(_base_result("content_mismatch", "python_behavior", f"candidate initialization raised {type(exc).__name__}: {exc}", tests_total=len(tests))))
        return 0

    function_name = str(spec.get("function") or "")
    function = namespace.get(function_name)
    if not callable(function):
        print(json.dumps(_base_result("content_mismatch", "python_behavior", f"required function {function_name!r} was not defined", tests_total=len(tests))))
        return 0

    failures = []
    passed = 0
    for index, case in enumerate(tests, 1):
        args = json.loads(json.dumps(case.get("args") or []))
        kwargs = json.loads(json.dumps(case.get("kwargs") or {}))
        expected = case.get("expected")
        try:
            actual = function(*args, **kwargs)
        except BaseException as exc:
            failures.append(f"case {index} raised {type(exc).__name__}: {str(exc)[:160]}")
            continue
        if _strict_equal(actual, expected):
            passed += 1
        else:
            failures.append(f"case {index} expected {expected!r}, got {actual!r}"[:300])
    verdict = "pass" if passed == len(tests) else "content_mismatch"
    error = "" if not failures else "; ".join(failures[:8])
    print(json.dumps(_base_result(verdict, "python_behavior", error, passed, len(tests), failures[:20])))
    return 0


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    raise SystemExit(_worker_main())
