"""Stateless, byte-bounded pages of cloud knowledge for agent tool calls."""
import base64
import hashlib
import json


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def query_page(kb, request):
    allowed = {'query', 'candidate_services', 'top_k', 'cursor', 'max_bytes'}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError('invalid query fields')
    query = request.get('query')
    if not isinstance(query, str) or not query.strip() or not 1 <= len(query) <= 512:
        raise ValueError('use a focused query of 1..512 characters, not the whole task')
    budget = request.get('max_bytes', 6000)
    if type(budget) is not int or not 4096 <= budget <= 12000:
        raise ValueError('max_bytes must be 4096..12000')
    context = kb.enrich(dict(task_id='tool-query', query=query,
        candidate_services=request.get('candidate_services'), top_k=request.get('top_k', 1)))
    # Bind pagination to the exact query and both source/description revisions.
    signature = hashlib.sha256(encoded({
        'query': query, 'services': [s['service_id'] for s in context['services']],
        'top_k': request.get('top_k', 1), 'corpus': context['corpus']})).hexdigest()
    records = []
    for service in context['services']:
        common = dict(service_id=service['service_id'], evidence_status=service['evidence_status'])
        records.append(dict(common, kind='service', binding=service['binding'], note=service['note']))
        for field, values in (service.get('description') or {}).items():
            values = values if isinstance(values, list) else [values]
            for index, value in enumerate(values):
                records.append(dict(common, kind='description', field=field, index=index, value=value))
        for citation in service['citations']:
            # Preserve the original citation identity and line range. Offsets are
            # Unicode character offsets into that original chunk, not new lines.
            text = citation['text']
            for offset in range(0, len(text), 256):
                records.append(dict(common, kind='citation', **{k: v for k, v in citation.items() if k != 'text'},
                    text=text[offset:offset+256], text_offset=offset, text_length=len(text)))
    offset = 0
    cursor = request.get('cursor')
    if cursor is not None:
        if not isinstance(cursor, str) or len(cursor) > 512:
            raise ValueError('invalid cursor')
        try:
            saved = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc
        if (not isinstance(saved, dict) or saved.get('signature') != signature
                or type(saved.get('offset')) is not int or not 0 <= saved['offset'] < len(records)):
            raise ValueError('stale cursor or different query')
        offset = saved['offset']

    def cursor_at(position):
        if position == len(records):
            return None
        return base64.urlsafe_b64encode(encoded(dict(signature=signature, offset=position))).decode('ascii')

    page = dict(index_revision=kb.metadata['revision'],
        description_revision=kb.metadata['description_revision'], items=[], next_cursor=cursor_at(offset))
    for position in range(offset, len(records)):
        trial = dict(page, items=page['items'] + [records[position]], next_cursor=cursor_at(position+1))
        if len(encoded(trial)) > budget:
            break
        page = trial
    if not page['items']:
        raise ValueError('knowledge item exceeds page budget')
    return page
