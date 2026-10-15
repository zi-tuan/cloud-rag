"""验证 ORM 迁移后的持久状态与跨进程事务保证。"""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sqlalchemy import event

from shared.communication.envelope import MAX_SEQ, ProtocolError, ReplayError
from services.crypto_service.repository.crypto import CounterRepository, CryptoRepository


def allocate_and_accept(path):
    repository = CounterRepository(path)
    sequences = [repository.allocate('send') for _ in range(20)]
    try:
        repository.accept('receive', 1)
        accepted = True
    except ReplayError:
        accepted = False
    return sequences, accepted


class CryptoRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'crypto.db'

    def test_old_sqlite_state_is_preserved(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE sessions(epoch TEXT PRIMARY KEY, suite TEXT NOT NULL)')
            db.execute('CREATE TABLE send_counter(domain TEXT PRIMARY KEY, seq INTEGER NOT NULL)')
            db.execute('CREATE TABLE receive_window(domain TEXT PRIMARY KEY, highest INTEGER NOT NULL, bitmap TEXT NOT NULL)')
            db.execute("INSERT INTO sessions VALUES ('old', 'AES-256-GCM')")
            db.execute("INSERT INTO send_counter VALUES ('send', 41)")
            db.execute("INSERT INTO receive_window VALUES ('receive', 70, '5')")
        repository = CryptoRepository(self.path)
        self.assertEqual(repository.epochs('AES-256-GCM'), {'old': 'AES-256-GCM'})
        self.assertEqual(repository.allocate('send'), 42)
        with self.assertRaises(ReplayError):
            repository.accept('receive', 68)
        with self.assertRaises(ReplayError):
            repository.accept('receive', 6)
        repository.accept('receive', 69)
        with self.assertRaises(ReplayError):
            CryptoRepository(self.path).accept('receive', 69)

    def test_multiple_processes_allocate_unique_sequences_and_accept_once(self):
        CounterRepository(self.path)
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(allocate_and_accept, [str(self.path)] * 8))
        self.assertEqual(sorted(seq for values, _ in results for seq in values), list(range(1, 161)))
        self.assertEqual(sum(accepted for _, accepted in results), 1)

    def test_full_unsigned_bitmap_survives_restart_and_sliding(self):
        repository = CounterRepository(self.path)
        for seq in range(1, 65):
            repository.accept('receive', seq)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT bitmap FROM receive_window').fetchone()[0], str((1 << 64) - 1))
        restarted = CounterRepository(self.path)
        with self.assertRaises(ReplayError):
            restarted.accept('receive', 1)
        restarted.accept('receive', 65)
        with self.assertRaises(ReplayError):
            restarted.accept('receive', 1)
        restarted.accept('receive', 129)
        restarted.accept('receive', 66)
        with self.assertRaises(ReplayError):
            restarted.accept('receive', 65)

    def test_sequence_exhaustion_does_not_overwrite_counter(self):
        repository = CounterRepository(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO send_counter VALUES (?, ?)', ('send', MAX_SEQ))
        with self.assertRaises(ProtocolError):
            repository.allocate('send')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT seq FROM send_counter').fetchone()[0], MAX_SEQ)

    def test_failed_commit_rolls_back_counter_and_window(self):
        repository = CounterRepository(self.path)

        def fail_commit(connection):
            raise RuntimeError('commit failed')

        event.listen(repository.engine, 'commit', fail_commit)
        with self.assertRaises(RuntimeError):
            repository.allocate('send')
        with self.assertRaises(RuntimeError):
            repository.accept('receive', 1)
        event.remove(repository.engine, 'commit', fail_commit)
        self.assertEqual(repository.allocate('send'), 1)
        repository.accept('receive', 1)

    def test_retire_preserves_counter_and_window(self):
        repository = CryptoRepository(self.path)
        repository.prepare_epoch('epoch', 'AES-256-GCM')
        repository.allocate('send')
        repository.accept('receive', 1)
        repository.retire_epoch('epoch', 'SM4-GCM')
        self.assertEqual(repository.epochs('AES-256-GCM'), {'epoch': 'AES-256-GCM'})
        repository.retire_epoch('epoch', 'AES-256-GCM')
        self.assertEqual(repository.epochs('AES-256-GCM'), {})
        restarted = CryptoRepository(self.path)
        restarted.prepare_epoch('epoch', 'AES-256-GCM')
        self.assertEqual(restarted.allocate('send'), 2)
        with self.assertRaises(ReplayError):
            restarted.accept('receive', 1)

    def test_conflicting_preparations_share_one_persisted_suite(self):
        repository = CryptoRepository(self.path)

        def prepare(suite):
            return CryptoRepository(self.path).prepare_epoch('epoch', suite)

        with ThreadPoolExecutor(max_workers=4) as pool:
            suites = list(pool.map(prepare, ['AES-256-GCM', 'SM4-GCM'] * 10))
        self.assertEqual(len(set(suites)), 1)
        self.assertEqual(repository.epochs(suites[0]), {'epoch': suites[0]})

    def test_sqlite_durability_and_lock_timeout(self):
        repository = CryptoRepository(self.path)
        with repository.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('PRAGMA synchronous').scalar_one(), 2)
            self.assertEqual(connection.exec_driver_sql('PRAGMA busy_timeout').scalar_one(), 15000)


if __name__ == '__main__':
    unittest.main()
