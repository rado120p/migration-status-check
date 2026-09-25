from migration_validator.gui.sessions import LoginThrottle, SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_session_create_lookup_drop():
    store = SessionStore(clock=Clock())
    token = store.create("rado")
    assert len(token) >= 40
    assert store.lookup(token).username == "rado"
    store.drop(token)
    assert store.lookup(token) is None
    assert store.lookup("nonsense") is None


def test_session_records_password_hash_at_creation():
    # finding #3: `user passwd` must end sessions created with the old
    # hash; the session needs to carry the hash it was created with.
    store = SessionStore(clock=Clock())
    token = store.create("rado", password_hash="pbkdf2_sha256$1$aa$bb")
    assert store.lookup(token).password_hash == "pbkdf2_sha256$1$aa$bb"


def test_session_idle_timeout_slides():
    clock = Clock()
    store = SessionStore(clock=clock, idle=100, absolute=1000)
    token = store.create("rado")
    clock.now += 90
    assert store.lookup(token).username == "rado"   # refreshes last_seen
    clock.now += 90
    assert store.lookup(token).username == "rado"
    clock.now += 101
    assert store.lookup(token) is None


def test_session_absolute_timeout():
    clock = Clock()
    store = SessionStore(clock=clock, idle=100, absolute=250)
    token = store.create("rado")
    for _ in range(3):
        clock.now += 80
        assert store.lookup(token).username == "rado"
    clock.now += 20  # 260 s since creation, only 20 s idle
    assert store.lookup(token) is None


def test_throttle_locks_after_five_failures_and_expires():
    clock = Clock()
    throttle = LoginThrottle(clock=clock, threshold=5, lockout=300)
    for _ in range(4):
        throttle.failure("rado", "1.2.3.4")
        assert throttle.retry_after("rado", "1.2.3.4") == 0
    throttle.failure("rado", "1.2.3.4")
    assert throttle.retry_after("rado", "1.2.3.4") == 300
    assert throttle.retry_after("rado", "5.6.7.8") == 0     # other IP unaffected
    assert throttle.retry_after("eva", "1.2.3.4") == 0      # other user unaffected
    clock.now += 299.5
    assert throttle.retry_after("rado", "1.2.3.4") == 1
    clock.now += 1
    assert throttle.retry_after("rado", "1.2.3.4") == 0
    throttle.failure("rado", "1.2.3.4")                     # counter restarted
    assert throttle.retry_after("rado", "1.2.3.4") == 0


def test_throttle_success_resets():
    throttle = LoginThrottle(clock=Clock(), threshold=5, lockout=300)
    for _ in range(4):
        throttle.failure("rado", "ip")
    throttle.success("rado", "ip")
    for _ in range(4):
        throttle.failure("rado", "ip")
    assert throttle.retry_after("rado", "ip") == 0
