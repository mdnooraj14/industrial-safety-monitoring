"""Unit tests for machine safety rules, state processing, severity classification and acknowledgement rules."""
from datetime import datetime, timedelta, timezone

import pytest

from app.business_rules.alarm_rules import (
    ack_deadline_at, ack_deadline_seconds, ack_delay_seconds, ack_status, at_least, classify_severity, escalate,
    is_missed, priority_rank,
)
from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.business_rules.safety_state import (
    ALLOWED_TRANSITIONS, InterlockReading, assess_state, check_summary, is_valid_transition, summarize_interlocks,
)
from app.business_rules.state_processing import (
    InterlockSpec, PreviousState, RuleViolation, process_state_event,
)

T0 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)


def R(itype="DOOR_SWITCH", status="ENGAGED", bypass=False, obs=None, required=True):
    return InterlockReading(itype, status, bypass, obs, required)


# ------------------------------------------------------------------ safety assessment truth table
@pytest.mark.parametrize("state,door,readings,expected", [
    # R-U1 door open while running
    ("RUNNING", "OPEN", [R(status="DISENGAGED", obs="OPEN")], ("UNSAFE", "R-U1")),
    # R-U2 required interlock inactive while running
    ("RUNNING", "CLOSED", [R(obs="CLOSED"), R("LIGHT_CURTAIN", "DISENGAGED")], ("UNSAFE", "R-U2")),
    ("RUNNING", "CLOSED", [R(obs="CLOSED"), R("E_STOP", "FAULT")], ("UNSAFE", "R-U2")),
    # R-U3 required interlock bypassed while running (bypass allowed)
    ("RUNNING", "CLOSED", [R(status="BYPASSED", bypass=True, obs="CLOSED")], ("UNSAFE", "R-U3")),
    # optional interlock inactive while running is not unsafe
    ("RUNNING", "CLOSED", [R(obs="CLOSED"), R("PRESSURE_MAT", "DISENGAGED", required=False)], ("SAFE", None)),
    # R-S2 running with everything engaged
    ("RUNNING", "CLOSED", [R(obs="CLOSED"), R("E_STOP")], ("SAFE", None)),
    ("RUNNING", "NOT_APPLICABLE", [R("E_STOP")], ("SAFE", None)),
    # R-S1 stopped with interlock engaged is the normal safe state
    ("STOPPED", "CLOSED", [R(obs="CLOSED")], ("SAFE", None)),
    # not running: door open / interlock disengaged is fine (operator access)
    ("STOPPED", "OPEN", [R(status="DISENGAGED", obs="OPEN")], ("SAFE", None)),
    ("MAINTENANCE", "OPEN", [R(status="DISENGAGED", obs="OPEN")], ("SAFE", None)),
    ("E_STOPPED", "CLOSED", [R("E_STOP", "DISENGAGED")], ("SAFE", None)),
    ("IDLE", "CLOSED", [R(status="BYPASSED", bypass=True, obs="CLOSED")], ("SAFE", None)),
    # R-I1 door interlock engaged although the door is open -> impossible
    ("RUNNING", "OPEN", [R(status="ENGAGED", obs="OPEN")], ("INVALID", "R-I1")),
    ("STOPPED", "OPEN", [R("GUARD_LOCK", "ENGAGED", obs="OPEN")], ("INVALID", "R-I1")),
    # R-I4 bypass where it is not allowed
    ("STOPPED", "CLOSED", [R(status="BYPASSED", bypass=False, obs="CLOSED")], ("INVALID", "R-I4")),
    ("RUNNING", "CLOSED", [R("E_STOP", "BYPASSED", bypass=False)], ("INVALID", "R-I4")),
    # precedence: INVALID beats UNSAFE
    ("RUNNING", "OPEN", [R(status="ENGAGED", obs="OPEN"), R("E_STOP", "DISENGAGED")], ("INVALID", "R-I1")),
])
def test_assess_state(state, door, readings, expected):
    assert assess_state(state, door, readings) == expected


def test_summary_counts_only_required_interlocks():
    assert summarize_interlocks([R(), R("E_STOP")]) == "ALL_ENGAGED"
    assert summarize_interlocks([R(status="DISENGAGED"), R("E_STOP", "FAULT")]) == "NONE_ENGAGED"
    assert summarize_interlocks([R(), R("E_STOP", "DISENGAGED")]) == "PARTIAL"
    assert summarize_interlocks([R(), R("PRESSURE_MAT", "DISENGAGED", required=False)]) == "ALL_ENGAGED"
    assert summarize_interlocks([]) == "ALL_ENGAGED"
    assert check_summary("PARTIAL", [R(), R("E_STOP", "DISENGAGED")])
    assert not check_summary("ALL_ENGAGED", [R(), R("E_STOP", "DISENGAGED")])  # R-I2


