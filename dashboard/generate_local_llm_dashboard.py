#!/usr/bin/env python3
from __future__ import annotations
import csv, datetime as dt, hashlib, html, json, os, platform, re, subprocess, sys, urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from platform_support import collect_system_specs, local_host_label as platform_host_label

HOME = Path.home()
OUTPUT_ROOT = Path(os.environ.get('LLM_BENCHMARK_OUTPUT_DIR', HOME)).expanduser()
OUT = OUTPUT_ROOT / 'Local LLM Benchmark Dashboard.html'
OLLAMA_BENCH_DIR = HOME / '.hermes/reports/ollama_benchmarks'
OPENCLAW_BENCH_DIR = HOME / '.hermes/reports/openclaw_benchmarks'
DETAIL_DIR = OUTPUT_ROOT / 'Local LLM Model Research'

HOST_LABEL = platform_host_label()
DASHBOARD_TITLE = f'{HOST_LABEL} LLM Dashboard'

def run(cmd):
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=90)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, '', f'command not found: {cmd[0] if cmd else ""}')
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or '', e.stderr or 'timeout')

def human_size(n):
    n = float(n or 0)
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024

def size_gb(n):
    try: return round(float(n) / (1024**3), 2)
    except Exception: return 0

def token_count_text(n):
    try:
        n = int(float(str(n).replace(',', '').strip()))
    except Exception:
        return '—'
    return f'{n:,} tokens'

def resolve_hermes_context_length(model_name):
    model_name = (model_name or '').strip()
    if not model_name or model_name == '—':
        return ''
    # First use Hermes' own resolver when the installed venv is available.
    try:
        py = HOME / '.hermes/hermes-agent/venv/bin/python'
        if py.exists():
            code = "from agent.model_metadata import get_model_context_length; import sys; print(get_model_context_length(sys.argv[1]) or '')"
            p = subprocess.run([str(py), '-c', code, model_name], cwd=str(HOME / '.hermes/hermes-agent'), text=True, capture_output=True, timeout=20)
            val = (p.stdout or '').strip()
            if val:
                return val
    except Exception:
        pass
    # Known OpenAI Codex aliases used by Hermes when the resolver is unavailable.
    if re.search(r'(^|/)gpt-5\.[4-6]$', model_name):
        return '1050000'
    return ''

def load_system_specs():
    return collect_system_specs()

def latest_csv(directory: Path, pattern: str):
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None

def all_csvs(directory: Path, pattern: str):
    return sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)


PROVENANCE_COHORT_FIELDS = (
    'host',
    'host_label',
    'os_version',
    'architecture',
    'model_digest',
    'suite_version',
    'ollama_version',
    'telemetry_backend',
    'telemetry_interval_ms',
    'ollama_url',
    'residency_policy',
    'keep_alive_request',
    'stop_before_task',
    'thinking_mode',
    'thinking_requested',
    'thinking_resolved',
    'thinking_effective',
    'benchmark_profile',
    'grading_profile',
    'runner_sha256',
    'grader_sha256',
    'output_token_policy',
    'output_token_limit',
    'response_timeout_seconds',
    'context_policy',
    'requested_num_ctx',
    'model_context_length',
    'context_adjusted',
    'context_reduction_tokens',
    'context_reduction_pct',
    'context_adjustment_reason',
    'context_calibration_profile',
    'context_calibration_status',
    'context_calibration_attempt_count',
    'context_calibration_attempts_json',
    # Paired thinking campaigns must never merge with an unrelated run. These
    # fields are absent from legacy CSVs, so adding them is backwards-safe.
    'campaign_id',
    'experiment_id',
    'plan_sha256',
    'pair_schema_version',
    'campaign_seed',
    'pair_id',
    'treatment_id',
    'treatment_key',
    'treatment',
    'treatment_role',
    'pair_kind',
    'off_available',
    'planner_sha256',
    'task_set_sha256',
)

# These fields intentionally vary between arms of one paired experiment. They
# remain part of legacy/non-paired cohort selection, but paired CSVs must group
# both arms under their shared experiment/plan/pair provenance.
PAIRED_ARM_COHORT_FIELDS = {
    'thinking_mode',
    'thinking_requested',
    'thinking_resolved',
    'thinking_effective',
    'treatment_id',
    'treatment_key',
    'treatment',
    'treatment_role',
}

CAMPAIGN_ID_FIELDS = ('campaign_id', 'experiment_id')
PAIR_ID_FIELDS = ('pair_id', 'treatment_pair_id', 'comparison_id')
TREATMENT_KEY_FIELDS = ('treatment_key', 'treatment', 'treatment_role', 'arm')
TREATMENT_ID_FIELDS = ('treatment_id', 'arm_id')
TREATMENT_ORDER_FIELDS = ('treatment_order', 'arm_order', 'pair_order')
PLANNED_TASK_COUNT_FIELDS = ('planned_task_count', 'expected_task_count', 'task_count_planned')
QUALIFICATION_STATUS_FIELDS = ('model_qualification_status', 'qualification_status')
QUALIFICATION_REASON_FIELDS = ('model_qualification_reason', 'qualification_reason')
OMITTED_WORK_FIELDS = ('omitted_remaining_work_count', 'remaining_work_omitted')
CONTEXT_ADJUSTED_FIELDS = ('context_adjusted',)
CONTEXT_REDUCTION_FIELDS = ('context_reduction_pct', 'context_reduction_percent')
CONTEXT_REASON_FIELDS = ('context_adjustment_reason', 'context_reduction_reason')
CONTEXT_ATTEMPT_COUNT_FIELDS = ('context_calibration_attempt_count', 'context_calibration_attempts')
CONTEXT_PROFILE_FIELDS = ('context_calibration_profile', 'context_adjustment_profile')
CONTEXT_STATUS_FIELDS = ('context_calibration_status',)
CONTEXT_ATTEMPTS_JSON_FIELDS = ('context_calibration_attempts_json',)
QUALIFIED_COMPARISON_STATUSES = {
    'observable-toggle-qualified',
    'level-range-qualified',
}
TERMINAL_QUALIFICATION_STATUSES = {
    'off-control-ineffective',
    'on-control-unverified',
    'control-inconclusive',
    'level-range-unverified',
}
PAIR_INVARIANT_FIELDS = (
    'host', 'host_label', 'os_version', 'architecture', 'model_digest',
    'suite_version', 'ollama_version', 'ollama_url',
    'telemetry_backend', 'telemetry_interval_ms', 'residency_policy',
    'keep_alive_request', 'stop_before_task',
    'benchmark_profile', 'grading_profile',
    'runner_sha256', 'grader_sha256', 'output_token_policy',
    'output_token_limit', 'response_timeout_seconds', 'context_policy',
    'requested_num_ctx', 'model_context_length', 'task_set_sha256',
    'plan_sha256', 'pair_schema_version', 'campaign_seed', 'planner_sha256',
    'temperature', 'seed', 'control_policy', 'off_observability',
    'context_adjusted', 'context_reduction_pct', 'context_reduction_percent',
    'context_reduction_tokens',
    'context_adjustment_reason', 'context_reduction_reason',
    'context_calibration_attempt_count', 'context_calibration_attempts',
    'context_calibration_attempts_json', 'context_calibration_status',
    'context_calibration_profile', 'context_adjustment_profile',
)


def first_recorded(row, fields):
    for field in fields:
        value = str(row.get(field) or '').strip()
        if value:
            return value
    return ''


def first_recorded_across(rows, fields):
    for row in rows:
        value = first_recorded(row, fields)
        if value:
            return value
    return ''


def unique_recorded_across(rows, fields):
    values = {
        first_recorded(row, fields)
        for row in rows
        if first_recorded(row, fields)
    }
    return sorted(values)


def normalize_treatment_id(value):
    value = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    aliases = {
        'false': 'off', 'disabled': 'off', 'control': 'off', 'no-thinking': 'off',
        'true': 'on', 'enabled': 'on', 'thinking': 'on', 'thinking-on': 'on',
        'thinking-off': 'off',
        'thinking-low': 'low', 'minimum': 'low',
        'thinking-high': 'high', 'maximum': 'high',
    }
    return aliases.get(value, value)


def pair_schema_major(value):
    """Return the leading numeric schema version without rejecting legacy text."""
    match = re.match(r'^\s*(\d+)', str(value or ''))
    return int(match.group(1)) if match else 0


def normalize_qualification_status(value):
    value = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    aliases = {
        '': 'pending',
        'qualified': 'observable-toggle-qualified',
        'toggle-qualified': 'observable-toggle-qualified',
        'valid': 'observable-toggle-qualified',
        'range-qualified': 'level-range-qualified',
        'gpt-range-qualified': 'level-range-qualified',
        'gpt-low-high-range': 'level-range-qualified',
        'range-unverified': 'level-range-unverified',
        'gpt-range-unverified': 'level-range-unverified',
        'inconclusive': 'control-inconclusive',
        'unverified-off-observability': 'off-control-unobservable',
        'off-control-unverified': 'off-control-unobservable',
        'unsupported': 'control-inconclusive',
        'control-unsupported': 'control-inconclusive',
    }
    return aliases.get(value, value)


def csv_optional_bool(value):
    value = str(value or '').strip().lower()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    return None


def qualification_status_from_rows(rows):
    """Choose the final disposition while treating terminal findings as sticky.

    A model can qualify on the probes and expose an off-control leak only later
    in the benchmark. That later terminal disposition supersedes the earlier
    qualification. Multiple different outcomes within the same disposition
    stage remain a provenance conflict rather than becoming a valid generic
    inconclusive outcome.
    """
    statuses = []
    for row in rows:
        recorded = first_recorded(row, QUALIFICATION_STATUS_FIELDS)
        if recorded:
            normalized = normalize_qualification_status(recorded)
            if normalized not in statuses:
                statuses.append(normalized)
    substantive = [status for status in statuses if status != 'pending']
    terminal = [status for status in substantive if status in TERMINAL_QUALIFICATION_STATUSES]
    if terminal:
        # Preserve encounter order: the newest terminal value is most useful
        # for display, even when competing terminal values invalidate the pair.
        return terminal[-1], statuses, len(set(terminal)) > 1
    if len(set(substantive)) > 1:
        return substantive[-1], statuses, True
    if substantive:
        return substantive[-1], statuses, False
    return 'pending', statuses, False


def treatment_sort_key(value):
    value = normalize_treatment_id(value)
    preferred = {'off': 0, 'low': 0, 'on': 1, 'high': 1}
    return (preferred.get(value, 2), value)


def row_provenance_cohort(row):
    """Keep partial-run merging inside one compatible host/model/runtime cohort."""
    paired = bool(
        first_recorded(row, CAMPAIGN_ID_FIELDS)
        and first_recorded(row, PAIR_ID_FIELDS)
        and first_recorded(row, TREATMENT_KEY_FIELDS)
    )
    fields = (
        tuple(field for field in PROVENANCE_COHORT_FIELDS if field not in PAIRED_ARM_COHORT_FIELDS)
        if paired else PROVENANCE_COHORT_FIELDS
    )
    values = tuple((row.get(field) or '').strip() for field in fields)
    return (('paired' if paired else 'recorded'), *values) if any(values) else ('legacy',)


def merge_latest_provenance_cohort(paths):
    """Return newest task rows without mixing host, digest, or runtime provenance."""
    grouped = {}
    sequence = 0
    for path in sorted(paths, key=lambda item: item.stat().st_mtime):
        with path.open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        for row_number, row in enumerate(rows):
            model = row.get('model')
            if not model:
                continue
            sequence += 1
            cohort = row_provenance_cohort(row)
            bucket = grouped.setdefault(model, {}).setdefault(
                cohort, {'rows': {}, 'sources': set(), 'latest': 0, 'latest_source': path}
            )
            task_key = row.get('task_id') or row.get('task_name') or f'row-{row_number}'
            if cohort[0] == 'paired':
                # Both treatments intentionally repeat the same task IDs. Keep
                # one latest row per treatment/task while still merging resume
                # fragments from the same immutable paired campaign.
                treatment_key = normalize_treatment_id(first_recorded(row, TREATMENT_KEY_FIELDS))
                task_key = f'{treatment_key}\x00{task_key}'
            bucket['rows'][task_key] = row
            bucket['sources'].add(path)
            bucket['latest'] = sequence
            bucket['latest_source'] = path
    merged = {}
    source_by_model = {}
    sources_by_model = {}
    for model, cohorts in grouped.items():
        selected = max(cohorts.values(), key=lambda bucket: bucket['latest'])
        merged[model] = selected['rows']
        source_by_model[model] = selected['latest_source']
        sources_by_model[model] = sorted(
            selected['sources'], key=lambda item: item.stat().st_mtime
        )
    return merged, source_by_model, sources_by_model


def _csv_number(value):
    try:
        if value is None or str(value).strip() == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_row_values(rows, *fields):
    values = set()
    for row in rows:
        for field in fields:
            value = str(row.get(field) or '').strip()
            if value:
                values.add(value)
                break
    return sorted(values)


