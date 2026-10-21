"""Merge traceable handoff observations; retain the original probe and its hashes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reconcile(snapshot):
    baseline = snapshot / 'baseline/evidence/universal-platform-auto-probe.json'
    auto = load(baseline)
    observations = {row['source']: row for row in auto['results']}
    assert len(observations) == len(auto['results']) == 128
    sources = []
    for name in ['diagnose_cat3_results.json', 'diagnose_content_results.json']:
        path = snapshot / 'received/tests' / name
        sources.append({'file': str(path.relative_to(ROOT)).replace('\\', '/'),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for row in load(path):
            # A guessed detail URL returning 404 is not evidence about the
            # platform's indexed public entry. Keep it in the raw handoff only.
            if row.get('type') not in {None, 'home'} or row.get('exception'):
                continue
            item = row.get('item') or row.get('first_item')
            if not item or row['platform'] not in observations:
                continue
            observations[row['platform']] = {
                'source': row['platform'], 'requested_url': row['url'],
                'mode': 'web' if name.startswith('diagnose_cat3') else 'auto',
                'render': 'auto', 'errors': row.get('errors', []),
                'items': [{**item, 'url': row['url']}], 'elapsed_s': row.get('elapsed_s'),
                'origin': 'received-diagnostic', 'evidence_file': sources[-1]['file'],
                'evidence_sha256': sources[-1]['sha256'],
                'observed_at': None,
                'time_note': 'Received diagnostic lacks an observation timestamp and code fingerprint.',
            }

    review_path = ROOT / 'evidence/handoff-review.json'
    review = load(review_path)
    for row in review['checks']:
        # Explicit Chrome-session tests are reported separately; they cannot
        # establish that the default automatic path passed on this date.
        if row['render'] != 'auto':
            continue
        observations[row['source']] = {
            'source': row['source'], 'requested_url': row['url'], 'mode': 'web', 'render': 'auto',
            'errors': row['errors'], 'items': [row['item']], 'state': row['state'],
            'job_id': row['job_id'], 'origin': 'current-chat-mcp',
            'observed_at': row['observed_at'],
            'evidence_file': str(review_path.relative_to(ROOT)).replace('\\', '/'),
            'production_code_sha256': review['production_code_sha256'],
        }

    auto['results'] = list(observations.values())
    auto['updated_at'] = datetime.now(timezone.utc).isoformat()
    auto['handoff_reconciliation'] = {
        'baseline_file': str(baseline.relative_to(ROOT)).replace('\\', '/'),
        'baseline_sha256': hashlib.sha256(baseline.read_bytes()).hexdigest(),
        'sources': sources,
        'review_file': str(review_path.relative_to(ROOT)).replace('\\', '/'),
        'note': 'Original probe metadata describes the baseline only. New rows retain their provenance; no whole-directory retest is claimed.',
        'excluded_claims': ['Manually filled aggregate rows', 'Recreated timeout summaries without original logs',
                            'Unverified VPN/server causation', 'Unverified iQIYI card count'],
    }
    (ROOT / 'evidence/universal-platform-auto-probe.json').write_text(
        json.dumps(auto, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Reconciled 128 entry observations from archived baseline, received diagnostics and real MCP jobs.')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python build/Reconcile-Handoff.py data/handoffs/<snapshot>')
    snapshot = (ROOT / sys.argv[1]).resolve()
    if not snapshot.is_relative_to(ROOT / 'data/handoffs'):
        raise SystemExit('Snapshot must be inside this workspace data/handoffs directory.')
    reconcile(snapshot)
