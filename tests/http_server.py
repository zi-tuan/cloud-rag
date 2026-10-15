"""测试用真实 Uvicorn 服务，随机端口并支持确定性关闭。"""
import socket
import threading
import time

import uvicorn


class LiveServer:
    def __init__(self, app):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.server_port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        self.thread = None

    def serve_forever(self):
        self.server.run(sockets=[self.socket])

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.shutdown()
                raise RuntimeError('Uvicorn failed to start')
            time.sleep(0.01)
        return self

    def shutdown(self):
        self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError('Uvicorn failed to stop')

    def server_close(self):
        self.socket.close()
