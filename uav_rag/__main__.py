import argparse
import json
import os
from pathlib import Path

from .index import build, KnowledgeBase
from .server import make_server, Artifacts
from .client import RagClient
from .workflow import CommandAgent, run_workflow


def main():
    parser = argparse.ArgumentParser(description='UAV GmSSL RAG knowledge service')
    commands = parser.add_subparsers(dest='command',required=True)
    index = commands.add_parser('index')
    index.add_argument('--source',default='../GmSSL')
    index.add_argument('--db',default='data/gmssl.sqlite3')
    serve = commands.add_parser('serve')
    serve.add_argument('--db',default='data/gmssl.sqlite3')
    serve.add_argument('--host',default='127.0.0.1')
    serve.add_argument('--port',default=8787,type=int)
    serve.add_argument('--artifacts',help='Trusted local artifact registry JSON')
    query = commands.add_parser('query')
    query.add_argument('query')
    query.add_argument('--services',nargs='+',required=True)
    query.add_argument('--url',default='http://127.0.0.1:8787')
    query.add_argument('--task-id',default='local-query')
    workflow = commands.add_parser('workflow')
    workflow.add_argument('--task',required=True,help='JSON with task_id and scenario')
    workflow.add_argument('--agent-command',required=True,help='JSON argv of a read-only Agent command')
    workflow.add_argument('--url',default='http://127.0.0.1:8787')
    args = parser.parse_args()
    token_file = os.environ.get('UAV_RAG_TOKEN_FILE')
    token = Path(token_file).read_text().strip() if token_file else os.environ.get('UAV_RAG_TOKEN')
    if args.command == 'index':
        result = build(args.source,args.db)
    elif args.command == 'serve':
        server = make_server(KnowledgeBase(args.db),args.host,args.port,token=token,artifacts=Artifacts(args.artifacts))
        print(f'RAG listening on {args.host}:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    elif args.command == 'query':
        result = RagClient(args.url,token).enrich(args.task_id,args.query,args.services)
    else:
        task = json.loads(Path(args.task).read_text(encoding='utf-8-sig'))
        agent = CommandAgent(json.loads(args.agent_command))
        result = run_workflow(task,agent,agent,RagClient(args.url,token))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
