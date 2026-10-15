"""按设备累计的滚动窗口配额，独立于认证会话与 epoch，重启后保留。"""
import time
from shared.communication.database import SQLiteDatabase
from shared.communication.envelope import ProtocolError


class QuotaExceeded(ProtocolError):
    pass


class DeviceQuota:
    def __init__(self, path, *, clock=time.time):
        self.db, self.clock = SQLiteDatabase(path), clock
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS quota_requests (device TEXT NOT NULL, lane TEXT NOT NULL, created REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS quota_window ON quota_requests(device,lane,created)')

    def consume(self, device, lane, limit):
        now = self.clock()
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM quota_requests WHERE created<=?', (now - 60,))
            count = db.execute('SELECT count(*) FROM quota_requests WHERE device=? AND lane=?', (device, lane)).fetchone()[0]
            if count >= limit:
                raise QuotaExceeded('device request quota exceeded')
            db.execute('INSERT INTO quota_requests VALUES (?,?,?)', (device, lane, now))
        return {'window_seconds': 60, 'used': count + 1, 'limit': limit}
