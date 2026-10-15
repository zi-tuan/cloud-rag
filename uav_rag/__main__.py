import argparse
import json
import os
from pathlib import Path

from .index import build, KnowledgeBase
from .server import make_server, Artifacts
from .client import RagClient


def main():
    parser = argparse.ArgumentParser(description='UAV OpenSSL RAG knowledge service')
    commands = parser.add_subparsers(dest='command',required=True)
    index = commands.add_parser('index')
    index.add_argument('--source',default='sources/openssl')
    index.add_argument('--db',default='data/openssl.sqlite3')
    serve = commands.add_parser('serve')
    serve.add_argument('--db',default='data/openssl.sqlite3')
    serve.add_argument('--host',default='127.0.0.1')
    serve.add_argument('--port',default=8787,type=int)
    serve.add_argument('--artifacts',help='Trusted local artifact registry JSON')
    query = commands.add_parser('query')
    query.add_argument('query')
    query.add_argument('--services',nargs='+',required=True)
    query.add_argument('--url',default='http://127.0.0.1:8787')
    query.add_argument('--task-id',default='local-query')
    lookup = commands.add_parser('lookup', help='Read one bounded knowledge page')
    lookup.add_argument('query')
    lookup.add_argument('--services', nargs='+', required=True)
    lookup.add_argument('--url', default='http://127.0.0.1:8787')
    lookup.add_argument('--cursor')
    lookup.add_argument('--top-k', type=int, default=1)
    lookup.add_argument('--max-bytes', type=int, default=6000)
    document = commands.add_parser('document', help='Read a separately stored knowledge document')
    document.add_argument('id', nargs='?', default='openssl-algorithms')
    document.add_argument('--db', default='data/openssl.sqlite3')
    args = parser.parse_args()
    token_file = os.environ.get('UAV_RAG_TOKEN_FILE')
    token = Path(token_file).read_text().strip() if token_file else os.environ.get('UAV_RAG_TOKEN')
    if args.command == 'index':
        result = build(args.source,args.db)
    elif args.command == 'document':
        result = KnowledgeBase(args.db).document(args.id)
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
        result = RagClient(args.url,token).query(args.query, args.services,
            cursor=args.cursor, top_k=args.top_k, max_bytes=args.max_bytes)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
