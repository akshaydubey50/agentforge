"""Shared test helpers. get_test_owner_id() gives every test that constructs
a Task/MemoryEntry directly (bypassing the real Google sign-in flow, which
can't run in a test) a real app_user row to satisfy the owner_id/FK NOT NULL
constraint added for per-user isolation -- see db/models.py's User.

Named get_test_owner_id, not test_owner_id -- a bare `test_` prefix would
make pytest collect and run this as a test case itself."""

from sqlmodel import select

from agentsys.db.models import User
from agentsys.db.session import get_session

_TEST_GOOGLE_SUB = "test-fixture-user"


def get_test_owner_id() -> str:
    """Idempotent get-or-create -- safe to call from every test that needs
    an owner_id, without worrying about test ordering or duplicate rows."""
    with get_session() as session:
        user = session.exec(select(User).where(User.google_sub == _TEST_GOOGLE_SUB)).first()
        if user is None:
            user = User(google_sub=_TEST_GOOGLE_SUB, email="test-fixture@example.com", name="Test Fixture")
            session.add(user)
            session.commit()
            session.refresh(user)
        return user.id
