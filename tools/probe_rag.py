"""Run from the actual host or Pod. No credentials in arguments or output."""
import json
from uav_rag.tools import environment_client

def main():
    client = environment_client()
    with client.request('/health') as response:
        health = json.load(response)
    page = client.query('签名用途与限制', ['auth-ed25519'])
    print(json.dumps({'health': health, 'query_items': len(page['items']),
                      'index_revision': page['index_revision'], 'has_next_page': bool(page['next_cursor'])},
                     ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
