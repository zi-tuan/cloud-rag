"""Agent contract: agent(task_text, tools), without prompt enrichment.

Production Pi uses the original runner and uav_rag.mcp_guard. This harness
validates source citations actually read through tools, not unseen database rows.
"""
from .tools import RagTools


def run_workflow(task, agent, client):
    scenario = task.get('scenario')
    if not isinstance(scenario, str) or not 1 <= len(scenario) <= 16000:
        raise ValueError('scenario must contain 1..16000 characters')
    tools = RagTools(client)
    choice = agent(scenario, tools)
    if not isinstance(choice, dict) or set(choice) != {'selected_services', 'reason', 'citation_ids'} or not isinstance(choice['reason'], str):
        raise ValueError('invalid final decision')
    selected, citations = choice['selected_services'], choice['citation_ids']
    if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('invalid selected services')
    if not isinstance(citations, list) or any(not isinstance(x, str) for x in citations):
        raise ValueError('invalid citations')
    evidence = {}
    for page in tools.pages:
        for item in page['items']:
            if item['kind'] == 'citation':
                evidence.setdefault(item['service_id'], []).append(item)
    known = {c['id'] for items in evidence.values() for c in items}
    if set(citations) - known:
        raise ValueError('fabricated or unread citation')
    for name in selected:
        if not any(c['evidence_status'] == 'available' and c['id'] in citations for c in evidence.get(name, [])):
            raise ValueError('selection lacks supporting candidate evidence')
    return dict(task_id=task['task_id'], state='planned' if selected else 'insufficient_evidence',
        retrieval=dict(pages=tools.pages), decision=choice, requires_execution_verification=True)