def test_transition_graph():
    assert is_valid_transition(None, "RUNNING")                  # first event
    assert is_valid_transition("RUNNING", "RUNNING")             # context change
    for src, dsts in ALLOWED_TRANSITIONS.items():
        for dst in dsts:
            assert is_valid_transition(src, dst)
    for src, dst in [("E_STOPPED", "RUNNING"), ("FAULT", "RUNNING"), ("STOPPED", "RUNNING"),
                     ("MAINTENANCE", "IDLE"), ("IDLE", "E_STOPPED")]:
        assert not is_valid_transition(src, dst), (src, dst)


# ------------------------------------------------------------------ state-event processing
SPECS = [InterlockSpec("ILK-1", "DOOR_SWITCH", True, False), InterlockSpec("ILK-2", "E_STOP", True, False)]
PREV = PreviousState("RUNNING", "CLOSED", T0, 5, {"ILK-1": "ENGAGED", "ILK-2": "ENGAGED"})


def go(state, door, statuses, prev=PREV, ts=T0 + timedelta(minutes=1)):
    return process_state_event(prev, ts, state, door, statuses, SPECS)


def test_processing_unsafe_door_open():
    r = go("RUNNING", "OPEN", {"ILK-1": "DISENGAGED"})
    assert (r.assessment, r.rule_code, r.summary, r.sequence_no) == ("UNSAFE", "R-U1", "PARTIAL", 6)
    assert r.changed_interlocks == ["ILK-1"] and r.previous_state == "RUNNING" and r.flagged


def test_processing_first_event_defaults_to_engaged():
    r = process_state_event(None, T0, "STOPPED", "CLOSED", {}, SPECS)
    assert (r.assessment, r.sequence_no, r.previous_state) == ("SAFE", 1, None)
    assert set(r.changed_interlocks) == {"ILK-1", "ILK-2"} and not r.flagged


def test_processing_door_closing_changes_door_interlock_observation():
    opened = PreviousState("STOPPED", "OPEN", T0, 2, {"ILK-1": "DISENGAGED", "ILK-2": "ENGAGED"})
    r = process_state_event(opened, T0 + timedelta(minutes=2), "STOPPED", "CLOSED", {"ILK-1": "ENGAGED"}, SPECS)
    assert r.changed_interlocks == ["ILK-1"] and r.assessment == "SAFE"


def test_processing_illegal_transition_is_flagged_not_dropped():
    prev = PreviousState("E_STOPPED", "CLOSED", T0, 3, {"ILK-1": "ENGAGED", "ILK-2": "DISENGAGED"})
    r = process_state_event(prev, T0 + timedelta(minutes=1), "RUNNING", "CLOSED", {"ILK-2": "ENGAGED"}, SPECS)
    assert (r.assessment, r.rule_code) == ("INVALID", "R-I3")


def test_processing_impossible_interlock_state_is_flagged():
    r = go("RUNNING", "OPEN", {"ILK-1": "ENGAGED"})  # door open but door interlock engaged
    assert (r.assessment, r.rule_code) == ("INVALID", "R-I1")


@pytest.mark.parametrize("kwargs,code", [
    (dict(state="FLYING", door="CLOSED", statuses={}), "BAD_MACHINE_STATE"),
    (dict(state="RUNNING", door="AJAR", statuses={}), "BAD_DOOR_STATE"),
    (dict(state="RUNNING", door="CLOSED", statuses={"ILK-9": "ENGAGED"}), "UNKNOWN_INTERLOCK"),
    (dict(state="RUNNING", door="CLOSED", statuses={"ILK-1": "BROKEN"}), "BAD_INTERLOCK_STATUS"),
    (dict(state="RUNNING", door="NOT_APPLICABLE", statuses={}), "DOOR_STATE_MISMATCH"),
    (dict(state="RUNNING", door="CLOSED", statuses={}), "NO_CHANGE"),
    (dict(state="RUNNING", door="CLOSED", statuses={"ILK-1": "FAULT"}, ts=T0), "TIMESTAMP_ORDER"),
    (dict(state="RUNNING", door="CLOSED", statuses={"ILK-1": "FAULT"}, ts=T0 - timedelta(seconds=5)), "TIMESTAMP_ORDER"),
])
def test_processing_hard_rejections(kwargs, code):
    kw = dict(kwargs)
    with pytest.raises(RuleViolation) as e:
        go(kw.pop("state"), kw.pop("door"), kw.pop("statuses"), **kw)
    assert e.value.code == code


