"""Add cloud read-only retrieval to the existing UAV guard without bypassing it."""
from .tools import KnowledgeError, RagTools, environment_client
from .catalog import SERVICES


def guard_class(base):
    class KnowledgeGuard(base):
        def __init__(self, *args, rag_client=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.knowledge = RagTools(rag_client if rag_client is not None else environment_client())

        def tools(self):
            return [{**tool, 'description': tool.get('description', '') +
                     (' 提交前必须调用 uav_knowledge_query 查询所选认证和加密算法；知识缺失不代表服务不存在。'
                      if tool['name'] in ('uav_combination_select', 'uav_protection_plan') else '')}
                    for tool in super().tools()] + self.knowledge.tools()

        def invoke(self, name, arguments):
            if name == 'uav_knowledge_query':
                return self.knowledge.invoke(name, arguments)
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
