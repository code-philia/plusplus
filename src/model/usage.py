"""Actual response token totals and explicitly estimated model costs, scoped to a workspace."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from utils.archive import snapshot_workspace
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog, current_run_id

_usage_lock = RLock()
LEGACY_ESTIMATES = {'deepseek': ['CNY', 0.02, 1.0, 4.0],
    'gpt-5.6-sol': ['USD', 0.5, 5.0, 30.0], 'gpt-6.1-sol': ['USD', 0.10, 2.00, 10.00]}


def enabled(name, default='1'):
    return os.environ.get(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


def pricing(model):
    configured = json.loads(os.environ.get('ARC_MODEL_PRICING_JSON', '{}'))
    if not isinstance(configured, dict):
        raise ValueError('ARC_MODEL_PRICING_JSON must be an object')
    name = model.lower()
    value = configured.get(model)
    source = 'configured'
    if value is None:
        source = 'legacy_estimate'
        value = next((rates for prefix, rates in LEGACY_ESTIMATES.items() if name.startswith(prefix)), None)
    if value is None:
        return None, 'unpriced'
    if not isinstance(value, list) or len(value) != 4 or value[0] not in {'USD', 'CNY'}:
        raise ValueError('Prices must be [USD|CNY, cached_input, uncached_input, output] per million tokens')
    rates = [float(rate) for rate in value[1:]]
    if any(not math.isfinite(rate) or rate < 0 for rate in rates):
        raise ValueError('Prices must be finite nonnegative numbers')
    return [value[0], *rates], source


def token_counts(usage):
    prompt = max(0, int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0))
    output = max(0, int(usage.get('completion_tokens') or usage.get('output_tokens') or 0))
    details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
    cached = max(0, min(prompt, int(details.get('cached_tokens') or usage.get('prompt_cache_hit_tokens') or 0)))
    return {'requests': 1, 'prompt_tokens': prompt, 'completion_tokens': output,
            'total_tokens': max(0, int(usage.get('total_tokens') or prompt + output)), 'cached_tokens': cached}


def add_counts(target, counts):
    for key, value in counts.items():
        target[key] = target.get(key, 0) + value


def record_usage(path, model, usage, *, schema_name, operation):
    if not enabled('ARC_USAGE_TRACKING'):
        return
    path = Path(path)
    root = path.parent.parent
    logger = SynchronousLog('Cost', workspace_root=root)
    with _usage_lock:
        summary_path = path.parent / 'cost_stack.json'
        try:
            state = json.loads(summary_path.read_text('utf-8-sig')) if summary_path.exists() else {}
            counts = token_counts(usage)
            try:
                rates, source = pricing(model) if enabled('ARC_COST_STACK') else (None, 'disabled')
            except (ValueError, TypeError, OverflowError) as error:
                rates, source = None, 'invalid_configuration'
                logger.warning('COST_PRICING_INVALID: ' + str(error))
            amount = None if rates is None else ((counts['cached_tokens'] * rates[1]
                + (counts['prompt_tokens'] - counts['cached_tokens']) * rates[2]
                + counts['completion_tokens'] * rates[3]) / 1_000_000)
            currency = rates[0] if rates else None
            event = {'id': uuid4().hex, 'timestamp': datetime.now(timezone.utc).isoformat(),
                'run_id': current_run_id(), 'model': model, 'schema_name': schema_name, 'operation': operation,
                'usage': usage, 'tokens': counts, 'cost': amount, 'currency': currency,
                'pricing_source': source, 'rates_per_million': rates, 'estimated': True}
            add_counts(state.setdefault('tokens', {}), counts)
            by_model = state.setdefault('models', {}).setdefault(model, {})
            add_counts(by_model.setdefault('tokens', {}), counts)
            run = state.setdefault('runs', {}).setdefault(current_run_id() or 'unscoped', {})
            add_counts(run.setdefault('tokens', {}), counts)
            if amount is not None:
                for target in (state, by_model, run):
                    totals = target.setdefault('totals', {})
                    totals[currency] = totals.get(currency, 0.0) + amount
            else:
                state['unpriced_requests'] = state.get('unpriced_requests', 0) + 1
            state.update(updated_at=event['timestamp'], last_usage=event)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as output:
                output.write(json.dumps(event, ensure_ascii=False) + '\n')
            write_json_atomic(summary_path, state)
            with (path.parent / 'cost_usage.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps({**event, 'totals': state.get('totals', {})}, ensure_ascii=False) + '\n')
            logger.info('COST_STACK ' + json.dumps({'model': model, 'tokens': state['tokens'],
                'totals': state.get('totals', {}), 'cost': amount, 'currency': currency,
                'pricing_source': source}, ensure_ascii=False))
            if enabled('ARC_COST_STACK'):
                threshold_snapshots(root, state, summary_path)
        except (OSError, ValueError, TypeError, OverflowError) as error:
            logger.warning('USAGE_RECORD_FAILED: ' + str(error))


def threshold_snapshots(root, state, path):
    rate = float(os.environ.get('ARC_USD_TO_CNY', '7.2'))
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError('ARC_USD_TO_CNY must be positive and finite')
    totals = state.get('totals', {})
    amounts = {'CNY': totals.get('CNY', 0) + totals.get('USD', 0) * rate, 'USD': totals.get('USD', 0)}
    thresholds = state.setdefault('thresholds', {})
    state['snapshot_totals'] = amounts
    for currency, amount in amounts.items():
        current = int(amount // 10)
        previous = thresholds.get(currency, 0)
        for threshold in range(previous + 1, current + 1):
            snapshot_workspace(root, f'{threshold * 10}{currency}')
            thresholds[currency] = threshold
    state['exchange_rate_usd_to_cny'] = rate
    write_json_atomic(path, state)
