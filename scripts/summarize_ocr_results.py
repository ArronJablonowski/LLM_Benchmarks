#!/usr/bin/env python3
"""Summarize OCR JSONL by model and difficulty without running models or writing feedback."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from benchmark_tests import suite_task_catalog


def summarize(paths):
    tasks = {t['id']: t for t in suite_task_catalog('ocr')}
    groups = defaultdict(lambda: {'pass': 0, 'mismatch': 0, 'infrastructure': 0,
                                  'skip': 0, 'format_mismatches': 0, 'transcriptions_scored': 0, 'fields_correct': 0, 'fields_total': 0,
                                  'character_edits': 0, 'reference_characters': 0,
                                  'word_edits': 0, 'reference_words': 0})
    seen = set()
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            row = record['row']
            task = tasks.get(row['task_id'])
            if task is None:
                continue
            identity = (row.get('run_id', ''), row['model'], row['task_id'])
            if identity in seen:
                raise ValueError(f'duplicate OCR observation: {identity}')
            seen.add(identity)
            group = groups[(row['model'], task['difficulty_level'])]
            grade = record['grading']
            verdict = grade['verdict']
            if verdict == 'skip':
                group['skip'] += 1
                continue
            if row['status'] != 'ok' or verdict not in ('pass', 'content_mismatch'):
                group['infrastructure'] += 1
                continue
            image_hash = (record.get('image_evidence') or {}).get('sha256') or row.get('image_sha256')
            if image_hash != task['image_sha256']:
                raise ValueError('OCR result has missing or mismatched image evidence')
            if grade.get('grader_version') != 'ocr-progressive-v1':
                raise ValueError('OCR result has a different grader version')
            group['pass' if verdict == 'pass' else 'mismatch'] += 1
            if 'response_schema' in grade.get('failures', []):
                group['format_mismatches'] += 1
            group['fields_correct'] += grade['tests_passed']
            group['fields_total'] += grade['tests_total']
            for metrics in grade.get('transcription_metrics', {}).values():
                group['transcriptions_scored'] += 1
                for field in ('character_edits', 'reference_characters', 'word_edits', 'reference_words'):
                    group[field] += metrics[field]
    output = []
    for (model, level), data in sorted(groups.items()):
        valid = data['pass'] + data['mismatch']
        output.append({'model': model, 'difficulty_level': level, **data,
                       'valid_cases': valid,
                       'pass_rate': data['pass'] / valid if valid else None,
                       'field_accuracy': data['fields_correct'] / data['fields_total'] if data['fields_total'] else None,
                       'cer': data['character_edits'] / data['reference_characters'] if data['reference_characters'] else None,
                       'wer': data['word_edits'] / data['reference_words'] if data['reference_words'] else None})
    return {'profile': 'ocr-progressive-v1', 'observations': len(seen), 'by_model_and_level': output,
            'note': 'Infrastructure and skips are excluded from quality denominators. CER/WER cover memo transcription only. Synthetic difficulty is a design, not an empirically calibrated scale.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.jsonl), indent=2))