# ------------------------------------------------------------------ alarm severity and acknowledgement
@pytest.mark.parametrize("default,assess,safety,risk,expected", [
    ("HIGH", "SAFE", True, "LOW", "HIGH"),
    ("HIGH", "UNSAFE", True, "LOW", "CRITICAL"),          # escalate while unsafe
    ("CRITICAL", "UNSAFE", True, "HIGH", "CRITICAL"),     # capped
    ("LOW", "UNSAFE", False, None, "MEDIUM"),
    ("MEDIUM", "SAFE", True, "HIGH", "HIGH"),             # safety alarm in a HIGH-risk zone is >= HIGH
    ("MEDIUM", "SAFE", False, "HIGH", "MEDIUM"),          # non-safety alarm unaffected by zone risk
    ("LOW", "SAFE", True, "MEDIUM", "LOW"),
    ("MEDIUM", "INVALID", True, "HIGH", "HIGH"),
    ("LOW", None, False, None, "LOW"),
])
def test_classify_severity(default, assess, safety, risk, expected):
    assert classify_severity(default, assess, safety, risk) == expected


def test_escalate_and_floor():
    assert [escalate(s) for s in ("LOW", "MEDIUM", "HIGH", "CRITICAL")] == ["MEDIUM", "HIGH", "CRITICAL", "CRITICAL"]
    assert escalate("LOW", 2) == "HIGH" and at_least("LOW", "HIGH") == "HIGH" and at_least("CRITICAL", "HIGH") == "CRITICAL"


def test_deadlines_and_priority():
    assert [ack_deadline_seconds(s) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")] == [120, 600, 1800, 7200]
    assert [priority_rank(s) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")] == [1, 2, 3, 4]
    assert ack_deadline_at(T0, "HIGH") == T0 + timedelta(minutes=10)
    # critical alarms always sort first
    alarms = [("LOW", 1), ("CRITICAL", 5), ("HIGH", 2), ("CRITICAL", 3)]
    assert [a[0] for a in sorted(alarms, key=lambda a: (priority_rank(a[0]), a[1]))] == ["CRITICAL", "CRITICAL", "HIGH", "LOW"]


def test_ack_delay_and_status_use_timestamps():
    assert ack_delay_seconds(T0, T0 + timedelta(seconds=45.26)) == 45.3
    assert ack_delay_seconds(T0, T0 - timedelta(seconds=30)) == -30.0       # invalid: ack before alarm
    dl = ack_deadline_at(T0, "CRITICAL")
    assert ack_status(dl, dl) == "ON_TIME"                                  # boundary is on time
    assert ack_status(dl + timedelta(seconds=1), dl) == "LATE"
    assert ack_status(T0, dl) == "ON_TIME"


def test_missed_alarm_rule():
    dl = T0 + timedelta(minutes=2)
    assert is_missed(False, dl, T0 + timedelta(hours=1))
    assert not is_missed(False, dl, T0)           # deadline not reached yet at cut-off
    assert not is_missed(True, dl, T0 + timedelta(hours=1))


# ------------------------------------------------------------------ configurable rules
def test_rules_config_from_env_overrides():
    cfg = RulesConfig.from_env({"RULE_FLOOD_THRESHOLD": "12", "RULE_ACK_DEADLINE_CRITICAL": "90",
                                "RULE_REPEATED_UNSAFE_WINDOW_DAYS": "14"})
    assert cfg.flood_threshold == 12 and cfg.repeated_unsafe_window_days == 14
    assert ack_deadline_seconds("CRITICAL", cfg) == 90 and ack_deadline_seconds("CRITICAL") == 120
    assert RulesConfig.from_env({}) == DEFAULT_RULES


@pytest.mark.parametrize("env", [
    {"RULE_ACK_DEADLINE_CRITICAL": "99999"},       # deadlines must increase with lower severity
    {"RULE_ESCALATION_HIGH": "10"},                # escalation before deadline
    {"RULE_FLOOD_END_BELOW": "50"},
])
def test_rules_config_rejects_inconsistent_values(env):
    with pytest.raises(ValueError):
        RulesConfig.from_env(env)
