"""Compatibility entrypoint: pass the original task unchanged to PiRunner.

Configure MCP as python -m uav_rag.mcp_guard to expose retrieval.
Set RAG connection variables in config.environment; the base runner filters env.
"""


def runner_class(base):
    class RagPiRunner(base):
        pass
    return RagPiRunner


def main():
    from jetson.pi_runner import app
    app.main()


if __name__ == '__main__':
    main()
