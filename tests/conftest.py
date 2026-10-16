import os
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/expense_test")
os.environ.setdefault("ADMIN_EMAIL", "teja@example.com")
os.environ.setdefault("MEMBER_EMAIL", "babbi@example.com")

from sqlalchemy import select  # noqa: E402

from core.db import Account, Base, Member, get_engine, make_sessionmaker, set_setting  # noqa: E402
from core.seed import seed  # noqa: E402


def fake_fx(d, currency):
    return Decimal("93.00") if currency == "USD" else Decimal("1")


@pytest.fixture()
def s():
    url = os.environ["DATABASE_URL"]
    eng = get_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    sess = make_sessionmaker(url)()
    seed(sess)
    teja = sess.scalar(select(Member).where(Member.name == "Teja"))
    babbi = sess.scalar(select(Member).where(Member.name == "Babbi"))
    sess.add_all([
        Account(member_id=teja.id, name="HDFC savings", bank="HDFC", kind="bank", last4="1234",
                own_upi_ids="teja.s@okicici,tejeswarsunkara@ybl"),
        Account(member_id=teja.id, name="HDFC credit card", bank="HDFC", kind="card", last4="4321"),
        Account(member_id=teja.id, name="US card", bank="Chase", kind="card", last4="7788", currency="USD"),
        Account(member_id=babbi.id, name="SBI savings", bank="SBI", kind="bank", last4="9876",
                own_upi_ids="babbi@oksbi"),
    ])
    set_setting(sess, "own_names", "Tejeswar Sunkara, Babitha")
    sess.commit()
    yield sess
    sess.close()


@pytest.fixture()
def people(s):
    return (s.scalar(select(Member).where(Member.name == "Teja")),
            s.scalar(select(Member).where(Member.name == "Babbi")))
