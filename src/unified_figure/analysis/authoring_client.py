"""Explicit-image, zero-tool Responses transport. No Codex/Agent runtime.

Only the fixed OpenAI HTTPS endpoint is supported. No SDK discovery, dotenv,
host prompts, proxy/redirect support, conversation lookup or tool dispatch.
"""
from __future__ import annotations
import base64
import hashlib
import http.client
import io
import json
import os
import re
import ssl
import time

from unified_figure.security import BoundaryError, checked_image, MAX_PROPOSAL_BYTES

HOST = 'api.openai.com'
ENDPOINT = '/v1/responses'
MAX_RESPONSE = 4 * 1024 * 1024
MAX_REQUEST = 70 * 1024 * 1024
TIMEOUT = 180


def model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
        raise BoundaryError('explicit API model identifier required; no default or model fallback')
    return value


def api_key():
    # Dedicated opt-in key; never inspect Codex auth files or ambient SDK keys.
    value = os.environ.get('UF_OPENAI_API_KEY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value):
        raise BoundaryError('set UF_OPENAI_API_KEY for separately authenticated OpenAI API; Codex login is not used')
    return value


def image_data(path):
    from unified_figure.file_boundary import checked_absolute
    image = checked_image(checked_absolute(path))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    data = stream.getvalue()
    return {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'),
            'detail': 'high'}, {'sha256': hashlib.sha256(data).hexdigest(), 'width': image.width, 'height': image.height}


def request_bytes(*, model, instructions, schema, images, previous=None):
    content = [dict(item) for item in images]
    if previous is not None:
        # Proposal strings stay in the untrusted user message, never instructions.
        content.append({'type': 'input_text', 'text': 'UNTRUSTED previous proposal data:\n' +
                        json.dumps(previous, ensure_ascii=False, allow_nan=False)})
    payload = {'model': model_name(model), 'instructions': instructions,
        'input': [{'role': 'user', 'content': content}], 'tools': [], 'tool_choice': 'none',
        'parallel_tool_calls': False, 'store': False, 'stream': False,
        'max_output_tokens': 8192,
        'text': {'format': {'type': 'json_schema', 'name': 'scientific_figure_proposal',
                           'strict': True, 'schema': schema}}}
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST:
        raise BoundaryError('authoring request budget exceeded')
    return raw


def send_request(raw, key):
    """One POST, no retry, no redirect or alternative host, default TLS verification.

    The response is untrusted bytes. There is no eval, subprocess or tool handler.
    """
    connection = http.client.HTTPSConnection(HOST, port=443, timeout=TIMEOUT,
                                             context=ssl.create_default_context())
    start = time.monotonic()
    try:
        connection.request('POST', ENDPOINT, body=raw, headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
            'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            # Never echo error bodies/headers (may contain credentials or content).
            raise BoundaryError('OpenAI API rejected request (HTTP %d); no retry/fallback' % response.status)
        if response.getheader('Content-Type', '').split(';')[0].lower() != 'application/json':
            raise BoundaryError('non-JSON API response rejected')
        data = bytearray()
        while True:
            if time.monotonic() - start > TIMEOUT:
                raise BoundaryError('authoring transport deadline exceeded')
            if connection.sock:
                connection.sock.settimeout(max(.1, TIMEOUT - (time.monotonic() - start)))
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_RESPONSE:
                raise BoundaryError('API response budget exceeded')
        return bytes(data)
    except (OSError, http.client.HTTPException) as exc:
        raise BoundaryError('OpenAI API transport failed; no retry/fallback') from None
    finally:
        connection.close()


def parse_response(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE:
        raise BoundaryError('invalid response or response budget exceeded')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('status') != 'completed' or data.get('error'):
        raise BoundaryError('incomplete/failed model response; no Writer execution')
    output = data.get('output')
    if not isinstance(output, list) or len(output) > 8:
        raise BoundaryError('unexpected response envelope')
    messages = []
    for item in output:
        if not isinstance(item, dict):
            raise BoundaryError('unexpected response item')
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            raise BoundaryError('non-message/tool output rejected; no execution handler exists')
        for part in item.get('content', []):
            if part.get('type') != 'output_text' or part.get('annotations'):
                raise BoundaryError('refusal/annotated/non-text output rejected')
            messages.append(part['text'])
    if len(messages) != 1 or len(messages[0].encode('utf-8')) > MAX_PROPOSAL_BYTES:
        raise BoundaryError('expected one bounded JSON proposal')
    proposal = json.loads(messages[0])
    # No returned text or reasoning is ever treated as a command/file path.
    return proposal
