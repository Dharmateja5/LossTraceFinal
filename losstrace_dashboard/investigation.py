"""UI-only discovery and explicit investigation launch. No financial commands.

A durable exclusive launch marker survives Streamlit reruns/restarts. An unknown
child process is never assumed dead: only its original Popen handle can release
that marker after observing exit. Recovery after server restart is manual.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from gateway import RuntimeGateway
from losstrace_workflow import load_case_from_runtime
from financial_stage import _manifest_and_state

PROJECT = Path(__file__).resolve().parents[1]
UV = shutil.which('uv')
_LOCK = threading.RLock()
_PROCESSES = {}
TIMEOUT_SECONDS = 600


class LaunchError(Exception):
    pass



def claim_summary(claim: dict, site_data: dict | None = None) -> str:
    """Present a source-grounded case distinction; never alter the claim record.

    A submitted amount is NOT a supported loss, coverage decision, or payment.
    Avoid inventing loss types: this dataset consists entirely of plumbing-water
    claims, with only limited documented differences between cases.
    """
    observation_texts = []
    if isinstance(site_data, dict) and site_data.get('claim_id') == claim.get('claim_id'):
        for observation in site_data.get('observations', []):
            if isinstance(observation, dict) and isinstance(observation.get('text'), str):
                observation_texts.append(observation['text'].casefold())
    notes = [note.get('text', '').casefold() for note in claim.get('notes', [])
             if isinstance(note, dict) and isinstance(note.get('text'), str)]
    if any('site visit remains pending' in note or 'no inspection has been completed' in note for note in notes):
        label = 'Site inspection pending'
    elif any('fungal-type growth' in text for text in observation_texts):
        label = 'Fungal-type growth observed'
    elif any('affected electrical devices' in text and 'wet wall' in text for text in observation_texts):
        # Five display-only phrasings of the SAME documented observation.
        # Claim identity makes the choice stable across UI refreshes.
        variants = (
            'Electrical devices affected at opened wet wall',
            'Opened wet-wall area with affected electrical devices',
            'Affected electrical devices at the wet-wall opening',
            'Wet-wall opening: affected electrical devices',
            'Electrical-device effects documented at opened wet wall',
        )
        match = re.fullmatch(r'CLM-\d{2}-(\d{6})', str(claim.get('claim_id') or ''))
        label = variants[int(match.group(1)) % len(variants)] if match else variants[0]
    elif any('water impact at lower finishes' in text for text in observation_texts):
        label = 'Water impact at lower finishes'
    else:
        label = claim.get('reported_cause') or claim.get('loss_type') or 'Not available'

    submitted = claim.get('submitted_amount')
    try:
        if isinstance(submitted, bool) or submitted is None:
            raise ValueError('Not a numeric submitted amount')
        amount = Decimal(str(submitted))
        if not amount.is_finite() or amount < 0:
            raise ValueError('Invalid submitted amount')
        formatted = f'${amount:,.0f}' if amount == amount.to_integral_value() else f'${amount:,.2f}'
    except (ValueError, InvalidOperation, TypeError):
        return label
    return f'{label} · {formatted} submitted'


def discover_claims(project=PROJECT):
    root = Path(project) / 'data/application_runtime'
    claims, rejected = [], 0
    for manifest in sorted((root / 'manifests').glob('d-*.json')):
        dossier = manifest.stem
        try:
            if not re.fullmatch(r'd-[0-9a-f]{12}', dossier):
                raise ValueError()
            if not manifest.resolve().is_relative_to(root.resolve()):
                raise ValueError()
            state, _ = load_case_from_runtime(dossier, runtime_root=root)
            gateway = RuntimeGateway(str(root))
            raw = gateway.get_claim_record(dossier)
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            if metadata.get('runtime_id') != dossier or metadata.get('claim_id') != state.claim_id:
                raise ValueError()
            # Optional, verified source record; missing observations must not
            # prevent a registered claim from appearing in the workspace.
            try:
                observations = gateway.get_site_observations(dossier)
            except (OSError, ValueError, KeyError, TypeError):
                observations = None
            try:
                insured = gateway.get_policy_record(dossier).get('named_insured')
            except (OSError, ValueError, KeyError, TypeError):
                insured = None
            claims.append(dict(dossier_id=dossier, claim_id=state.claim_id,
                               insured=insured if isinstance(insured, str) else None,
                               claim_summary=claim_summary(raw, observations),
                               loss_type=raw.get('reported_cause') or raw.get('loss_type') or 'Not available',
                               loss_date=raw.get('date_of_loss') or 'Not available',
                               location=raw.get('location') or raw.get('property_address') or 'Not available',
                               case_version=state.case_version))
        except Exception:
            rejected += 1  # Do not surface raw errors or document payloads.
    return claims, rejected


def freshness(snapshot, project=PROJECT):
    """Existing byte verifier plus current registered claim version; no writes."""
    try:
        if snapshot['audit'] != 'PASS' or snapshot['issues'] or snapshot['state'] is None:
            return 'Freshness not verified'
        r = snapshot['record']
        root = Path(project) / 'data/application_runtime'
        current, _ = load_case_from_runtime(r['dossier_id'], runtime_root=root)
        if (current.claim_id, current.case_version, current.evidence_snapshot_hash) != (
                r['claim_id'], r['case_version'], r['evidence_hash']):
            return 'STALE — registered case version or evidence snapshot differs'
        _manifest_and_state(root, r['dossier_id'], snapshot['state'])
        return 'Verified against current registered source bytes'
    except Exception:
        return 'Freshness not verified — sources missing, changed or inconsistent'


def owned_root(project=PROJECT):
    project = Path(project).resolve()
    root = project / 'data/dashboard_runs'
    if root.resolve() != root or not root.resolve().is_relative_to(project):
        raise LaunchError('The investigation directory must be a local, non-redirected application directory.')
    return root


def database_paths(configured, project=PROJECT):
    root = owned_root(project)
    paths = [Path(p).expanduser().resolve() for p in configured if str(p).strip()]
    if root.is_dir():
        paths += [p for p in sorted(root.glob('run-*.sqlite3'))
                  if re.fullmatch(r'run-[0-9a-f]{32}\.sqlite3', p.name)
                  and p.is_file() and not p.is_symlink()]
    return list(dict.fromkeys(paths))


def saved_runs(paths, list_cases, read_snapshot):
    runs, problems = [], []
    for path in paths:
        try:
            for case in list_cases(path):
                snap = read_snapshot(path, case['dossier_id'])
                r = snap['record']
                runs.append(dict(path=str(path), snapshot=snap,
                                 key=json.dumps([str(path), r['dossier_id'], r['claim_id'], r['run_id']])))
        except Exception:
            problems.append(f'Database not found or unreadable: {Path(path).name}. No migration or creation attempted.')
    runs.sort(key=lambda x: x['snapshot']['record']['updated_at'], reverse=True)
    return runs, problems


def is_completed(snapshot):
    # HITL and failures remain saved outcomes; no automatic retry is allowed.
    return snapshot['record']['status'] not in {'IN_PROGRESS', 'RUNNING'}


def preferred_run(runs, project=PROJECT):
    for run in runs:
        s = run['snapshot']
        if is_completed(s) and freshness(s, project).startswith('Verified'):
            return run['key']
    return runs[0]['key'] if runs else None


def _marker(root):
    path = root / 'active.json'
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not re.fullmatch(r'run-[0-9a-f]{32}\.sqlite3', value['database']):
            raise ValueError()
        return value
    except Exception:
        raise LaunchError('Investigation lock is unreadable. Completion is uncertain; new launches are blocked.') from None


def launch_state(project=PROJECT):
    """Poll only; never launches, retries, kills or assumes completion from a PID."""
    with _LOCK:
        root = owned_root(project)
        marker = _marker(root)
        if marker is None:
            return None
        entry = _PROCESSES.get(str(root))
        if entry is None or entry['database'] != marker['database']:
            return dict(marker, state='COMPLETION UNKNOWN', message='Process ownership was lost or launch was interrupted. New launches are blocked; verify the process externally before recovery.')
        process = entry['process']
        code = process.poll()
        if code is None:
            elapsed = time.monotonic() - entry['clock']
            uncertain = elapsed >= TIMEOUT_SECONDS
            return dict(marker, state='TIMEOUT / STILL RUNNING' if uncertain else 'INVESTIGATION RUNNING',
                        message='No second process will be started. Refresh to check the same process.')
        result = dict(marker, state='PROCESS EXITED', returncode=code,
                      finished_at=datetime.now(timezone.utc).isoformat())
        result_file = root / (marker['database'] + '.result.json')
        # No stdout/stderr or environment is persisted by the presentation layer.
        with result_file.open('x', encoding='utf-8') as f:
            json.dump(result, f)
        (root / 'active.json').unlink()
        _PROCESSES.pop(str(root), None)
        return result


def result_receipts(project=PROJECT):
    root = owned_root(project)
    results = []
    if root.is_dir():
        for p in root.glob('run-*.sqlite3.result.json'):
            if p.is_symlink():
                continue
            try:
                v = json.loads(p.read_text(encoding='utf-8'))
                if p.name == v['database'] + '.result.json' and re.fullmatch(r'run-[0-9a-f]{32}\.sqlite3', v['database']):
                    results.append(v)
            except Exception:
                continue
    return sorted(results, key=lambda x: x.get('started_at', ''), reverse=True)


def launch(dossier, *, explicit_click, configured, list_cases, read_snapshot, project=PROJECT):
    if explicit_click is not True:
        raise LaunchError('An explicit Run Live Investigation click is required.')
    with _LOCK:
        root = owned_root(project)
        if _marker(root) is not None:
            raise LaunchError('An investigation may already be running. A second launch is blocked.')
        claims, _ = discover_claims(project)
        trusted = {c['dossier_id']: c for c in claims}
        if dossier not in trusted:
            raise LaunchError('Dossier is not in the verified registered claim list.')
        # Re-read immediately before launching, not just when drawing the button.
        runs, problems = saved_runs(database_paths(configured, project), list_cases, read_snapshot)
        if problems:
            raise LaunchError('A registered investigation database is unreadable. Correct or remove that registration before launching.')
        if any(r['snapshot']['record']['dossier_id'] == dossier for r in runs):
            raise LaunchError('A saved investigation exists. View it; fresh reruns are intentionally disabled.')
        if any(r.get('dossier_id') == dossier for r in result_receipts(project)):
            raise LaunchError('A prior launch is recorded for this claim. Inspect its result; automatic retries are disabled.')
        if not UV:
            raise LaunchError('uv was not found when the dashboard started. Install it and restart the dashboard.')
        root.mkdir(parents=True, exist_ok=True)
        name = f'run-{uuid.uuid4().hex}.sqlite3'
        target = root / name
        if target.exists():
            raise LaunchError('New database allocation collided; launch blocked.')
        marker = dict(database=name, dossier_id=dossier, claim_id=trusted[dossier]['claim_id'],
                      started_at=datetime.now(timezone.utc).isoformat())
        try:
            with (root / 'active.json').open('x', encoding='utf-8') as f:
                json.dump(marker, f)
        except FileExistsError:
            raise LaunchError('Another session acquired the investigation lock. No process started.') from None
        # Recheck after acquiring the cross-process marker. Another short run
        # could have completed while this session was waiting to acquire it.
        latest, issues = saved_runs(database_paths(configured, project), list_cases, read_snapshot)
        if issues or any(r['snapshot']['record']['dossier_id'] == dossier for r in latest) or any(
                r.get('dossier_id') == dossier for r in result_receipts(project)):
            (root / 'active.json').unlink()
            raise LaunchError('Saved results or an unreadable registration appeared before dispatch. No process started.')
        command = [UV, 'run', '--frozen', 'python', 'losstrace_workflow.py',
                   '--database', str(target), 'run', '--dossier', dossier]
        try:
            process = subprocess.Popen(command, cwd=str(Path(project).resolve()), shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            _PROCESSES[str(root)] = dict(process=process, database=name, clock=time.monotonic())
        except Exception:
            # A partially completed launch must not be retried automatically.
            raise LaunchError('Launch could not be confirmed. New launches remain blocked pending process inspection.') from None
        return dict(marker, state='INVESTIGATION STARTED')