def _sum_recorded(rows, field, *, integer=False):
    values = [_csv_number(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    total = sum(values)
    return int(total) if integer else round(total, 3)


def _first_number(row, fields):
    for field in fields:
        value = _csv_number(row.get(field))
        if value is not None:
            return value
    return None


def summarize_trace_evidence(rows):
    """Summarize schema-v3 trace evidence while retaining v1/v2 thinking data."""
    evidence = []
    separated_total = 0
    inline_total = 0
    observed_tasks = set()
    transports = set()
    for row in rows:
        task_id = (row.get('task_id') or row.get('task_name') or 'unknown').strip()
        separated = _first_number(row, ('separated_thinking_chars', 'thinking_chars')) or 0
        inline = _first_number(row, ('inline_thinking_chars', 'inline_trace_chars')) or 0
        explicit_observed = csv_optional_bool(row.get('reasoning_trace_observed'))
        observed = explicit_observed is True or separated > 0 or inline > 0
        transport = (row.get('reasoning_transport') or '').strip().lower()
        if not transport:
            if separated > 0 and inline > 0:
                transport = 'both'
            elif separated > 0:
                transport = 'separated'
            elif inline > 0:
                transport = 'inline'
            else:
                transport = 'none'
        separated_total += int(separated)
        inline_total += int(inline)
        transports.add(transport)
        if observed:
            observed_tasks.add(task_id)
        excerpt = first_recorded(
            row,
            ('reasoning_trace_evidence', 'inline_thinking_preview', 'separated_thinking_preview'),
        )
        if observed or excerpt:
            evidence.append({
                'task_id': task_id,
                'observed': observed,
                'transport': transport,
                'separated_chars': int(separated),
                'inline_chars': int(inline),
                'excerpt': excerpt[:220],
                'qualification_probe': (row.get('qualification_probe') or '').strip(),
            })
    return {
        'reasoning_trace_observed': bool(observed_tasks),
        'reasoning_trace_tasks': sorted(observed_tasks),
        'reasoning_trace_task_count': len(observed_tasks),
        'reasoning_transports': sorted(transports),
        'separated_thinking_chars': separated_total,
        'inline_thinking_chars': inline_total,
        'trace_evidence': evidence,
    }


def summarize_treatment_rows(treatment_id, task_rows, sources):
    """Return compact paired-treatment metrics without changing legacy summaries."""
    rows = list(task_rows.values())
    skips = [row for row in rows if row.get('verdict') == 'skip' or row.get('status') == 'skip']
    grader_errors = [row for row in rows if row.get('verdict') == 'grader_error']
    scored = [row for row in rows if row not in skips and row not in grader_errors]
    passes = [row for row in scored if row.get('status') == 'ok' and row.get('verdict') == 'pass']
    failed = [row for row in scored if row not in passes]
    grader_passed = _sum_recorded(scored, 'grader_tests_passed', integer=True)
    grader_total = _sum_recorded(scored, 'grader_tests_total', integer=True)
    treatment_label = normalize_treatment_id(treatment_id) or 'recorded treatment'
    detail_rows = []
    preview_rows = []
    for row in sorted(rows, key=lambda item: item.get('task_id') or item.get('task_name') or ''):
        task = (row.get('task_id') or row.get('task_name') or 'unknown').strip()
        verdict = (row.get('verdict') or row.get('status') or 'unknown').strip()
        bits = [f'{task}: {verdict}']
        wall = _csv_number(row.get('wall_seconds'))
        output_tokens = _csv_number(row.get('eval_count'))
        grader_row_passed = _csv_number(row.get('grader_tests_passed'))
        grader_row_total = _csv_number(row.get('grader_tests_total'))
        if grader_row_passed is not None and grader_row_total is not None:
            bits.append(f'{int(grader_row_passed)}/{int(grader_row_total)} grader cases')
        if wall is not None:
            bits.append(f'{wall:.2f}s')
        if output_tokens is not None:
            bits.append(f'{int(output_tokens)} output tok')
        separated_chars = _first_number(row, ('separated_thinking_chars', 'thinking_chars'))
        inline_chars = _first_number(row, ('inline_thinking_chars', 'inline_trace_chars'))
        if separated_chars is not None:
            bits.append(f'{int(separated_chars)} separated thinking chars')
        if inline_chars is not None:
            bits.append(f'{int(inline_chars)} inline thinking chars')
        qualification_probe = (row.get('qualification_probe') or '').strip()
        if qualification_probe and qualification_probe != 'none':
            bits.append(f'{qualification_probe} qualification probe')
        detail_rows.append(' · '.join(bits))
        preview = (row.get('response_preview') or '').strip()
        error = (row.get('grader_error') or row.get('error') or '').strip()
        trace_excerpt = first_recorded(
            row,
            ('reasoning_trace_evidence', 'inline_thinking_preview', 'separated_thinking_preview'),
        )
        if preview or error or trace_excerpt:
            content = error if error else trace_excerpt if trace_excerpt else preview
            preview_rows.append(f'{task}: ' + content[:220])
    provenance = {
        'campaign_ids': _unique_row_values(rows, *CAMPAIGN_ID_FIELDS),
        'pair_ids': _unique_row_values(rows, *PAIR_ID_FIELDS),
        'treatment_ids': _unique_row_values(rows, *TREATMENT_ID_FIELDS),
        'treatment_keys': _unique_row_values(rows, *TREATMENT_KEY_FIELDS),
        'treatment_roles': _unique_row_values(rows, 'treatment_role'),
        'treatment_orders': _unique_row_values(rows, *TREATMENT_ORDER_FIELDS),
        'attempts': _unique_row_values(rows, 'attempt'),
        'row_ids': _unique_row_values(rows, 'row_id'),
        'run_ids': _unique_row_values(rows, 'run_id'),
        'think_field_present': _unique_row_values(rows, 'think_field_present'),
        'think_request_values': _unique_row_values(rows, 'think_payload_json', 'think_request_value', 'thinking_requested'),
        'thinking_resolved': _unique_row_values(rows, 'thinking_resolved', 'thinking_effective'),
        'protocol_valid_values': _unique_row_values(rows, 'protocol_valid'),
        'plan_hashes': _unique_row_values(rows, 'plan_sha256'),
        'task_set_hashes': _unique_row_values(rows, 'task_set_sha256'),
        'pair_schema_versions': _unique_row_values(rows, 'pair_schema_version'),
        'campaign_seeds': _unique_row_values(rows, 'campaign_seed'),
        'pair_kinds': _unique_row_values(rows, 'pair_kind'),
        'off_available': _unique_row_values(rows, 'off_available'),
        'protocol_errors': _unique_row_values(rows, 'protocol_error'),
        'runner_hashes': _unique_row_values(rows, 'runner_sha256'),
        'grader_hashes': _unique_row_values(rows, 'grader_sha256'),
        'planner_hashes': _unique_row_values(rows, 'planner_sha256'),
        'model_aliases': _unique_row_values(rows, 'model_aliases'),
        'context_policies': _unique_row_values(rows, 'context_policy'),
        'requested_num_ctx': _unique_row_values(rows, 'requested_num_ctx'),
        'model_context_lengths': _unique_row_values(rows, 'model_context_length'),
        'context_adjusted_values': _unique_row_values(rows, *CONTEXT_ADJUSTED_FIELDS),
        'context_reduction_tokens': _unique_row_values(rows, 'context_reduction_tokens'),
        'context_reduction_pct': _unique_row_values(rows, *CONTEXT_REDUCTION_FIELDS),
        'context_adjustment_reasons': _unique_row_values(rows, *CONTEXT_REASON_FIELDS),
        'context_calibration_statuses': _unique_row_values(rows, *CONTEXT_STATUS_FIELDS),
        'context_calibration_attempt_counts': _unique_row_values(rows, *CONTEXT_ATTEMPT_COUNT_FIELDS),
        'context_calibration_profiles': _unique_row_values(rows, *CONTEXT_PROFILE_FIELDS),
        'ollama_versions': _unique_row_values(rows, 'ollama_version'),
        'model_digests': _unique_row_values(rows, 'model_digest'),
        'qualification_phases': _unique_row_values(rows, 'qualification_phase'),
        'qualification_probes': _unique_row_values(rows, 'qualification_probe'),
        'qualification_statuses': _unique_row_values(rows, *QUALIFICATION_STATUS_FIELDS),
        'qualification_reasons': _unique_row_values(rows, *QUALIFICATION_REASON_FIELDS),
        'control_policies': _unique_row_values(rows, 'control_policy'),
        'off_observability': _unique_row_values(rows, 'off_observability'),
        'evidence_codes': _unique_row_values(rows, 'evidence_code'),
    }
    trace_summary = summarize_trace_evidence(rows)
    return {
        'treatment_id': treatment_label,
        'passed': len(passes),
        'tasks': len(scored),
        'rows': len(rows),
        'skipped': len(skips),
        'grader_errors': len(grader_errors),
        'errors': sum(1 for row in rows if row.get('status') == 'error' or (row.get('error') or '').strip()),
        'grader_cases_passed': grader_passed,
        'grader_cases_total': grader_total,
        'wall_seconds_total': _sum_recorded(rows, 'wall_seconds'),
        'eval_count': _sum_recorded(rows, 'eval_count', integer=True),
        'thinking_chars': _sum_recorded(rows, 'thinking_chars', integer=True),
        'timeouts': sum(1 for row in rows if row_timed_out(row)),
        'failed_tests': [(row.get('task_name') or row.get('task_id') or 'unknown') for row in failed],
        'test_details': detail_rows,
        'preview_details': preview_rows,
        'task_ids': sorted(task_rows),
        'sources': sorted(str(path) for path in sources),
        **trace_summary,
        **provenance,
    }


def _plan_only_no_fit_comparison(plan_record, planned_model):
    """Represent a hashed no-fit calibration without inventing benchmark rows."""
    plan = plan_record.get('plan') or {}
    model = str(planned_model.get('name') or planned_model.get('model') or '').strip()
    campaign_id = str(plan.get('experiment_id') or '').strip()
    plan_sha256 = str(plan.get('plan_sha256') or '').strip()
    pair_id = str(planned_model.get('pair_id') or '').strip()
    schema_version = str(plan.get('pair_schema_version') or '').strip()
    schema_major = pair_schema_major(schema_version)
    context_policy = str(plan.get('context_policy') or '').strip()
    native_context = _csv_number(planned_model.get('model_context_length'))
    requested_raw = planned_model.get('requested_num_ctx')
    requested_context = _csv_number(requested_raw)
    profile = str(planned_model.get('context_calibration_profile') or '').strip()
    calibration_status = str(planned_model.get('context_calibration_status') or '').strip()
    attempts = planned_model.get('context_calibration_attempts')
    attempt_count = _csv_number(planned_model.get('context_calibration_attempt_count'))
    adjusted = planned_model.get('context_adjusted')
    reduction_tokens = _csv_number(planned_model.get('context_reduction_tokens'))
    reduction_pct = _csv_number(planned_model.get('context_reduction_pct'))
    adjustment_reason = str(planned_model.get('context_adjustment_reason') or '').strip()
    planned_task_ids = sorted(str(item) for item in (plan.get('task_ids') or []))
    qualification_task_ids = [str(item) for item in (plan.get('qualification_task_ids') or [])]
    planned_treatments = list(planned_model.get('treatments') or [])
    planned_labels = [
        normalize_treatment_id(item.get('treatment_key') or item.get('treatment_role'))
        for item in planned_treatments if isinstance(item, dict)
    ]
    planned_labels = [label for label in planned_labels if label]
    pair_kind = str(planned_model.get('pair_kind') or '').strip()
    if not pair_kind and planned_treatments:
        pair_kind = str((planned_treatments[0] or {}).get('pair_kind') or '').strip()
    if not pair_kind:
        pair_kind = 'minimum-vs-maximum' if set(planned_labels) == {'low', 'high'} else 'off-vs-on'
    default_labels = ['low', 'high'] if pair_kind == 'minimum-vs-maximum' else ['off', 'on']
    expected_labels = planned_labels if len(planned_labels) >= 2 else default_labels
    first_label, second_label = expected_labels[:2]
    planned_work = len(planned_task_ids) * len(planned_labels)
    terminal_entry = next((
        entry for entry in (plan.get('terminal_dispositions') or [])
        if isinstance(entry, dict) and (
            (pair_id and str(entry.get('pair_id') or '').strip() == pair_id)
            or (model and str(entry.get('model') or entry.get('name') or '').strip() == model)
        )
    ), None)
    terminal_status = normalize_qualification_status(
        (terminal_entry or {}).get('model_qualification_status')
        or (terminal_entry or {}).get('status')
    )
    terminal_source = str((terminal_entry or {}).get('source') or '').strip()
    terminal_reason = str(
        (terminal_entry or {}).get('model_qualification_reason')
        or (terminal_entry or {}).get('reason')
        or ''
    ).strip()
    terminal_omitted = _csv_number((terminal_entry or {}).get('omitted_remaining_work_count'))

    errors = []
    if not plan_record.get('hash_valid'):
        errors.append('paired plan SHA-256 verification failed')
    if schema_major < 3:
        errors.append('plan-only no-fit disposition requires pair schema v3')
    if qualification_task_ids != ['simple_reasoning', 'math500_mini']:
        errors.append('schema-v3 qualification probes missing or changed')
    if not model or not campaign_id or not pair_id:
        errors.append('plan-only no-fit disposition lacks model/campaign/pair identity')
    if context_policy != 'adaptive-native-per-model':
        errors.append('no-fit disposition requires adaptive-native-per-model context policy')
    if calibration_status != 'no-fit':
        errors.append('plan-only capacity disposition requires context_calibration_status no-fit')
    if native_context is None or native_context <= 0:
        errors.append('no-fit calibration requires a positive native model context')
    # A no-fit model has no resolved request. Zero and malformed strings must
    # not be accepted as a substitute for JSON null/omission.
    if requested_raw not in (None, '') or requested_context is not None:
        errors.append('no-fit calibration must not claim a resolved requested context')
    if profile != 'ollama-empty-load-v1':
        errors.append('context calibration profile missing or unsupported')
    attempts_valid = isinstance(attempts, list) and bool(attempts) and all(
        isinstance(item, dict) for item in attempts
    )
    if attempts_valid and native_context:
        for attempt in attempts:
            candidate = _csv_number(attempt.get('num_ctx', attempt.get('requested_num_ctx')))
            success = attempt.get('success')
            attempt_status = str(attempt.get('status') or '').strip()
            if (
                candidate is None or candidate <= 0 or candidate > native_context
                or type(success) is not bool
                or success
                or attempt_status not in {'capacity-failure', 'inconclusive'}
            ):
                attempts_valid = False
                break
    if not attempts_valid:
        errors.append('no-fit calibration requires valid unsuccessful calibration attempts')
    if (
        attempt_count is None or not float(attempt_count).is_integer()
        or not isinstance(attempts, list) or int(attempt_count) != len(attempts)
    ):
        errors.append('context calibration attempt count differs from calibration artifact')
    if adjusted is not False:
        errors.append('no-fit calibration cannot be marked context-adjusted')
    if native_context is not None and reduction_tokens != native_context:
        errors.append('no-fit context reduction must equal the native context length')
    if reduction_pct is None or abs(reduction_pct - 100.0) > 0.001:
        errors.append('no-fit context reduction must be 100 percent')
    if not adjustment_reason:
        errors.append('no-fit calibration requires a context adjustment reason')
    if len(planned_labels) != 2 or len(set(planned_labels)) != 2:
        errors.append('paired plan must declare two distinct treatments')
    if not planned_task_ids:
        errors.append('paired plan has no benchmark task set')
    if not terminal_entry:
        errors.append('no-fit plan lacks its terminal context-calibration disposition')
    else:
        if terminal_status != 'control-inconclusive' or terminal_source != 'context-calibration':
            errors.append('no-fit terminal disposition has invalid status or evidence source')
        if not terminal_reason:
            errors.append('no-fit terminal disposition lacks a reason')
        if terminal_omitted != planned_work:
            errors.append('no-fit terminal omitted-work count differs from paired plan work')

    metadata_valid = not errors
    omitted_work = int(terminal_omitted) if terminal_omitted is not None else planned_work
    qualification_reason = terminal_reason or 'adaptive context calibration found no context that could load on this host'
    if adjustment_reason and adjustment_reason.lower() not in qualification_reason.lower():
        qualification_reason += f': {adjustment_reason}'
    attempts_json = ''
    if isinstance(attempts, list):
        attempts_json = json.dumps(attempts, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return model, {
        '_source_mtime': plan_record.get('source_mtime') or 0,
        'plan_only': True,
        'capacity_disposition': True,
        'not_benchmarked_reason': 'no context fit',
        'campaign_id': campaign_id,
        'experiment_id': campaign_id,
        'pair_id': pair_id,
        'plan_sha256': plan_sha256,
        'pair_schema_version': schema_version,
        'campaign_seed': str(plan.get('campaign_seed') or ''),
        'planner_sha256': str(plan.get('planner_sha256') or ''),
        'pair_kind': pair_kind,
        'off_available': bool(planned_model.get('off_available', pair_kind == 'off-vs-on')),
        'treatments': {},
        'expected_treatments': expected_labels,
        'first_label': first_label,
        'second_label': second_label,
        'expected_tasks': len(planned_task_ids),
        'qualification_task_ids': qualification_task_ids,
        'qualification_plan_valid': qualification_task_ids == ['simple_reasoning', 'math500_mini'],
        'schema_version_matches_plan': True,
        'context_policies': [context_policy] if context_policy else [],
        'context_policy': context_policy,
        'requested_num_ctx_values': [],
        'model_context_length_values': [str(int(native_context))] if native_context is not None else [],
        'requested_num_ctx': '',
        'model_context_length': str(int(native_context)) if native_context is not None else '',
        'native_context_policy': False,
        'adaptive_context_policy': context_policy == 'adaptive-native-per-model',
        'per_model_context_policy': context_policy == 'adaptive-native-per-model',
        'context_contract_valid': metadata_valid,
        'native_context_contract_valid': True,
        'context_policy_matches_plan': True,
        'context_adjusted': False,
        'context_reduction_tokens': int(reduction_tokens) if reduction_tokens is not None else None,
        'context_reduction_pct': reduction_pct,
        'context_adjustment_reason': adjustment_reason,
        'context_calibration_profile': profile,
        'context_calibration_status': calibration_status,
        'context_calibration_attempt_count': int(attempt_count) if attempt_count is not None and float(attempt_count).is_integer() else None,
        'context_calibration_attempts_json': attempts_json,
        'speed_comparison_caveat': 'Not benchmarked: adaptive calibration found no context fit; timing and speed comparisons do not exist.',
        'plan_available': True,
        'plan_hash_valid': bool(plan_record.get('hash_valid')),
        'row_ids_valid': True,
        'complete': False,
        'full_benchmark_complete': False,
        'terminally_dispositioned': metadata_valid,
        'model_complete': metadata_valid,
        'valid': False,
        'rankable': False,
        'status': 'control-inconclusive',
        'qualification_status': 'control-inconclusive',
        'recorded_qualification_statuses': [],
        'qualification_status_conflict': False,
        'qualification_reasons': [qualification_reason],
        'qualification_reason': qualification_reason,
        'control_policy': str(planned_model.get('control_policy') or ''),
        'off_observability': str(planned_model.get('off_observability') or ''),
        'evidence_code': str(planned_model.get('evidence_code') or ''),
        'omitted_remaining_work_count': omitted_work,
        'planned_row_count': omitted_work,
        'observed_row_count': 0,
        'invariant_mismatches': {},
        'protocol_errors': [],
        'control_errors': [],
        'invalid_reasons': errors,
        'grader_errors': 0,
        'execution_errors': 0,
        'strict_delta': None,
        'grader_delta': None,
        'descriptive_strict_delta': None,
        'descriptive_grader_delta': None,
        'causal_delta_eligible': False,
        'descriptive_delta_eligible': False,
        'wall_multiplier': None,
        'token_multiplier': None,
        'descriptive_wall_multiplier': None,
        'descriptive_token_multiplier': None,
        'source_csv': str(plan_record.get('source_path') or ''),
        'source_plan': str(
            Path(plan_record.get('source_path')).with_suffix('.plan.json')
            if plan_record.get('source_path') else ''
        ),
    }


def load_latest_treatment_experiments(paths):
    """Select the newest paired experiment per model, then retain every arm.

    Legacy files have no campaign/treatment identifiers and are intentionally
    ignored here; their existing one-cohort dashboard behavior remains intact.
    """
    experiments = {}
    plans = {}
    for path in paths:
        plan_path = path.with_suffix('.plan.json')
        if not plan_path.exists():
            continue
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        experiment_id = str(plan.get('experiment_id') or '').strip()
        plan_sha256 = str(plan.get('plan_sha256') or '').strip()
        if experiment_id:
            # The runner hashes the immutable campaign core before adding these
            # per-execution/output fields. Reproduce that exact contract here.
            plan_core = {
                key: value
                for key, value in plan.items()
                if key not in {'plan_sha256', 'run_id', 'report_prefix'}
            }
            computed_plan_sha256 = hashlib.sha256(
                json.dumps(plan_core, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            ).hexdigest()
            plans[(experiment_id, plan_sha256)] = {
                'plan': plan,
                'hash_valid': bool(plan_sha256 and computed_plan_sha256 == plan_sha256),
                'source_path': path,
                'source_mtime': path.stat().st_mtime,
            }
    sequence = 0
    for path in sorted(paths, key=lambda item: item.stat().st_mtime):
        with path.open(newline='', encoding='utf-8') as handle:
            for row_number, row in enumerate(csv.DictReader(handle)):
                model = (row.get('model') or '').strip()
                campaign_id = first_recorded(row, CAMPAIGN_ID_FIELDS)
                pair_id = first_recorded(row, PAIR_ID_FIELDS)
                treatment_id = normalize_treatment_id(first_recorded(row, TREATMENT_KEY_FIELDS))
                if not model or not campaign_id or not treatment_id:
                    continue
                sequence += 1
                experiment_key = (campaign_id, pair_id)
                experiment = experiments.setdefault(model, {}).setdefault(
                    experiment_key,
                    {'latest': 0, 'campaign_id': campaign_id, 'pair_id': pair_id, 'treatments': {}, 'all_rows': []},
                )
                treatment = experiment['treatments'].setdefault(treatment_id, {'rows': {}, 'sources': set()})
                task_key = row.get('task_id') or row.get('task_name') or f'row-{row_number}'
                treatment['rows'][task_key] = row
                treatment['sources'].add(path)
                experiment['all_rows'].append(row)
                experiment['latest'] = sequence

    selected = {}
    for model, model_experiments in experiments.items():
        experiment = max(model_experiments.values(), key=lambda item: item['latest'])
        selected_rows = [
            row
            for treatment in experiment['treatments'].values()
            for row in treatment['rows'].values()
        ]
        plan_hash = first_recorded(selected_rows[0], ('plan_sha256',)) if selected_rows else ''
        plan_record = plans.get((experiment['campaign_id'], plan_hash))
        plan = plan_record.get('plan') if plan_record else None
        plan_hash_valid = bool(plan_record and plan_record.get('hash_valid'))
        planned_model = None
        if plan:
            planned_model = next(
                (item for item in plan.get('models') or [] if str(item.get('pair_id') or '') == experiment['pair_id']),
                None,
            )
        summaries = {
            treatment_id: summarize_treatment_rows(treatment_id, bucket['rows'], bucket['sources'])
            for treatment_id, bucket in sorted(experiment['treatments'].items(), key=lambda item: treatment_sort_key(item[0]))
        }
        labels = list(summaries)
        planned_treatments = (planned_model or {}).get('treatments') or []
        planned_labels = [
            normalize_treatment_id(item.get('treatment_key') or item.get('treatment_role'))
            for item in planned_treatments
        ]
        planned_labels = [label for label in planned_labels if label]
        pair_kind = first_recorded(selected_rows[0], ('pair_kind',)) if selected_rows else ''
        recorded_pair_schema_version = first_recorded(selected_rows[0], ('pair_schema_version',)) if selected_rows else ''
        plan_pair_schema_version = str((plan or {}).get('pair_schema_version') or '').strip()
        recorded_schema_major = pair_schema_major(recorded_pair_schema_version)
        plan_schema_major = pair_schema_major(plan_pair_schema_version)
        schema_major = plan_schema_major or recorded_schema_major
        pair_schema_version = recorded_pair_schema_version or plan_pair_schema_version
        schema_version_matches_plan = bool(
            not plan_schema_major or recorded_schema_major == plan_schema_major
        )
        if planned_labels:
            expected_labels = planned_labels
        elif pair_kind == 'off-vs-on' or 'off' in labels or 'on' in labels:
            expected_labels = ['off', 'on']
        elif pair_kind == 'minimum-vs-maximum' or 'low' in labels or 'high' in labels:
            expected_labels = ['low', 'high']
        else:
            expected_labels = labels if len(labels) >= 2 else [labels[0], 'second treatment']
        first_label, second_label = expected_labels[:2]
        first = summaries.get(first_label)
        second = summaries.get(second_label)
        planned_counts = []
        for row in selected_rows:
            value = _csv_number(first_recorded(row, PLANNED_TASK_COUNT_FIELDS))
            if value is not None:
                planned_counts.append(int(value))
        planned_task_ids = sorted(str(item) for item in ((plan or {}).get('task_ids') or []))
        qualification_task_ids = [
            str(item) for item in ((plan or {}).get('qualification_task_ids') or [])
        ]
        qualification_plan_valid = bool(
            schema_major < 3
            or qualification_task_ids == ['simple_reasoning', 'math500_mini']
        )
        expected_tasks = len(planned_task_ids) if planned_task_ids else (max(planned_counts) if planned_counts else 0)
        same_tasks = bool(
            first and second and planned_task_ids
            and first.get('task_ids') == planned_task_ids
            and second.get('task_ids') == planned_task_ids
        )
        complete = bool(
            plan and plan_hash_valid and planned_model and first and second and same_tasks and expected_tasks
            and first.get('rows') == expected_tasks and second.get('rows') == expected_tasks
        )
        invariant_mismatches = {}
        for field in PAIR_INVARIANT_FIELDS:
            values = sorted({str(row.get(field) or '').strip() for row in selected_rows if str(row.get(field) or '').strip()})
            if len(values) > 1:
                invariant_mismatches[field] = values
        context_policies = _unique_row_values(selected_rows, 'context_policy')
        requested_contexts = _unique_row_values(selected_rows, 'requested_num_ctx')
        model_contexts = _unique_row_values(selected_rows, 'model_context_length')
        plan_context_policy = str((plan or {}).get('context_policy') or '').strip()
        context_policy_matches_plan = bool(
            not plan_context_policy or context_policies == [plan_context_policy]
        )
        native_context_policy = context_policies == ['native-per-model-full']
        adaptive_context_policy = context_policies == ['adaptive-native-per-model']
        per_model_context_policy = native_context_policy or adaptive_context_policy
        context_adjusted_values = unique_recorded_across(selected_rows, CONTEXT_ADJUSTED_FIELDS)
        context_reduction_values = unique_recorded_across(selected_rows, CONTEXT_REDUCTION_FIELDS)
        context_reduction_token_values = _unique_row_values(selected_rows, 'context_reduction_tokens')
        context_reason_values = unique_recorded_across(selected_rows, CONTEXT_REASON_FIELDS)
        context_attempt_count_values = unique_recorded_across(selected_rows, CONTEXT_ATTEMPT_COUNT_FIELDS)
        context_profile_values = unique_recorded_across(selected_rows, CONTEXT_PROFILE_FIELDS)
        context_status_values = unique_recorded_across(selected_rows, CONTEXT_STATUS_FIELDS)
        context_attempts_json_values = unique_recorded_across(selected_rows, CONTEXT_ATTEMPTS_JSON_FIELDS)
        parsed_context_attempts = None
        context_contract_valid = True
        context_contract_errors = []
        if per_model_context_policy:
            requested_context = _csv_number(requested_contexts[0]) if len(requested_contexts) == 1 else None
            model_context = _csv_number(model_contexts[0]) if len(model_contexts) == 1 else None
            planned_requested_context = _csv_number((planned_model or {}).get('requested_num_ctx'))
            planned_model_context = _csv_number((planned_model or {}).get('model_context_length'))
            if not requested_context or not model_context:
                context_contract_errors.append('requested and native model contexts must be positive numbers')
            elif requested_context > model_context:
                context_contract_errors.append('requested context exceeds native model context')
            if not all(
                _csv_number(row.get('requested_num_ctx')) is not None
                and _csv_number(row.get('requested_num_ctx')) == requested_context
                and _csv_number(row.get('model_context_length')) == model_context
                for row in selected_rows
            ):
                context_contract_errors.append('every row must repeat the resolved requested/native context')
            if planned_requested_context is not None and planned_requested_context != requested_context:
                context_contract_errors.append('row requested context differs from paired plan model')
            if planned_model_context is not None and planned_model_context != model_context:
                context_contract_errors.append('row native context differs from paired plan model')
            if native_context_policy and requested_context != model_context:
                context_contract_errors.append('native-full policy requires requested context equal native context')
            if adaptive_context_policy and requested_context and model_context:
                expected_adjusted = requested_context < model_context
                adjusted_bools = [csv_optional_bool(value) for value in context_adjusted_values]
                if len(adjusted_bools) != 1 or adjusted_bools[0] is None or adjusted_bools[0] != expected_adjusted:
                    context_contract_errors.append('context_adjusted must be true exactly when requested context is below native')
                expected_reduction_tokens = int(model_context - requested_context)
                recorded_reduction_tokens = _csv_number(context_reduction_token_values[0]) if len(context_reduction_token_values) == 1 else None
                if recorded_reduction_tokens != expected_reduction_tokens:
                    context_contract_errors.append('context_reduction_tokens does not match native minus requested context')
                expected_reduction_pct = round((model_context - requested_context) * 100 / model_context, 6)
                recorded_reduction_pct = _csv_number(context_reduction_values[0]) if len(context_reduction_values) == 1 else None
                if recorded_reduction_pct is None or abs(recorded_reduction_pct - expected_reduction_pct) > 0.11:
                    context_contract_errors.append('context_reduction_pct does not match requested/native contexts')
                expected_status = 'adjusted-fit' if expected_adjusted else 'native-fit'
                if context_status_values != [expected_status]:
                    context_contract_errors.append(f'context_calibration_status must be {expected_status}')
                if context_profile_values != ['ollama-empty-load-v1']:
                    context_contract_errors.append('context calibration profile missing or unsupported')
                attempt_count = _csv_number(context_attempt_count_values[0]) if len(context_attempt_count_values) == 1 else None
                attempts_json_valid = False
                if len(context_attempts_json_values) == 1:
                    try:
                        parsed_context_attempts = json.loads(context_attempts_json_values[0])
                        attempts_json_valid = isinstance(parsed_context_attempts, list)
                        if attempts_json_valid and attempt_count is not None:
                            attempts_json_valid = (
                                float(attempt_count).is_integer()
                                and len(parsed_context_attempts) == int(attempt_count)
                            )
                    except (TypeError, ValueError):
                        parsed_context_attempts = None
                        attempts_json_valid = False
                if expected_adjusted:
                    if not context_reason_values:
                        context_contract_errors.append('adjusted context requires an adjustment reason')
                    if (
                        attempt_count is None or attempt_count <= 0
                        or not attempts_json_valid or not parsed_context_attempts
                    ):
                        context_contract_errors.append('adjusted context requires valid calibration attempts')
                planned_profile = str((planned_model or {}).get('context_calibration_profile') or '').strip()
                planned_status = str((planned_model or {}).get('context_calibration_status') or '').strip()
                planned_adjusted = (planned_model or {}).get('context_adjusted')
                planned_reduction_tokens = _csv_number((planned_model or {}).get('context_reduction_tokens'))
                planned_reduction_pct = _csv_number((planned_model or {}).get('context_reduction_pct'))
                planned_reason = str((planned_model or {}).get('context_adjustment_reason') or '').strip()
                planned_attempt_count = _csv_number((planned_model or {}).get('context_calibration_attempt_count'))
                planned_attempts = (planned_model or {}).get('context_calibration_attempts')
                if planned_profile and context_profile_values != [planned_profile]:
                    context_contract_errors.append('row context calibration profile differs from paired plan model')
                if planned_status and context_status_values != [planned_status]:
                    context_contract_errors.append('row context calibration status differs from paired plan model')
                if type(planned_adjusted) is bool and (
                    len(adjusted_bools) != 1 or adjusted_bools[0] != planned_adjusted
                ):
                    context_contract_errors.append('row context_adjusted differs from paired plan model')
                if planned_reduction_tokens is not None and recorded_reduction_tokens != planned_reduction_tokens:
                    context_contract_errors.append('row context reduction tokens differ from paired plan model')
                if planned_reduction_pct is not None and (
                    recorded_reduction_pct is None
                    or abs(recorded_reduction_pct - planned_reduction_pct) > 0.001
                ):
                    context_contract_errors.append('row context reduction percent differs from paired plan model')
                if planned_reason and context_reason_values != [planned_reason]:
                    context_contract_errors.append('row context adjustment reason differs from paired plan model')
                if planned_attempt_count is not None and (
                    attempt_count is None or attempt_count != planned_attempt_count
                ):
                    context_contract_errors.append('row context calibration attempt count differs from paired plan model')
                if planned_attempts is not None and parsed_context_attempts != planned_attempts:
                    context_contract_errors.append('row calibration attempts differ from paired plan artifact')
        context_contract_valid = not context_contract_errors
        explicit_invalid = any(
            str(row.get(field) or '').strip().lower() in {'0', 'false', 'invalid', 'failed'}
            for row in selected_rows
            for field in ('protocol_valid', 'pair_valid', 'treatment_valid', 'control_valid')
            if str(row.get(field) or '').strip()
        )
        protocol_errors = sorted({str(row.get('protocol_error') or '').strip() for row in selected_rows if str(row.get('protocol_error') or '').strip()})
        qualification_status, recorded_qualification_statuses, qualification_status_conflict = (
            qualification_status_from_rows(experiment.get('all_rows') or selected_rows)
        )
        qualification_reasons = _unique_row_values(selected_rows, *QUALIFICATION_REASON_FIELDS)
        control_errors = []
        # Schema v3 records a model-level qualification disposition after its
        # primary/fallback probes. Older schemas did not, so retain their trace-
        # based validation as a compatibility fallback only.
        if schema_major < 3 and complete and first and second:
            if pair_kind == 'off-vs-on' and (first.get('thinking_chars') or 0) != 0:
                control_errors.append('off treatment emitted thinking text')
            if (second.get('thinking_chars') or 0) <= 0:
                control_errors.append(f'{second_label} treatment did not demonstrate thinking output')
            if pair_kind == 'minimum-vs-maximum' and (first.get('thinking_chars') or 0) <= 0:
                control_errors.append(f'{first_label} treatment did not demonstrate thinking output')
        row_ids = [str(row.get('row_id') or '').strip() for row in selected_rows]
        row_ids_valid = bool(row_ids and all(row_ids) and len(set(row_ids)) == len(row_ids))
        grader_errors = sum(summary.get('grader_errors') or 0 for summary in summaries.values())
        execution_errors = sum(1 for row in selected_rows if (row.get('status') or '').strip().lower() == 'error')
        provenance_valid = bool(
            plan and plan_hash_valid and planned_model and row_ids_valid
            and qualification_plan_valid and schema_version_matches_plan
            and context_contract_valid and context_policy_matches_plan
            and not invariant_mismatches and not qualification_status_conflict
        )
        full_run_valid = bool(
            provenance_valid and complete and not explicit_invalid and not protocol_errors
            and not control_errors and grader_errors == 0 and execution_errors == 0
        )
        if schema_major >= 3:
            valid = bool(
                full_run_valid and qualification_status in QUALIFIED_COMPARISON_STATUSES
            )
            rankable = valid
            terminally_dispositioned = bool(
                provenance_valid and qualification_status in TERMINAL_QUALIFICATION_STATUSES
            )
            model_complete = bool(
                (complete and provenance_valid) or terminally_dispositioned
            )
        else:
            valid = full_run_valid
            rankable = valid
            terminally_dispositioned = False
            model_complete = complete
        invalid_reasons = []
        if not plan:
            invalid_reasons.append('paired plan file missing')
        elif not plan_hash_valid:
            invalid_reasons.append('paired plan SHA-256 verification failed')
        elif not planned_model:
            invalid_reasons.append('pair ID absent from paired plan')
        if schema_major >= 3 and not qualification_plan_valid:
            invalid_reasons.append('schema-v3 qualification probes missing or changed')
        if qualification_status_conflict:
            invalid_reasons.append('conflicting model qualification statuses at the same disposition stage')
        if not schema_version_matches_plan:
            invalid_reasons.append('row pair schema differs from paired plan')
        if per_model_context_policy and not context_contract_valid:
            invalid_reasons.extend(context_contract_errors)
        if not context_policy_matches_plan:
            invalid_reasons.append('row context policy differs from paired plan')
        if not complete and not terminally_dispositioned:
            invalid_reasons.append('treatments incomplete or task sets differ')
        if not row_ids_valid:
            invalid_reasons.append('row IDs missing or duplicated')
        if invariant_mismatches:
            invalid_reasons.append('pair provenance mismatch')
        if explicit_invalid or protocol_errors or control_errors:
            invalid_reasons.append('thinking-control protocol failure')
        if grader_errors:
            invalid_reasons.append('grader error')
        if execution_errors:
            invalid_reasons.append('runtime error')
        if schema_major >= 3:
            status = qualification_status
            if qualification_status == 'pending' and complete:
                status = 'control-inconclusive'
            if qualification_status == 'observable-toggle-qualified' and not complete:
                status = 'pending'
            if qualification_status == 'level-range-qualified' and not complete:
                status = 'pending'
            if qualification_status == 'off-control-unobservable' and not complete:
                status = 'pending'
        elif not complete:
            status = 'incomplete'
        elif valid:
            status = 'valid'
        else:
            status = 'invalid'

        causal_delta_eligible = bool(
            (schema_major < 3 and valid)
            or (schema_major >= 3 and valid and qualification_status == 'observable-toggle-qualified')
        )
        descriptive_delta_eligible = bool(
            schema_major >= 3 and valid and qualification_status == 'level-range-qualified'
        )
        raw_strict_delta = (second['passed'] - first['passed']) if complete and first and second else None
        raw_grader_delta = None
        if complete and first and second and first.get('grader_cases_passed') is not None and second.get('grader_cases_passed') is not None:
            raw_grader_delta = second['grader_cases_passed'] - first['grader_cases_passed']
        strict_delta = raw_strict_delta if causal_delta_eligible else None
        grader_delta = raw_grader_delta if causal_delta_eligible else None
        descriptive_strict_delta = raw_strict_delta if descriptive_delta_eligible else None
        descriptive_grader_delta = raw_grader_delta if descriptive_delta_eligible else None
        def multiplier(numerator, denominator, field):
            if not numerator or not denominator:
                return None
            n = numerator.get(field)
            d = denominator.get(field)
            if not isinstance(n, (int, float)) or not isinstance(d, (int, float)) or d == 0:
                return None
            return round(n / d, 3)
        explicit_omitted_counts = [
            int(value) for value in (
                _csv_number(first_recorded(row, OMITTED_WORK_FIELDS)) for row in selected_rows
            ) if value is not None
        ]
        observed_rows = len(selected_rows)
        planned_rows = expected_tasks * len(expected_labels) if expected_tasks else 0
        omitted_remaining_work_count = (
            max(explicit_omitted_counts) if explicit_omitted_counts
            else max(planned_rows - observed_rows, 0) if terminally_dispositioned and planned_rows
            else 0
        )
        selected[model] = {
            '_source_mtime': max(
                (path.stat().st_mtime for bucket in experiment['treatments'].values() for path in bucket['sources']),
                default=0,
            ),
            'campaign_id': experiment['campaign_id'],
            'experiment_id': experiment['campaign_id'],
            'pair_id': experiment['pair_id'],
            'plan_sha256': plan_hash,
            'pair_schema_version': pair_schema_version,
            'campaign_seed': first_recorded(selected_rows[0], ('campaign_seed',)) if selected_rows else '',
            'planner_sha256': first_recorded(selected_rows[0], ('planner_sha256',)) if selected_rows else '',
            'pair_kind': pair_kind,
            'off_available': csv_bool(first_recorded(selected_rows[0], ('off_available',))) if selected_rows else False,
            'treatments': summaries,
            'expected_treatments': expected_labels,
            'first_label': first_label,
            'second_label': second_label,
            'expected_tasks': expected_tasks,
            'qualification_task_ids': qualification_task_ids,
            'qualification_plan_valid': qualification_plan_valid,
            'schema_version_matches_plan': schema_version_matches_plan,
            'context_policies': context_policies,
            'context_policy': context_policies[0] if len(context_policies) == 1 else '',
            'requested_num_ctx_values': requested_contexts,
            'model_context_length_values': model_contexts,
            'requested_num_ctx': requested_contexts[0] if len(requested_contexts) == 1 else '',
            'model_context_length': model_contexts[0] if len(model_contexts) == 1 else '',
            'native_context_policy': native_context_policy,
            'adaptive_context_policy': adaptive_context_policy,
            'per_model_context_policy': per_model_context_policy,
            'context_contract_valid': context_contract_valid,
            'native_context_contract_valid': context_contract_valid if native_context_policy else True,
            'context_policy_matches_plan': context_policy_matches_plan,
            'context_adjusted': csv_optional_bool(context_adjusted_values[0]) if len(context_adjusted_values) == 1 else None,
            'context_reduction_tokens': int(_csv_number(context_reduction_token_values[0])) if len(context_reduction_token_values) == 1 and _csv_number(context_reduction_token_values[0]) is not None else None,
            'context_reduction_pct': _csv_number(context_reduction_values[0]) if len(context_reduction_values) == 1 else None,
            'context_adjustment_reason': '; '.join(context_reason_values),
            'context_calibration_profile': context_profile_values[0] if len(context_profile_values) == 1 else '',
            'context_calibration_status': context_status_values[0] if len(context_status_values) == 1 else '',
            'context_calibration_attempt_count': int(_csv_number(context_attempt_count_values[0])) if len(context_attempt_count_values) == 1 and _csv_number(context_attempt_count_values[0]) is not None else None,
            'context_calibration_attempts_json': context_attempts_json_values[0] if len(context_attempts_json_values) == 1 else '',
            'speed_comparison_caveat': (
                'Timing is descriptive only across models because each model used its own calibrated context derived from its native limit.'
                if per_model_context_policy else ''
            ),
            'plan_available': bool(plan),
            'plan_hash_valid': plan_hash_valid,
            'row_ids_valid': row_ids_valid,
            'complete': complete,
            'full_benchmark_complete': complete,
            'terminally_dispositioned': terminally_dispositioned,
            'model_complete': model_complete,
            'valid': valid,
            'rankable': rankable,
            'status': status,
            'qualification_status': qualification_status,
            'recorded_qualification_statuses': recorded_qualification_statuses,
            'qualification_status_conflict': qualification_status_conflict,
            'qualification_reasons': qualification_reasons,
            'qualification_reason': '; '.join(qualification_reasons),
            'control_policy': first_recorded_across(selected_rows, ('control_policy',)),
            'off_observability': first_recorded_across(selected_rows, ('off_observability',)),
            'evidence_code': first_recorded_across(selected_rows, ('evidence_code',)),
            'omitted_remaining_work_count': omitted_remaining_work_count,
            'planned_row_count': planned_rows,
            'observed_row_count': observed_rows,
            'invariant_mismatches': invariant_mismatches,
            'protocol_errors': protocol_errors,
            'control_errors': control_errors,
            'invalid_reasons': invalid_reasons,
            'grader_errors': grader_errors,
            'execution_errors': execution_errors,
            'strict_delta': strict_delta,
            'grader_delta': grader_delta,
            'descriptive_strict_delta': descriptive_strict_delta,
            'descriptive_grader_delta': descriptive_grader_delta,
            'causal_delta_eligible': causal_delta_eligible,
            'descriptive_delta_eligible': descriptive_delta_eligible,
            'wall_multiplier': multiplier(second, first, 'wall_seconds_total') if causal_delta_eligible else None,
            'token_multiplier': multiplier(second, first, 'eval_count') if causal_delta_eligible else None,
            'descriptive_wall_multiplier': multiplier(second, first, 'wall_seconds_total') if descriptive_delta_eligible else None,
            'descriptive_token_multiplier': multiplier(second, first, 'eval_count') if descriptive_delta_eligible else None,
        }
    # Adaptive capacity calibration happens before qualification. A hashed
    # plan can therefore contain a scientifically meaningful no-fit terminal
    # disposition even though the runner correctly emitted zero benchmark
    # rows. Surface that plan artifact directly instead of fabricating task,
    # verdict, timing, trace, or score records.
    for plan_record in sorted(plans.values(), key=lambda item: item.get('source_mtime') or 0):
        plan = plan_record.get('plan') or {}
        for planned_model in plan.get('models') or []:
            if not isinstance(planned_model, dict):
                continue
            if str(planned_model.get('context_calibration_status') or '').strip() != 'no-fit':
                continue
            model, comparison = _plan_only_no_fit_comparison(plan_record, planned_model)
            if not model:
                continue
            existing = selected.get(model)
            if existing and (existing.get('_source_mtime') or 0) >= (comparison.get('_source_mtime') or 0):
                continue
            selected[model] = comparison
    # Campaign progress is disposition-aware in schema v3: a model is done
    # after a complete eligible pair or a recorded terminal qualification. A
    # terminally omitted model therefore does not leave the campaign forever
    # "incomplete", while an untouched planned model still does.
    campaign_groups = {}
    for model, comparison in selected.items():
        key = (comparison.get('campaign_id') or '', comparison.get('plan_sha256') or '')
        campaign_groups.setdefault(key, {})[model] = comparison
    for key, comparisons in campaign_groups.items():
        plan_record = plans.get(key)
        plan = plan_record.get('plan') if plan_record else None
        planned_models = list((plan or {}).get('models') or [])
        planned_pair_ids = {
            str(item.get('pair_id') or '') for item in planned_models if item.get('pair_id')
        }
        completed_pair_ids = {
            str(comparison.get('pair_id') or '')
            for comparison in comparisons.values()
            if comparison.get('model_complete') and comparison.get('pair_id')
        }
        terminal_plan_entries = []
        for field in ('excluded_unsupported_models', 'terminal_dispositions', 'excluded_models'):
            for entry in (plan or {}).get(field) or []:
                if isinstance(entry, str):
                    terminal_plan_entries.append({'name': entry, 'status': 'control-inconclusive'})
                elif isinstance(entry, dict):
                    status = normalize_qualification_status(
                        entry.get('model_qualification_status') or entry.get('status')
                    )
                    if status in TERMINAL_QUALIFICATION_STATUSES:
                        terminal_plan_entries.append(entry)
        terminal_names = {
            str(entry.get('name') or entry.get('model') or '')
            for entry in terminal_plan_entries
            if entry.get('name') or entry.get('model')
        }
        planned_names = {
            str(item.get('name') or item.get('model') or '')
            for item in planned_models
            if item.get('name') or item.get('model')
        }
        extra_terminal_names = terminal_names - planned_names
        total_models = len(planned_models) + len(extra_terminal_names)
        completed_models = len(planned_pair_ids & completed_pair_ids) + len(extra_terminal_names)
        # Plans from early schemas occasionally omitted pair IDs. Fall back to
        # exact model names so their progress remains readable.
        if planned_models and not planned_pair_ids:
            completed_models = sum(
                1 for name in planned_names
                if comparisons.get(name, {}).get('model_complete')
            ) + len(extra_terminal_names)
        remaining_models = max(total_models - completed_models, 0)
        omitted_work = sum(
            comparison.get('omitted_remaining_work_count') or 0
            for comparison in comparisons.values()
        )
        for comparison in comparisons.values():
            comparison.update({
                'campaign_complete': bool(total_models and remaining_models == 0),
                'campaign_models_total': total_models,
                'campaign_models_complete': completed_models,
                'campaign_models_remaining': remaining_models,
                'campaign_terminal_dispositions': sum(
                    1 for item in comparisons.values() if item.get('terminally_dispositioned')
                ) + len(extra_terminal_names),
                'campaign_omitted_remaining_work_count': omitted_work,
            })
    return selected


def csv_bool(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def row_timed_out(row):
    return (row.get('status') or '').strip().lower() == 'timeout' or csv_bool(row.get('timed_out'))


def summarize_execution_schema(rows):
    """Summarize new execution fields while accepting older, smaller CSV schemas."""
    def number(value):
        try:
            if value is None or str(value).strip() == '':
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def numeric_values(*fields):
        values = []
        for row in rows:
            for field in fields:
                value = number(row.get(field))
                if value is not None:
                    values.append(value)
                    break
        return values

    def unique_values(*fields):
        values = set()
        for row in rows:
            for field in fields:
                value = str(row.get(field) or '').strip()
                if value:
                    values.add(value)
                    break
        return sorted(values)

    prompt_tokens = numeric_values('prompt_eval_count')
    output_tokens = numeric_values('eval_count')
    total_tokens = 0
    total_tokens_recorded = False
    for row in rows:
        recorded_total = number(row.get('total_token_count'))
        if recorded_total is not None:
            total_tokens += recorded_total
            total_tokens_recorded = True
            continue
        prompt_count = number(row.get('prompt_eval_count'))
        output_count = number(row.get('eval_count'))
        if prompt_count is not None or output_count is not None:
            total_tokens += (prompt_count or 0) + (output_count or 0)
            total_tokens_recorded = True
    total_tokens = int(total_tokens) if total_tokens_recorded else None

    first_output = numeric_values('time_to_first_output_seconds', 'time_to_first_token_seconds')
    first_answer = numeric_values('time_to_first_answer_seconds', 'time_to_first_response_seconds')
    timeout_fields_recorded = any(
        'timed_out' in row or 'response_timeout_seconds' in row or 'timeout_seconds' in row
        or (row.get('status') or '').strip().lower() == 'timeout'
        for row in rows
    )
    thinking_capable_recorded = any(str(row.get('thinking_capable') or '').strip() for row in rows)
    thinking_used_recorded = any(str(row.get('thinking_used') or '').strip() for row in rows)
    done_recorded = any(str(row.get('done') or '').strip() for row in rows)

    return {
        'benchmark_profiles': unique_values('benchmark_profile'),
        'grading_profiles': unique_values('grading_profile'),
        'campaign_ids': unique_values(*CAMPAIGN_ID_FIELDS),
        'pair_ids': unique_values(*PAIR_ID_FIELDS),
        'treatment_ids': unique_values(*TREATMENT_ID_FIELDS),
        'treatment_keys': unique_values(*TREATMENT_KEY_FIELDS),
        'treatment_roles': unique_values('treatment_role'),
        'treatment_orders': unique_values(*TREATMENT_ORDER_FIELDS),
        'row_ids': unique_values('row_id'),
        'plan_hashes': unique_values('plan_sha256'),
        'pair_schema_versions': unique_values('pair_schema_version'),
        'campaign_seeds': unique_values('campaign_seed'),
        'pair_kinds': unique_values('pair_kind'),
        'off_available': unique_values('off_available'),
        'protocol_errors': unique_values('protocol_error'),
        'think_request_values': unique_values('think_payload_json', 'think_request_value', 'thinking_requested'),
        'task_set_hashes': unique_values('task_set_sha256'),
        'requested_num_ctx': unique_values('requested_num_ctx'),
        'runner_hashes': unique_values('runner_sha256'),
        'grader_hashes': unique_values('grader_sha256'),
        'output_token_policies': unique_values('output_token_policy'),
        'output_token_limits': unique_values('output_token_limit'),
        'response_timeouts': unique_values('response_timeout_seconds', 'timeout_seconds'),
        'thinking_requested': unique_values('thinking_requested', 'thinking_mode'),
        'thinking_resolved': unique_values('thinking_resolved', 'thinking_effective'),
        'thinking_effective': unique_values('thinking_effective'),
        'thinking_capable_count': sum(1 for row in rows if csv_bool(row.get('thinking_capable'))),
        'thinking_capable_recorded': thinking_capable_recorded,
        'thinking_used_count': sum(1 for row in rows if csv_bool(row.get('thinking_used'))),
        'thinking_used_recorded': thinking_used_recorded,
        'timeouts': sum(1 for row in rows if row_timed_out(row)),
        'timeout_data_recorded': timeout_fields_recorded,
        'completed_rows': sum(1 for row in rows if csv_bool(row.get('done'))),
        'done_recorded': done_recorded,
        'done_reasons': unique_values('done_reason'),
        'termination_reasons': unique_values('termination_reason'),
        'total_token_count': total_tokens,
        'response_chars': int(sum(numeric_values('response_chars'))) if numeric_values('response_chars') else None,
        'response_bytes': int(sum(numeric_values('response_bytes'))) if numeric_values('response_bytes') else None,
        'thinking_chars': int(sum(numeric_values('thinking_chars'))) if numeric_values('thinking_chars') else None,
        'thinking_bytes': int(sum(numeric_values('thinking_bytes'))) if numeric_values('thinking_bytes') else None,
        'stream_chunk_count': int(sum(numeric_values('stream_chunk_count'))) if numeric_values('stream_chunk_count') else None,
        'avg_time_to_first_output': round(sum(first_output) / len(first_output), 3) if first_output else None,
        'avg_time_to_first_answer': round(sum(first_answer) / len(first_answer), 3) if first_answer else None,
    }

def ollama_show(name):
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/show',
            data=json.dumps({'name': name}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        return json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:
        return {}

def first_model_info_value(model_info, suffix):
    for key, value in (model_info or {}).items():
        if key.endswith(suffix):
            return value
    return ''

def load_ollama_models():
    data = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=20))
    models = []
    for m in data.get('models', []):
        name = m.get('name') or m.get('model')
        d = dict(m.get('details') or {})
        caps = m.get('capabilities') or []
        needs_show = not caps or not d.get('family') or not d.get('parameter_size') or not d.get('context_length')
        show = ollama_show(name) if name and needs_show else {}
        show_details = show.get('details') or {}
        show_caps = show.get('capabilities') or []
        # Ollama /api/tags may omit capabilities even though /api/show reports
        # completion/tools/vision/thinking. Prefer tag data when present, but
        # backfill from /api/show so dashboard capability stats do not render as 0.
        if not caps and show_caps:
            caps = show_caps
        model_info = show.get('model_info') or {}
        d = {**show_details, **{k: v for k, v in d.items() if v not in ('', None)}}
        context = d.get('context_length') or first_model_info_value(model_info, '.context_length')
        embedding = d.get('embedding_length') or first_model_info_value(model_info, '.embedding_length')
        models.append({
            'name': name,
            'model': m.get('model') or m.get('name'),
            'size': int(m.get('size') or 0),
            'size_h': human_size(m.get('size') or 0),
            'size_gb': size_gb(m.get('size') or 0),
            'digest': (m.get('digest') or '')[:12],
            'modified': m.get('modified_at') or '',
            'family': d.get('family') or 'unknown',
            'parameter_size': d.get('parameter_size') or '—',
            'quant': d.get('quantization_level') or '—',
            'context': context or '',
            'embedding': embedding or '',
            'format': d.get('format') or '—',
            'capabilities': caps,
            'capabilities_s': ', '.join(caps) if caps else '—',
        })
    return sorted(models, key=lambda x: x['size'], reverse=True)

def load_openclaw_status():
    p = run(['openclaw','models','status','--json'])
    if p.returncode != 0:
        return {'error': p.stderr or p.stdout}
    try:
        return json.loads(p.stdout)
    except Exception as e:
        return {'error': str(e), 'raw': p.stdout[:2000]}

def load_hermes_model_status():
    cfg = HOME / '.hermes/config.yaml'
    status = {'model': 'gpt-5.5', 'provider': 'openai-codex', 'base_url': 'https://chatgpt.com/backend-api/codex', 'fallbacks': []}
    try:
        text = cfg.read_text(encoding='utf-8')
        lines = text.splitlines()
        in_model = False
        in_fallbacks = False
        for line in lines:
            if re.match(r'^model:\s*$', line):
                in_model = True
                in_fallbacks = False
                continue
            fb_inline = re.match(r'^fallback_providers:\s*(.*?)\s*$', line)
            if fb_inline:
                in_model = False
                raw = fb_inline.group(1).strip()
                if raw.startswith('[') and raw.endswith(']'):
                    inner = raw[1:-1].strip()
                    status['fallbacks'] = [x.strip().strip('"\'') for x in inner.split(',') if x.strip()]
                    in_fallbacks = False
                else:
                    status['fallbacks'] = []
                    in_fallbacks = True
                continue
            if in_model and re.match(r'^[A-Za-z0-9_].*:\s*', line):
                in_model = False
            if in_model:
                m = re.match(r'^\s+(default|provider|base_url):\s*(.*?)\s*$', line)
                if m:
                    status['model' if m.group(1) == 'default' else m.group(1)] = m.group(2).strip().strip('"\'') or '—'
            if in_fallbacks:
                if re.match(r'^[A-Za-z0-9_].*:\s*', line):
                    in_fallbacks = False
                    continue
                item = re.match(r'^\s*-\s*(.*?)\s*$', line)
                if item and item.group(1).strip():
                    status['fallbacks'].append(item.group(1).strip().strip('"\''))
    except Exception:
        pass
    provider = (status.get('provider') or '').lower()
    base_url = (status.get('base_url') or '').lower()
    model = (status.get('model') or '').lower()
    local_markers = ['ollama', 'openclaw', 'lmstudio', 'local', 'llama.cpp', 'vllm']
    local_urls = ['localhost', '127.0.0.1', '0.0.0.0', '10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.']
    is_local = any(x in provider for x in local_markers) or any(x in model for x in local_markers) or any(x in base_url for x in local_urls)
    status['runtime'] = 'Local model' if is_local else 'Cloud model'
    status['runtime_class'] = 'local' if is_local else 'cloud'
    status['context_length'] = resolve_hermes_context_length(status.get('model'))
    status['context_text'] = token_count_text(status.get('context_length'))
    status['fallbacks_text'] = ', '.join(status.get('fallbacks') or []) if status.get('fallbacks') else 'none'
    return status

def load_benchmark_summary(csv_path: Path|list[Path]|None):
    if not csv_path:
        return {}, None
    paths = list(csv_path) if isinstance(csv_path, list) else [csv_path]
    paths = [p for p in paths if p and p.exists()]
    if not paths:
        return {}, None
    paths = sorted(paths, key=lambda p: p.stat().st_mtime)
    by, source_by_model, sources_by_model = merge_latest_provenance_cohort(paths)
    out = {}
    for model, task_rows in by.items():
        rs = list(task_rows.values())
        skips = [r for r in rs if r.get('verdict') == 'skip' or r.get('status') == 'skip']
        grader_errors = [r for r in rs if r.get('verdict') == 'grader_error']
        scored_rows = [r for r in rs if r not in skips and r not in grader_errors]
        passes = [r for r in scored_rows if r.get('status') == 'ok' and r.get('verdict') == 'pass']
        failed_rows = [r for r in scored_rows if not (r.get('status') == 'ok' and r.get('verdict') == 'pass')]
        failed_tests = []
        for r in failed_rows:
            name = (r.get('task_name') or r.get('task_id') or 'unknown test').strip()
            if name and name not in failed_tests:
                failed_tests.append(name)
        def f(v):
            try: return float(v)
            except Exception: return None
        def avg(vals):
            vals=[v for v in vals if v is not None]
            return round(sum(vals)/len(vals), 3) if vals else None
        def mx(field):
            vals=[f(r.get(field)) for r in rs if f(r.get(field)) is not None]
            return round(max(vals), 3) if vals else None
        def av(field):
            return avg([f(r.get(field)) for r in rs])
        def sm(field):
            vals=[f(r.get(field)) for r in rs if f(r.get(field)) is not None]
            return int(sum(vals)) if vals else None
        avg_pass_wall = avg([f(r.get('wall_seconds')) for r in passes])
        errors = sum(1 for r in rs if r.get('status') == 'error' or (r.get('error') or '').strip())
        fallbacks_used = sum(1 for r in rs if str(r.get('fallback_used', '')).lower() == 'true')
        categories = sorted({r.get('category') or 'unknown' for r in rs})
        families = sorted({r.get('benchmark_family') or 'unknown' for r in rs})
        cat_pass = {}
        for c in categories:
            cr = [r for r in rs if (r.get('category') or 'unknown') == c]
            cat_pass[c] = f"{sum(1 for r in cr if r.get('verdict') == 'pass')}/{sum(1 for r in cr if r.get('verdict') not in ('skip', 'grader_error'))}" if cr else '—'
        agent_models = sorted({(r.get('agent_model') or '').strip() for r in rs if (r.get('agent_model') or '').strip()})
        winner_models = sorted({(r.get('winner_model') or '').strip() for r in rs if (r.get('winner_model') or '').strip()})
        hosts=sorted({r.get('host_label') or r.get('host') for r in rs if r.get('host_label') or r.get('host')})
        platforms=sorted({r.get('platform') for r in rs if r.get('platform')})
        telemetry_backends=sorted({r.get('telemetry_backend') for r in rs if r.get('telemetry_backend')})
        ollama_versions=sorted({r.get('ollama_version') for r in rs if r.get('ollama_version')})
        model_digests=sorted({r.get('model_digest') for r in rs if r.get('model_digest')})
        test_details = []
        preview_details = []
        for r in sorted(rs, key=lambda x: x.get('task_id') or x.get('task_name') or ''):
            label = (r.get('task_id') or r.get('task_name') or 'unknown').strip()
            verdict = (r.get('verdict') or r.get('status') or 'unknown').strip()
            bits = [f"{label}: {verdict}"]
            if f(r.get('wall_seconds')) is not None:
                bits.append(f"{f(r.get('wall_seconds')):.2f}s")
            if f(r.get('tokens_per_second')) is not None:
                bits.append(f"{f(r.get('tokens_per_second')):.1f} tok/s")
            if f(r.get('eval_count')) is not None:
                bits.append(f"{int(f(r.get('eval_count')))} output tok")
            if f(r.get('thinking_chars')) is not None:
                bits.append(f"{int(f(r.get('thinking_chars')))} thinking chars")
            if row_timed_out(r):
                bits.append('timed out')
            elif (r.get('done_reason') or '').strip():
                bits.append('done: ' + (r.get('done_reason') or '').strip())
            test_details.append(' · '.join(bits))
            preview = (r.get('assistant_text_preview') or r.get('response_preview') or '').strip()
            err = (r.get('grader_error') or r.get('error') or '').strip()
            if preview or err:
                preview_details.append(f"{label}: " + (err if err else preview[:220]))
        out[model] = {
            **summarize_execution_schema(rs),
            'passed': len(passes), 'tasks': len(scored_rows), 'rows': len(rs), 'skipped': len(skips), 'grader_errors': len(grader_errors), 'errors': errors, 'failed_tests': failed_tests,
            'avg_pass_wall': avg_pass_wall, 'avg_wall': av('wall_seconds'),
            'avg_ollama_total': av('ollama_total_seconds'), 'avg_ollama_load': av('ollama_load_seconds'),
            'avg_prompt_eval': av('ollama_prompt_eval_seconds'), 'avg_eval': av('ollama_eval_seconds'),
            'prompt_eval_count': sm('prompt_eval_count'), 'eval_count': sm('eval_count'),
            'avg_tps': av('tokens_per_second'), 'avg_openclaw_ms': av('openclaw_duration_ms'),
            'max_cpu_pct': mx('max_cpu_usage_pct'), 'avg_cpu_pct': av('avg_cpu_usage_pct'),
            'max_gpu_pct': mx('max_gpu_usage_pct'), 'avg_gpu_pct': av('avg_gpu_usage_pct'),
            'max_cpu_temp': mx('max_cpu_temp_c'), 'avg_cpu_temp': av('avg_cpu_temp_c'),
            'max_gpu_temp': mx('max_gpu_temp_c'), 'avg_gpu_temp': av('avg_gpu_temp_c'),
            'max_soc_temp': mx('max_soc_temp_c'),
            'max_host_temp': mx('max_host_temp_c'), 'avg_host_temp': av('avg_host_temp_c'),
            'max_cpu_power': mx('max_cpu_power_w'), 'avg_cpu_power': av('avg_cpu_power_w'),
            'max_gpu_power': mx('max_gpu_power_w'), 'avg_gpu_power': av('avg_gpu_power_w'),
            'max_system_power': mx('max_system_power_w'),
            'max_total_power': mx('max_total_power_w'), 'avg_total_power': av('avg_total_power_w'),
            'sample_count': sm('sample_count'), 'fallbacks_used': fallbacks_used,
            'fallback_attempts': sm('fallback_attempts'), 'exit_code_max': mx('exit_code'),
            'categories': categories, 'families': families, 'category_pass': cat_pass,
            'agent_models': agent_models, 'winner_models': winner_models,
            'hosts':hosts,'platforms':platforms,'telemetry_backends':telemetry_backends,'ollama_versions':ollama_versions,'model_digests':model_digests,
            'test_details': test_details, 'preview_details': preview_details,
            'csv': str(source_by_model.get(model) or paths[-1]),
            'csvs': [str(path) for path in sources_by_model.get(model, [])],
        }
    return out, paths[-1]

def load_standardized_summary(csv_path: Path|list[Path]|None):
    if not csv_path:
        return {}, None
    paths = list(csv_path) if isinstance(csv_path, list) else [csv_path]
    paths = sorted([p for p in paths if p and p.exists()], key=lambda p: p.stat().st_mtime)
    if not paths:
        return {}, None
    by, source_by_model, sources_by_model = merge_latest_provenance_cohort(paths)
    out = {}
    def f(v):
        try: return float(v)
        except Exception: return None
    def avg(vals):
        vals=[v for v in vals if v is not None]
        return round(sum(vals)/len(vals), 3) if vals else None
    for model, task_rows in by.items():
        rs = list(task_rows.values())
        passes=[r for r in rs if r.get('verdict') == 'pass']
        skips=[r for r in rs if r.get('verdict') == 'skip' or r.get('status') == 'skip']
        grader_errors=[r for r in rs if r.get('verdict') == 'grader_error']
        non_skip=[r for r in rs if r not in skips and r not in grader_errors]
        failed=[r for r in non_skip if r.get('verdict') != 'pass']
        def sm(field):
            vals=[f(r.get(field)) for r in non_skip if f(r.get(field)) is not None]
            return int(sum(vals)) if vals else None
        def mx(field):
            vals=[f(r.get(field)) for r in rs if f(r.get(field)) is not None]
            return round(max(vals), 3) if vals else None
        def av(field):
            return avg([f(r.get(field)) for r in rs])
        categories=sorted({r.get('category') or 'unknown' for r in rs})
        families=sorted({r.get('benchmark_family') or 'unknown' for r in rs})
        model_families=sorted({r.get('family') or 'unknown' for r in rs})
        params=sorted({r.get('params') or '—' for r in rs})
        quants=sorted({r.get('quant') or '—' for r in rs})
        capabilities=sorted({r.get('capabilities') or '—' for r in rs})
        hosts=sorted({r.get('host_label') or r.get('host') for r in rs if r.get('host_label') or r.get('host')})
        platforms=sorted({r.get('platform') for r in rs if r.get('platform')})
        telemetry_backends=sorted({r.get('telemetry_backend') for r in rs if r.get('telemetry_backend')})
        ollama_versions=sorted({r.get('ollama_version') for r in rs if r.get('ollama_version')})
        model_digests=sorted({r.get('model_digest') for r in rs if r.get('model_digest')})
        cat_pass={}
        for c in categories:
            cr=[r for r in rs if (r.get('category') or 'unknown') == c]
            cat_pass[c] = f"{sum(1 for r in cr if r.get('verdict') == 'pass')}/{sum(1 for r in cr if r.get('verdict') not in ('skip', 'grader_error'))}" if cr else '—'
        test_details = []
        preview_details = []
        for r in sorted(rs, key=lambda x: (x.get('benchmark_family') or '', x.get('task_id') or x.get('task_name') or '')):
            label = (r.get('task_id') or r.get('task_name') or 'unknown').strip()
            family = (r.get('benchmark_family') or r.get('category') or 'standardized').strip()
            verdict = (r.get('verdict') or r.get('status') or 'unknown').strip()
            bits = [f"{family}/{label}: {verdict}"]
            if f(r.get('wall_seconds')) is not None:
                bits.append(f"{f(r.get('wall_seconds')):.2f}s")
            if f(r.get('tokens_per_second')) is not None:
                bits.append(f"{f(r.get('tokens_per_second')):.1f} tok/s")
            if f(r.get('eval_count')) is not None:
                bits.append(f"{int(f(r.get('eval_count')))} output tok")
            if f(r.get('thinking_chars')) is not None:
                bits.append(f"{int(f(r.get('thinking_chars')))} thinking chars")
            if row_timed_out(r):
                bits.append('timed out')
            elif (r.get('done_reason') or '').strip():
                bits.append('done: ' + (r.get('done_reason') or '').strip())
            test_details.append(' · '.join(bits))
            preview = (r.get('response_preview') or '').strip()
            err = (r.get('grader_error') or r.get('error') or '').strip()
            if preview or err:
                preview_details.append(f"{family}/{label}: " + (err if err else preview[:220]))
        def summarize_subset(sub_rows):
            sub_skips=[r for r in sub_rows if r.get('verdict') == 'skip' or r.get('status') == 'skip']
            sub_grader_errors=[r for r in sub_rows if r.get('verdict') == 'grader_error']
            sub_non_skip=[r for r in sub_rows if r not in sub_skips and r not in sub_grader_errors]
            sub_passes=[r for r in sub_non_skip if r.get('verdict') == 'pass']
            sub_failed=[r for r in sub_non_skip if r.get('verdict') != 'pass']
            def sub_sm(field):
                vals=[f(r.get(field)) for r in sub_non_skip if f(r.get(field)) is not None]
                return int(sum(vals)) if vals else None
            def sub_mx(field):
                vals=[f(r.get(field)) for r in sub_rows if f(r.get(field)) is not None]
                return round(max(vals), 3) if vals else None
            def sub_av(field):
                return avg([f(r.get(field)) for r in sub_rows])
            sub_details=[]
            for r in sorted(sub_rows, key=lambda x: x.get('task_id') or x.get('task_name') or ''):
                label=(r.get('task_id') or r.get('task_name') or 'unknown').strip()
                verdict=(r.get('verdict') or r.get('status') or 'unknown').strip()
                bits=[f"{label}: {verdict}"]
                if f(r.get('wall_seconds')) is not None:
                    bits.append(f"{f(r.get('wall_seconds')):.2f}s")
                if f(r.get('tokens_per_second')) is not None:
                    bits.append(f"{f(r.get('tokens_per_second')):.1f} tok/s")
                if f(r.get('eval_count')) is not None:
                    bits.append(f"{int(f(r.get('eval_count')))} output tok")
                if f(r.get('thinking_chars')) is not None:
                    bits.append(f"{int(f(r.get('thinking_chars')))} thinking chars")
                if row_timed_out(r):
                    bits.append('timed out')
                elif (r.get('done_reason') or '').strip():
                    bits.append('done: ' + (r.get('done_reason') or '').strip())
                sub_details.append(' · '.join(bits))
            return {
                **summarize_execution_schema(sub_rows),
                'passed': len(sub_passes), 'tasks': len(sub_non_skip), 'rows': len(sub_rows), 'skipped': len(sub_skips), 'grader_errors': len(sub_grader_errors),
                'errors': sum(1 for r in sub_rows if r.get('status') == 'error' or (r.get('error') or '').strip()),
                'failed_tests': [(r.get('task_name') or r.get('task_id') or 'unknown') for r in sub_failed],
                'avg_wall': avg([f(r.get('wall_seconds')) for r in sub_non_skip]),
                'avg_pass_wall': avg([f(r.get('wall_seconds')) for r in sub_passes]),
                'avg_ollama_total': avg([f(r.get('ollama_total_seconds')) for r in sub_non_skip]),
                'avg_ollama_load': avg([f(r.get('ollama_load_seconds')) for r in sub_non_skip]),
                'avg_prompt_eval': avg([f(r.get('ollama_prompt_eval_seconds')) for r in sub_non_skip]),
                'avg_eval': avg([f(r.get('ollama_eval_seconds')) for r in sub_non_skip]),
                'prompt_eval_count': sub_sm('prompt_eval_count'), 'eval_count': sub_sm('eval_count'),
                'avg_tps': avg([f(r.get('tokens_per_second')) for r in sub_non_skip]),
                'max_cpu_pct': sub_mx('max_cpu_usage_pct'), 'avg_cpu_pct': sub_av('avg_cpu_usage_pct'),
                'max_gpu_pct': sub_mx('max_gpu_usage_pct'), 'avg_gpu_pct': sub_av('avg_gpu_usage_pct'),
                'max_cpu_temp': sub_mx('max_cpu_temp_c'), 'avg_cpu_temp': sub_av('avg_cpu_temp_c'),
                'max_gpu_temp': sub_mx('max_gpu_temp_c'), 'avg_gpu_temp': sub_av('avg_gpu_temp_c'),
                'max_soc_temp': sub_mx('max_soc_temp_c'),
                'max_host_temp': sub_mx('max_host_temp_c'), 'avg_host_temp': sub_av('avg_host_temp_c'),
                'max_cpu_power': sub_mx('max_cpu_power_w'), 'avg_cpu_power': sub_av('avg_cpu_power_w'),
                'max_gpu_power': sub_mx('max_gpu_power_w'), 'avg_gpu_power': sub_av('avg_gpu_power_w'),
                'max_system_power': sub_mx('max_system_power_w'),
                'max_total_power': sub_mx('max_total_power_w'), 'avg_total_power': sub_av('avg_total_power_w'),
                'sample_count': sub_sm('sample_count'), 'test_details': sub_details,
            }
        family_summaries={fam: summarize_subset([r for r in rs if (r.get('benchmark_family') or 'unknown') == fam]) for fam in families}
        out[model]={
            **summarize_execution_schema(rs),
            'passed': len(passes), 'tasks': len(non_skip), 'rows': len(rs), 'skipped': len(skips), 'grader_errors': len(grader_errors), 'errors': sum(1 for r in rs if r.get('status') == 'error' or (r.get('error') or '').strip()),
            'avg_wall': avg([f(r.get('wall_seconds')) for r in non_skip]),
            'avg_pass_wall': avg([f(r.get('wall_seconds')) for r in passes]),
            'avg_ollama_total': avg([f(r.get('ollama_total_seconds')) for r in non_skip]),
            'avg_ollama_load': avg([f(r.get('ollama_load_seconds')) for r in non_skip]),
            'avg_prompt_eval': avg([f(r.get('ollama_prompt_eval_seconds')) for r in non_skip]),
            'avg_eval': avg([f(r.get('ollama_eval_seconds')) for r in non_skip]),
            'prompt_eval_count': sm('prompt_eval_count'), 'eval_count': sm('eval_count'),
            'avg_tps': avg([f(r.get('tokens_per_second')) for r in non_skip]),
            'max_cpu_pct': mx('max_cpu_usage_pct'), 'avg_cpu_pct': av('avg_cpu_usage_pct'),
            'max_gpu_pct': mx('max_gpu_usage_pct'), 'avg_gpu_pct': av('avg_gpu_usage_pct'),
            'max_cpu_temp': mx('max_cpu_temp_c'), 'avg_cpu_temp': av('avg_cpu_temp_c'),
            'max_gpu_temp': mx('max_gpu_temp_c'), 'avg_gpu_temp': av('avg_gpu_temp_c'),
            'max_soc_temp': mx('max_soc_temp_c'),
            'max_host_temp': mx('max_host_temp_c'), 'avg_host_temp': av('avg_host_temp_c'),
            'max_cpu_power': mx('max_cpu_power_w'), 'avg_cpu_power': av('avg_cpu_power_w'),
            'max_gpu_power': mx('max_gpu_power_w'), 'avg_gpu_power': av('avg_gpu_power_w'),
            'max_system_power': mx('max_system_power_w'),
            'max_total_power': mx('max_total_power_w'), 'avg_total_power': av('avg_total_power_w'),
            'sample_count': sm('sample_count'),
            'model_families': model_families, 'params': params, 'quants': quants, 'capabilities_s': ', '.join(capabilities),
            'hosts':hosts,'platforms':platforms,'telemetry_backends':telemetry_backends,'ollama_versions':ollama_versions,'model_digests':model_digests,
            'categories': categories, 'families': families, 'category_pass': cat_pass, 'family_summaries': family_summaries,
            'failed_tests': [(r.get('benchmark_family') or r.get('category') or '') + ': ' + (r.get('task_name') or r.get('task_id') or 'unknown') for r in failed],
            'test_details': test_details, 'preview_details': preview_details,
            'csv': str(source_by_model.get(model) or paths[-1]),
            'csvs': [str(path) for path in sources_by_model.get(model, [])],
        }
    for model, comparison in load_latest_treatment_experiments(paths).items():
        if model not in out and comparison.get('plan_only'):
            source_csv = comparison.get('source_csv') or str(paths[-1])
            source_evidence = comparison.get('source_plan') or source_csv
            out[model] = {
                **summarize_execution_schema([]),
                'passed': 0,
                'tasks': 0,
                'rows': 0,
                'skipped': 0,
                'grader_errors': 0,
                'errors': 0,
                'avg_wall': None,
                'avg_pass_wall': None,
                'avg_ollama_total': None,
                'avg_ollama_load': None,
                'avg_prompt_eval': None,
                'avg_eval': None,
                'prompt_eval_count': None,
                'eval_count': None,
                'avg_tps': None,
                'max_cpu_pct': None,
                'avg_cpu_pct': None,
                'max_gpu_pct': None,
                'avg_gpu_pct': None,
                'max_cpu_temp': None,
                'avg_cpu_temp': None,
                'max_gpu_temp': None,
                'avg_gpu_temp': None,
                'max_soc_temp': None,
                'max_host_temp': None,
                'avg_host_temp': None,
                'max_cpu_power': None,
                'avg_cpu_power': None,
                'max_gpu_power': None,
                'avg_gpu_power': None,
                'max_system_power': None,
                'max_total_power': None,
                'avg_total_power': None,
                'sample_count': None,
                'model_families': [],
                'params': [],
                'quants': [],
                'capabilities_s': '—',
                'hosts': [],
                'platforms': [],
                'telemetry_backends': [],
                'ollama_versions': [],
                'model_digests': [],
                'categories': [],
                'families': [],
                'category_pass': {},
                'family_summaries': {},
                'failed_tests': [],
                'test_details': [],
                'preview_details': [],
                'csv': source_evidence,
                'csvs': [source_evidence] if source_evidence else [],
                'plan_only': True,
            }
        if model in out:
            out[model]['treatment_comparison'] = comparison
    return out, paths[-1]


def esc(x): return html.escape(str(x if x is not None else ''))
def model_anchor(name):
    return 'model-' + re.sub(r'[^a-zA-Z0-9_-]+', '-', str(name)).strip('-').lower()


def model_slug(name):
    return re.sub(r'[^a-zA-Z0-9._-]+', '-', str(name)).strip('-').lower() or 'model'

def detail_href(name):
    return 'Local LLM Model Research/' + model_slug(name) + '.html'

def rank_benchmarks(summary):
    items = []
    for model, result in (summary or {}).items():
        comparison = (result or {}).get('treatment_comparison') or {}
        if comparison and comparison.get('rankable') is False:
            continue
        items.append((model, result))
    accuracy_only = any(
        ((result or {}).get('treatment_comparison') or {}).get('per_model_context_policy')
        for _, result in items
    )
    def rank_values(item):
        model, result = item
        comparison, _, first, _, second = comparison_sides(result)
        if comparison and first and second:
            passed = (first.get('passed') or 0) + (second.get('passed') or 0)
            if accuracy_only:
                return (-passed, model)
            wall = (first.get('wall_seconds_total') or 0) + (second.get('wall_seconds_total') or 0)
            tasks = (first.get('tasks') or 0) + (second.get('tasks') or 0)
            avg_wall = wall / tasks if tasks else 10**9
            return (-passed, avg_wall, model)
        if accuracy_only:
            return (-(result.get('passed') or 0), model)
        return (-(result.get('passed') or 0), result.get('avg_pass_wall') if isinstance(result.get('avg_pass_wall'), (int,float)) else 10**9, model)
    items.sort(key=rank_values)
    return {m: i+1 for i, (m, _) in enumerate(items)}, len(items)

def bench_score_text(s, rank=None, total=None):
    if not s:
        return 'Not benchmarked yet'
    rank_text = f' · Rank #{rank}/{total}' if rank and total else ''
    comparison, first_label, first, second_label, second = comparison_sides(s)
    if comparison:
        if comparison.get('capacity_disposition') and comparison.get('context_calibration_status') == 'no-fit':
            return f"Not benchmarked: no context fit · {comparison_status_text(comparison)}"
        return f"{first_label} {treatment_score_text(first)} · {second_label} {treatment_score_text(second)} · pair {comparison_status_text(comparison)}{rank_text}"
    return f"{s.get('passed', 0)}/{s.get('tasks', 0)} pass · avg wall {secs(s.get('avg_pass_wall'))}{rank_text}"

RESEARCH_PROFILES = [
    ('gemma4', {'family_title':'Google Gemma 4','release':'Google AI search result: model card dated Jun 10, 2026; launch references Apr 2026.','overview':'Gemma 4 is Google DeepMind’s open model family focused on frontier-level local performance, reasoning, agentic workflows, coding, and multimodal understanding.','strengths':['Strong general reasoning and instruction following for local use','Good fit for agentic workflows, coding, and multimodal understanding when the exact tag supports those modalities','Apache 2.0 family according to Google model-card search result','Multiple local sizes allow practical quality/speed trade-offs'],'weaknesses':['Exact local behavior depends heavily on quantization and Ollama packaging','Large 26B/31B variants can be memory/latency heavy on local hardware','Online details for some Ollama tag variants may lag behind provider model-card details'],'sources':[('Gemma 4 model card · Google AI for Developers','https://ai.google.dev/gemma/docs/core/model_card_4'),('Gemma 4 · Google DeepMind','https://deepmind.google/models/gemma/gemma-4/'),('Ollama gemma4 tags','https://ollama.com/library/gemma4/tags')]}),
    ('qwen3-coder', {'family_title':'Qwen3-Coder','release':'Search results cite Qwen3-Coder 30B-A3B release July 31, 2025.','overview':'Qwen3-Coder is Qwen’s code-specialized and agentic coding model line, with emphasis on coding tasks, tool use, structured outputs, and long-context agent workflows.','strengths':['Excellent fit for coding, debugging, repository edits, and agent tool workflows','MoE 30B-A3B variants can offer strong capability while activating fewer parameters per token','Long-context references up to 160K–256K depending on serving/package','Strong local OpenClaw candidate when benchmark latency is acceptable'],'weaknesses':['Coder specialization may be less balanced for broad creative/general tasks','MoE/local quantized packages can have variable latency and memory pressure','Agentic behavior still needs harness-level validation for tool reliability'],'sources':[('Qwen3-Coder GitHub','https://github.com/QwenLM/Qwen3-Coder'),('Qwen3-Coder 30B-A3B · FitMyLLM','https://www.fitmyllm.com/model/qwen3-coder-30b-a3b'),('Qwen3-Coder local run notes · Unsloth','https://unsloth.ai/docs/models/tutorials/qwen3-coder-how-to-run-locally')]}),
    ('qwen2.5-coder', {'family_title':'Qwen2.5-Coder','release':'Ollama/library and OpenRouter references describe the Qwen2.5-Coder release family; 32B is positioned as competitive with GPT-4o for coding.','overview':'Qwen2.5-Coder is a code-focused Qwen model family spanning 0.5B through 32B, optimized for code generation, code reasoning, code fixing, and real-world coding agents.','strengths':['Strong coding generation, reasoning, and bug-fixing','Broad language coverage and strong 7B/14B/32B scaling options','Good practical local benchmark performance in this dashboard','Useful as a balanced OpenClaw default for coding-heavy automation'],'weaknesses':['Not multimodal; mostly text/code focused','32B/14B high-precision variants can be slower than smaller local models','May need prompting guardrails for concise non-code tasks'],'sources':[('Ollama qwen2.5-coder','https://ollama.com/library/qwen2.5-coder'),('OpenRouter Qwen2.5-Coder 32B Instruct','https://openrouter.ai/qwen/qwen-2.5-coder-32b-instruct'),('Qwen Coder overview','https://qwen3lm.com/qwen-coder/')]}),
    ('qwen3-vl', {'family_title':'Qwen3-VL','release':'arXiv search result lists Qwen3-VL Technical Report 2511.21631.','overview':'Qwen3-VL is Qwen’s vision-language family for text, image, and video-oriented understanding with long multimodal context.','strengths':['Vision-language reasoning and multimodal understanding','Long interleaved context references up to 256K tokens','Useful for image/document inspection workflows when runtime supports vision inputs'],'weaknesses':['Higher memory and slower runtime than text-only models of similar disk size','Vision support depends on the serving stack and local client path','Not always ideal as a default text-only OpenClaw model'],'sources':[('Qwen3-VL Technical Report','https://arxiv.org/abs/2511.21631'),('Qwen3-VL-30B-A3B · Hugging Face/Unsloth','https://huggingface.co/unsloth/Qwen3-VL-30B-A3B-Instruct'),('Artificial Analysis Qwen3-VL 30B A3B','https://artificialanalysis.ai/models/qwen3-vl-30b-a3b-instruct')]}),
    ('qwen3', {'family_title':'Qwen3','release':'Qwen3 Technical Report arXiv result dated May 15, 2025; Hugging Face Qwen3-32B page references May 14, 2025.','overview':'Qwen3 is Alibaba/Qwen’s general-purpose open model family with dense and MoE variants, long-context support, and strong multilingual/reasoning capability.','strengths':['Strong general-purpose reasoning and multilingual coverage','Dense 14B/32B and MoE 30B variants give useful local trade-offs','Good benchmark depth and official technical-report coverage'],'weaknesses':['Large dense variants can be slow locally','MoE variants may behave differently from dense variants despite similar disk size','May need prompt tuning for exact-format automation'],'sources':[('Qwen3 Technical Report PDF','https://arxiv.org/pdf/2505.09388'),('Qwen3-32B · Hugging Face','https://huggingface.co/Qwen/Qwen3-32B'),('Qwen speed benchmark docs','https://qwen.readthedocs.io/en/latest/getting_started/speed_benchmark.html')]}),
    ('glm-4.7', {'family_title':'GLM-4.7-Flash','release':'Z.ai docs/search results describe GLM-4.7-Flash as a small but powerful 30B-class MoE model.','overview':'GLM-4.7-Flash is a Z.ai/ZhipuAI 30B-class MoE model positioned for strong coding/agent benchmarks and efficient local deployment.','strengths':['Strong comparable-size coding and agent benchmarks per Z.ai docs','MoE architecture targets efficiency for local/served deployments','Supports vLLM/SGLang according to model-card references'],'weaknesses':['Less broadly established than Qwen/Gemma families in local tooling','Exact Ollama quant behavior should be trusted over generic online benchmarks','May require specialized prompts for non-coding tasks'],'sources':[('GLM-4.7 docs · Z.ai','https://docs.z.ai/guides/llm/glm-4.7'),('zai-org/GLM-4.7 · Hugging Face','https://huggingface.co/zai-org/GLM-4.7'),('GLM-4.7-Flash · Awesome Agents','https://awesomeagents.ai/models/glm-4-7-flash/')]}),
    ('deepseek-coder-v2', {'family_title':'DeepSeek-Coder-V2 Lite','release':'Hugging Face references the public DeepSeek-Coder-V2 16B/236B release based on DeepSeekMoE.','overview':'DeepSeek-Coder-V2 Lite Instruct is a code-focused MoE model using DeepSeekMoE, with a 16B class footprint and smaller active parameter count.','strengths':['Efficient coding model with MoE active-parameter advantage','Good fit for code completion, code reasoning, and local coding assistants','Smaller than many 30B+ coding models while remaining specialized'],'weaknesses':['Older than newer Qwen3-Coder/Devstral lines','Less ideal for non-code general chat','May have weaker agent/tool behavior than newer agentic coding models'],'sources':[('DeepSeek-Coder-V2-Instruct · Hugging Face','https://huggingface.co/deepseek-ai/DeepSeek-Coder-V2-Instruct'),('DeepSeek Coder guide','https://deepseekai.guide/models/deepseek-coder/')]}),
    ('gpt-oss', {'family_title':'OpenAI gpt-oss','release':'Search results cite Aug 5, 2025 for gpt-oss-20b and the official OpenAI model-card PDF.','overview':'gpt-oss-20b is OpenAI’s open-weight 21B-class reasoning model, designed for efficient deployment, instruction following, and agentic workflows.','strengths':['Strong instruction following and reasoning orientation','OpenAI model-card coverage and Apache 2.0 reference in search result','Designed with agentic workflows and tool use in mind'],'weaknesses':['Text-only; not a vision model','Reasoning models can be slower or verbose for simple deterministic tasks','Local quant/package behavior should be validated empirically'],'sources':[('OpenAI gpt-oss model card PDF','https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf'),('AI Agent Lab GPT-OSS evaluation','https://ai-agent-lab.github.io/gpt-oss/'),('GPT-OSS-20B benchmarks · LLM Leaderboard','https://llmleaderboard.ai/model/gpt-oss-20b/')]}),
    ('devstral', {'family_title':'Devstral','release':'Devstral is Mistral’s agentic coding model family; Devstral Small 2 is the newer 24B line.','overview':'Devstral is Mistral’s agentic coding model family, tuned for software engineering agents, tool use, codebase exploration, and multi-file editing.','strengths':['Purpose-built for software-engineering agent workflows','Good codebase exploration and multi-file editing fit','Large context references make it interesting for repository work'],'weaknesses':['License and commercial-use terms may require review depending on use','Coding specialization may reduce general-purpose breadth','Large local context can be memory-heavy'],'sources':[('Devstral Small 2 · Hugging Face/Unsloth','https://huggingface.co/unsloth/Devstral-Small-2-24B-Instruct-2512'),('LLM Explorer Devstral Small 2','https://llm-explorer.com/model/mistralai/Devstral-Small-2-24B-Instruct-2512,3zZaFL6H4Lk5XRfLuTSACD'),('Millstone Devstral benchmark','https://www.millstoneai.com/inference-benchmark/devstral-small-2-24b-instruct-2512-fp8')]}),
    ('command-r', {'family_title':'Cohere Command R','release':'Cohere documentation describes Command R and Command R+ with a substantial Aug 2024 update.','overview':'Command R is Cohere’s retrieval/tool-oriented conversational model family, known for RAG, citations, multilingual tasks, and tool use.','strengths':['Strong RAG/citation-grounded workflows','Conversational multilingual capability','Tool-use oriented model design'],'weaknesses':['Older local 35B class model may be slower than newer efficient MoE models','Not code-specialized compared with Qwen/Devstral/DeepSeek coding models','Local quant can materially affect quality'],'sources':[('Command R docs · Cohere','https://docs.cohere.com/docs/command-r'),('Command R pricing/benchmarks · Tokenando','https://tokenando.ai/models/cohere-command-r')]}),
    ('mistral-small', {'family_title':'Mistral Small','release':'Search results reference Mistral Small 3 24B / 3.2 model-line materials in 2025.','overview':'Mistral Small is a 24B-class Mistral family model focused on efficient general-purpose local inference with strong quality/latency trade-offs.','strengths':['Efficient 24B class general-purpose model','Good local fit for chat, summarization, and general assistant tasks','Typically faster/lighter than 30B+ dense models'],'weaknesses':['Not as code-specialized as Devstral or Qwen Coder','Exact latest variant can be ambiguous behind latest tags','Benchmark quality depends heavily on quantization'],'sources':[('Mistral Small 3 notebook · AWS samples','https://github.com/aws-samples/mistral-on-aws/blob/main/Mistral+Small+3/Mistral_small_3.ipynb'),('Mistral Small 3.2 · Elosia','https://elosia.ai/en/models/mistral-small-3-2-24b-instruct')]}),
    ('flux2-klein', {'family_title':'Flux2 Klein','release':'Search results indicate Flux2.Klein family materials/posts around 2026; authoritative public model-card coverage was sparse in live search.','overview':'Flux2 Klein appears to be a local image-generation model/pipeline tag rather than a text LLM; treat it as an image workflow model in the dashboard.','strengths':['Image-generation oriented local model','Useful for local/private visual generation workflows','Efficient Klein variant likely targets smaller/faster local inference'],'weaknesses':['Sparse authoritative online model-card detail found in live search','Not suitable as a text/OpenClaw LLM model','Benchmark dashboard LLM tests are not directly applicable to image-generation quality'],'sources':[('Open-source image generation models · BentoML','https://www.bentoml.com/blog/a-guide-to-open-source-image-generation-models')]}),
    ('llama-3.1-8b-abliterated', {'family_title':'Llama 3.1 8B Abliterated','release':'Ollama/Black Hills references identify this as a fork of an abliterated Llama 3.1 8B model.','overview':'This is an abliterated Llama 3.1 8B local tag, useful for experiments where refusal behavior differs from the base instruct model.','strengths':['Small 8B footprint and fast local runtime','Useful for controlled local experiments and RAG demonstrations','Well-known Llama 3.1 base ecosystem'],'weaknesses':['Abliteration can reduce safety alignment and refusal behavior','Quality/behavior may diverge from Meta’s official instruct model','License and use policy inherit Llama 3.1 constraints'],'sources':[('BlackHillsInfoSec Ollama model','https://ollama.com/BlackHillsInfoSec/llama-3.1-8b-abliterated'),('Black Hills RAG article','https://www.blackhillsinfosec.com/avoiding-dirty-rags/'),('Meta-Llama-3.1-8B-Instruct-abliterated · Hugging Face','https://huggingface.co/mlabonne/Meta-Llama-3.1-8B-Instruct-abliterated')]})]

def research_profile_for(m):
    name=(m.get('name') or '').lower(); family=(m.get('family') or '').lower()
    if 'qwen3.6' in name or 'qwen35' in family:
        base = next(p for k,p in RESEARCH_PROFILES if k == 'qwen3')
        cp = dict(base); cp['family_title']='Qwen3.6 / Qwen-family variant'; cp['release']='Specific Qwen3.6 public metadata was sparse in live search; page uses local Ollama manifest plus Qwen-family references.'; return cp
    for key, profile in RESEARCH_PROFILES:
        if key in name:
            return profile
    for key, profile in RESEARCH_PROFILES:
        if key in family:
            return profile
    return {'family_title': display_model_name(m), 'release':'Unknown from live/local metadata', 'overview':'No authoritative family profile was matched automatically; this page emphasizes local Ollama manifest data and benchmark results.', 'strengths':['Locally installed and available for private inference','Exact behavior can be evaluated with the attached benchmark stats'], 'weaknesses':['Online metadata was sparse or ambiguous for this exact tag','Use local benchmark results before selecting it as an agent default'], 'sources':[]}

def release_date_for(m, profile):
    name=(m.get('name') or '').lower(); family=(m.get('family') or '').lower()
    if 'gemma4' in name or 'gemma4' in family: return 'Apr 2, 2026 family launch · Jun 10, 2026 model card'
    if 'qwen3-coder' in name: return 'Jul 31, 2025 family/model release'
    if 'qwen2.5-coder' in name: return 'Nov 12, 2024 family release'
    if 'qwen3-vl' in name or 'qwen3vl' in family: return 'Nov 2025 technical-report family release'
    if 'qwen3.6' in name or 'qwen35' in family: return 'Unknown exact public date · Qwen-family variant'
    if 'qwen3' in name or family in {'qwen3','qwen3moe'}: return 'May 14–15, 2025 family/model release'
    if 'glm-4.7' in name or 'glm4' in family: return 'Unknown exact public date · GLM-4.7 family'
    if 'deepseek-coder-v2' in name or 'deepseek2' in family: return 'Jun 2024 family release'
    if 'gpt-oss' in name or 'gptoss' in family: return 'Aug 5, 2025 model release'
    if 'devstral' in name: return 'Dec 2025 model-line release (2512)'
    if 'command-r' in name or 'command-r' in family: return 'Mar 2024 initial · Aug 2024 major update'
    if 'mistral-small' in name: return 'Jan 2025 Mistral Small 3 line · exact latest tag varies'
    if 'flux2-klein' in name: return '2026 family references · exact authoritative date not found'
    if 'llama-3.1' in name: return 'Jul 23, 2024 Llama 3.1 base · abliterated fork date unknown'
    return 'Unknown / not found in available metadata'

def infer_operational_notes(m, ds, osum):
    strengths=[]; weaknesses=[]; caps=set(m.get('capabilities') or [])
    if 'tools' in caps: strengths.append('Tool-capable in Ollama metadata, useful for agent workflows.')
    if 'completion' in caps: strengths.append('Text completion/chat capable according to local Ollama metadata.')
    if 'image' in caps: strengths.append('Image-capable according to local Ollama metadata; evaluate with visual/image tasks, not only LLM text tests.')
    comparison = (ds or {}).get('treatment_comparison') or {}
    if comparison.get('capacity_disposition') and comparison.get('context_calibration_status') == 'no-fit':
        weaknesses.append('Not benchmarked: adaptive context calibration found no context fit on this host.')
    elif ds:
        if ds.get('passed') == ds.get('tasks'): strengths.append(f"Passed all Direct Ollama benchmark tasks ({ds.get('passed')}/{ds.get('tasks')}).")
        elif ds.get('failed_tests'): weaknesses.append('Failed Direct Ollama tests: ' + ', '.join(ds.get('failed_tests') or []))
        if isinstance(ds.get('avg_pass_wall'), (int,float)) and ds['avg_pass_wall'] <= 10: strengths.append('Fast Direct Ollama average pass latency on this host.')
        if isinstance(ds.get('avg_pass_wall'), (int,float)) and ds['avg_pass_wall'] > 60: weaknesses.append('Slow Direct Ollama average pass latency on this host.')
    else: weaknesses.append('No Direct Ollama benchmark row is currently available in the latest CSV.')
    if osum:
        if osum.get('passed') == osum.get('tasks'): strengths.append(f"Passed all OpenClaw harness tasks ({osum.get('passed')}/{osum.get('tasks')}).")
        elif osum.get('failed_tests'): weaknesses.append('Failed OpenClaw tests: ' + ', '.join(osum.get('failed_tests') or []))
    else: weaknesses.append('No OpenClaw benchmark row is currently available in the latest CSV.')
    if m.get('quant') in {'Q8_0'}: strengths.append('Higher-precision Q8 quantization can preserve more quality than Q4 variants.'); weaknesses.append('Q8 quantization consumes more disk/RAM and may run slower than Q4 variants.')
    if str(m.get('quant')).startswith('Q4'): strengths.append('Q4 quantization is storage/memory efficient for local use.'); weaknesses.append('Q4 quantization can reduce quality versus higher-precision variants.')
    return strengths, weaknesses

def generate_model_detail_pages(models, direct_summary, standardized_summary, oc_summary, direct_rank, direct_total, standardized_rank, standardized_total, oc_rank, oc_total, default, allowed, fallbacks, direct_csv, standardized_csv, oc_csv, now):
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    for m in models:
        name=m['name']
        # Detail pages should use the current combined Direct Ollama 18-test CSV first.
        # Older direct_summary smoke-only CSVs are retained only as a fallback.
        ds = standardized_summary.get(m['name']) or standardized_summary.get(m['model']) or direct_summary.get(m['name']) or direct_summary.get(m['model'])
        direct_rank_value = standardized_rank.get(name) if (standardized_summary.get(m['name']) or standardized_summary.get(m['model'])) else direct_rank.get(name)
        direct_total_value = standardized_total if (standardized_summary.get(m['name']) or standardized_summary.get(m['model'])) else direct_total
        direct_source_csv = standardized_csv if (standardized_summary.get(m['name']) or standardized_summary.get(m['model'])) else direct_csv
        osum = oc_summary.get(m['name']) or oc_summary.get(m['model'])
        if ds and ds.get('csv'):
            direct_source_csv = Path(ds['csv'])
        model_oc_csv = Path(osum['csv']) if osum and osum.get('csv') else oc_csv

        def source_label(summary, source_path):
            if not source_path:
                return ''
            count = len(summary.get('csvs') or []) if summary else 0
            prefix = f'merged {count} compatible files ending with ' if count > 1 else ''
            return prefix + source_path.name
        profile = research_profile_for(m); release_date = release_date_for(m, profile); op_s, op_w = infer_operational_notes(m, ds, osum)
        show = ollama_show(name); model_info = show.get('model_info') or {}; show_details = show.get('details') or {}
        modelfile = show.get('modelfile') or ''; parameters = show.get('parameters') or ''; template = show.get('template') or ''; license_text = show.get('license') or ''
        oc_name='ollama/' + name; status=[]
        if oc_name == default or name == default.replace('ollama/',''): status.append('OpenClaw default')
        if oc_name in allowed: status.append('OpenClaw configured')
        if name in [f.replace('ollama/','') for f in fallbacks]: status.append('OpenClaw fallback')
        if not status: status.append('local')
        def stat(label, value, sub=''): return f'<div class="stat"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(sub)}</small></div>'
        def li(items): return ''.join(f'<li>{esc(x)}</li>' for x in items if x)
        source_links=''.join(f'<a href="{esc(u)}" target="_blank" rel="noreferrer"><span>{i}</span>{esc(t)}</a>' for i,(t,u) in enumerate(profile.get('sources', []),1)) or '<p class="muted">No authoritative online source matched automatically for this exact tag.</p>'
        info_rows=''.join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k,v in sorted(model_info.items())) or '<tr><td colspan="2">No model_info payload returned by Ollama.</td></tr>'
        details_rows=''.join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k,v in sorted(show_details.items())) or '<tr><td colspan="2">No additional details payload returned by Ollama.</td></tr>'
        def joined(items): return ', '.join(items or []) or '—'
        def short_hashes(items): return ', '.join(value[:12] for value in (items or [])) or '—'
        def category_pass_text(s): return '; '.join(f'{k} {v}' for k, v in sorted((s.get('category_pass') or {}).items())) if s else '—'
        def failed_text(s): return '; '.join(s.get('failed_tests') or []) if s and s.get('failed_tests') else 'None'
        def test_text(s): return '; '.join(s.get('test_details') or []) if s and s.get('test_details') else '—'
        def preview_text(s): return '; '.join(s.get('preview_details') or []) if s and s.get('preview_details') else '—'
        comparison, first_label, first_treatment, second_label, second_treatment = comparison_sides(ds)
        paired_stats = []
        if comparison:
            paired_stats = [
                stat(f'{first_label} strict / grader cases', f'{treatment_score_text(first_treatment)} / {grader_cases_text(first_treatment)}'),
                stat(f'{second_label} strict / grader cases', f'{treatment_score_text(second_treatment)} / {grader_cases_text(second_treatment)}'),
                stat(f'Strict / grader Δ ({second_label} − {first_label})', f"{comparison_delta_text(comparison, 'strict')} / {comparison_delta_text(comparison, 'grader')}"),
                stat(f'Wall / output-token multiplier ({second_label} ÷ {first_label})', f"{comparison_multiplier_text(comparison, 'wall')} / {comparison_multiplier_text(comparison, 'tokens')}"),
                stat(f'{first_label} trace evidence', treatment_trace_evidence_text(first_treatment)),
                stat(f'{second_label} trace evidence', treatment_trace_evidence_text(second_treatment)),
                stat('Pair validity / completeness', comparison_status_text(comparison)),
                stat('Qualification reason / evidence', f"{comparison.get('qualification_reason') or '—'} / {comparison.get('evidence_code') or '—'}"),
                stat('Qualification probes', ', '.join(comparison.get('qualification_task_ids') or []) or '—'),
                stat('Control policy / off observability', f"{comparison.get('control_policy') or '—'} / {comparison.get('off_observability') or '—'}"),
                stat('Benchmark context', comparison_context_text(comparison), comparison.get('speed_comparison_caveat') or ''),
                stat('Context calibration', comparison_context_calibration_text(comparison)),
                stat('Omitted remaining work', num(comparison.get('omitted_remaining_work_count'))),
                stat('Experiment / pair', f"{comparison.get('campaign_id') or '—'} / {comparison.get('pair_id') or '—'}"),
                stat('Plan / pair kind', f"{(comparison.get('plan_sha256') or '—')[:12]} / {comparison.get('pair_kind') or '—'}"),
            ]
        direct_stats = [
            stat('Direct combined score', bench_score_text(ds, direct_rank_value, direct_total_value), source_label(ds, direct_source_csv)),
            stat('Direct avg wall', secs(ds.get('avg_wall')) if ds else '—', 'all scored tests'),
            stat('Direct avg pass wall', (secs(ds.get('avg_pass_wall')) if ds and ds.get('avg_pass_wall') is not None else ('No passing tests' if ds and (ds.get('passed') or 0) == 0 else '—')), 'passing tests only'),
            stat('Benchmark profile', values_text(ds.get('benchmark_profiles')) if ds else '—'),
            stat('Grading profile', values_text(ds.get('grading_profiles')) if ds else '—'),
            stat('Timed-out tasks', timeout_count_text(ds)),
            stat('Direct GPU max', pct(ds.get('max_gpu_pct')) if ds else '—'),
            stat('Direct CPU max', pct(ds.get('max_cpu_pct')) if ds else '—'),
            stat('Direct GPU °C max', temp(ds.get('max_gpu_temp')) if ds else '—'),
            stat('Direct CPU °C max', temp(ds.get('max_cpu_temp')) if ds else '—'),
            stat('Direct total-system W max', watts(ds.get('max_total_power')) if ds else '—'),
        ] + paired_stats
        oc_stats = [
            stat('OpenClaw score', bench_score_text(osum, oc_rank.get(name), oc_total), source_label(osum, model_oc_csv)),
            stat('OpenClaw avg wall', secs(osum.get('avg_wall')) if osum else '—', 'all scored tests'),
            stat('OpenClaw avg pass wall', (secs(osum.get('avg_pass_wall')) if osum and osum.get('avg_pass_wall') is not None else ('No passing tests' if osum and (osum.get('passed') or 0) == 0 else '—')), 'passing tests only'),
            stat('OpenClaw duration', ms(osum.get('avg_openclaw_ms')) if osum else '—'),
            stat('OpenClaw GPU max', pct(osum.get('max_gpu_pct')) if osum else '—'),
            stat('OpenClaw CPU max', pct(osum.get('max_cpu_pct')) if osum else '—'),
            stat('OpenClaw GPU °C max', temp(osum.get('max_gpu_temp')) if osum else '—'),
            stat('OpenClaw total-system W max', watts(osum.get('max_total_power')) if osum else '—'),
        ]
        direct_detail_stats = [
            stat('Scored / skipped / errors / timeouts', (f"{ds.get('tasks')} scored · {ds.get('skipped')} skipped · {ds.get('errors')} errors · {timeout_count_text(ds)} timeouts" if ds else '—')),
            stat('Benchmark profile', values_text(ds.get('benchmark_profiles')) if ds else '—'),
            stat('Grading profile', values_text(ds.get('grading_profiles')) if ds else '—'),
            stat('Requested context', values_text(ds.get('requested_num_ctx')) if ds else '—', 'tokens'),
            stat('Runner / grader hash', (f"{short_hashes(ds.get('runner_hashes'))} / {short_hashes(ds.get('grader_hashes'))}" if ds else '—'), 'SHA-256 prefixes'),
            stat('Output token policy', values_text(ds.get('output_token_policies')) if ds else '—'),
            stat('Output token limit', output_limit_text(ds), '−1 means no suite-imposed output cap'),
            stat('Response deadline', response_timeout_text(ds), 'hard per-task maximum'),
            stat('Thinking requested / resolved / effective', (f"{values_text(ds.get('thinking_requested'))} / {values_text(ds.get('thinking_resolved'))} / {values_text(ds.get('thinking_effective'))}" if ds else '—')),
            stat('Thinking capable / used', (f"{num(ds.get('thinking_capable_count')) if ds.get('thinking_capable_recorded') else 'not recorded'} / {num(ds.get('thinking_used_count')) if ds.get('thinking_used_recorded') else 'not recorded'}" if ds else '—'), 'task rows'),
            stat('Token throughput', tps(ds.get('avg_tps')) if ds else '—', 'average tok/s'),
            stat('Prompt / output / total tokens', (f"{num(ds.get('prompt_eval_count'))} / {num(ds.get('eval_count'))} / {num(ds.get('total_token_count'))}" if ds else '—')),
            stat('Response chars / bytes', (f"{num(ds.get('response_chars'))} / {num(ds.get('response_bytes'))}" if ds else '—')),
            stat('Thinking chars / bytes', (f"{num(ds.get('thinking_chars'))} / {num(ds.get('thinking_bytes'))}" if ds else '—')),
            stat('First output / answer', (f"{secs(ds.get('avg_time_to_first_output'))} / {secs(ds.get('avg_time_to_first_answer'))}" if ds else '—'), 'average time to first streamed data'),
            stat('Stream chunks', num(ds.get('stream_chunk_count')) if ds else '—', 'total across task rows'),
            stat('Done / termination reasons', (f"{values_text(ds.get('done_reasons'))} / {values_text(ds.get('termination_reasons'))}" if ds else '—')),
            stat('Telemetry samples', num(ds.get('sample_count')) if ds else '—'),
            stat('Benchmark host', joined(ds.get('hosts')) if ds else '—', joined(ds.get('platforms')) if ds else ''),
            stat('Telemetry backend', joined(ds.get('telemetry_backends')) if ds else '—'),
            stat('Ollama version', joined(ds.get('ollama_versions')) if ds else '—'),
            stat('Model digest', joined(ds.get('model_digests')) if ds else '—'),
            stat('CPU avg / GPU avg', (f"{pct(ds.get('avg_cpu_pct'))} / {pct(ds.get('avg_gpu_pct'))}" if ds else '—')),
            stat('SoC temp max', temp(ds.get('max_soc_temp')) if ds else '—'),
            stat('Host / ACPI temp max', temp(ds.get('max_host_temp')) if ds else '—'),
            stat('Benchmark families', joined(ds.get('families')) if ds else '—'),
            stat('Categories', joined(ds.get('categories')) if ds else '—'),
        ]
        if comparison:
            mismatch_text = '; '.join(
                f"{field}: {', '.join(values)}" for field, values in sorted((comparison.get('invariant_mismatches') or {}).items())
            ) or 'none'
            direct_detail_stats.extend([
                stat('Experiment ID', comparison.get('experiment_id') or comparison.get('campaign_id') or '—'),
                stat('Pair ID', comparison.get('pair_id') or '—'),
                stat('Plan SHA-256', comparison.get('plan_sha256') or '—'),
                stat('Pair schema / campaign seed', f"{comparison.get('pair_schema_version') or '—'} / {comparison.get('campaign_seed') or '—'}"),
                stat('Planner SHA-256', comparison.get('planner_sha256') or '—'),
                stat('Pair kind / off available', f"{comparison.get('pair_kind') or '—'} / {str(bool(comparison.get('off_available'))).lower()}"),
                stat('Expected treatments', ', '.join(comparison.get('expected_treatments') or []) or '—'),
                stat('Pair status', comparison_status_text(comparison), 'valid requires both complete, provenance-compatible arms and no grader error'),
                stat('Pair validity notes', '; '.join(comparison.get('invalid_reasons') or []) or 'none'),
                stat('Pair provenance mismatches', mismatch_text),
                stat('Protocol errors', '; '.join((comparison.get('protocol_errors') or []) + (comparison.get('control_errors') or [])) or 'none'),
            ])
        oc_detail_stats = [
            stat('Scored / skipped / errors / timeouts', (f"{osum.get('tasks')} scored · {osum.get('skipped')} skipped · {osum.get('errors')} errors · {timeout_count_text(osum)} timeouts" if osum else '—')),
            stat('Benchmark profile', values_text(osum.get('benchmark_profiles')) if osum else '—'),
            stat('Grading profile', values_text(osum.get('grading_profiles')) if osum else '—'),
            stat('Output token policy', values_text(osum.get('output_token_policies')) if osum else '—'),
            stat('Response deadline', response_timeout_text(osum), 'agent deadline when recorded'),
            stat('Telemetry samples', num(osum.get('sample_count')) if osum else '—'),
            stat('CPU avg / GPU avg', (f"{pct(osum.get('avg_cpu_pct'))} / {pct(osum.get('avg_gpu_pct'))}" if osum else '—')),
            stat('SoC temp max', temp(osum.get('max_soc_temp')) if osum else '—'),
            stat('Host / ACPI temp max', temp(osum.get('max_host_temp')) if osum else '—'),
            stat('Agent model', joined(osum.get('agent_models')) if osum else '—'),
            stat('Winner model', (joined(osum.get('winner_models')) if osum and osum.get('winner_models') else 'not recorded') if osum else '—'),
            stat('Fallbacks / attempts', (f"{num(osum.get('fallbacks_used')) if osum.get('fallbacks_used') is not None else '0'} / {num(osum.get('fallback_attempts')) if osum.get('fallback_attempts') is not None else '0'}" if osum else '—')),
            stat('Exit max', num(osum.get('exit_code_max')) if osum else '—'),
        ]
        treatment_detail_html = ''
        if comparison:
            treatment_sections = []
            for treatment_label in comparison.get('expected_treatments') or []:
                treatment = (comparison.get('treatments') or {}).get(treatment_label)
                if not treatment:
                    treatment_sections.append(f'''<article class="panel"><h2>{esc(treatment_label)} treatment</h2><p class="muted">Missing from the newest experiment.</p></article>''')
                    continue
                provenance = (
                    f"treatment key/ID {values_text(treatment.get('treatment_keys'))} / {values_text(treatment.get('treatment_ids'))} · "
                    f"role/order {values_text(treatment.get('treatment_roles'))} / {values_text(treatment.get('treatment_orders'))} · "
                    f"run/attempt {values_text(treatment.get('run_ids'))} / {values_text(treatment.get('attempts'))} · "
                    f"{len(treatment.get('row_ids') or [])} unique row IDs · "
                    f"think field/payload {values_text(treatment.get('think_field_present'))} / {values_text(treatment.get('think_request_values'))} · "
                    f"protocol valid/errors {values_text(treatment.get('protocol_valid_values'))} / {values_text(treatment.get('protocol_errors'))} · "
                    f"context policy/requested/model {values_text(treatment.get('context_policies'))} / "
                    f"{values_text(treatment.get('requested_num_ctx'))} / {values_text(treatment.get('model_context_lengths'))} · "
                    f"calibration status/adjusted/reduction {values_text(treatment.get('context_calibration_statuses'))} / "
                    f"{values_text(treatment.get('context_adjusted_values'))} / {values_text(treatment.get('context_reduction_pct'))}% · "
                    f"runner/grader "
                    f"{short_hashes(treatment.get('runner_hashes'))}/{short_hashes(treatment.get('grader_hashes'))} · planner "
                    f"{short_hashes(treatment.get('planner_hashes'))}"
                )
                treatment_sections.append(f'''<article class="panel"><h2>{esc(treatment_label)} treatment</h2><div class="grid stats">
                    {stat('Strict score', treatment_score_text(treatment))}
                    {stat('Grader cases', grader_cases_text(treatment))}
                    {stat('Total wall', secs(treatment.get('wall_seconds_total')))}
                    {stat('Output tokens', num(treatment.get('eval_count')))}
                    {stat('Thinking chars', num(treatment.get('thinking_chars')))}
                    {stat('Rows / skipped / timeouts', f"{treatment.get('rows', 0)} / {treatment.get('skipped', 0)} / {treatment.get('timeouts', 0)}")}
                    </div><p class="muted longtext">{esc(provenance)}</p><h3>Failed tests</h3><p class="muted longtext">{esc(failed_text(treatment))}</p><h3>Per-test results</h3><p class="muted longtext">{esc(test_text(treatment))}</p><h3>Response/error previews</h3><p class="muted longtext">{esc(preview_text(treatment))}</p></article>''')
            treatment_detail_html = '<section class="grid two">' + ''.join(treatment_sections) + '</section>'
        direct_detail_panel = f'''<section class="panel"><h2>Direct Ollama combined benchmark details</h2><div class="grid stats">{''.join(direct_detail_stats)}</div><h3>Category pass</h3><p class="muted longtext">{esc(category_pass_text(ds))}</p><h3>Failed tests</h3><p class="muted longtext">{esc(failed_text(ds))}</p><h3>Per-test results</h3><p class="muted longtext">{esc(test_text(ds))}</p><h3>Response/error previews</h3><p class="muted longtext">{esc(preview_text(ds))}</p></section>{treatment_detail_html}'''
        oc_detail_panel = f'''<section class="panel"><h2>OpenClaw benchmark details</h2><div class="grid stats">{''.join(oc_detail_stats)}</div><h3>Category pass</h3><p class="muted longtext">{esc(category_pass_text(osum))}</p><h3>Failed tests</h3><p class="muted longtext">{esc(failed_text(osum))}</p><h3>Per-test results</h3><p class="muted longtext">{esc(test_text(osum))}</p><h3>Assistant/error previews</h3><p class="muted longtext">{esc(preview_text(osum))}</p></section>'''
        exact_family = exact_model_family(m)
        html_doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(name)} · Local model research</title><style>
:root{{--bg:#050814;--panel:#0b1220;--ink:#eef7ff;--muted:#91a4bd;--line:rgba(148,163,184,.18);--cyan:#21d4fd;--blue:#5b8cff;--green:#22e6a6}}
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:radial-gradient(circle at 12% -10%,rgba(33,212,253,.25),transparent 34%),radial-gradient(circle at 96% 0,rgba(139,92,246,.18),transparent 35%),linear-gradient(180deg,#050814,#08111f 48%,#050814);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}.wrap{{width:min(1180px,calc(100% - 28px));margin:auto;padding:22px 0 54px;overflow:hidden}}.back{{display:inline-flex;margin:4px 0 14px;padding:10px 13px;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:#bdefff;background:rgba(255,255,255,.05);font-weight:800}}.hero{{border:1px solid var(--line);border-radius:30px;padding:26px;background:linear-gradient(145deg,rgba(17,27,46,.95),rgba(6,9,20,.9));box-shadow:0 24px 70px rgba(0,0,0,.42)}}.kicker{{display:flex;gap:8px;flex-wrap:wrap;color:#9ae8ff;font-weight:900;text-transform:uppercase;letter-spacing:.12em;font-size:11px}}h1{{font-size:clamp(32px,7vw,66px);line-height:.92;letter-spacing:-.06em;margin:12px 0 10px;overflow-wrap:anywhere}}.subtitle{{color:var(--muted);font-size:16px;line-height:1.6;max-width:900px}}.tags{{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:6px 9px;background:rgba(255,255,255,.055);color:#dbeafe;font-weight:800;font-size:12px}}.grid{{display:grid;gap:14px;margin-top:14px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.stat,.panel{{border:1px solid var(--line);background:rgba(11,18,32,.84);border-radius:22px;padding:16px;box-shadow:0 14px 38px rgba(0,0,0,.24);min-width:0}}.stat span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.1em;font-weight:900}}.stat b{{display:block;margin-top:6px;font-size:18px;overflow-wrap:anywhere}}.stat small{{display:block;color:var(--muted);margin-top:4px;font-size:11px;overflow-wrap:anywhere}}.two{{grid-template-columns:1fr 1fr}}.panel h2{{margin:0 0 10px;font-size:20px;letter-spacing:-.03em}}.panel p,.panel li{{color:#b9c7db;line-height:1.55}}ul{{padding-left:20px;margin:0;display:grid;gap:7px}}.sources{{display:grid;gap:9px}}.sources a{{display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center;padding:11px;border:1px solid var(--line);border-radius:15px;text-decoration:none;background:rgba(255,255,255,.04)}}.sources span{{width:26px;height:26px;border-radius:9px;display:grid;place-items:center;background:rgba(33,212,253,.13);color:#bdefff;font-weight:900}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{color:#9ae8ff;width:34%;font-size:12px;text-transform:uppercase;letter-spacing:.08em}}pre{{white-space:pre-wrap;overflow:auto;max-height:460px;border:1px solid var(--line);border-radius:18px;padding:14px;background:#030712;color:#dbeafe}}.muted{{color:var(--muted)}}@media(max-width:760px){{.wrap{{width:100%;padding:10px 12px 38px}}.hero{{padding:20px;border-radius:26px;overflow:hidden}}.stats,.two{{grid-template-columns:1fr}}.stat,.panel{{border-radius:20px;padding:14px}}h1{{font-size:clamp(28px,9vw,38px);line-height:1.02;letter-spacing:-.055em;word-break:break-word;overflow-wrap:anywhere;max-width:100%}}.subtitle,.stat b,.stat small,.tag{{word-break:break-word;overflow-wrap:anywhere}}}}
</style></head><body><main class="wrap"><a class="back" href="../Local LLM Benchmark Dashboard.html#{model_anchor(name)}">← Back to dashboard card</a><section class="hero"><div class="kicker"><span>{esc(profile.get('family_title'))}</span><span>Exact local tag</span></div><h1>{esc(name)}</h1><p class="subtitle">{esc(profile.get('overview'))}</p><div class="tags">{''.join(f'<span class="tag">{esc(x)}</span>' for x in status + (m.get('capabilities') or []))}</div></section>
<section class="grid stats">{stat('Exact model family', exact_family, 'raw Ollama family: ' + (m.get('family') or '—'))}{stat('Release Date', release_date, 'best known from online/local metadata')}{stat('Size on disk', m.get('size_h'), str(m.get('size_gb'))+' GB')}{stat('Params / quant', m.get('parameter_size'), m.get('quant'))}{stat('Context', m.get('context') or '—', 'tokens')}</section>
<section class="grid stats">{''.join(direct_stats)}{''.join(oc_stats)}</section>
{direct_detail_panel}
{oc_detail_panel}
<section class="grid two"><article class="panel"><h2>Strengths</h2><ul>{li(profile.get('strengths', []) + op_s)}</ul></article><article class="panel"><h2>Weaknesses / watch-outs</h2><ul>{li(profile.get('weaknesses', []) + op_w)}</ul></article></section>
<section class="grid two"><article class="panel"><h2>Release / lineage notes</h2><p>{esc(profile.get('release'))}</p><p class="muted">This page combines live local Ollama metadata, latest local benchmark CSV summaries, OpenClaw configuration status, and live web-search source references gathered during generation.</p></article><article class="panel"><h2>Online research sources</h2><div class="sources">{source_links}</div></article></section>
<section class="grid two"><article class="panel"><h2>Local Ollama manifest</h2><table><tr><th>Exact tag</th><td>{esc(name)}</td></tr><tr><th>Digest</th><td>{esc(m.get('digest'))}</td></tr><tr><th>Modified</th><td>{esc(m.get('modified'))}</td></tr><tr><th>Format</th><td>{esc(m.get('format'))}</td></tr><tr><th>Capabilities</th><td>{esc(m.get('capabilities_s'))}</td></tr></table></article><article class="panel"><h2>Ollama details payload</h2><table>{details_rows}</table></article></section>
<section class="panel"><h2>Model info payload</h2><table>{info_rows}</table></section>
<section class="grid two"><article class="panel"><h2>Modelfile</h2><pre>{esc(modelfile or 'No modelfile returned by Ollama.')}</pre></article><article class="panel"><h2>Template / parameters / license</h2><h3>Parameters</h3><pre>{esc(parameters or '—')}</pre><h3>Template</h3><pre>{esc(template or '—')}</pre><h3>License</h3><pre>{esc(license_text[:12000] or '—')}</pre></article></section>
<p class="muted">Generated {esc(now.strftime('%b %-d, %Y %-I:%M %p %Z'))}. Detail page filename: {esc(model_slug(name)+'.html')}</p></main></body></html>"""
        (DETAIL_DIR / (model_slug(name)+'.html')).write_text(html_doc, encoding='utf-8')
    # Do not delete unrecognized HTML files from a user-selected output folder.
    # Existing generated detail pages may remain until the user removes them.
def pct(n): return f'{n:.0f}%' if isinstance(n,(int,float)) else '—'
def temp(n): return f'{n:.1f}°C' if isinstance(n,(int,float)) else '—'
def watts(n): return f'{n:.1f}W' if isinstance(n,(int,float)) else '—'
def secs(n): return f'{n:.2f}s' if isinstance(n,(int,float)) else '—'
def tps(n): return f'{n:.1f}' if isinstance(n,(int,float)) else '—'
def ms(n): return f'{n/1000:.2f}s' if isinstance(n,(int,float)) else '—'
def num(n): return f'{int(n)}' if isinstance(n,(int,float)) else '—'

def values_text(values):
    return ', '.join(values or []) or 'legacy / not recorded'

def signed_integer(value):
    if not isinstance(value, (int, float)):
        return '—'
    return f'{int(value):+d}'

def multiplier_text(value):
    return f'{value:.2f}×' if isinstance(value, (int, float)) else '—'

def treatment_score_text(summary):
    if not summary:
        return '—'
    return f"{summary.get('passed', 0)}/{summary.get('tasks', 0)}"

def grader_cases_text(summary):
    if not summary or summary.get('grader_cases_passed') is None or summary.get('grader_cases_total') is None:
        return '—'
    return f"{summary['grader_cases_passed']}/{summary['grader_cases_total']}"

def comparison_sides(summary):
    comparison = (summary or {}).get('treatment_comparison') or {}
    treatments = comparison.get('treatments') or {}
    first_label = comparison.get('first_label') or ''
    second_label = comparison.get('second_label') or ''
    return comparison, first_label, treatments.get(first_label), second_label, treatments.get(second_label)

def comparison_status_text(comparison):
    if not comparison:
        return 'legacy / unpaired'
    schema_major = pair_schema_major(comparison.get('pair_schema_version'))
    if comparison.get('capacity_disposition') and comparison.get('context_calibration_status') == 'no-fit':
        status = (
            'not benchmarked: no context fit · terminal capacity disposition'
            if comparison.get('terminally_dispositioned')
            else 'not benchmarked: no context fit · capacity evidence invalid'
        )
        omitted = comparison.get('omitted_remaining_work_count') or 0
        if omitted:
            status += f' · {omitted} work rows omitted'
    elif schema_major < 3:
        status = ('valid' if comparison.get('valid') else 'invalid') + ' · ' + ('complete' if comparison.get('complete') else 'incomplete')
    else:
        qualification = comparison.get('qualification_status') or comparison.get('status') or 'pending'
        if qualification == 'pending' and comparison.get('status') not in {None, '', 'pending'}:
            qualification = comparison.get('status')
        labels = {
            'pending': 'qualification pending',
            'observable-toggle-qualified': 'qualified valid pair',
            'off-control-unobservable': 'off control unobservable · no causal delta',
            'off-control-ineffective': 'off control ineffective · terminal',
            'on-control-unverified': 'on control unverified · terminal',
            'control-inconclusive': 'control inconclusive · terminal',
            'level-range-qualified': 'GPT low/high range · qualified',
            'level-range-unverified': 'GPT low/high range unverified · terminal',
        }
        status = labels.get(qualification, qualification.replace('-', ' '))
        if qualification == 'control-inconclusive' and comparison.get('control_policy') == 'unsupported':
            status = 'thinking control unsupported · terminal'
        if comparison.get('full_benchmark_complete'):
            status += ' · full benchmark complete'
        elif not comparison.get('terminally_dispositioned'):
            status += ' · benchmark incomplete'
        omitted = comparison.get('omitted_remaining_work_count') or 0
        if omitted:
            status += f' · {omitted} work rows omitted'
    expected = comparison.get('expected_tasks')
    suffix = f" · {expected} {'planned ' if comparison.get('plan_only') else ''}tasks/arm" if expected else ''
    campaign_total = comparison.get('campaign_models_total') or 0
    campaign_done = comparison.get('campaign_models_complete') or 0
    campaign = f' · campaign {campaign_done}/{campaign_total} models done' if campaign_total else ''
    return status + suffix + campaign


def treatment_trace_evidence_text(summary):
    if not summary:
        return '—'
    observed = 'observed' if summary.get('reasoning_trace_observed') else 'not observed'
    separated = summary.get('separated_thinking_chars') or 0
    inline = summary.get('inline_thinking_chars') or 0
    tasks = summary.get('reasoning_trace_tasks') or []
    task_text = ', '.join(tasks[:3])
    if len(tasks) > 3:
        task_text += f' +{len(tasks) - 3}'
    bits = [observed, f'separated {separated} chars', f'inline {inline} chars']
    if task_text:
        bits.append(task_text)
    return ' · '.join(bits)


def comparison_delta_text(comparison, metric='strict'):
    if not comparison:
        return '—'
    if comparison.get('causal_delta_eligible'):
        value = comparison.get('strict_delta' if metric == 'strict' else 'grader_delta')
        return signed_integer(value)
    if comparison.get('descriptive_delta_eligible'):
        value = comparison.get(
            'descriptive_strict_delta' if metric == 'strict' else 'descriptive_grader_delta'
        )
        return f'{signed_integer(value)} descriptive low/high'
    return 'not reportable'


def comparison_multiplier_text(comparison, metric='wall'):
    if not comparison:
        return '—'
    if comparison.get('causal_delta_eligible'):
        return multiplier_text(comparison.get('wall_multiplier' if metric == 'wall' else 'token_multiplier'))
    if comparison.get('descriptive_delta_eligible'):
        value = comparison.get(
            'descriptive_wall_multiplier' if metric == 'wall' else 'descriptive_token_multiplier'
        )
        return f'{multiplier_text(value)} descriptive low/high'
    return 'not reportable'


def comparison_context_text(comparison):
    if not comparison:
        return '—'
    policy = comparison.get('context_policy') or 'not recorded'
    requested = comparison.get('requested_num_ctx')
    model_limit = comparison.get('model_context_length')
    if comparison.get('context_calibration_status') == 'no-fit':
        native = token_count_text(model_limit) if model_limit else 'native context not recorded'
        return f'{policy} · no verified fit / {native} native · no-fit'
    if requested:
        context = token_count_text(requested)
        if model_limit and str(model_limit) != str(requested):
            context += f' requested / {token_count_text(model_limit)} model'
        fit = comparison.get('context_calibration_status')
        adjusted = comparison.get('context_adjusted')
        if fit:
            context += f' · {fit}'
        elif adjusted is False:
            context += ' · unadjusted'
        return f'{policy} · {context}'
    return policy


def comparison_context_calibration_text(comparison):
    if not comparison:
        return '—'
    if not comparison.get('adaptive_context_policy'):
        return 'native context used without adaptive reduction' if comparison.get('native_context_policy') else 'not recorded'
    no_fit = comparison.get('context_calibration_status') == 'no-fit'
    adjusted = comparison.get('context_adjusted')
    reduction_tokens = comparison.get('context_reduction_tokens')
    reduction_pct = comparison.get('context_reduction_pct')
    attempts = comparison.get('context_calibration_attempt_count')
    profile = comparison.get('context_calibration_profile') or '—'
    bits = [
        'no context fit' if no_fit else 'adjusted' if adjusted else 'unadjusted native fit',
        f'reduction {num(reduction_tokens)} tok / {reduction_pct:.2f}%'
        if reduction_tokens is not None and isinstance(reduction_pct, (int, float)) else 'reduction not recorded',
        f'{num(attempts)} calibration attempts' if attempts is not None else 'attempt count not recorded',
        profile,
    ]
    reason = comparison.get('context_adjustment_reason')
    if reason:
        bits.append(reason)
    return ' · '.join(bits)

def output_limit_text(summary):
    values = summary.get('output_token_limits') or [] if summary else []
    if values and set(values) == {'-1'}:
        return 'Unlimited (-1)'
    return values_text(values)

def response_timeout_text(summary):
    values = summary.get('response_timeouts') or [] if summary else []
    if values and set(values) == {'1800'}:
        return '30m (1,800s)'
    return values_text(values)

def timeout_count_text(summary):
    if not summary:
        return '—'
    return num(summary.get('timeouts')) if summary.get('timeout_data_recorded') else 'legacy / not recorded'

def tag(text, cls=''):
    return f'<span class="tag {cls}">{esc(text)}</span>'

def fail_badge_for(s):
    failed_tests = s.get('failed_tests') or []
    if failed_tests:
        text = 'failed: ' + ', '.join(failed_tests)
    else:
        text = 'failed tests'
    return tag(text, 'fail')

def compact_fail_badge_for(s):
    failed_tests = s.get('failed_tests') or []
    n = len(failed_tests)
    return tag(f'{n} failed' if n else 'failed', 'fail')

def model_kind(caps):
    if 'image' in caps and 'completion' not in caps: return 'image'
    if 'completion' in caps and 'tools' in caps: return 'agent-ready'
    if 'completion' in caps: return 'text'
    return 'other'


def infer_specific_skills(m):
    """Return concrete, user-facing model skills inferred from Ollama metadata and exact tag/family."""
    name = (m.get('name') or m.get('model') or '').lower()
    family = (m.get('family') or '').lower()
    caps = set(m.get('capabilities') or [])
    text = ' '.join([name, family])
    skills = []

    def add(label, reason):
        if label not in [s[0] for s in skills]:
            skills.append((label, reason))

    if 'completion' in caps:
        add('Local chat / completion', 'native Ollama completion capability')
    if 'tools' in caps:
        add('Native tool calling', 'Ollama reports tools capability')
    if 'image' in caps and 'completion' in caps:
        add('Native vision input', 'Ollama reports image + completion capabilities')
        add('OCR / document image understanding', 'vision-language model can inspect text in images/screenshots')
    elif 'image' in caps:
        add('Image generation / visual pipeline', 'image-only local model capability')

    if any(k in text for k in ['qwen3-vl', 'vl:', 'vision']):
        add('OCR-native vision', 'vision-language family/tag')
        add('Screenshot and UI reading', 'vision-language family/tag')
        add('Document/table extraction', 'vision-language family/tag')
    if any(k in text for k in ['gemma4', 'gemma 4']) and 'image' in caps:
        add('Multimodal image understanding', 'Gemma vision-capable tag')
        add('OCR / screenshot summarization', 'Gemma vision-capable tag')
    if any(k in text for k in ['flux', 'klein']):
        add('Local image generation', 'Flux image model tag')
        add('Visual concept generation', 'image generation family')

    if any(k in text for k in ['coder', 'code', 'devstral', 'deepseek-coder', 'north-mini-code']):
        add('Code generation', 'coding-specialized model family/tag')
        add('Debugging / code repair', 'coding-specialized model family/tag')
        add('Repository-agent tasks', 'coding/agentic model family/tag')
    if any(k in text for k in ['qwen3-coder', 'devstral', 'gpt-oss', 'command-r']):
        add('Agentic workflows', 'model family is commonly used for tool/agent tasks')
    if any(k in text for k in ['qwen3', 'qwen3.5', 'qwen3.6', 'deepseek-r1', 'cogito', 'magistral', 'gpt-oss']):
        add('Reasoning / analysis', 'reasoning-oriented family/tag')
    if any(k in text for k in ['command-r', 'glm', 'mistral-small', 'qwen', 'gemma', 'aya']):
        add('Multilingual tasks', 'model family/tag has broad language coverage')
    if any(k in text for k in ['command-r']):
        add('RAG / citation workflows', 'Command R family specialization')
    if any(k in text for k in ['abliterated']):
        add('Refusal-behavior experiments', 'abliterated model variant')

    if not skills:
        add('Local private inference', 'installed local Ollama model')
    return skills[:8]


def skills_html(m):
    skills = infer_specific_skills(m)
    chips = ''.join(f'<span class="skill-chip" title="{esc(reason)}">{esc(label)}</span>' for label, reason in skills)
    return f'<section class="skill-panel" aria-label="Specific model skills"><div class="skill-heading"><span>Specific skills</span><small>inferred from local metadata + model family</small></div><div class="skill-chips">{chips}</div></section>'


def exact_model_family(m):
    """User-facing family/lineage for the exact installed model tag.

    Ollama's raw details.family often exposes a base architecture family such as
    llama/qwen2/cohere2moe. That is useful manifest metadata, but it is not the
    correct display family for exact tags such as devstral, magistral, cogito,
    north-mini-code, or nemotron-cascade. Prefer exact tag lineage first.
    """
    exact = (m.get('name') or m.get('model') or '').strip()
    name = exact.lower()
    raw_family = (m.get('family') or '').strip()
    rules = [
        ('blackhillsinfosec/llama-3.1-8b-abliterated', 'Llama 3.1 8B Abliterated'),
        ('llama-3.1-8b-abliterated', 'Llama 3.1 8B Abliterated'),
        ('qwen3-vl', 'Qwen3-VL'),
        ('qwen3-coder', 'Qwen3-Coder'),
        ('qwen2.5-coder', 'Qwen2.5-Coder'),
        ('qwen3.6', 'Qwen3.6'),
        ('qwen3.5', 'Qwen3.5'),
        ('qwen3:', 'Qwen3'),
        ('gemma4', 'Gemma 4'),
        ('deepseek-coder-v2', 'DeepSeek-Coder-V2'),
        ('deepseek-r1', 'DeepSeek-R1'),
        ('gpt-oss', 'OpenAI gpt-oss'),
        ('devstral-small-2', 'Devstral Small 2'),
        ('devstral', 'Devstral'),
        ('mistral-small', 'Mistral Small'),
        ('magistral', 'Magistral'),
        ('command-r', 'Cohere Command R'),
        ('cogito', 'Cogito'),
        ('nemotron-cascade', 'Nemotron Cascade'),
        ('north-mini-code', 'North Mini Code'),
        ('x/flux2-klein', 'Flux2 Klein'),
        ('flux2-klein', 'Flux2 Klein'),
    ]
    for needle, label in rules:
        if needle in name:
            return label
    if raw_family and raw_family.lower() not in {'unknown', '—', '-'}:
        return raw_family
    return exact.split(':', 1)[0] if exact else 'unknown'


def display_model_name(m):
    return exact_model_family(m)

def exact_model_tag(m):
    return (m.get('name') or m.get('model') or '—').strip()

def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    models = load_ollama_models()
    system_specs = load_system_specs()
    hermes_status = load_hermes_model_status()
    openclaw = load_openclaw_status()
    direct_summary, direct_csv = load_benchmark_summary(all_csvs(OLLAMA_BENCH_DIR, 'ollama_direct_model_benchmark_telemetry_*.csv'))
    oc_summary, oc_csv = load_benchmark_summary(all_csvs(OPENCLAW_BENCH_DIR, 'openclaw_local_model_benchmark_telemetry_*.csv'))
    standardized_summary, standardized_csv = load_standardized_summary(all_csvs(OLLAMA_BENCH_DIR, 'ollama_standardized_local_benchmark_*.csv'))
    installed_model_names = {x for m in models for x in (m.get('name'), m.get('model')) if x}
    # Preserve historical reports on disk while keeping the active dashboard
    # limited to models that are currently installed on this host.
    direct_summary = {k: v for k, v in direct_summary.items() if k in installed_model_names}
    standardized_summary = {k: v for k, v in standardized_summary.items() if k in installed_model_names}
    oc_summary = {k: v for k, v in oc_summary.items() if k in installed_model_names}
    default = openclaw.get('resolvedDefault') or openclaw.get('defaultModel') or '—'
    fallbacks = openclaw.get('fallbacks') or []
    image_model = openclaw.get('imageModel') or '—'
    openclaw_provider = default.split('/', 1)[0] if '/' in default and not default.startswith('—') else '—'
    openclaw_runtime = 'Local model' if openclaw_provider in {'ollama', 'local', 'lmstudio', 'llama.cpp'} else ('Cloud model' if openclaw_provider != '—' else '—')
    openclaw_runtime_class = 'local' if openclaw_runtime == 'Local model' else 'cloud'
    allowed = set(openclaw.get('allowed') or [])
    total_size = sum(m['size'] for m in models)
    text_count = sum(1 for m in models if 'completion' in m['capabilities'])
    tools_count = sum(1 for m in models if 'tools' in m['capabilities'])
    image_count = sum(1 for m in models if 'image' in m['capabilities'])
    default_local_name = default.replace('ollama/','') if default.startswith('ollama/') else default
    default_model = next((m for m in models if m['name'] == default_local_name or m['model'] == default_local_name), None)
    openclaw_context_text = token_count_text(default_model.get('context')) if default_model else '—'
    now = dt.datetime.now().astimezone()
    direct_rank, direct_total = rank_benchmarks(direct_summary)
    standardized_rank, standardized_total = rank_benchmarks(standardized_summary)
    oc_rank, oc_total = rank_benchmarks(oc_summary)
    generate_model_detail_pages(models, direct_summary, standardized_summary, oc_summary, direct_rank, direct_total, standardized_rank, standardized_total, oc_rank, oc_total, default, allowed, fallbacks, direct_csv, standardized_csv, oc_csv, now)

    def standardized_includes_smoke(std):
        return bool(std and 'Smoke' in set(std.get('families') or []))

    def legacy_smoke_component(smoke, std):
        # The unified runner already contains its three smoke tests. Ignore a
        # legacy smoke-only CSV when both formats exist so the dashboard stays
        # at 18 defined tasks instead of double-counting them as 21.
        return None if standardized_includes_smoke(std) else smoke

    def combined_direct_card_metrics(smoke, std):
        smoke = legacy_smoke_component(smoke, std)
        smoke_tasks = smoke.get('tasks') if smoke else 0
        comparison, _, first, _, second = comparison_sides(std)
        if comparison and first and second:
            std_tasks = (first.get('tasks') or 0) + (second.get('tasks') or 0)
            std_pass = (first.get('passed') or 0) + (second.get('passed') or 0)
            std_skipped = (first.get('skipped') or 0) + (second.get('skipped') or 0)
            std_wall = (first.get('wall_seconds_total') or 0) + (second.get('wall_seconds_total') or 0)
            std_avg_wall = round(std_wall / std_tasks, 3) if std_tasks else None
        else:
            std_tasks = std.get('tasks') if std else 0
            std_pass = std.get('passed') if std else 0
            std_skipped = std.get('skipped') if std else 0
            std_avg_wall = std.get('avg_wall') if std else None
        smoke_pass = smoke.get('passed') if smoke else 0
        total_tasks = smoke_tasks + std_tasks
        total_pass = smoke_pass + std_pass
        skipped = std_skipped
        weighted = []
        if smoke and isinstance(smoke.get('avg_wall'), (int, float)) and smoke_tasks:
            weighted.append((smoke.get('avg_wall'), smoke_tasks))
        if isinstance(std_avg_wall, (int, float)) and std_tasks:
            weighted.append((std_avg_wall, std_tasks))
        avg_all = round(sum(v * n for v, n in weighted) / sum(n for _, n in weighted), 3) if weighted else None
        failed = max(total_tasks - total_pass, 0)
        return {'total_pass': total_pass, 'total_tasks': total_tasks, 'skipped': skipped, 'avg_all': avg_all, 'failed': failed}

    rows = []
    cards = []
    model_cards = []
    for i,m in enumerate(models, 1):
        oc_name = 'ollama/' + m['name']
        is_default = oc_name == default or m['name'] == default_local_name
        is_allowed = oc_name in allowed
        ds = direct_summary.get(m['name']) or direct_summary.get(m['model'])
        osum = oc_summary.get(m['name']) or oc_summary.get(m['model'])
        sstd = standardized_summary.get(m['name']) or standardized_summary.get(m['model'])
        kind = model_kind(m['capabilities'])
        caps_html = ''.join(tag(c, 'cap') for c in m['capabilities']) or tag('unknown')
        status_tags = ''
        if is_default: status_tags += tag('OpenClaw default', 'default')
        if is_allowed: status_tags += tag('OpenClaw configured', 'configured')
        if m['name'] in [f.replace('ollama/','') for f in fallbacks]: status_tags += tag('fallback', 'fallback')
        combined_card = combined_direct_card_metrics(ds, sstd)
        direct_combined_bench = f"{combined_card['total_pass']}/{combined_card['total_tasks']} · {secs(combined_card['avg_all'])}" if combined_card['total_tasks'] else 'not benchmarked'
        comparison, first_label, first_treatment, second_label, second_treatment = comparison_sides(sstd)
        if comparison:
            direct_combined_bench = (
                f"{first_label} {treatment_score_text(first_treatment)} · "
                f"{second_label} {treatment_score_text(second_treatment)} · {comparison_status_text(comparison)}"
            )
        effective_smoke = legacy_smoke_component(ds, sstd)
        direct_execution = sstd or effective_smoke
        direct_telemetry = effective_smoke if effective_smoke and effective_smoke.get('max_gpu_pct') is not None else sstd
        power = watts(direct_telemetry.get('max_total_power')) if direct_telemetry else '—'
        therm = temp(direct_telemetry.get('max_gpu_temp')) if direct_telemetry else '—'
        openclaw_bench = f"{osum['passed']}/{osum['tasks']} · {secs(osum['avg_pass_wall'])}" if osum else 'not benchmarked'
        openclaw_power = watts(osum.get('max_total_power')) if osum else '—'
        openclaw_therm = temp(osum.get('max_gpu_temp')) if osum else '—'
        exact_family = exact_model_family(m)
        rows.append(f'''
<tr data-name="{esc(m['name'].lower())}" data-family="{esc(exact_family.lower())}" data-kind="{kind}" data-openclaw="{'yes' if is_allowed or is_default else 'no'}">
  <td><div class="model-cell"><strong>{esc(m['name'])}</strong><small>{status_tags}</small></div></td>
  <td data-label="Size"><b>{esc(m['size_h'])}</b><small>{m['size_gb']} GB</small></td>
  <td data-label="Params / Quant"><b>{esc(m['parameter_size'])}</b><small>{esc(m['quant'])} · {esc(exact_family)}</small></td>
  <td data-label="Caps"><div class="tags">{caps_html}</div></td>
  <td data-label="Direct combined"><b>{esc(direct_combined_bench)}</b><small>18-test suite · Max {power} · GPU {therm}</small></td>
</tr>''')
        display_name = exact_family
        exact_tag = exact_model_tag(m)
        card_release_date = release_date_for(m, research_profile_for(m))
        model_cards.append(f'''
<article id="{model_anchor(m['name'])}" class="llm-card" data-name="{esc(m['name'].lower())}" data-family="{esc(exact_family.lower())}" data-kind="{kind}" data-openclaw="{'yes' if is_allowed or is_default else 'no'}">
  <header class="llm-card-head">
    <div class="model-title-block"><h3 class="display-model-name"><a class="family-detail-link" href="{esc(detail_href(m['name']))}" title="Open detailed research page for {esc(exact_tag)}">{esc(display_name)}</a></h3><code class="exact-model-tag">{esc(exact_tag)}</code><div class="status-stack">{status_tags or tag('local')}</div></div>
  </header>
  <div class="spec-grid" aria-label="Model inventory stats">
    <div class="spec release-date-spec"><span>Release Date</span><b>{esc(card_release_date)}</b><small>best known</small></div>
    <div class="spec"><span>File size</span><b>{esc(m['size_h'])}</b><small>{m['size_gb']} GB</small></div>
    <div class="spec"><span>Params</span><b>{esc(m['parameter_size'])}</b><small>{esc(m['format'])}</small></div>
    <div class="spec"><span>Quant</span><b>{esc(m['quant'])}</b><small>{esc(m['digest'])}</small></div>
    <div class="spec"><span>Context</span><b>{esc(m['context'] or '—')}</b><small>tokens</small></div>
  </div>
  <div class="tags card-caps">{caps_html}</div>
  {skills_html(m)}
  <div class="bench-pair">
    <section class="bench-box direct">
      <h4>Direct Ollama combined 18-test suite</h4>
      <div class="bench-stats">
        <div><span>Total Pass</span><b>{esc(f"{combined_card['total_pass']}/{combined_card['total_tasks']}" if combined_card['total_tasks'] else '—')}</b></div>
        <div><span>Avg wall</span><b>{esc(secs(combined_card['avg_all']) if combined_card['total_tasks'] else '—')}</b></div>
        <div><span>Failed</span><b>{esc(num(combined_card['failed']) if combined_card['total_tasks'] else '—')}</b></div>
        <div><span>Skipped</span><b>{esc(num(combined_card['skipped']) if combined_card['total_tasks'] else '—')}</b></div>
        <div><span>Timeouts</span><b>{esc(timeout_count_text(direct_execution))}</b></div>
        <div><span>Output tokens</span><b>{esc(num(direct_execution.get('eval_count')) if direct_execution else '—')}</b></div>
        <div><span>Profile</span><b>{esc(values_text(direct_execution.get('benchmark_profiles')) if direct_execution else '—')}</b></div>
        <div><span>Grader</span><b>{esc(values_text(direct_execution.get('grading_profiles')) if direct_execution else '—')}</b></div>
        <div><span>Requested context</span><b>{esc(values_text(direct_execution.get('requested_num_ctx')) if direct_execution else '—')}</b></div>
        <div><span>GPU max</span><b>{esc(pct(direct_telemetry.get('max_gpu_pct')) if direct_telemetry else '—')}</b></div>
        <div><span>CPU max</span><b>{esc(pct(direct_telemetry.get('max_cpu_pct')) if direct_telemetry else '—')}</b></div>
        <div><span>GPU °C</span><b>{esc(therm)}</b></div>
        <div><span>Total-system W</span><b>{esc(power)}</b></div>
      </div>
    </section>
    <section class="bench-box openclaw">
      <h4>OpenClaw local model</h4>
      <div class="bench-stats">
        <div><span>Pass</span><b>{esc(f"{osum['passed']}/{osum['tasks']}" if osum else '—')}</b></div>
        <div><span>Avg wall</span><b>{esc(secs(osum['avg_pass_wall']) if osum else '—')}</b></div>
        <div><span>GPU max</span><b>{esc(pct(osum.get('max_gpu_pct')) if osum else '—')}</b></div>
        <div><span>CPU max</span><b>{esc(pct(osum.get('max_cpu_pct')) if osum else '—')}</b></div>
        <div><span>GPU °C</span><b>{esc(openclaw_therm)}</b></div>
        <div><span>Total-system W</span><b>{esc(openclaw_power)}</b></div>
        <div><span>Fails</span><b>{esc(num(max((osum.get('tasks') or 0) - (osum.get('passed') or 0), 0)) if osum else '—')}</b></div>
        <div><span>Source</span><b>{esc('OpenClaw' if osum else '—')}</b></div>
      </div>
    </section>
  </div>
</article>''')
        if i <= 6:
            cards.append(f'''
<article class="mini-card">
  <span>{esc(exact_model_family(m))}</span>
  <h3>{esc(m['name'])}</h3>
  <p>{esc(m['size_h'])} · {esc(m['parameter_size'])} · {esc(m['quant'])}</p>
</article>''')

    targeted_leader_headers = ['Rank','Model','Pass','Avg wall','GPU max','CPU max','GPU °C max','CPU °C max','SoC °C','Total-system W max']
    direct_leader_headers = ['Rank','Model','Pass','Err','Avg pass wall','Avg wall','Ollama total','Load','Prompt eval','Eval','Prompt tok','Output tok','Tok/s','CPU max','CPU avg','GPU max','GPU avg','CPU °C max','CPU °C avg','GPU °C max','GPU °C avg','SoC °C','CPU W max','CPU W avg','GPU W max','GPU W avg','System W','Total-system W max','Total-system W avg','Samples']
    openclaw_leader_headers = ['Rank','Model','Pass','Err','Avg pass wall','Avg wall','OpenClaw dur','CPU max','CPU avg','GPU max','GPU avg','CPU °C max','CPU °C avg','GPU °C max','GPU °C avg','SoC °C','CPU W max','GPU W max','Total-system W max','Samples','Families','Categories','Category pass','Agent model','Winner model','Fallbacks','Attempts','Exit','Failed tests','Per-test results','Assistant/error previews']
    standardized_leader_headers = ['Rank','Model','Pass','Skipped','Timeouts','Avg wall','Output tok','Total tok','Tok/s','Profile','Categories','Failed tests']
    direct_combined_targeted_headers = ['Rank','Model','Control / min strict','Thinking / max strict','Grader cases (A / B)','Strict Δ','Wall ×','Output tok ×','Control / min trace','Thinking / max trace','Pair status','Benchmark context','Context calibration','Qualification reason','Omitted work','Total Pass','Timeouts','Avg wall','Output tok','Profile','GPU max','CPU max','GPU °C max','CPU °C max','SoC °C','Total-system W max']
    direct_combined_headers = [
        'Rank','Model','Model family','Params','Quant','Capabilities','Experiment','Plan SHA','Pair schema','Campaign seed','Planner SHA','Pair ID','Pair kind','Off available','Pair status','Benchmark context','Context calibration','Calibration attempts JSON','Speed comparability','Qualification status','Qualification reason','Qualification probes','Control policy','Off observability','Evidence code','Omitted remaining work','Campaign complete','Campaign progress','First treatment','First strict','First grader cases','First trace evidence','Second treatment','Second strict','Second grader cases','Second trace evidence','Strict Δ','Grader Δ','Wall ×','Output tok ×','Total pass','Skipped','Avg wall all tests',
        'Smoke pass','Smoke err','Smoke avg pass wall','Smoke avg wall','Smoke Ollama total','Smoke load','Smoke prompt eval','Smoke eval','Smoke prompt tok','Smoke output tok','Smoke tok/s',
        'Smoke CPU max','Smoke CPU avg','Smoke GPU max','Smoke GPU avg','Smoke CPU °C max','Smoke CPU °C avg','Smoke GPU °C max','Smoke GPU °C avg','Smoke SoC °C','Smoke CPU W max','Smoke GPU W max','Smoke total-system W max','Smoke samples',
        'Combined pass','Combined skipped','Combined err','Combined timeouts','Profile','Output policy','Output limit','Response deadline','Thinking requested','Thinking resolved','Thinking effective','Thinking chars','Response chars','First output','First answer','Combined avg pass wall','Combined avg wall','Combined Ollama total','Combined load','Combined prompt eval','Combined eval','Combined prompt tok','Combined output tok','Combined total tok','Combined tok/s','Stream chunks','Done reasons','Termination reasons','Combined CPU max','Combined CPU avg','Combined GPU max','Combined GPU avg','Combined CPU °C max','Combined CPU °C avg','Combined GPU °C max','Combined GPU °C avg','Combined SoC °C','Combined CPU W max','Combined GPU W max','Combined total-system W max','Combined samples','Combined families','Combined categories','Category pass','Failed tests','Per-test results','Response/error previews'
    ]

    def hrow(headers):
        return ''.join(f'<th>{esc(h)}</th>' for h in headers)

    def cell(label, value):
        return f'<td><span class="metric-label">{esc(label)}</span><b>{esc(value)}</b></td>'

    def targeted_leader_row(rank, model, s, badge=''):
        failed = s.get('passed') != s.get('tasks') or (s.get('errors') or 0) > 0 or (s.get('grader_errors') or 0) > 0
        fail_badge = fail_badge_for(s) if failed else ''
        row_classes = []
        if failed: row_classes.append('fail-row')
        if rank > 10: row_classes.append('over-top10')
        row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ''
        vals = [
            f'<td class="rank-cell"><span class="rank">#{rank}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a> {badge} {fail_badge}</b></td>',
            cell('Pass', f"{s['passed']}/{s['tasks']}"),
            cell('Avg wall', secs(s.get('avg_wall'))),
            cell('GPU max', pct(s.get('max_gpu_pct'))),
            cell('CPU max', pct(s.get('max_cpu_pct'))),
            cell('GPU °C max', temp(s.get('max_gpu_temp'))),
            cell('CPU °C max', temp(s.get('max_cpu_temp'))),
            cell('SoC °C', temp(s.get('max_soc_temp'))),
            cell('Total-system W max', watts(s.get('max_total_power'))),
        ]
        return '<tr' + row_cls + f' data-rank="{rank}">' + ''.join(vals) + '</tr>'

    def direct_leader_row(rank, model, s, badge=''):
        failed = s.get('passed') != s.get('tasks') or (s.get('errors') or 0) > 0
        fail_badge = fail_badge_for(s) if failed else ''
        row_classes = []
        if failed: row_classes.append('fail-row')
        if rank > 10: row_classes.append('over-top10')
        row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ''
        vals = [
            f'<td class="rank-cell"><span class="rank">#{rank}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a> {badge} {fail_badge}</b></td>',
            cell('Pass', f"{s['passed']}/{s['tasks']}"), cell('Err', num(s.get('errors'))),
            cell('Avg pass wall', secs(s.get('avg_pass_wall'))), cell('Avg wall', secs(s.get('avg_wall'))),
            cell('Ollama total', secs(s.get('avg_ollama_total'))), cell('Load', secs(s.get('avg_ollama_load'))),
            cell('Prompt eval', secs(s.get('avg_prompt_eval'))), cell('Eval', secs(s.get('avg_eval'))),
            cell('Prompt tok', num(s.get('prompt_eval_count'))), cell('Output tok', num(s.get('eval_count'))),
            cell('Tok/s', tps(s.get('avg_tps'))), cell('CPU max', pct(s.get('max_cpu_pct'))), cell('CPU avg', pct(s.get('avg_cpu_pct'))),
            cell('GPU max', pct(s.get('max_gpu_pct'))), cell('GPU avg', pct(s.get('avg_gpu_pct'))),
            cell('CPU °C max', temp(s.get('max_cpu_temp'))), cell('CPU °C avg', temp(s.get('avg_cpu_temp'))),
            cell('GPU °C max', temp(s.get('max_gpu_temp'))), cell('GPU °C avg', temp(s.get('avg_gpu_temp'))),
            cell('SoC °C', temp(s.get('max_soc_temp'))), cell('CPU W max', watts(s.get('max_cpu_power'))), cell('CPU W avg', watts(s.get('avg_cpu_power'))),
            cell('GPU W max', watts(s.get('max_gpu_power'))), cell('GPU W avg', watts(s.get('avg_gpu_power'))),
            cell('System W', watts(s.get('max_system_power'))), cell('Total-system W max', watts(s.get('max_total_power'))), cell('Total-system W avg', watts(s.get('avg_total_power'))),
            cell('Samples', num(s.get('sample_count'))),
        ]
        return '<tr' + row_cls + f' data-rank="{rank}">' + ''.join(vals) + '</tr>'

    def openclaw_leader_row(rank, model, s, badge=''):
        failed = s.get('passed') != s.get('tasks') or (s.get('errors') or 0) > 0
        fail_badge = compact_fail_badge_for(s) if failed else ''
        row_classes = []
        if failed: row_classes.append('fail-row')
        if rank > 10: row_classes.append('over-top10')
        row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ''
        category_pass = '; '.join(f'{k} {v}' for k, v in sorted((s.get('category_pass') or {}).items())) or '—'
        families = ', '.join(s.get('families') or []) or '—'
        categories = ', '.join(s.get('categories') or []) or '—'
        agent_models = ', '.join(s.get('agent_models') or []) or '—'
        winner_models = ', '.join(s.get('winner_models') or []) or '—'
        failed_text = '; '.join(s.get('failed_tests') or []) or 'None'
        per_test_text = '; '.join(s.get('test_details') or []) or '—'
        preview_text = '; '.join(s.get('preview_details') or []) or '—'
        vals = [
            f'<td class="rank-cell"><span class="rank">#{rank}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a> {badge} {fail_badge}</b></td>',
            cell('Pass', f"{s['passed']}/{s['tasks']}"), cell('Err', num(s.get('errors'))),
            cell('Avg pass wall', secs(s.get('avg_pass_wall'))), cell('Avg wall', secs(s.get('avg_wall'))),
            cell('OpenClaw dur', ms(s.get('avg_openclaw_ms'))),
            cell('CPU max', pct(s.get('max_cpu_pct'))), cell('CPU avg', pct(s.get('avg_cpu_pct'))),
            cell('GPU max', pct(s.get('max_gpu_pct'))), cell('GPU avg', pct(s.get('avg_gpu_pct'))),
            cell('CPU °C max', temp(s.get('max_cpu_temp'))), cell('CPU °C avg', temp(s.get('avg_cpu_temp'))),
            cell('GPU °C max', temp(s.get('max_gpu_temp'))), cell('GPU °C avg', temp(s.get('avg_gpu_temp'))),
            cell('SoC °C', temp(s.get('max_soc_temp'))), cell('CPU W max', watts(s.get('max_cpu_power'))),
            cell('GPU W max', watts(s.get('max_gpu_power'))), cell('Total-system W max', watts(s.get('max_total_power'))),
            cell('Samples', num(s.get('sample_count'))), cell('Families', families), cell('Categories', categories), cell('Category pass', category_pass),
            cell('Agent model', agent_models), cell('Winner model', winner_models), cell('Fallbacks', num(s.get('fallbacks_used'))),
            cell('Attempts', num(s.get('fallback_attempts'))), cell('Exit', num(s.get('exit_code_max'))),
            cell('Failed tests', failed_text), cell('Per-test results', per_test_text), cell('Assistant/error previews', preview_text),
        ]
        return '<tr' + row_cls + f' data-rank="{rank}">' + ''.join(vals) + '</tr>'

    def standardized_leader_row(rank, model, s):
        failed = s.get('passed') != s.get('tasks') or (s.get('errors') or 0) > 0
        row_classes = []
        if failed: row_classes.append('fail-row')
        if rank is None:
            row_classes.append('unranked')
        elif rank > 10:
            row_classes.append('over-top10')
        row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ''
        fail_text = '; '.join((s.get('failed_tests') or [])[:4]) or 'None'
        cats = ', '.join(s.get('categories') or []) or '—'
        vals = [
            f'<td class="rank-cell"><span class="rank">{("#" + str(rank)) if rank is not None else "—"}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a></b></td>',
            cell('Pass', f"{s.get('passed')}/{s.get('tasks')}"),
            cell('Skipped', num(s.get('skipped'))),
            cell('Timeouts', timeout_count_text(s)),
            cell('Avg wall', secs(s.get('avg_wall'))),
            cell('Output tok', num(s.get('eval_count'))),
            cell('Total tok', num(s.get('total_token_count'))),
            cell('Tok/s', tps(s.get('avg_tps'))),
            cell('Profile', values_text(s.get('benchmark_profiles'))),
            cell('Categories', cats),
            cell('Failed tests', fail_text),
        ]
        return '<tr' + row_cls + f' data-rank="{rank if rank is not None else "unranked"}">' + ''.join(vals) + '</tr>'

    def direct_combined_metrics(smoke, std):
        smoke = legacy_smoke_component(smoke, std)
        smoke_tasks = smoke.get('tasks') if smoke else 0
        comparison, _, first, _, second = comparison_sides(std)
        if comparison and first and second:
            std_tasks = (first.get('tasks') or 0) + (second.get('tasks') or 0)
            std_pass = (first.get('passed') or 0) + (second.get('passed') or 0)
            skipped = (first.get('skipped') or 0) + (second.get('skipped') or 0)
            std_wall = (first.get('wall_seconds_total') or 0) + (second.get('wall_seconds_total') or 0)
            std_avg_wall = round(std_wall / std_tasks, 3) if std_tasks else None
        else:
            std_tasks = std.get('tasks') if std else 0
            std_pass = std.get('passed') if std else 0
            skipped = std.get('skipped') if std else 0
            std_avg_wall = std.get('avg_wall') if std else None
        smoke_pass = smoke.get('passed') if smoke else 0
        total_tasks = smoke_tasks + std_tasks
        total_pass = smoke_pass + std_pass
        weighted = []
        if smoke and isinstance(smoke.get('avg_wall'), (int, float)) and smoke_tasks:
            weighted.append((smoke.get('avg_wall'), smoke_tasks))
        if isinstance(std_avg_wall, (int, float)) and std_tasks:
            weighted.append((std_avg_wall, std_tasks))
        avg_all = round(sum(v * n for v, n in weighted) / sum(n for _, n in weighted), 3) if weighted else None
        return {'total_pass': total_pass, 'total_tasks': total_tasks, 'skipped': skipped, 'avg_all': avg_all}

    def direct_combined_failed_tests(smoke, std):
        failed_tests = []
        std_families_set = set(std.get('families') or []) if std else set()
        combined_script_includes_smoke = bool(std and 'Smoke' in std_families_set)
        if smoke and smoke.get('failed_tests') and not combined_script_includes_smoke:
            failed_tests.extend('Smoke telemetry: ' + x for x in (smoke.get('failed_tests') or []))
        comparison = (std or {}).get('treatment_comparison') or {}
        if comparison:
            for treatment_label, treatment in (comparison.get('treatments') or {}).items():
                failed_tests.extend(f'{treatment_label}: {item}' for item in (treatment.get('failed_tests') or []))
        elif std and std.get('failed_tests'):
            failed_tests.extend(std.get('failed_tests') or [])
        # Preserve order but avoid duplicates when older smoke telemetry and combined-suite rows overlap.
        deduped = []
        for item in failed_tests:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def direct_combined_fail_badge(smoke, std):
        failed_tests = direct_combined_failed_tests(smoke, std)
        if failed_tests:
            return fail_badge_for({'failed_tests': failed_tests})
        return ''

    def direct_combined_row_state(rank, smoke, std):
        combined = direct_combined_metrics(smoke, std)
        smoke = legacy_smoke_component(smoke, std)
        row_classes = []
        # Mirror OpenClaw: visually flag any model that did not pass every scored task.
        # Skipped OCR/image rows are excluded from total_tasks and are not failures.
        if (combined.get('total_pass') or 0) != (combined.get('total_tasks') or 0) or (smoke and ((smoke.get('errors') or 0) > 0 or (smoke.get('grader_errors') or 0) > 0)) or (std and ((std.get('errors') or 0) > 0 or (std.get('grader_errors') or 0) > 0)):
            row_classes.append('fail-row')
        comparison = (std or {}).get('treatment_comparison') or {}
        if (
            comparison and not comparison.get('valid')
            and not comparison.get('capacity_disposition')
            and 'fail-row' not in row_classes
        ):
            row_classes.append('fail-row')
        if rank is None:
            row_classes.append('unranked')
        elif rank > 10:
            row_classes.append('over-top10')
        row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ''
        return combined, row_cls

    def direct_combined_targeted_row(rank, model, smoke, std):
        combined, row_cls = direct_combined_row_state(rank, smoke, std)
        smoke = legacy_smoke_component(smoke, std)
        telemetry = smoke if smoke and smoke.get('max_gpu_pct') is not None else std
        execution = std or smoke
        fail_badge = direct_combined_fail_badge(smoke, std)
        comparison, first_label, first, second_label, second = comparison_sides(std)
        paired_timeouts = ((first.get('timeouts') or 0) + (second.get('timeouts') or 0)) if first and second else None
        paired_output_tokens = ((first.get('eval_count') or 0) + (second.get('eval_count') or 0)) if first and second else None
        vals = [
            f'<td class="rank-cell"><span class="rank">{("#" + str(rank)) if rank is not None else "—"}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a> {fail_badge}</b></td>',
            cell('Control / min strict', f'{first_label} {treatment_score_text(first)}' if comparison else '—'),
            cell('Thinking / max strict', f'{second_label} {treatment_score_text(second)}' if comparison else '—'),
            cell('Grader cases (A / B)', f'{grader_cases_text(first)} / {grader_cases_text(second)}' if comparison else '—'),
            cell('Strict Δ', comparison_delta_text(comparison, 'strict')),
            cell('Wall ×', comparison_multiplier_text(comparison, 'wall')),
            cell('Output tok ×', comparison_multiplier_text(comparison, 'tokens')),
            cell('Control / min trace', treatment_trace_evidence_text(first)),
            cell('Thinking / max trace', treatment_trace_evidence_text(second)),
            cell('Pair status', comparison_status_text(comparison)),
            cell('Benchmark context', comparison_context_text(comparison)),
            cell('Context calibration', comparison_context_calibration_text(comparison)),
            cell('Qualification reason', comparison.get('qualification_reason') or '—' if comparison else '—'),
            cell('Omitted work', num(comparison.get('omitted_remaining_work_count')) if comparison else '—'),
            cell('Total Pass', f"{combined['total_pass']}/{combined['total_tasks']}"),
            cell('Timeouts', num(paired_timeouts) if comparison else timeout_count_text(execution)),
            cell('Avg wall', secs(combined.get('avg_all'))),
            cell('Output tok', num(paired_output_tokens) if comparison else (num(execution.get('eval_count')) if execution else '—')),
            cell('Profile', values_text(execution.get('benchmark_profiles')) if execution else '—'),
            cell('GPU max', pct(telemetry.get('max_gpu_pct')) if telemetry else '—'),
            cell('CPU max', pct(telemetry.get('max_cpu_pct')) if telemetry else '—'),
            cell('GPU °C max', temp(telemetry.get('max_gpu_temp')) if telemetry else '—'),
            cell('CPU °C max', temp(telemetry.get('max_cpu_temp')) if telemetry else '—'),
            cell('SoC °C', temp(telemetry.get('max_soc_temp')) if telemetry else '—'),
            cell('Total-system W max', watts(telemetry.get('max_total_power')) if telemetry else '—'),
        ]
        return '<tr' + row_cls + f' data-rank="{rank if rank is not None else "unranked"}">' + ''.join(vals) + '</tr>'

    def direct_combined_leader_row(rank, model, smoke, std):
        combined, row_cls = direct_combined_row_state(rank, smoke, std)
        failed_bits = []
        std_families_set = set(std.get('families') or []) if std else set()
        combined_script_includes_smoke = bool(std and 'Smoke' in std_families_set)
        effective_smoke = legacy_smoke_component(smoke, std)
        display_smoke = ((std.get('family_summaries') or {}).get('Smoke') if combined_script_includes_smoke else effective_smoke)
        if effective_smoke and effective_smoke.get('failed_tests'):
            failed_bits.extend('Smoke telemetry: ' + x for x in (effective_smoke.get('failed_tests') or [])[:3])
        elif not effective_smoke and not combined_script_includes_smoke:
            failed_bits.append('Smoke telemetry: not benchmarked')
        comparison = (std or {}).get('treatment_comparison') or {}
        if comparison:
            for treatment_label, treatment in (comparison.get('treatments') or {}).items():
                failed_bits.extend(f'{treatment_label}: ' + x for x in (treatment.get('failed_tests') or [])[:4])
        elif std and std.get('failed_tests'):
            failed_bits.extend('Combined: ' + x for x in (std.get('failed_tests') or [])[:4])
        elif not std:
            failed_bits.append('Combined: not benchmarked')
        failed_tests = direct_combined_failed_tests(smoke, std)
        failed_text = '; '.join(failed_tests) or 'None'
        fail_badge = compact_fail_badge_for({'failed_tests': failed_tests}) if failed_tests else ''
        category_pass = '; '.join(f'{k} {v}' for k, v in sorted((std.get('category_pass') or {}).items())) if std else '—'
        std_families = ', '.join(std.get('families') or []) if std else '—'
        std_categories = ', '.join(std.get('categories') or []) if std else '—'
        model_families = ', '.join(std.get('model_families') or []) if std else '—'
        params = ', '.join(std.get('params') or []) if std else '—'
        quants = ', '.join(std.get('quants') or []) if std else '—'
        capabilities_text = std.get('capabilities_s') if std else '—'
        comparison, first_label, first, second_label, second = comparison_sides(std)
        per_test_bits = []
        if effective_smoke and effective_smoke.get('test_details'):
            per_test_bits.extend('Smoke telemetry ' + x for x in effective_smoke.get('test_details'))
        elif display_smoke and display_smoke.get('test_details'):
            per_test_bits.extend('Smoke ' + x for x in display_smoke.get('test_details'))
        elif not effective_smoke and not combined_script_includes_smoke:
            per_test_bits.append('Smoke telemetry: not benchmarked')
        if comparison:
            for treatment_label, treatment in (comparison.get('treatments') or {}).items():
                per_test_bits.extend(f'{treatment_label} ' + x for x in (treatment.get('test_details') or []))
        elif std and std.get('test_details'):
            per_test_bits.extend('Combined ' + x for x in std.get('test_details'))
        elif not std:
            per_test_bits.append('Combined: not benchmarked')
        per_test_text = '; '.join(per_test_bits) or '—'
        if comparison:
            preview_text = '; '.join(
                f'{treatment_label} {item}'
                for treatment_label, treatment in (comparison.get('treatments') or {}).items()
                for item in (treatment.get('preview_details') or [])
            ) or '—'
        else:
            preview_text = '; '.join(std.get('preview_details') or []) if std else '—'
        vals = [
            f'<td class="rank-cell"><span class="rank">{("#" + str(rank)) if rank is not None else "—"}</span></td>',
            f'<td class="leader-model"><b><a class="model-jump" href="#{model_anchor(model)}">{esc(model)}</a> {fail_badge}</b></td>',
            cell('Model family', model_families),
            cell('Params', params),
            cell('Quant', quants),
            cell('Capabilities', capabilities_text),
            cell('Experiment', (comparison.get('experiment_id') or comparison.get('campaign_id')) if comparison else 'legacy / unpaired'),
            cell('Plan SHA', (comparison.get('plan_sha256') or '')[:12] if comparison else '—'),
            cell('Pair schema', comparison.get('pair_schema_version') if comparison else '—'),
            cell('Campaign seed', comparison.get('campaign_seed') if comparison else '—'),
            cell('Planner SHA', (comparison.get('planner_sha256') or '')[:12] if comparison else '—'),
            cell('Pair ID', comparison.get('pair_id') if comparison else '—'),
            cell('Pair kind', comparison.get('pair_kind') if comparison else '—'),
            cell('Off available', str(bool(comparison.get('off_available'))).lower() if comparison else '—'),
            cell('Pair status', comparison_status_text(comparison)),
            cell('Benchmark context', comparison_context_text(comparison)),
            cell('Context calibration', comparison_context_calibration_text(comparison)),
            cell('Calibration attempts JSON', comparison.get('context_calibration_attempts_json') or '—' if comparison else '—'),
            cell('Speed comparability', comparison.get('speed_comparison_caveat') or 'same-context controls recorded' if comparison else '—'),
            cell('Qualification status', comparison.get('qualification_status') if comparison else '—'),
            cell('Qualification reason', comparison.get('qualification_reason') or '—' if comparison else '—'),
            cell('Qualification probes', ', '.join(comparison.get('qualification_task_ids') or []) if comparison else '—'),
            cell('Control policy', comparison.get('control_policy') or '—' if comparison else '—'),
            cell('Off observability', comparison.get('off_observability') or '—' if comparison else '—'),
            cell('Evidence code', comparison.get('evidence_code') or '—' if comparison else '—'),
            cell('Omitted remaining work', num(comparison.get('omitted_remaining_work_count')) if comparison else '—'),
            cell('Campaign complete', str(bool(comparison.get('campaign_complete'))).lower() if comparison else '—'),
            cell('Campaign progress', f"{comparison.get('campaign_models_complete', 0)}/{comparison.get('campaign_models_total', 0)} models" if comparison else '—'),
            cell('First treatment', first_label or '—'),
            cell('First strict', treatment_score_text(first)),
            cell('First grader cases', grader_cases_text(first)),
            cell('First trace evidence', treatment_trace_evidence_text(first)),
            cell('Second treatment', second_label or '—'),
            cell('Second strict', treatment_score_text(second)),
            cell('Second grader cases', grader_cases_text(second)),
            cell('Second trace evidence', treatment_trace_evidence_text(second)),
            cell('Strict Δ', comparison_delta_text(comparison, 'strict')),
            cell('Grader Δ', comparison_delta_text(comparison, 'grader')),
            cell('Wall ×', comparison_multiplier_text(comparison, 'wall')),
            cell('Output tok ×', comparison_multiplier_text(comparison, 'tokens')),
            cell('Total pass', f"{combined['total_pass']}/{combined['total_tasks']}"),
            cell('Skipped', num(combined.get('skipped'))),
            cell('Avg wall all tests', secs(combined.get('avg_all'))),
            cell('Smoke pass', f"{display_smoke.get('passed')}/{display_smoke.get('tasks')}" if display_smoke else '—'),
            cell('Smoke err', num(display_smoke.get('errors')) if display_smoke else '—'),
            cell('Smoke avg pass wall', secs(display_smoke.get('avg_pass_wall')) if display_smoke else '—'),
            cell('Smoke avg wall', secs(display_smoke.get('avg_wall')) if display_smoke else '—'),
            cell('Smoke Ollama total', secs(display_smoke.get('avg_ollama_total')) if display_smoke else '—'),
            cell('Smoke load', secs(display_smoke.get('avg_ollama_load')) if display_smoke else '—'),
            cell('Smoke prompt eval', secs(display_smoke.get('avg_prompt_eval')) if display_smoke else '—'),
            cell('Smoke eval', secs(display_smoke.get('avg_eval')) if display_smoke else '—'),
            cell('Smoke prompt tok', num(display_smoke.get('prompt_eval_count')) if display_smoke else '—'),
            cell('Smoke output tok', num(display_smoke.get('eval_count')) if display_smoke else '—'),
            cell('Smoke tok/s', tps(display_smoke.get('avg_tps')) if display_smoke else '—'),
            cell('Smoke CPU max', pct(display_smoke.get('max_cpu_pct')) if display_smoke else '—'),
            cell('Smoke CPU avg', pct(display_smoke.get('avg_cpu_pct')) if display_smoke else '—'),
            cell('Smoke GPU max', pct(display_smoke.get('max_gpu_pct')) if display_smoke else '—'),
            cell('Smoke GPU avg', pct(display_smoke.get('avg_gpu_pct')) if display_smoke else '—'),
            cell('Smoke CPU °C max', temp(display_smoke.get('max_cpu_temp')) if display_smoke else '—'),
            cell('Smoke CPU °C avg', temp(display_smoke.get('avg_cpu_temp')) if display_smoke else '—'),
            cell('Smoke GPU °C max', temp(display_smoke.get('max_gpu_temp')) if display_smoke else '—'),
            cell('Smoke GPU °C avg', temp(display_smoke.get('avg_gpu_temp')) if display_smoke else '—'),
            cell('Smoke SoC °C', temp(display_smoke.get('max_soc_temp')) if display_smoke else '—'),
            cell('Smoke CPU W max', watts(display_smoke.get('max_cpu_power')) if display_smoke else '—'),
            cell('Smoke GPU W max', watts(display_smoke.get('max_gpu_power')) if display_smoke else '—'),
            cell('Smoke total-system W max', watts(display_smoke.get('max_total_power')) if display_smoke else '—'),
            cell('Smoke samples', num(display_smoke.get('sample_count')) if display_smoke else '—'),
            cell('Combined pass', f"{std.get('passed')}/{std.get('tasks')}" if std else '—'),
            cell('Combined skipped', num(std.get('skipped')) if std else '—'),
            cell('Combined err', num(std.get('errors')) if std else '—'),
            cell('Combined timeouts', timeout_count_text(std)),
            cell('Profile', values_text(std.get('benchmark_profiles')) if std else '—'),
            cell('Output policy', values_text(std.get('output_token_policies')) if std else '—'),
            cell('Output limit', output_limit_text(std)),
            cell('Response deadline', response_timeout_text(std)),
            cell('Thinking requested', values_text(std.get('thinking_requested')) if std else '—'),
            cell('Thinking resolved', values_text(std.get('thinking_resolved')) if std else '—'),
            cell('Thinking effective', values_text(std.get('thinking_effective')) if std else '—'),
            cell('Thinking chars', num(std.get('thinking_chars')) if std else '—'),
            cell('Response chars', num(std.get('response_chars')) if std else '—'),
            cell('First output', secs(std.get('avg_time_to_first_output')) if std else '—'),
            cell('First answer', secs(std.get('avg_time_to_first_answer')) if std else '—'),
            cell('Combined avg pass wall', secs(std.get('avg_pass_wall')) if std else '—'),
            cell('Combined avg wall', secs(std.get('avg_wall')) if std else '—'),
            cell('Combined Ollama total', secs(std.get('avg_ollama_total')) if std else '—'),
            cell('Combined load', secs(std.get('avg_ollama_load')) if std else '—'),
            cell('Combined prompt eval', secs(std.get('avg_prompt_eval')) if std else '—'),
            cell('Combined eval', secs(std.get('avg_eval')) if std else '—'),
            cell('Combined prompt tok', num(std.get('prompt_eval_count')) if std else '—'),
            cell('Combined output tok', num(std.get('eval_count')) if std else '—'),
            cell('Combined total tok', num(std.get('total_token_count')) if std else '—'),
            cell('Combined tok/s', tps(std.get('avg_tps')) if std else '—'),
            cell('Stream chunks', num(std.get('stream_chunk_count')) if std else '—'),
            cell('Done reasons', values_text(std.get('done_reasons')) if std else '—'),
            cell('Termination reasons', values_text(std.get('termination_reasons')) if std else '—'),
            cell('Combined CPU max', pct(std.get('max_cpu_pct')) if std else '—'),
            cell('Combined CPU avg', pct(std.get('avg_cpu_pct')) if std else '—'),
            cell('Combined GPU max', pct(std.get('max_gpu_pct')) if std else '—'),
            cell('Combined GPU avg', pct(std.get('avg_gpu_pct')) if std else '—'),
            cell('Combined CPU °C max', temp(std.get('max_cpu_temp')) if std else '—'),
            cell('Combined CPU °C avg', temp(std.get('avg_cpu_temp')) if std else '—'),
            cell('Combined GPU °C max', temp(std.get('max_gpu_temp')) if std else '—'),
            cell('Combined GPU °C avg', temp(std.get('avg_gpu_temp')) if std else '—'),
            cell('Combined SoC °C', temp(std.get('max_soc_temp')) if std else '—'),
            cell('Combined CPU W max', watts(std.get('max_cpu_power')) if std else '—'),
            cell('Combined GPU W max', watts(std.get('max_gpu_power')) if std else '—'),
            cell('Combined total-system W max', watts(std.get('max_total_power')) if std else '—'),
            cell('Combined samples', num(std.get('sample_count')) if std else '—'),
            cell('Combined families', std_families),
            cell('Combined categories', std_categories),
            cell('Category pass', category_pass),
            cell('Failed tests', failed_text),
            cell('Per-test results', per_test_text),
            cell('Response/error previews', preview_text),
        ]
        return '<tr' + row_cls + f' data-rank="{rank if rank is not None else "unranked"}">' + ''.join(vals) + '</tr>'

    ranking = []
    ranking_targeted = []
    sortable = []
    for model, s in direct_summary.items():
        sortable.append((-(s['passed']), s['avg_pass_wall'] if s['avg_pass_wall'] is not None else 999999, model, s))
    for rank, (_,__,model,s) in enumerate(sorted(sortable), 1):
        ranking.append(direct_leader_row(rank, model, s))
        ranking_targeted.append(targeted_leader_row(rank, model, s))

    openclaw_ranking = []
    openclaw_ranking_targeted = []
    openclaw_sortable = []
    local_allowed = {a.replace('ollama/','') for a in allowed}
    local_allowed.add(default_local_name)
    for model, s in oc_summary.items():
        if model in local_allowed or ('ollama/' + model) in allowed:
            openclaw_sortable.append((-(s['passed']), s['avg_pass_wall'] if s['avg_pass_wall'] is not None else 999999, model, s))
    for rank, (_,__,model,s) in enumerate(sorted(openclaw_sortable), 1):
        default_badge = tag('default', 'default') if model == default_local_name else ''
        openclaw_ranking.append(openclaw_leader_row(rank, model, s, default_badge))
        openclaw_ranking_targeted.append(targeted_leader_row(rank, model, s, default_badge))

    standardized_ranking = []
    standardized_sortable = []
    standardized_accuracy_only = any(
        (s.get('treatment_comparison') or {}).get('per_model_context_policy')
        for s in standardized_summary.values()
    )
    for model, s in standardized_summary.items():
        comparison = s.get('treatment_comparison') or {}
        rankable = not comparison or comparison.get('rankable', comparison.get('valid', False))
        speed_key = 0 if standardized_accuracy_only else (s.get('avg_wall') if isinstance(s.get('avg_wall'), (int,float)) else 999999)
        standardized_sortable.append((0 if rankable else 1, -(s.get('passed') or 0), speed_key, model, s))
    eligible_rank = 0
    for unranked, _, __, model, s in sorted(standardized_sortable):
        rank = None
        if not unranked:
            eligible_rank += 1
            rank = eligible_rank
        standardized_ranking.append(standardized_leader_row(rank, model, s))

    direct_combined_ranking = []
    direct_combined_targeted_ranking = []
    combined_models = sorted(set(direct_summary) | set(standardized_summary))
    combined_sortable = []
    combined_accuracy_only = any(
        ((standardized_summary.get(model) or {}).get('treatment_comparison') or {}).get('per_model_context_policy')
        for model in combined_models
    )
    for model in combined_models:
        smoke = direct_summary.get(model)
        std = standardized_summary.get(model)
        combined = direct_combined_metrics(smoke, std)
        comparison = (std or {}).get('treatment_comparison') or {}
        rankable = not comparison or comparison.get('rankable', comparison.get('valid', False))
        speed_key = 0 if combined_accuracy_only else (combined.get('avg_all') if isinstance(combined.get('avg_all'), (int, float)) else 999999)
        combined_sortable.append((0 if rankable else 1, -(combined.get('total_pass') or 0), speed_key, model, smoke, std))
    eligible_rank = 0
    for unranked, _, __, model, smoke, std in sorted(combined_sortable):
        rank = None
        if not unranked:
            eligible_rank += 1
            rank = eligible_rank
        direct_combined_targeted_ranking.append(direct_combined_targeted_row(rank, model, smoke, std))
        direct_combined_ranking.append(direct_combined_leader_row(rank, model, smoke, std))

    allowed_items = []
    for a in sorted(allowed):
        local = a.replace('ollama/','')
        installed = any(m['name'] == local for m in models)
        cls = 'ok' if installed else 'warn'
        extra = 'installed' if installed else 'not installed'
        allowed_items.append(f'<li><span class="dot {cls}"></span><code>{esc(a)}</code><small>{extra}</small></li>')

    direct_test_details = '''
      <details class="ranking-details test-suite-details">
        <summary><span>Direct Ollama full test-suite details</span><small>Collapsed by default · 18 defined tests</small></summary>
        <div class="ranking-body test-notes"><strong>Direct Ollama combined benchmark suite</strong><ul>
          <li><b>exact_reply</b> — smoke test for strict instruction following; model must return one exact token with no extra text.</li>
          <li><b>simple_reasoning</b> — smoke test for basic arithmetic/reasoning plus required final-answer formatting.</li>
          <li><b>coding_micro</b> — smoke test for producing a compact private-IPv4 helper function/code answer.</li>
          <li><b>ifeval_exact</b> — IFEval-style constrained output; tests exact compliance with a tightly specified response.</li>
          <li><b>ifeval_json</b> — IFEval-style structured output; tests whether the model returns valid compact JSON with required keys/values.</li>
          <li><b>gsm8k_mini</b> — GSM8K-style grade-school math word problem; tests simple quantitative reasoning and final-answer discipline.</li>
          <li><b>math500_mini</b> — MATH-500-style algebra task; tests symbolic manipulation and concise numeric answer formatting.</li>
          <li><b>mmlu_pro_security</b> — MMLU-Pro-style security multiple choice; tests defensive security knowledge and option selection.</li>
          <li><b>arc_challenge_mini</b> — ARC-Challenge-style science reasoning; tests commonsense/scientific cause-effect reasoning.</li>
          <li><b>hellaswag_mini</b> — HellaSwag-style plausible continuation; tests commonsense completion and distractor rejection.</li>
          <li><b>truthfulqa_mini</b> — TruthfulQA-style uncertainty check; tests resistance to overclaiming and ability to answer truthfully.</li>
          <li><b>humaneval_mini</b> — HumanEval+-style Python generation; tests coding ability for a small function specification.</li>
          <li><b>mbpp_mini</b> — MBPP+-style programming task; tests practical Python function generation for log/IP parsing.</li>
          <li><b>bfcl_mini</b> — BFCL-style function-call selection; tests JSON tool selection and argument extraction.</li>
          <li><b>ragas_mini</b> — RAGAS/RAG-style grounded QA; tests answering from supplied context without inventing facts.</li>
          <li><b>prompt_injection_mini</b> — prompt-injection resistance; tests ignoring malicious retrieved-context instructions.</li>
          <li><b>cyber_soc_mini</b> — defensive cyber/SOC triage; tests classification of suspicious SSH activity and defensive action reasoning.</li>
          <li><b>ocrbench_mini</b> — OCRBench/TextVQA-style text-in-image reading; tests image/OCR capability and is skipped for text-only models.</li>
        </ul></div>
      </details>'''

    openclaw_test_details = '''
      <details class="ranking-details test-suite-details">
        <summary><span>OpenClaw full test-suite details</span><small>Collapsed by default · 17 scored text tests</small></summary>
        <div class="ranking-body test-notes"><strong>OpenClaw text benchmark suite</strong><ul>
          <li><b>exact_reply</b> — smoke test for strict instruction following through the OpenClaw agent harness.</li>
          <li><b>simple_reasoning</b> — smoke test for basic arithmetic/reasoning and final-answer formatting through OpenClaw.</li>
          <li><b>coding_micro</b> — smoke test for small code-generation behavior through OpenClaw.</li>
          <li><b>ifeval_exact</b> — constrained output compliance; tests whether OpenClaw/model routing preserves exact instruction following.</li>
          <li><b>ifeval_json</b> — structured-output compliance; tests valid JSON generation with required fields.</li>
          <li><b>gsm8k_mini</b> — grade-school math reasoning; tests arithmetic and concise final-answer formatting.</li>
          <li><b>math500_mini</b> — algebra reasoning; tests solving a simple equation and returning the requested final value.</li>
          <li><b>mmlu_pro_security</b> — defensive security knowledge; tests selecting the best control for credential-stuffing risk.</li>
          <li><b>arc_challenge_mini</b> — science reasoning; tests basic physical-world cause/effect reasoning.</li>
          <li><b>hellaswag_mini</b> — commonsense continuation; tests choosing the plausible next event among distractors.</li>
          <li><b>truthfulqa_mini</b> — truthful uncertainty; tests avoiding absolute/false claims about VPN anonymity.</li>
          <li><b>humaneval_mini</b> — Python function generation; tests code synthesis for private IPv4 detection.</li>
          <li><b>mbpp_mini</b> — basic programming; tests code synthesis for counting unique IPs in log lines.</li>
          <li><b>bfcl_mini</b> — tool-call JSON selection; tests mapping a user request to the correct tool and arguments.</li>
          <li><b>ragas_mini</b> — context-grounded answer; tests extracting the answer from provided context only.</li>
          <li><b>prompt_injection_mini</b> — prompt-injection resistance; tests ignoring malicious retrieved text and following system/user facts.</li>
          <li><b>cyber_soc_mini</b> — defensive SOC triage; tests identifying likely SSH brute force/scanning activity and defensive response.</li>
        </ul><p class="muted"><b>OCR disabled for OpenClaw:</b> <code>ocrbench_mini</code> is intentionally not run because <code>openclaw agent</code> currently has no image attachment option.</p></div>
      </details>'''

    model_meta = {m['name']: m for m in models}
    ranked_models = []
    for rank, (_, __, model, s) in enumerate(sorted(openclaw_sortable), 1):
        meta = model_meta.get(model, {})
        is_current = model == default_local_name
        title = 'Current OpenClaw default' if is_current else ('Top measured result' if rank == 1 else 'Benchmarked local option')
        why = f"{s.get('passed', 0)}/{s.get('tasks', 0)} pass with {secs(s.get('avg_pass_wall'))} average successful wall time on this {HOST_LABEL}."
        capabilities = set(meta.get('capabilities') or [])
        uses = 'Tool-capable agent workflows.' if 'tools' in capabilities else 'General local text workflows; validate tool use separately.'
        ranked_models.append({'rank': rank, 'model': model, 's': s, 'meta': meta, 'title': title, 'why': why, 'uses': uses})
    top_recommendations = ranked_models[:5]
    top_rec_rows = []
    for i, r in enumerate(top_recommendations, 1):
        s = r['s']; meta = r['meta']
        default_tag = tag('current default', 'default') if r['model'] == default_local_name else ''
        top_rec_rows.append(f"<tr><td><span class='rank'>#{i}</span></td><td><b><a class='model-jump' href='#{model_anchor(r['model'])}'>{esc(r['model'])}</a></b> {default_tag}<small>{esc(r['title'])}</small></td><td><b>{esc(str(s.get('passed')) + '/' + str(s.get('tasks')))}</b></td><td><b>{esc(secs(s.get('avg_pass_wall')))}</b></td><td>{esc(token_count_text(meta.get('context')))}</td><td>{esc(meta.get('size_h') or '—')} · {esc(meta.get('parameter_size') or '—')} · {esc(meta.get('quant') or '—')}</td><td>{esc(r['uses'])}</td></tr>")
    all_rank_rows = []
    for r in ranked_models:
        s = r['s']; meta = r['meta']
        failed = ', '.join(s.get('failed_tests') or []) or 'None'
        default_tag = ' · current default' if r['model'] == default_local_name else ''
        all_rank_rows.append(f"<tr><td>#{r['rank']}</td><td><a class='model-jump' href='#{model_anchor(r['model'])}'>{esc(r['model'])}</a>{esc(default_tag)}</td><td>{esc(str(s.get('passed')) + '/' + str(s.get('tasks')))}</td><td>{esc(secs(s.get('avg_pass_wall')))}</td><td>{esc(token_count_text(meta.get('context')))}</td><td>{esc(temp(s.get('max_gpu_temp')))}</td><td>{esc(watts(s.get('max_total_power')))}</td><td>{esc(failed)}</td></tr>")
    not_default_rows = []
    for r in ranked_models:
        s = r['s']
        if (s.get('tasks') or 0) and (s.get('passed') or 0) < (s.get('tasks') or 0):
            not_default_rows.append(f"<tr><td><a class='model-jump' href='#{model_anchor(r['model'])}'>{esc(r['model'])}</a></td><td>{esc(str(s.get('passed')) + '/' + str(s.get('tasks')))}</td><td>{esc(secs(s.get('avg_pass_wall')))}</td><td>{esc('One or more scored tests did not pass; review the failed-test details before selecting it as the default.')}</td></tr>")
    if ranked_models:
        highlighted = next((r for r in ranked_models if r['model'] == default_local_name), ranked_models[0])
        highlighted_label = 'Current default benchmark' if highlighted['model'] == default_local_name else 'Top measured model'
        highlighted_summary = f"{highlighted['s'].get('passed', 0)}/{highlighted['s'].get('tasks', 0)} pass · {secs(highlighted['s'].get('avg_pass_wall'))} average successful wall time."
        not_default_section = f'''<h3>Models requiring review before default use</h3>
          <div class="leader-table-wrap wide"><table class="leader-table recommendation-table"><thead><tr><th>Model</th><th>Pass</th><th>Avg wall</th><th>Why</th></tr></thead><tbody>{''.join(not_default_rows)}</tbody></table></div>''' if not_default_rows else ''
        hardware_ranking_section = f'''
      <details class="ranking-details" id="openclaw-hardware-ranking">
        <summary><span>Recommended OpenClaw model ranking for this {esc(HOST_LABEL)}</span><small>Collapsed by default · based on available local OpenClaw benchmark summaries</small></summary>
        <div class="ranking-body">
          <p class="muted"><b>Current default:</b> <code>{esc(default)}</code>. Ranking uses this {esc(HOST_LABEL)}'s local OpenClaw telemetry: pass rate first, then average successful wall time, with context size, capability fit, thermals, and power as tie-breakers for practical agent use.</p>
          <div class="recommendation-callout"><b>{esc(highlighted_label)}:</b> <code>{esc(highlighted['model'])}</code><span>{esc(highlighted_summary)}</span></div>
          <div class="leader-table-wrap wide"><table class="leader-table recommendation-table"><thead><tr><th>Pick</th><th>Model</th><th>Pass</th><th>Avg wall</th><th>Context</th><th>Local package</th><th>Best example uses</th></tr></thead><tbody>{''.join(top_rec_rows)}</tbody></table></div>
          <h3>Complete OpenClaw ranking on this hardware</h3>
          <div class="leader-table-wrap wide"><table class="leader-table recommendation-table"><thead><tr><th>Rank</th><th>Model</th><th>Pass</th><th>Avg wall</th><th>Context</th><th>GPU °C max</th><th>Total-system W max</th><th>Failed tests</th></tr></thead><tbody>{''.join(all_rank_rows)}</tbody></table></div>
          {not_default_section}
        </div>
      </details>'''
    else:
        hardware_ranking_section = ''
    families = sorted({exact_model_family(m) for m in models}, key=lambda s: s.lower())
    family_options = ''.join(f'<option value="{esc(f.lower())}">{esc(f)}</option>' for f in families)

    html_doc = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(DASHBOARD_TITLE)}</title>
<style>
:root {{
  --bg:#060914; --panel:#0b1220; --panel2:#111b2e; --ink:#edf6ff; --muted:#8fa0b8; --line:rgba(148,163,184,.18);
  --cyan:#21d4fd; --blue:#5b8cff; --violet:#8b5cf6; --green:#22e6a6; --amber:#f7c86a; --red:#ff647c;
  --shadow:0 22px 70px rgba(0,0,0,.45); --radius:24px;
}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:radial-gradient(circle at 16% -10%,rgba(33,212,253,.28),transparent 34%),radial-gradient(circle at 96% 0,rgba(139,92,246,.20),transparent 36%),linear-gradient(180deg,#060914 0%,#08111f 52%,#060914 100%); color:var(--ink);font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;}}
a{{color:inherit}} .wrap{{width:min(1220px,calc(100% - 28px));margin:auto;padding:22px 0 58px}} .hero{{position:relative;overflow:hidden;border:1px solid var(--line);background:linear-gradient(145deg,rgba(17,27,46,.94),rgba(6,9,20,.88));border-radius:32px;box-shadow:var(--shadow);padding:24px;margin-top:12px}} .hero:before{{content:"";position:absolute;right:-90px;top:-90px;width:260px;height:260px;border-radius:999px;background:linear-gradient(135deg,rgba(33,212,253,.24),rgba(139,92,246,.18));filter:blur(6px)}} .eyebrow{{display:inline-flex;gap:8px;align-items:center;color:#bdefff;font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:11px}} .pulse{{width:9px;height:9px;border-radius:99px;background:var(--green);box-shadow:0 0 0 6px rgba(34,230,166,.12)}} h1{{margin:12px 0 8px;font-size:clamp(32px,8vw,74px);line-height:.92;letter-spacing:-.06em}} .hero p{{color:var(--muted);font-size:16px;line-height:1.6;max-width:780px}} .hero-actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}} .btn{{border:1px solid var(--line);border-radius:999px;padding:11px 14px;background:rgba(255,255,255,.05);text-decoration:none;font-weight:800}} .btn.primary{{background:linear-gradient(135deg,var(--cyan),var(--blue));color:#03101c;border:0}} .stats{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:16px 0}} .stat{{border:1px solid var(--line);background:rgba(255,255,255,.055);border-radius:20px;padding:14px}} .stat span{{display:block;color:var(--muted);font-size:12px}} .stat b{{display:block;font-size:24px;letter-spacing:-.04em;line-height:1.05;overflow-wrap:anywhere}} .grid{{display:grid;gap:14px;margin-top:14px}} .grid>*{{min-width:0}} .panel{{border:1px solid var(--line);background:rgba(11,18,32,.82);border-radius:var(--radius);box-shadow:0 16px 46px rgba(0,0,0,.28);padding:18px;min-width:0}} .panel h2{{margin:0 0 12px;font-size:20px;letter-spacing:-.03em}} .openclaw-current{{display:grid;gap:12px}} .model-highlight{{background:linear-gradient(135deg,rgba(33,212,253,.16),rgba(91,140,255,.12));border:1px solid rgba(33,212,253,.24);border-radius:20px;padding:16px}} code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}} .model-highlight code{{display:block;font-size:13px;word-break:break-all;color:#dff8ff}} .model-highlight strong{{display:block;font-size:18px;margin-bottom:4px}} .muted, small{{color:var(--muted)}} ul.clean{{list-style:none;padding:0;margin:0;display:grid;gap:8px}} ul.clean li{{display:flex;gap:9px;align-items:center;justify-content:space-between;border:1px solid var(--line);background:rgba(255,255,255,.035);border-radius:14px;padding:10px;min-width:0}} ul.clean code{{font-size:12px;overflow:hidden;text-overflow:ellipsis}} .dot{{width:9px;height:9px;border-radius:50%;background:var(--green);flex:none}} .dot.warn{{background:var(--amber)}} .rank-row{{display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;border-bottom:1px solid var(--line);padding:10px 0}} .rank-row:last-child{{border-bottom:0}} .rank{{width:36px;height:36px;border-radius:12px;background:rgba(33,212,253,.12);display:grid;place-items:center;color:#bdefff;font-weight:900}} .rank-row b,.rank-row small{{display:block}} .rank-row em{{font-style:normal;color:#dff8ff;font-weight:900}} .toolbar{{display:grid;gap:10px;margin:18px 0 10px}} .search,.family-select{{width:100%;background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:16px;padding:14px 15px;color:var(--ink);outline:none}}.family-filter{{display:grid;gap:6px;min-width:220px}}.family-filter label{{color:#9ae8ff;font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:900}}.family-select{{appearance:none;background-image:linear-gradient(45deg,transparent 50%,#9ae8ff 50%),linear-gradient(135deg,#9ae8ff 50%,transparent 50%);background-position:calc(100% - 18px) 52%,calc(100% - 12px) 52%;background-size:6px 6px,6px 6px;background-repeat:no-repeat;padding-right:34px}}.family-select option{{background:#0b1220;color:#edf6ff}} .chips{{display:flex;gap:8px;overflow:auto;padding-bottom:4px}} .chip{{border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.05);padding:9px 12px;color:var(--muted);white-space:nowrap;font-weight:800}} .chip.active{{background:rgba(33,212,253,.14);color:#c8f7ff;border-color:rgba(33,212,253,.32)}} .cards{{display:grid;grid-template-columns:1fr;gap:10px}} .mini-card{{border:1px solid var(--line);border-radius:20px;background:rgba(255,255,255,.05);padding:14px}} .mini-card span{{color:#9ae8ff;font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:900}} .mini-card h3{{font-size:15px;margin:8px 0;word-break:break-word}} .mini-card p{{color:var(--muted);margin:0;font-size:13px}} .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:22px;background:rgba(255,255,255,.035)}} table{{width:100%;border-collapse:collapse;min-width:980px}} th,td{{padding:14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{color:#b9c7db;font-size:12px;text-transform:uppercase;letter-spacing:.09em;background:rgba(255,255,255,.04);position:sticky;top:0}} td small,.model-cell small{{display:block;margin-top:5px}} .model-cell strong{{word-break:break-word}} .tags{{display:flex;flex-wrap:wrap;gap:6px}} .tag{{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:4px 8px;margin:2px 4px 2px 0;color:#ccd7e8;background:rgba(255,255,255,.045);font-size:11px;font-weight:800}} .tag.default{{background:rgba(34,230,166,.14);border-color:rgba(34,230,166,.3);color:#bfffe9}} .tag.configured{{background:rgba(91,140,255,.13);border-color:rgba(91,140,255,.32);color:#cfe0ff}} .tag.cap{{color:#cbd5e1}} .footer{{color:var(--muted);text-align:center;margin-top:22px;font-size:12px}} @media(min-width:720px){{.wrap{{padding-top:30px}}.hero{{padding:34px}}.stats{{grid-template-columns:repeat(4,1fr)}}.grid.two{{grid-template-columns:1.05fr .95fr}}#benchmarks.grid.two{{grid-template-columns:1fr}}.cards{{grid-template-columns:repeat(3,1fr)}}.toolbar{{grid-template-columns:1fr auto}}}} @media(max-width:719px){{body{{overflow-x:hidden}}.wrap{{width:calc(100% - 24px);padding-left:0;padding-right:0}}.hero{{padding:24px;max-width:100%}}.hero p{{max-width:100%;overflow-wrap:normal}}.hero-actions{{display:grid;grid-template-columns:1fr}}.btn{{display:block;max-width:100%}}.hero-actions .btn{{width:auto;text-align:center}}.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.panel{{padding:16px;overflow:hidden}}.rank-row{{grid-template-columns:auto minmax(0,1fr) auto}}.rank-row b{{overflow-wrap:anywhere}}ul.clean li{{min-width:0;align-items:flex-start}}ul.clean code{{min-width:0;white-space:normal;overflow-wrap:anywhere;text-overflow:clip}}.model-highlight code{{overflow-wrap:anywhere}}table{{min-width:0}} thead{{display:none}} tr{{display:block;padding:10px;border-bottom:1px solid var(--line)}} td{{display:flex;justify-content:space-between;gap:14px;border-bottom:0;padding:8px 6px;min-width:0}} td:before{{content:attr(data-label);color:var(--muted);font-size:12px;flex:0 0 38%}} td:first-child{{display:block}} td:first-child:before{{display:none}}td b,td small,.model-cell strong{{overflow-wrap:anywhere}}}}
.model-jump{{color:#dff8ff;text-decoration:none;border-bottom:1px dashed rgba(33,212,253,.45)}}.model-jump:hover{{color:#9ae8ff;border-bottom-color:#9ae8ff}}.family-detail-link{{color:inherit;text-decoration:none;border-bottom:1px dashed rgba(33,212,253,.38)}}.family-detail-link:hover{{color:#dff8ff;border-bottom-color:#9ae8ff}}.release-date-spec{{grid-column:1/-1}}.release-date-spec b{{font-size:14px!important;line-height:1.25!important}}.ranking-details{{margin-top:16px;border:1px solid rgba(33,212,253,.18);border-radius:18px;background:rgba(2,6,23,.28);overflow:hidden}}.ranking-details summary{{cursor:pointer;list-style:none;display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px 16px;color:#eafcff;font-weight:900}}.ranking-details summary::-webkit-details-marker{{display:none}}.ranking-details summary span{{font-size:16px}}.ranking-details summary small{{color:var(--muted);font-weight:700}}.ranking-body{{padding:0 16px 16px}}.recommendation-callout{{display:grid;gap:6px;margin:12px 0 14px;padding:14px;border-radius:16px;border:1px solid rgba(34,197,94,.28);background:linear-gradient(135deg,rgba(34,197,94,.14),rgba(33,212,253,.08))}}.recommendation-callout b{{color:#bbf7d0;text-transform:uppercase;font-size:12px;letter-spacing:.08em}}.recommendation-callout span{{color:#dff8ff}}.recommendation-table td small{{display:block;color:var(--muted);margin-top:4px}}.leader-table td{{vertical-align:top}}.leader-table td b{{display:block;max-height:7.5em;overflow:auto;line-height:1.35;scrollbar-width:thin}}.leader-table .leader-model b{{max-height:3.4em}}.tag.fail{{background:rgba(255,100,124,.16);border-color:rgba(255,100,124,.42);color:#ffc2cc}}.fail-row{{background:linear-gradient(90deg,rgba(255,100,124,.14),rgba(255,100,124,.035) 42%,transparent)}}.fail-row td:first-child{{box-shadow:inset 4px 0 0 rgba(255,100,124,.8)}}.fail-row .rank{{background:rgba(255,100,124,.15);color:#ffc2cc}}.llm-card{{scroll-margin-top:18px}}.llm-card:target{{outline:2px solid rgba(33,212,253,.75);box-shadow:0 0 0 6px rgba(33,212,253,.13),0 18px 48px rgba(0,0,0,.32)}}.leader-panel-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}.leader-controls{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.leader-panel-head h2{{margin:0}}.view-toggle{{display:inline-flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:999px;background:rgba(3,8,18,.35)}}.view-btn,.range-btn{{border:0;border-radius:999px;background:transparent;color:var(--muted);font-weight:900;font-size:12px;padding:8px 12px;cursor:pointer}}.view-btn.active,.range-btn.active{{background:linear-gradient(135deg,rgba(33,212,253,.24),rgba(91,140,255,.18));color:#dff8ff;box-shadow:0 8px 20px rgba(0,0,0,.22)}}.hidden{{display:none!important}}.over-top10{{display:none!important}}.leader-panel.show-all .over-top10{{display:table-row!important}}.test-notes{{margin:12px 0 4px;padding:12px 14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.035);color:var(--muted);font-size:13px;line-height:1.45}}.test-notes strong{{display:block;color:#dff8ff;margin-bottom:6px}}.test-notes ul{{margin:0;padding-left:18px;display:grid;gap:4px}}.test-notes b{{color:#edf6ff}}.targeted-table{{min-width:980px}}@media(max-width:719px){{.leader-panel-head{{align-items:flex-start;flex-direction:column}}.view-toggle{{width:100%;display:grid;grid-template-columns:1fr 1fr}}.view-btn,.range-btn{{width:100%}}.targeted-table{{min-width:0}}}}.collapsible-panel{{padding:0;overflow:hidden}}.collapse-summary{{list-style:none;cursor:pointer;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:18px;border-radius:var(--radius);user-select:none}}.collapse-summary::-webkit-details-marker{{display:none}}.collapse-summary:after{{content:'+';width:34px;height:34px;border-radius:12px;display:grid;place-items:center;background:rgba(33,212,253,.12);border:1px solid rgba(33,212,253,.24);color:#bdefff;font-weight:900;font-size:20px;grid-column:2;grid-row:1 / span 2}}.collapsible-panel[open] .collapse-summary:after{{content:'−'}}.collapse-summary span{{font-size:20px;font-weight:900;letter-spacing:-.03em;min-width:0}}.collapse-summary small{{grid-column:1;color:var(--muted);font-size:12px}}.collapse-body{{padding:0 18px 18px;margin-top:0!important}}.collapsible-panel:not([open]){{background:rgba(11,18,32,.66)}}.collapsible-panel[open] .collapse-summary{{border-bottom:1px solid var(--line);border-radius:var(--radius) var(--radius) 0 0;margin-bottom:14px;background:rgba(255,255,255,.025)}}@media(max-width:719px){{.collapse-summary{{grid-template-columns:1fr auto;padding:16px}}.collapse-summary span{{font-size:18px}}.collapse-body{{padding:0 16px 16px}}}}.openclaw-banner.hermes-banner{{border-color:rgba(139,92,246,.28);background:linear-gradient(135deg,rgba(139,92,246,.13),rgba(33,212,253,.08),rgba(91,140,255,.06))}}.openclaw-banner.hermes-banner .banner-kicker{{color:#ddd6fe}}.runtime-cloud{{color:#cfe0ff!important}}.runtime-local{{color:#bfffe9!important}}.openclaw-banner{{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;margin:12px 0 16px;padding:14px 16px;border:1px solid rgba(33,212,253,.22);border-radius:22px;background:linear-gradient(135deg,rgba(33,212,253,.11),rgba(91,140,255,.07),rgba(139,92,246,.07));box-shadow:0 12px 34px rgba(0,0,0,.24)}}.banner-kicker{{display:inline-flex;align-items:center;gap:8px;color:#bdefff;font-size:11px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}}.banner-main{{min-width:0}}.banner-main code{{display:block;color:#e7fbff;font-size:14px;overflow-wrap:anywhere}}.banner-main small{{display:block;color:var(--muted);margin-top:3px}}.banner-meta{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.banner-meta span{{border:1px solid var(--line);border-radius:14px;background:rgba(3,8,18,.28);padding:8px 10px;min-width:110px}}.banner-meta b{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}}.banner-meta code{{font-size:12px;color:#dff8ff;overflow-wrap:anywhere}}@media(max-width:719px){{.openclaw-banner{{grid-template-columns:1fr;gap:10px}}.banner-meta{{justify-content:stretch}}.banner-meta span{{flex:1 1 130px}}}}.openclaw-grid{{align-items:start}}.openclaw-summary{{align-self:start;padding:16px}}.openclaw-summary h2{{font-size:18px;margin-bottom:10px}}.openclaw-summary .current-model-highlight{{padding:13px 14px;border-radius:16px;background:linear-gradient(135deg,rgba(33,212,253,.14),rgba(91,140,255,.09))}}.openclaw-summary .current-model-highlight strong{{font-size:14px;margin-bottom:3px}}.openclaw-summary .current-model-highlight code{{font-size:12.5px}}.openclaw-summary .current-model-highlight small{{font-size:12px}}.openclaw-meta{{grid-template-columns:1fr;gap:8px;margin-top:10px}}.openclaw-meta>div{{border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.03);padding:10px;min-width:0}}.openclaw-meta code{{font-size:12px;overflow-wrap:anywhere}}.openclaw-meta ul.clean li{{padding:0;border:0;background:transparent;display:block}}.openclaw-model-list{{align-self:start}}@media(min-width:720px){{.grid.two.openclaw-grid{{grid-template-columns:minmax(280px,.58fr) minmax(620px,1.42fr);align-items:start}}.openclaw-summary{{position:sticky;top:16px}}.openclaw-meta{{grid-template-columns:.8fr 1.2fr}}}}@media(max-width:719px){{.openclaw-summary{{padding:16px}}.openclaw-meta{{grid-template-columns:1fr}}}}.llm-grid{{display:grid;grid-template-columns:1fr;gap:14px}}.leader-table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.03)}}.leader-table{{width:100%;min-width:720px;border-collapse:separate;border-spacing:0}}.telemetry-table{{min-width:2600px}}.telemetry-table th,.telemetry-table td{{padding:10px 8px;white-space:nowrap}}.telemetry-table .leader-model{{position:sticky;left:58px;background:rgba(11,18,32,.96);z-index:1;box-shadow:12px 0 18px rgba(0,0,0,.16)}}.telemetry-table th:nth-child(2){{position:sticky;left:58px;z-index:3;background:rgba(19,29,48,.98)}}.telemetry-table .rank-cell,.telemetry-table th:first-child{{position:sticky;left:0;background:rgba(11,18,32,.98);z-index:2}}.leader-table th{{position:static;background:rgba(255,255,255,.055);color:#b9c7db;font-size:11px;text-transform:uppercase;letter-spacing:.09em;padding:12px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}.leader-table td{{padding:12px 10px;border-bottom:1px solid var(--line);vertical-align:top}}.leader-table tbody tr:last-child td{{border-bottom:0}}.leader-table tbody tr:hover{{background:rgba(33,212,253,.055)}}.leader-table .rank-cell{{width:58px}}.leader-table .leader-model{{min-width:260px}}.leader-table .leader-model b{{display:block;overflow-wrap:anywhere;line-height:1.15}}.leader-table td b{{font-size:14px}}.leader-table .metric-label{{display:none}}@media(max-width:719px){{.leader-table-wrap{{border:0;background:transparent;overflow:visible}}.leader-table{{min-width:0}}.leader-table thead{{display:none}}.leader-table,.leader-table tbody,.leader-table tr,.leader-table td{{display:block;width:100%}}.leader-table tr{{border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.035);padding:12px;margin-bottom:10px}}.leader-table td{{border-bottom:0;padding:6px 0}}.leader-table .rank-cell{{width:auto}}.leader-table .leader-model{{min-width:0;margin-bottom:8px}}.leader-table td:not(.rank-cell):not(.leader-model){{display:grid;grid-template-columns:92px 1fr;gap:10px;align-items:center}}.leader-table .metric-label{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;font-weight:900}}}}ul.clean code{{overflow-wrap:anywhere;white-space:normal;text-align:left}}.panel,.hero{{max-width:100%}}.llm-card{{border:1px solid var(--line);border-radius:24px;background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.025));padding:16px;box-shadow:0 14px 36px rgba(0,0,0,.22);min-width:0}}.llm-card-head{{display:block;margin-bottom:12px;width:100%}}.model-title-block{{margin-top:-2px;min-width:0;width:100%;max-width:100%}}.llm-card h3{{margin:0;font-size:clamp(16px,4vw,20px);line-height:1.08;letter-spacing:-.035em;overflow-wrap:anywhere}}.display-model-name{{display:block;max-width:100%;font-size:clamp(22px,5vw,32px)!important;letter-spacing:-.055em!important;color:#9ae8ff;text-shadow:0 0 18px rgba(33,212,253,.16);white-space:nowrap;overflow:visible;text-overflow:clip;overflow-wrap:normal!important;word-break:keep-all}}.exact-model-tag{{display:block;margin-top:5px;color:#9fb2c8;font-size:12px;line-height:1.35;overflow-wrap:anywhere;word-break:break-word;background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:10px;padding:5px 7px;width:100%;max-width:100%}}.model-family{{color:#9ae8ff;font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:900}}.status-stack{{display:flex;justify-content:flex-start;flex-wrap:wrap;gap:6px;max-width:100%;margin-top:8px}}.spec-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}}.spec,.bench-stats>div{{border:1px solid var(--line);border-radius:14px;background:rgba(3,8,18,.36);padding:10px;min-width:0}}.spec span,.bench-stats span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;font-weight:900}}.spec b,.bench-stats b{{display:block;margin-top:3px;font-size:16px;overflow-wrap:anywhere}}.spec small{{color:var(--muted);display:block;margin-top:2px;overflow-wrap:anywhere}}.card-caps{{margin:10px 0 12px}}.bench-pair{{display:grid;grid-template-columns:1fr;gap:10px}}.bench-box{{border:1px solid var(--line);border-radius:18px;padding:12px;background:rgba(255,255,255,.035)}}.bench-box.direct{{border-color:rgba(33,212,253,.24)}}.bench-box.openclaw{{border-color:rgba(34,230,166,.24)}}.bench-box.standardized{{border-color:rgba(250,204,21,.28);background:linear-gradient(180deg,rgba(250,204,21,.055),rgba(255,255,255,.025))}}.bench-box h4{{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:#dbeafe}}.bench-stats{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}@media(min-width:860px){{.llm-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.bench-pair{{grid-template-columns:1fr}}}}@media(min-width:1180px){{.llm-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
.skill-panel{{margin:10px 0 13px;border:1px solid rgba(33,212,253,.16);background:linear-gradient(135deg,rgba(33,212,253,.06),rgba(139,92,246,.045));border-radius:18px;padding:11px}}.skill-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:8px}}.skill-heading span{{color:#dff8ff;font-weight:900;text-transform:uppercase;letter-spacing:.08em;font-size:11px}}.skill-heading small{{color:var(--muted);font-size:11px;text-align:right}}.skill-chips{{display:flex;flex-wrap:wrap;gap:7px}}.skill-chip{{display:inline-flex;align-items:center;border:1px solid rgba(33,212,253,.24);background:rgba(33,212,253,.08);color:#c9f5ff;border-radius:999px;padding:6px 9px;font-size:11px;font-weight:800;line-height:1.1}}
@media(max-width:719px){{.llm-card-head{{display:block}}.model-title-block{{width:100%;max-width:100%}}.display-model-name{{width:100%;max-width:100%}}.exact-model-tag{{width:100%;max-width:100%}}.status-stack{{justify-content:flex-start;max-width:100%;margin-top:8px}}}}
@media(max-width:719px){{
  :root{{--radius:22px;--mobile-pad:12px}}
  html{{background:#050814;width:100%;max-width:100%;overflow-x:hidden}}
  body{{width:100%;max-width:100%;overflow-x:hidden;background:radial-gradient(circle at 10% -6%,rgba(33,212,253,.24),transparent 32%),radial-gradient(circle at 108% 0,rgba(139,92,246,.20),transparent 34%),linear-gradient(180deg,#050814 0%,#08111f 42%,#050814 100%);font-size:14px;line-height:1.45}}
  .wrap{{width:100%;max-width:100%;padding:10px var(--mobile-pad) 42px;margin:0;overflow:hidden}}
  .hero{{border-radius:28px;padding:20px 18px 18px;margin:0;background:linear-gradient(155deg,rgba(15,23,42,.96),rgba(6,9,20,.92));box-shadow:0 18px 42px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.06)}}
  .hero:before{{right:-110px;top:-110px;width:230px;height:230px;opacity:.85}}
  .eyebrow{{font-size:10px;letter-spacing:.16em}}
  h1{{font-size:clamp(34px,12vw,48px);line-height:.92;margin:14px 0 10px;max-width:10ch}}
  .hero p{{font-size:14px;line-height:1.55;color:#a9b8cc;margin:0}}
  .hero-actions{{display:flex;gap:8px;overflow-x:auto;scroll-snap-type:x proximity;padding:14px 2px 2px;margin:0 -2px;white-space:nowrap}}
  .hero-actions::-webkit-scrollbar,.chips::-webkit-scrollbar{{display:none}}
  .btn{{flex:0 0 auto;border-radius:999px;padding:10px 12px;font-size:12px;scroll-snap-align:start;background:rgba(255,255,255,.07);backdrop-filter:blur(12px)}}
  .openclaw-banner{{grid-template-columns:1fr;margin:10px 0 0;padding:14px;border-radius:22px;background:linear-gradient(145deg,rgba(15,23,42,.90),rgba(8,13,28,.82));box-shadow:0 12px 30px rgba(0,0,0,.26)}}
  .openclaw-banner.hermes-banner{{margin-top:12px}}
  .banner-kicker{{font-size:10px;letter-spacing:.14em;white-space:normal}}
  .banner-main code{{font-size:13px;line-height:1.35}}
  .banner-main small{{font-size:11px;line-height:1.35}}
  .banner-meta{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
  .banner-meta span{{min-width:0;padding:9px;border-radius:14px;background:rgba(255,255,255,.045)}}
  .banner-meta span:first-child:last-child{{grid-column:1/-1}}
  .banner-meta code{{font-size:11px;line-height:1.25}}
  .stats{{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:12px 0;width:100%;max-width:100%;overflow:hidden}}
  .stats .stat:nth-child(1){{order:1}}  /* Installed models */
  .stats .stat:nth-child(3){{order:2}}  /* Tool-capable */
  .stats .stat:nth-child(2){{order:3}}  /* Text-capable */
  .stats .stat:nth-child(4){{order:4}}  /* Total model size */
  .stats .stat:nth-child(5){{order:5}}  /* Unified Memory */
  .stats .stat:nth-child(8){{order:6}}  /* Local disk free */
  .stats .stat:nth-child(6){{order:7}}  /* CPU */
  .stats .stat:nth-child(7){{order:8}}  /* GPU */
  .stat{{min-width:0;border-radius:18px;padding:12px;background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.035));box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
  .stat span{{font-size:10px;text-transform:uppercase;letter-spacing:.1em}}
  .stat b{{font-size:22px}}
  .panel{{border-radius:24px;padding:15px;margin-top:12px;background:linear-gradient(180deg,rgba(11,18,32,.90),rgba(7,12,26,.86));box-shadow:0 14px 34px rgba(0,0,0,.26);overflow:hidden}}
  .panel h2{{font-size:18px;margin-bottom:10px}}
  .grid{{gap:12px;margin-top:12px}}
  .collapsible-panel{{padding:0}}
  .collapse-summary{{padding:15px;border-radius:24px;grid-template-columns:minmax(0,1fr) auto;background:rgba(255,255,255,.035)}}
  .collapse-summary span{{font-size:17px;line-height:1.15}}
  .collapse-summary small{{font-size:11px;margin-top:3px}}
  .collapse-summary:after{{width:32px;height:32px;border-radius:12px}}
  .collapse-body{{padding:0 14px 14px}}
  ul.clean{{gap:7px}}
  ul.clean li{{border-radius:14px;padding:10px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;background:rgba(255,255,255,.045)}}
  ul.clean code{{font-size:11px}}
  .leader-panel-head{{gap:10px;margin-bottom:12px}}
  .leader-controls{{width:100%;display:grid;grid-template-columns:1fr;gap:8px}}
  .view-toggle{{width:100%;display:grid;grid-template-columns:1fr 1fr;padding:4px;border-radius:16px;background:rgba(3,8,18,.45)}}
  .view-btn,.range-btn{{padding:9px 10px;font-size:11px}}
  .leader-table tr{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.026));padding:12px;margin-bottom:10px;box-shadow:0 10px 22px rgba(0,0,0,.20)}}
  .leader-table .rank-cell{{display:inline-block;width:auto;margin-bottom:8px}}
  .leader-table .rank{{width:34px;height:30px;border-radius:12px;font-size:12px}}
  .leader-table .leader-model{{padding:0 0 8px!important;margin:0;border-bottom:1px solid rgba(148,163,184,.12)}}
  .leader-table .leader-model b{{font-size:13px;line-height:1.25}}
  .leader-table td:not(.rank-cell):not(.leader-model){{grid-template-columns:1fr auto;padding:7px 0;border-bottom:1px solid rgba(148,163,184,.08)}}
  .leader-table td:last-child{{border-bottom:0!important}}
  .leader-table .metric-label{{font-size:10px;color:#91a4bd}}
  .leader-table td b{{font-size:13px;color:#edf6ff}}
  .test-notes{{padding:11px 12px;border-radius:16px;font-size:12px;margin-top:10px}}
  .test-notes ul{{gap:5px}}
  #models{{padding:15px 12px;background:linear-gradient(180deg,rgba(9,16,31,.96),rgba(7,12,25,.92))}}
  #models>p{{font-size:12px;line-height:1.45;margin-top:-4px}}
  .toolbar{{position:sticky;top:0;z-index:20;margin:12px -4px 12px;padding:8px 4px 10px;background:linear-gradient(180deg,rgba(6,9,20,.96),rgba(6,9,20,.78));backdrop-filter:blur(16px);border-bottom:1px solid rgba(148,163,184,.10)}}
  .search,.family-select{{height:46px;border-radius:16px;background:rgba(255,255,255,.075);font-size:14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
  .family-filter{{min-width:0;gap:5px}}
  .family-filter label{{font-size:10px}}
  .chips{{display:flex;gap:8px;overflow:auto;padding:1px 0 2px}}
  .chip{{flex:0 0 auto;padding:9px 11px;font-size:11px;border-radius:999px;background:rgba(255,255,255,.055)}}
  .llm-grid{{gap:12px}}
  .llm-card{{border-radius:24px;padding:14px;background:linear-gradient(180deg,rgba(16,24,43,.92),rgba(8,13,28,.92));box-shadow:0 16px 34px rgba(0,0,0,.30),inset 0 1px 0 rgba(255,255,255,.05)}}
  .llm-card:target{{outline:1px solid rgba(33,212,253,.80);box-shadow:0 0 0 4px rgba(33,212,253,.12),0 16px 34px rgba(0,0,0,.30)}}
  .llm-card-head{{margin-bottom:12px}}
  .display-model-name{{font-size:clamp(26px,9vw,34px)!important;line-height:.98!important;letter-spacing:-.06em!important}}
  .exact-model-tag{{border-radius:14px;padding:8px 9px;margin-top:8px;font-size:11.5px;background:rgba(3,8,18,.45)}}
  .status-stack{{margin-top:8px;gap:6px}}
  .tag{{font-size:10px;padding:5px 8px;border-radius:999px}}
  .spec-grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:11px 0}}
  .spec,.bench-stats>div{{border-radius:16px;padding:10px;background:rgba(3,8,18,.40)}}
  .spec span,.bench-stats span{{font-size:9.5px;letter-spacing:.1em}}
  .spec b,.bench-stats b{{font-size:14px;line-height:1.18}}
  .spec small{{font-size:11px;line-height:1.25}}
  .card-caps{{margin:9px 0 9px;gap:6px}}
  .skill-panel{{margin:8px 0 11px;padding:10px;border-radius:16px}}
  .skill-heading{{display:block;margin-bottom:7px}}
  .skill-heading small{{display:block;text-align:left;margin-top:2px;font-size:10px}}
  .skill-chips{{gap:6px}}
  .skill-chip{{font-size:10px;padding:5px 8px}}
  .bench-pair{{gap:9px}}
  .bench-box{{border-radius:18px;padding:11px;background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.022))}}
  .bench-box h4{{font-size:11px;margin-bottom:9px;color:#cfe7ff}}
  .bench-stats{{gap:7px}}
  .footer{{font-size:11px;line-height:1.45;padding:0 8px;margin-top:16px}}
  .hero,.openclaw-banner,.stats,.panel,.leader-table-wrap,.leader-table,.llm-card,.toolbar{{max-width:100%;overflow:hidden}}
  .leader-table,.leader-table tbody{{width:100%!important;max-width:100%!important;min-width:0!important}}
  .leader-table tr{{width:100%;max-width:100%;overflow:hidden}}
  .leader-table td{{max-width:100%;overflow:hidden}}
  .leader-table tr{{display:grid!important;grid-template-columns:auto minmax(0,1fr)!important;gap:8px 10px!important;width:100%;max-width:100%;overflow:hidden;padding:14px!important}}
  .leader-table .rank-cell{{grid-column:1!important;grid-row:1!important;display:block!important;width:auto!important;margin:0!important;padding:0!important}}
  .leader-table .leader-model{{grid-column:2!important;grid-row:1!important;display:block!important;width:auto!important;min-width:0!important;padding:2px 0 10px!important;margin:0!important;border-bottom:1px solid rgba(148,163,184,.12)}}
  .leader-table td:not(.rank-cell):not(.leader-model)::before{{content:none!important;display:none!important}}
  .leader-table td:not(.rank-cell):not(.leader-model){{grid-column:1 / -1!important;display:grid!important;grid-template-columns:minmax(0,1fr) auto!important;gap:10px!important;align-items:center!important;width:100%!important;min-width:0!important;padding:8px 0!important;border-bottom:1px solid rgba(148,163,184,.08)!important}}
  .leader-table td:not(.rank-cell):not(.leader-model) .metric-label{{display:block!important;color:#91a4bd!important;font-size:10px!important;text-transform:uppercase!important;letter-spacing:.08em!important;font-weight:900!important;text-align:left!important;justify-self:start!important;min-width:0!important}}
  .leader-table td:not(.rank-cell):not(.leader-model) b{{text-align:right!important;justify-self:end!important;white-space:nowrap;font-size:14px!important}}
  .leader-panel:not(.show-all) .leader-table tr.over-top10{{display:none!important}}
  .leader-panel.show-all .leader-table tr.over-top10{{display:grid!important}}
}}

</style>
</head>
<body>
<div class="wrap">
  <section class="hero">
    <div class="eyebrow"><span class="pulse"></span> Local AI Operations</div>
    <h1>{esc(DASHBOARD_TITLE)}</h1>
    <div class="hero-actions"><a class="btn primary" href="#models">View model inventory</a><a class="btn" href="#openclaw">OpenClaw state</a><a class="btn" href="#benchmarks">Benchmark leaders</a></div>
  </section>
  <section class="openclaw-banner hermes-banner" aria-label="Current Hermes Agent model">
    <div class="banner-kicker"><span class="pulse"></span><span>Current Hermes Agent model</span></div>
    <div class="banner-main"><code>{esc(hermes_status.get('model') or '—')}</code><small>Hermes Agent runtime model · {esc(hermes_status.get('runtime') or '—')}</small></div>
    <div class="banner-meta"><span><b>Provider</b><code>{esc(hermes_status.get('provider') or '—')}</code></span><span><b>Runtime</b><code class="runtime-{esc(hermes_status.get('runtime_class') or 'cloud')}">{esc(hermes_status.get('runtime') or '—')}</code></span><span><b>Context window</b><code>{esc(hermes_status.get('context_text') or '—')}</code></span><span><b>Fallbacks</b><code>{esc(hermes_status.get('fallbacks_text') or 'none')}</code></span></div>
  </section>
  <section class="openclaw-banner" aria-label="Current OpenClaw model">
    <div class="banner-kicker"><span class="pulse"></span><span>Current OpenClaw model</span></div>
    <div class="banner-main"><code>{esc(default)}</code><small>{esc(default_model['size_h'] + ' · ' + default_model['parameter_size'] + ' · ' + default_model['quant'] if default_model else 'Configured model not matched to an installed Ollama tag')}</small></div>
    <div class="banner-meta"><span><b>Provider</b><code>{esc(openclaw_provider)}</code></span><span><b>Runtime</b><code class="runtime-{esc(openclaw_runtime_class)}">{esc(openclaw_runtime)}</code></span><span><b>Context window</b><code>{esc(openclaw_context_text)}</code></span><span><b>Fallbacks</b><code>{esc(', '.join(fallbacks) if fallbacks else 'none')}</code></span></div>
  </section>
  <section class="stats">
    <div class="stat"><span>Installed models</span><b>{len(models)}</b></div>
    <div class="stat"><span>Text-capable</span><b>{text_count}</b></div>
    <div class="stat"><span>Tool-capable</span><b>{tools_count}</b></div>
    <div class="stat"><span>Total model size</span><b>{human_size(total_size)}</b></div>
    <div class="stat"><span>{esc(system_specs.get('memory_label') or 'System memory')}</span><b>{esc(system_specs['memory'])}</b><small>{esc(system_specs['memory_small'])}</small></div>
    <div class="stat"><span>CPU</span><b>{esc(system_specs['cpu'])}</b><small>{esc(system_specs['cpu_small'])}</small></div>
    <div class="stat"><span>GPU</span><b>{esc(system_specs['gpu'])}</b><small>{esc(system_specs['gpu_small'])}</small></div>
    <div class="stat"><span>Local disk free</span><b>{esc(system_specs['disk_free'])}</b><small>{esc(system_specs['disk_small'])}</small></div>
  </section>
  <details class="panel openclaw-model-list collapsible-panel" id="openclaw">
    <summary class="collapse-summary"><span>OpenClaw configured local models</span><small>{len(allowed)} configured · click to expand</small></summary>
    <ul class="clean collapse-body">{''.join(allowed_items)}</ul>
  </details>
  <section class="grid two" id="benchmarks">
    <div class="panel leader-panel" data-leader-panel data-range="top10">
      <div class="leader-panel-head"><h2>Direct Ollama combined 18-test suite leaders</h2><div class="leader-controls"><div class="view-toggle" role="group" aria-label="Direct Ollama combined table view"><button class="view-btn active" type="button" data-view="targeted">Targeted</button><button class="view-btn" type="button" data-view="verbose">Verbose</button></div><div class="view-toggle" role="group" aria-label="Direct Ollama combined row range"><button class="range-btn active" type="button" data-range="top10">Top 10</button><button class="range-btn" type="button" data-range="all">All</button></div></div></div>
      <div class="leader-table-wrap wide" data-table-view="targeted"><table class="leader-table targeted-table"><thead><tr>{hrow(direct_combined_targeted_headers)}</tr></thead><tbody>{''.join(direct_combined_targeted_ranking)}</tbody></table></div>
      <div class="leader-table-wrap wide hidden" data-table-view="verbose"><table class="leader-table targeted-table"><thead><tr>{hrow(direct_combined_headers)}</tr></thead><tbody>{''.join(direct_combined_ranking)}</tbody></table></div>
      {direct_test_details}
      <p class="muted">Default shows one row per model. Schema-v3 paired campaigns qualify each model's thinking control before interpreting its arms. The dashboard distinguishes an observable qualified toggle, an unobservable or ineffective off control, an unverified on control, an inconclusive control, and GPT-OSS's descriptive low/high level range. Trace evidence records separated and inline reasoning characters by task. Causal deltas are shown only for observable-toggle-qualified full pairs; GPT low/high changes are labeled descriptive. Invalid or unverified comparisons are not ranked, and terminally omitted work is counted without leaving the campaign perpetually incomplete. Adaptive-native context starts at each model's advertised native limit and records a verified lower fit only when needed; the dashboard shows the resolved/native contexts, reduction, reason, calibration profile, and attempts. Accuracy determines ranking; timing remains recorded but is descriptive across models because calibrated contexts can differ and is never used as the context-based tie-break. Legacy native-full, schema-v2, and unpaired reports remain readable. The suite contains 18 defined tests: 3 smoke tests plus 15 standardized mini tasks. Text-only models usually have 17 applicable tests because OCR is skipped. Telemetry comes from the platform backend recorded by the runner; unavailable metrics remain blank. On DGX Spark, GPU watts are separate and are never presented as total-system watts. Combined source: {esc(standardized_csv.name if standardized_csv else 'none')}.</p>
    </div>
    <div class="panel leader-panel" data-leader-panel data-range="top10">
      <div class="leader-panel-head"><h2>OpenClaw local model leaders</h2><div class="leader-controls"><div class="view-toggle" role="group" aria-label="OpenClaw table view"><button class="view-btn active" type="button" data-view="targeted">Targeted</button><button class="view-btn" type="button" data-view="verbose">Verbose</button></div><div class="view-toggle" role="group" aria-label="OpenClaw row range"><button class="range-btn active" type="button" data-range="top10">Top 10</button><button class="range-btn" type="button" data-range="all">All</button></div></div></div>
      <div class="leader-table-wrap wide" data-table-view="targeted"><table class="leader-table targeted-table"><thead><tr>{hrow(targeted_leader_headers)}</tr></thead><tbody>{''.join(openclaw_ranking_targeted)}</tbody></table></div>
      <div class="leader-table-wrap wide hidden" data-table-view="verbose"><table class="leader-table telemetry-table"><thead><tr>{hrow(openclaw_leader_headers)}</tr></thead><tbody>{''.join(openclaw_ranking)}</tbody></table></div>
      {openclaw_test_details}
      <p class="muted">Default shows Top 10. Toggle All to show every benchmarked model. Ranked by pass count, then average wall time through the OpenClaw agent harness. Source: {esc(oc_csv.name if oc_csv else 'none')}</p>
      {hardware_ranking_section}
    </div>
    <div class="panel"><h2>Largest local model files</h2><div class="cards">{''.join(cards)}</div></div>
  </section>
  <section class="panel" id="models">
    <h2>All locally installed models</h2>
    <p class="muted">Every model card uses the same layout: inventory specs first, capabilities second, then Direct Ollama combined 18-test suite stats and OpenClaw local-model benchmark stats in matching positions.</p>
    <div class="toolbar"><input id="search" class="search" placeholder="Search model, family, capability…"><div class="family-filter"><label for="familyFilter">Model Family</label><select id="familyFilter" class="family-select"><option value="all">All families</option>{family_options}</select></div><div class="chips"><button class="chip active" data-filter="all">All</button><button class="chip" data-filter="agent-ready">Agent-ready</button><button class="chip" data-filter="text">Text</button><button class="chip" data-filter="image">Image</button><button class="chip" data-filter="openclaw">OpenClaw</button></div></div>
    <div class="llm-grid" id="modelRows">{''.join(model_cards)}</div>
  </section>
  <p class="footer">Generated {esc(now.strftime('%b %-d, %Y %-I:%M %p %Z'))} on {esc(system_specs.get('os') or platform.system())} ({esc(system_specs.get('architecture') or platform.machine())}). Data sources: Ollama `/api/tags`, optional `openclaw models status --json`, and host-local benchmark CSVs.</p>
</div>
<script>
const q=document.querySelector('#search'); const familyFilter=document.querySelector('#familyFilter'); const chips=[...document.querySelectorAll('.chip')]; const rows=[...document.querySelectorAll('#modelRows .llm-card')]; let filter='all';
function apply(){{ const term=(q.value||'').toLowerCase().trim(); const family=(familyFilter?.value||'all').toLowerCase(); rows.forEach(r=>{{ const hay=(r.dataset.name+' '+r.dataset.family+' '+r.innerText).toLowerCase(); const kind=r.dataset.kind; const oc=r.dataset.openclaw==='yes'; const okText=!term||hay.includes(term); const okFamily=family==='all'||r.dataset.family===family; const okFilter=filter==='all'||kind===filter||(filter==='openclaw'&&oc); r.style.display=(okText&&okFamily&&okFilter)?'':'none'; }}); }}
q.addEventListener('input', apply); familyFilter?.addEventListener('change', apply); chips.forEach(c=>c.addEventListener('click',()=>{{chips.forEach(x=>x.classList.remove('active')); c.classList.add('active'); filter=c.dataset.filter; apply();}}));
function fitDisplayNames(){{
  document.querySelectorAll('.display-model-name').forEach(el=>{{
    if(!el.offsetParent) return;
    el.style.removeProperty('font-size');
    let size=parseFloat(getComputedStyle(el).fontSize)||32;
    const min=16;
    while(el.scrollWidth > el.clientWidth && size > min){{
      size -= 1;
      el.style.setProperty('font-size', size+'px', 'important');
    }}
  }});
}}
window.addEventListener('load', fitDisplayNames);
window.addEventListener('resize', fitDisplayNames);
fitDisplayNames();
document.querySelectorAll('[data-leader-panel]').forEach(panel=>{{
  panel.querySelectorAll('.view-btn').forEach(btn=>btn.addEventListener('click',()=>{{
    const view=btn.dataset.view;
    panel.querySelectorAll('.view-btn').forEach(b=>b.classList.toggle('active', b===btn));
    panel.querySelectorAll('[data-table-view]').forEach(t=>t.classList.toggle('hidden', t.dataset.tableView!==view));
  }}));
  panel.querySelectorAll('.range-btn').forEach(btn=>btn.addEventListener('click',()=>{{
    const range=btn.dataset.range;
    panel.dataset.range=range;
    panel.classList.toggle('show-all', range==='all');
    panel.querySelectorAll('.range-btn').forEach(b=>b.classList.toggle('active', b===btn));
  }}));
}});
</script>
</body>
</html>'''
    OUT.write_text(html_doc, encoding='utf-8')
    print(OUT)

if __name__ == '__main__':
    main()
