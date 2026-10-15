"""Add cloud read-only retrieval to the existing UAV guard without bypassing it."""
from .tools import KnowledgeError, RagTools, environment_client
from .catalog import SERVICES
import copy
import json


def guard_class(base):
    class KnowledgeGuard(base):
        def __init__(self, *args, rag_client=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.knowledge = RagTools(rag_client if rag_client is not None else environment_client())

        def tools(self):
            result = copy.deepcopy(super().tools())
            for tool in result:
                if tool['name'] in ('uav_combination_select', 'uav_protection_plan'):
                    tool['description'] += ' 提交前按需查询所选算法；citation_ids 必须覆盖每个所选算法且来自本任务已读页。证据不足则停止提交，知识缺失不代表服务不存在。'
                    schema = tool['inputSchema']
                    schema['properties']['citation_ids'] = {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 32}
                    schema['required'].append('citation_ids')
            return result + self.knowledge.tools()

        def invoke(self, name, arguments):
            if name == 'uav_knowledge_query':
                return self.knowledge.invoke(name, arguments)
            if name == 'uav_service_activate' and isinstance(arguments, dict):
                sid = arguments.get('service_id')
                evidenced = {item['service_id'] for page in self.knowledge.pages
                             for item in page['items'] if item.get('kind') == 'citation'}
                if sid not in evidenced:
                    raise KnowledgeError('retrieve service evidence before activation; failed queries cannot authorize changes')
            # Require an actual lookup before writing a final selection receipt.
            # A lookup is not proof of implementation: not_found is still allowed
            # as knowledge absence, and the original guard validates deployment.
            if isinstance(arguments, dict):
                targets = []
                if name == 'uav_combination_select':
                    targets = [arguments]
                elif name == 'uav_protection_plan' and isinstance(arguments.get('phases'), list):
                    targets = [p.get('target', {}) for p in arguments['phases'] if isinstance(p, dict)]
                required = {sid for target in targets if isinstance(target, dict)
                    for sid, entry in SERVICES.items() if entry['binding']
                    and all(target.get(k) == v for k, v in entry['binding'].items())}
                read = {item['service_id'] for page in self.knowledge.pages for item in page['items']}
                if required - read:
                    raise KnowledgeError('query uav_knowledge_query before selection: ' + ','.join(sorted(required-read)))
                if name in ('uav_combination_select', 'uav_protection_plan'):
                    ids = arguments.get('citation_ids')
                    available = {item['id']: item for page in self.knowledge.pages
                                 for item in page['items'] if item.get('kind') == 'citation'}
                    if (not isinstance(ids, list) or not 1 <= len(ids) <= 32
                            or any(not isinstance(cid, str) or cid not in available for cid in ids)):
                        raise KnowledgeError('citation_ids must come from pages actually read in this task')
                    covered = {available[cid]['service_id'] for cid in ids}
                    if required - covered:
                        raise KnowledgeError('insufficient retrieved citations for selected services; query more or stop')
                    evidence = {'citation_ids': ids, 'index_revision': self.knowledge.revision[0],
                                'description_revision': self.knowledge.revision[1],
                                'citations': [available[cid] for cid in dict.fromkeys(ids)]}
                    # Original task protocol and original Guard validation remain unchanged.
                    original = {k: v for k, v in arguments.items() if k != 'citation_ids'}
                    result = super().invoke(name, original)
                    if getattr(self, 'selection_file', None):
                        evidence_path = self.selection_file.with_suffix('.knowledge.json')
                        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                    return result
            return super().invoke(name, arguments)
    return KnowledgeGuard


def main():
    from jetson.edge_agent import mcp_guard
    from .mcp import serve
    mcp_guard.GuardTools = guard_class(mcp_guard.GuardTools)
    mcp_guard.serve = serve
    mcp_guard.main()


if __name__ == '__main__':
    main()
