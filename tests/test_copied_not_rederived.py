"""`InsecureBind` is COPIED from the lane service, not re-derived — and this proves it.

Two services can run on one device in one gate housing, and both decide the same
question: may this host be bound without a credential? Two independent answers to
one question agree by convention until they do not, and the day they diverge is
the day one of them starts binding an interface the other would have refused.

So the copy is checked rather than intended, and it is checked two ways because
neither alone is enough:

  * **`is_loopback` is compared BYTE FOR BYTE** against the one in the installed
    `lane_controller`. It is pure logic with no wording in it, so identity is the
    right standard, and a divergence of a single line goes red.
  * **`assert_bind_allowed` is compared BY BEHAVIOUR**, over a table of hosts and
    credentials. Its message is not identical and must not be — the exposures are
    different, and a monitor telling an operator that its port publishes "where a
    vehicle was" would be describing the wrong service. What must be identical is
    the RULE, so the rule is what is compared.

This is only possible because `lane_controller` is installed here as a TEST
dependency. It is not a dependency of the package, which
`tests/test_no_opening_authority.py` enforces.
"""

from __future__ import annotations

import inspect

import pytest
from lane_controller import service as lane_service

from gate_agent import service as monitor_service

#: Every case the rule has to answer, and both sides of it. A table with only
#: refusals in it would be satisfied by a function that refuses everything.
CASES = [
    ("127.0.0.1", None),
    ("::1", None),
    ("localhost", None),
    ("localhost.localdomain", None),
    ("192.168.1.10", None),
    ("192.168.1.10", "a-token"),
    ("0.0.0.0", None),
    ("0.0.0.0", "a-token"),
    ("", None),
    ("", "a-token"),
    ("monitor.local", None),
    ("monitor.local", "a-token"),
    ("10.0.0.1", ""),
    ("::ffff:127.0.0.1", None),
]


def test_is_loopback_is_the_lane_services_own_function_character_for_character():
    """Pure logic, no wording: identity is the right standard.

    The one question it answers decides whether a credential is required, and a
    hostname that one service proves is loopback and the other does not is a port
    exposed by exactly the difference between them.
    """
    ours = inspect.getsource(monitor_service.is_loopback)
    theirs = inspect.getsource(lane_service.is_loopback)
    assert ours == theirs, (
        "is_loopback has diverged from lane_controller's. It is copied, not re-derived:\n"
        f"--- monitor ---\n{ours}\n--- lane ---\n{theirs}"
    )


@pytest.mark.parametrize(("host", "token"), CASES)
def test_the_bind_rule_answers_identically_in_both_services(host, token):
    """The READ rule, compared by behaviour rather than by text.

    The messages differ on purpose: this surface publishes which of a site's
    lanes are broken, and the lane's publishes where a vehicle was. What may not
    differ is which binds are allowed.

    **The lane's rule grew a SECOND refusal in its round 6 and this one has
    not**, so the comparison names what it is comparing. A lane on contract
    version 2 serves `POST /v1/lane/vend`, and off loopback it refuses a bind
    that carries a read token and no ACT token -- because the read token does
    not authorise an act. No surface in this package serves an act route
    (`agent_service.ACT_ROUTES` is empty and `test_agent_contract.py` requires
    it), so there is no second token here to require and nothing to compare.
    The lane's act half is therefore SATISFIED in the call below, and what is
    compared is the half both services have.

    The divergence is a property of the SURFACES, not of the copy: this is the
    one thing that may differ, it is named, and the assertion under it is what
    keeps everything else identical.
    """

    def verdict(module, **extra):
        try:
            module.assert_bind_allowed(host, 9999, token, **extra)
        except module.InsecureBind:
            return "refused"
        return "allowed"

    assert verdict(monitor_service) == verdict(lane_service, act_token="an-act-token"), (
        f"the two services disagree about binding {host!r} with token {token!r}"
    )


def test_this_package_serves_no_act_route_which_is_why_the_act_half_is_satisfied_above():
    """The control for the exemption in the test above, and it is not optional.

    The act half is satisfied there because nothing in this package serves an
    act route. If one ever did, that sentence would silently become false and
    the comparison would go on passing while the two services disagreed about a
    bind that can open a barrier. So it is asserted, from the surfaces
    themselves -- and the lane's own non-empty set is the positive control that
    the attribute being read is the one that carries the answer.
    """
    from gate_agent import agent_service, capture_service

    assert agent_service.ACT_ROUTES == ()
    assert capture_service.ACT_ROUTES == ()
    assert monitor_service.ACT_ROUTES == ()
    # THE CONTROL: a surface that DOES serve one reads non-empty through the
    # same attribute, so the three empties above are a measurement.
    assert lane_service.ACT_ROUTES, lane_service.ACT_ROUTES


def test_the_table_holds_cases_on_both_sides_of_the_rule():
    """The control for the parametrised test above.

    A table of only-refusals or only-allowals would be satisfied by a constant.
    """
    verdicts = set()
    for host, token in CASES:
        try:
            monitor_service.assert_bind_allowed(host, 9999, token)
            verdicts.add("allowed")
        except monitor_service.InsecureBind:
            verdicts.add("refused")
    assert verdicts == {"allowed", "refused"}


def test_the_two_messages_are_not_the_same_and_that_is_deliberate():
    """A copied message would describe the wrong service's exposure.

    Stated as a check rather than left as a comment, because "copy this" read
    literally would have produced a monitor warning an operator about where a
    vehicle was — which is not what this port publishes, and an operator acting
    on it would be defending the wrong thing.
    """
    ours = _refusal(monitor_service)
    theirs = _refusal(lane_service)
    assert ours != theirs
    assert "lanes are broken" in ours
    assert "where a vehicle was" in theirs
    # And both name the same two ways out, because the RULE is the same.
    for message in (ours, theirs):
        assert "--auth-token-file" in message
        assert "127.0.0.1" in message


def _refusal(module) -> str:
    try:
        module.assert_bind_allowed("192.168.1.10", 9999, None)
    except module.InsecureBind as exc:
        return str(exc)
    raise AssertionError("that bind should have been refused")


def test_the_token_flag_is_spelt_the_way_the_other_two_services_spell_it():
    """`--auth-token-file`, not `--token-file`.

    One concept, three services that may run on one device, and an installer or a
    supervisor unit configuring them should not need three spellings for it. The
    lane repository reconciled its own to the engine's for this reason; this is
    the third, matched at birth rather than after the drift.
    """
    from gate_agent.cli import build_parser

    args = build_parser().parse_args(
        ["monitor", "--config", "monitor.toml", "--auth-token-file", "tok"]
    )
    assert str(args.auth_token_file) == "tok"

    with pytest.raises(SystemExit):
        build_parser().parse_args(["monitor", "--config", "monitor.toml", "--token-file", "tok"])
