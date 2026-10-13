"""Real corpus + real local HTTP; deterministic Agent double, no flight commands."""
import json
from pathlib import Path
import threading

from uav_rag.index import build, KnowledgeBase
from uav_rag.server import make_server
from uav_rag.client import RagClient
from uav_rag.workflow import run_workflow


def main():
    root = Path(__file__).resolve().parent
    db = root/'data/gmssl.sqlite3'
    if not db.exists():
        source = root/'sources/GmSSL' if (root/'sources/GmSSL').exists() else root.parent/'GmSSL'
        build(source,db)
    server = make_server(KnowledgeBase(db),port=0)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    task = dict(task_id='demo-sm2-sm4',scenario='为无人机通信选择国密签名认证与 GCM 加密服务，说明接口依据。')
    def agent(scenario, tools):
        assert scenario == task['scenario']
        citations = []
        for service in ['auth-sm2', 'sm4-gcm-v1']:
            arguments = dict(query='签名 加密 接口依据', candidate_services=[service])
            while True:
                page = tools.invoke('uav_knowledge_query', arguments)
                found = [item['id'] for item in page['items'] if item['kind'] == 'citation']
                if found:
                    citations.append(found[0])
                    break
                if not page['next_cursor']:
                    raise ValueError('demo has no source evidence')
                arguments['cursor'] = page['next_cursor']
        return dict(selected_services=['auth-sm2', 'sm4-gcm-v1'],
            reason='演示替身按需调用云端查询工具；不代表真实模型判断。', citation_ids=citations)
    try:
        result = run_workflow(task,agent,RagClient(f'http://127.0.0.1:{server.server_port}'))
        result['test_scope'] = '真实 GmSSL 索引和 HTTP；Agent 为确定性替身；没有启动服务或飞控。'
        report = root/'data/demo-result.json'
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(state=result['state'],selected=result['decision']['selected_services'],
            pages=len(result['retrieval']['pages']),report=str(report),test_scope=result['test_scope']),ensure_ascii=False,indent=2))
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == '__main__':
    main()
