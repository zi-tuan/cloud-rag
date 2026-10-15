"""ORM 映射与 Repository：会话、发送计数器和接收窗口持久化。"""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Float, Integer, Text, URL, create_engine, delete, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from shared.communication.envelope import MAX_SEQ, ProtocolError, ReplayError


class Base(DeclarativeBase):
    pass


class SendCounter(Base):
    __tablename__ = "send_counter"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)


class ReceiveWindow(Base):
    __tablename__ = "receive_window"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    highest: Mapped[int] = mapped_column(Integer)
    # 64 位无符号位图可能超过 SQLite 有符号 INTEGER 的范围。
    bitmap: Mapped[str] = mapped_column(Text)


class CryptoSession(Base):
    __tablename__ = "sessions"

    epoch: Mapped[str] = mapped_column(Text, primary_key=True)
    suite: Mapped[str] = mapped_column(Text)


class EpochLease(Base):
    __tablename__ = 'epoch_leases'
    epoch: Mapped[str] = mapped_column(Text, primary_key=True)
    profile: Mapped[str] = mapped_column(Text)
    expires: Mapped[int] = mapped_column(Integer)
    retired: Mapped[int] = mapped_column(Integer, default=0)


class EpochRetirement(Base):
    __tablename__ = 'epoch_retirements'
    epoch: Mapped[str] = mapped_column(Text, primary_key=True)
    suite: Mapped[str] = mapped_column(Text)
    retire_at: Mapped[float] = mapped_column(Float)


class CounterRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.engine = create_engine(
            URL.create("sqlite", database=self.path),
            connect_args={"timeout": 15}, poolclass=NullPool,
        )

        @event.listens_for(self.engine, "connect")
        def configure_connection(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA synchronous=FULL")
            finally:
                cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin_transaction(connection):
            # 写事务在读取旧值前获取锁，不能以普通 BEGIN 或进程锁代替。
            immediate = connection.get_execution_options().get("sqlite_write", False)
            connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

        Base.metadata.create_all(self.engine, tables=[SendCounter.__table__, ReceiveWindow.__table__, EpochRetirement.__table__])

    @contextmanager
    def _transaction(self, *, write=False):
        with Session(self.engine) as db, db.begin():
            db.connection(execution_options={"sqlite_write": write})
            yield db

    def allocate(self, domain: str) -> int:
        with self._transaction(write=True) as db:
            counter = db.get(SendCounter, domain)
            seq = 1 if counter is None else counter.seq + 1
            if seq > MAX_SEQ:
                raise ProtocolError("session sequence exhausted; rotate epoch")
            if counter is None:
                db.add(SendCounter(domain=domain, seq=seq))
            else:
                counter.seq = seq
        return seq

    def accept(self, domain: str, seq: int):
        with self._transaction(write=True) as db:
            window = db.get(ReceiveWindow, domain)
            highest, bitmap = (0, 0) if window is None else (window.highest, int(window.bitmap))
            if seq > highest:
                shift = seq - highest
                bitmap = 1 if shift >= 64 else ((bitmap << shift) | 1) & ((1 << 64) - 1)
                highest = seq
            else:
                age = highest - seq
                if age >= 64 or bitmap & (1 << age):
                    raise ReplayError("duplicate or expired message")
                bitmap |= 1 << age
            if window is None:
                db.add(ReceiveWindow(domain=domain, highest=highest, bitmap=str(bitmap)))
            else:
                window.highest, window.bitmap = highest, str(bitmap)

    def schedule_retirement(self, epoch, suite, retire_at):
        with self._transaction(write=True) as db:
            current = db.get(EpochRetirement, epoch)
            if current is None:
                db.add(EpochRetirement(epoch=epoch, suite=suite, retire_at=retire_at))
            elif current.suite != suite or current.retire_at != retire_at:
                raise ProtocolError('retirement deadline cannot change')

    def valid_retirement(self, epoch, now):
        with self._transaction() as db:
            current = db.get(EpochRetirement, epoch)
            if current is not None and now >= current.retire_at:
                raise ProtocolError('business key retired at transition deadline')


class CryptoRepository(CounterRepository):
    def __init__(self, path):
        super().__init__(path)
        CryptoSession.__table__.create(self.engine, checkfirst=True)
        EpochLease.__table__.create(self.engine, checkfirst=True)

    def lease(self, epoch, profile, expires):
        with self._transaction(write=True) as db:
            current = db.get(EpochLease, epoch)
            if current is None:
                if db.get(EpochRetirement, epoch) is not None:
                    raise ProtocolError('retired epoch cannot acquire a new lease')
                db.add(EpochLease(epoch=epoch, profile=profile, expires=expires, retired=0))
            elif current.retired or current.profile != profile or current.expires != expires:
                raise ProtocolError('epoch lease cannot change or be resurrected')

    def valid_lease(self, epoch, now):
        self.valid_retirement(epoch, now)
        with self._transaction() as db:
            lease = db.get(EpochLease, epoch)
            if lease is None or lease.retired or now >= lease.expires:
                raise ProtocolError('business key expired or retired')

    def epochs(self, suite: str) -> dict[str, str]:
        with self._transaction() as db:
            sessions = db.scalars(select(CryptoSession).where(CryptoSession.suite == suite))
            return {session.epoch: session.suite for session in sessions}

    def prepare_epoch(self, epoch: str, suite: str) -> str:
        """原子地创建会话或返回已有算法，算法冲突由业务层处理。"""
        with self._transaction(write=True) as db:
            session = db.get(CryptoSession, epoch)
            if session is None:
                db.add(CryptoSession(epoch=epoch, suite=suite))
                return suite
            return session.suite

    def retire_epoch(self, epoch: str, suite: str):
        # 保留计数器与重放窗口，重新准备同一 epoch 也不能复用 nonce。
        with self._transaction(write=True) as db:
            db.execute(delete(CryptoSession).where(CryptoSession.epoch == epoch, CryptoSession.suite == suite))
            lease = db.get(EpochLease, epoch)
            if lease is not None:
                lease.retired = 1
            retirement = db.get(EpochRetirement, epoch)
            if retirement is None:
                db.add(EpochRetirement(epoch=epoch, suite=suite, retire_at=0))
            # 已登记的截止时间不可改；lease.retired 负责立即撤销。
            # 保留原值使应答丢失后的同一退役计划仍能幂等重发。
