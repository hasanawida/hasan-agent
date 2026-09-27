"""Short-lived QR login tickets must never reuse the persistent master key."""
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from hassan_ai.remote import PairingCodes


def test_ticket_is_opaque_unique_and_single_use():
    pairing = PairingCodes()
    first = pairing.new_ticket()
    assert first["ticket"].startswith("qr_") and len(first["ticket"]) >= 40
    assert first["expires_in"] == 300
    second = pairing.new_ticket()
    assert first["ticket"] != second["ticket"]
    assert not pairing.redeem_ticket(first["ticket"])
    assert pairing.redeem_ticket(second["ticket"])
    assert not pairing.redeem_ticket(second["ticket"])


def test_numeric_code_and_qr_ticket_are_independent():
    pairing = PairingCodes()
    code = pairing.new()["code"]
    ticket = pairing.new_ticket()["ticket"]
    assert pairing.redeem(code)
    assert pairing.redeem_ticket(ticket)
    ticket = pairing.new_ticket()["ticket"]
    code = pairing.new()["code"]
    assert pairing.redeem_ticket(ticket)
    assert pairing.redeem(code)


def test_wrong_qr_ticket_does_not_lock_numeric_code_or_valid_ticket():
    pairing = PairingCodes()
    code = pairing.new()["code"]
    ticket = pairing.new_ticket()["ticket"]
    for _ in range(10):
        assert not pairing.redeem_ticket("qr_" + "x" * 43)
    assert pairing.tries == 0
    assert pairing.redeem_ticket(ticket)
    assert pairing.redeem(code)


def test_numeric_guess_lockout_does_not_destroy_independent_qr():
    pairing = PairingCodes()
    code = pairing.new()["code"]
    ticket = pairing.new_ticket()["ticket"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(pairing.MAX_TRIES):
        assert not pairing.redeem(wrong)
    assert not pairing.redeem(code)
    assert pairing.redeem_ticket(ticket)


def test_ticket_expiry_boundary_is_closed_and_restart_drops_ticket(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    pairing = PairingCodes()
    ticket = pairing.new_ticket()["ticket"]
    now[0] = 1300
    assert not pairing.redeem_ticket(ticket)
    assert pairing.ticket is None
    fresh = pairing.new_ticket()["ticket"]
    assert not PairingCodes().redeem_ticket(fresh)


def test_concurrent_redemption_has_exactly_one_winner():
    pairing = PairingCodes()
    ticket = pairing.new_ticket()["ticket"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(pairing.redeem_ticket, [ticket] * 16))
    assert outcomes.count(True) == 1


@pytest.mark.parametrize("candidate", [None, 123, "", "123456", "x" * 1000, "qr_🔐", "qr_" + "🔐" * 40])
def test_bad_ticket_input_cannot_consume_valid_ticket(candidate):
    pairing = PairingCodes()
    ticket = pairing.new_ticket()["ticket"]
    assert not pairing.redeem_ticket(candidate)
    assert pairing.redeem_ticket(ticket)
