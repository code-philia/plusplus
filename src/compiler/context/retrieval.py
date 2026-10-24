"""Lexical retrieval primitives; callers own candidate selection and weighting."""
import json
import re


def terms(value):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', json.dumps(value, ensure_ascii=False))
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.lower()))


def overlap(query, value):
    return len(query & terms(value))
