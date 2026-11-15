"""Unit tests for incident creation, repeated unsafe conditions, corrective-action rules and alarm patterns."""
from datetime import datetime, timedelta, timezone

from app.business_rules.alarm_patterns import detect_floods, detect_nuisance_clusters, detect_repeated_alarms
from app.business_rules.config import RulesConfig
from app.business_rules.corrective_action_rules import (
    action_closed_properly, can_close_incident, completion_rate, valid_action_transition,
    valid_incident_transition, verification_rate,
)
from app.business_rules.incident_rules import (
    find_repeated_unsafe, find_unsafe_episodes, incident_candidates, incident_severity,
)

T0 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)
END = T0 + timedelta(days=30)


def s(seconds=0.0, minutes=0.0, hours=0.0, days=0.0):
    return T0 + timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)


def ev(n, mid, ts, assessment, code=None):
    return dict(machine_state_event_id=f"MSE-{mid}-{n:06d}", machine_id=mid, event_ts=ts,
                safety_assessment=assessment, unsafe_rule_code=code)


def al(n, mid, ts, sev="HIGH", dfn="ADEF-0001", clear=None):
    return dict(alarm_event_id=f"ALM-{mid}-{n:06d}", machine_id=mid, alarm_definition_id=dfn, severity=sev,
                raised_at=ts, cleared_at=(ts + timedelta(seconds=clear)) if clear is not None else None)


# ---------------------------------------------------------------- unsafe episodes
def test_episode_spans_consecutive_unsafe_events_until_next_safe_event():
    evs = [ev(1, "M1", s(0), "SAFE"), ev(2, "M1", s(60), "UNSAFE", "R-U1"), ev(3, "M1", s(90), "UNSAFE", "R-U2"),
           ev(4, "M1", s(150), "SAFE"), ev(5, "M1", s(600), "UNSAFE", "R-U3")]
    eps = find_unsafe_episodes(evs, END)
    assert len(eps) == 2
    assert (eps[0].rule_code, eps[0].start_ts, eps[0].end_ts, eps[0].duration_seconds) == ("R-U1", s(60), s(150), 90)
    assert eps[0].event_ids == ["MSE-M1-000002", "MSE-M1-000003"]
    assert eps[1].end_ts == END                     # still unsafe at the data cut-off


def test_episodes_are_per_machine_and_order_independent():
    evs = [ev(2, "M1", s(60), "UNSAFE", "R-U1"), ev(1, "M2", s(0), "UNSAFE", "R-U2"), ev(3, "M1", s(120), "SAFE"),
           ev(4, "M2", s(30), "SAFE")]
    eps = find_unsafe_episodes(evs, END)
    assert [(e.machine_id, e.duration_seconds) for e in eps] == [("M2", 30), ("M1", 60)]


# ---------------------------------------------------------------- incident creation (IN-1, IN-2)
def test_incident_created_for_unsafe_episode_with_high_alarm():
    eps = find_unsafe_episodes([ev(1, "M1", s(100), "UNSAFE", "R-U1"), ev(2, "M1", s(200), "SAFE")], END)
    alarms = [al(1, "M1", s(103), "CRITICAL"), al(2, "M1", s(110), "LOW"), al(3, "M2", s(105), "CRITICAL")]
    (c,) = incident_candidates(eps, alarms)
    assert c.trigger_alarm_id == "ALM-M1-000001"
    assert c.linked_alarm_ids == ["ALM-M1-000001", "ALM-M1-000002"]    # trigger first, other machine excluded
    assert c.severity == "CRITICAL"


def test_incident_rules_exclusions():
    short = find_unsafe_episodes([ev(1, "M1", s(100), "UNSAFE", "R-U1"), ev(2, "M1", s(105), "SAFE")], END)
    assert incident_candidates(short, [al(1, "M1", s(101), "CRITICAL")]) == []          # < 10 s
    ep = find_unsafe_episodes([ev(1, "M1", s(100), "UNSAFE", "R-U1"), ev(2, "M1", s(200), "SAFE")], END)
    assert incident_candidates(ep, [al(1, "M1", s(110), "MEDIUM")]) == []                # needs HIGH/CRITICAL
    assert incident_candidates(ep, [al(1, "M1", s(300), "CRITICAL")]) == []              # outside episode window
    assert len(incident_candidates(ep, [al(1, "M1", s(45), "HIGH")])) == 1               # inside 60 s look-back
    assert incident_candidates(ep, [al(1, "M1", s(30), "HIGH")]) == []                   # beyond look-back


def test_incident_thresholds_are_configurable():
    ep = find_unsafe_episodes([ev(1, "M1", s(100), "UNSAFE", "R-U1"), ev(2, "M1", s(105), "SAFE")], END)
    cfg = RulesConfig(incident_min_unsafe_seconds=3)
    assert len(incident_candidates(ep, [al(1, "M1", s(101), "HIGH")], cfg)) == 1


def test_incident_severity_rule():
    assert incident_severity(["HIGH", "LOW"]) == "HIGH"
    assert incident_severity(["HIGH", "CRITICAL"]) == "CRITICAL"


# ---------------------------------------------------------------- repeated unsafe conditions (IN-5)
def test_repeated_unsafe_condition_needs_three_within_seven_days():
    def eps(starts, mid="M1", code="R-U1"):
        evs = []
        for i, t in enumerate(starts):
            evs += [ev(2 * i + 1, mid, t, "UNSAFE", code), ev(2 * i + 2, mid, t + timedelta(seconds=30), "SAFE")]
        return find_unsafe_episodes(evs, END)
    assert len(find_repeated_unsafe(eps([s(days=0), s(days=3), s(days=6)]))) == 1
    assert find_repeated_unsafe(eps([s(days=0), s(days=3), s(days=8)])) == []           # spread over > 7 days
    assert find_repeated_unsafe(eps([s(days=0), s(days=1)])) == []                      # only two
    mixed = eps([s(days=0), s(days=1)], code="R-U1") + eps([s(days=2)], code="R-U2")
    assert find_repeated_unsafe(mixed) == []                                            # different rule codes
    (g,) = find_repeated_unsafe(eps([s(days=0), s(days=1), s(days=2), s(days=20)]))
    assert len(g) == 3


# ---------------------------------------------------------------- corrective actions and incident lifecycle
def A(status, **kw):
    base = dict(status=status, assigned_engineer_id="EMP-20001", verified_by_id="EMP-30001", verification_result="PASSED",
                planned_at=s(), completed_at=s(hours=5), verified_at=s(hours=6), closed_at=s(hours=7))
    base.update(kw)
    return base


def test_action_closure_requires_verification():
    assert action_closed_properly(A("CLOSED"))
    assert not action_closed_properly(A("CLOSED", verified_at=None, verification_result=None, verified_by_id=None))
    assert not action_closed_properly(A("CLOSED", verification_result="FAILED"))
    assert not action_closed_properly(A("CLOSED", verified_by_id="EMP-20001"))            # self-verification
    assert not action_closed_properly(A("CLOSED", verified_at=s(hours=4)))               # verified before completion
    assert not action_closed_properly(A("CLOSED", closed_at=s(hours=5)))                 # closed before verification
    assert action_closed_properly(A("IN_PROGRESS", verification_result=None))            # rule only constrains CLOSED


def test_incident_can_close_only_when_every_live_action_is_closed():
    assert can_close_incident([A("CLOSED"), A("CLOSED")])
    assert can_close_incident([A("CLOSED"), A("CANCELLED")])
    assert not can_close_incident([])                                                    # needs >= 1 action
    assert not can_close_incident([A("CLOSED"), A("VERIFIED")])
    assert not can_close_incident([A("CANCELLED")])
    assert not can_close_incident([A("CLOSED", verified_at=None, verification_result=None, verified_by_id=None)])


def test_lifecycle_transitions():
    ok = [(None, "OPEN"), ("OPEN", "INVESTIGATING"), ("INVESTIGATING", "ACTION_IN_PROGRESS"),
          ("ACTION_IN_PROGRESS", "RESOLVED"), ("RESOLVED", "CLOSED")]
    assert all(valid_incident_transition(a, b) for a, b in ok)
    assert not valid_incident_transition("OPEN", "CLOSED") and not valid_incident_transition("CLOSED", "OPEN")
    assert not valid_incident_transition(None, "RESOLVED")
    aok = [(None, "PLANNED"), ("PLANNED", "IN_PROGRESS"), ("IN_PROGRESS", "COMPLETED"), ("COMPLETED", "VERIFIED"),
           ("VERIFIED", "CLOSED"), ("VERIFIED", "CANCELLED"), ("PLANNED", "CANCELLED")]
    assert all(valid_action_transition(a, b) for a, b in aok)
    for a, b in [("COMPLETED", "CLOSED"), ("PLANNED", "COMPLETED"), ("CLOSED", "PLANNED"), ("CANCELLED", "IN_PROGRESS")]:
        assert not valid_action_transition(a, b), (a, b)


def test_completion_and_verification_rates():
    acts = [A("CLOSED"), A("VERIFIED"), A("COMPLETED", verification_result=None), A("IN_PROGRESS", verification_result=None),
            A("CANCELLED")]
    assert completion_rate(acts) == 3 / 4          # cancelled excluded; closed+verified+completed done
    assert verification_rate(acts) == 2 / 3        # of completed actions, 2 have a verification result
    assert completion_rate([]) == 0.0 and verification_rate([]) == 0.0


# ---------------------------------------------------------------- alarm floods (AL-5)
def test_flood_detected_with_threshold_and_episode_end():
    burst = [al(i, "M1", s(seconds=i * 20)) for i in range(1, 13)]                    # 12 alarms in ~4 min
    tail = [al(100 + i, "M1", s(seconds=240 + i * 60)) for i in range(1, 6)]         # keeps >= 5 in trailing window
    quiet = [al(200, "M1", s(hours=3))]
    (e,) = detect_floods(burst + tail + quiet)
    assert e.machine_id == "M1" and e.start == s(seconds=20) and e.alarm_count >= 12
    assert "ALM-M1-000200" not in e.alarm_ids


def test_flood_not_triggered_below_threshold_or_across_machines():
    nine = [al(i, "M1", s(seconds=i * 30)) for i in range(1, 10)]
    assert detect_floods(nine) == []
    split = [al(i, "M1" if i % 2 else "M2", s(seconds=i * 20)) for i in range(1, 15)]  # 7 per machine
    assert detect_floods(split) == []
    assert len(detect_floods(nine, RulesConfig(flood_threshold=9, flood_end_below=3))) == 1


def test_two_separate_floods():
    a = [al(i, "M1", s(seconds=i * 10)) for i in range(1, 15)]
    b = [al(50 + i, "M1", s(hours=5, seconds=i * 10)) for i in range(1, 15)]
    assert len(detect_floods(a + b)) == 2


# ---------------------------------------------------------------- nuisance (AL-6) and repeated alarms (AL-7)
def test_chattering_nuisance_cluster():
    chat = [al(i, "M1", s(minutes=i * 4), "LOW", "ADEF-0007", clear=45) for i in range(1, 7)]   # 6 in 24 min, quick clears
    (c,) = detect_nuisance_clusters(chat)
    assert (c.machine_id, c.alarm_definition_id, c.rules) == ("M1", "ADEF-0007", {"N1"}) and len(c.alarm_ids) == 6


def test_chatter_requires_quick_clear_and_window():
    slow = [al(i, "M1", s(minutes=i * 4), "LOW", "ADEF-0007", clear=600) for i in range(1, 7)]
    assert detect_nuisance_clusters(slow) == []                                      # not cleared within 120 s
    spread = [al(i, "M1", s(minutes=i * 20), "LOW", "ADEF-0007", clear=30) for i in range(1, 7)]
    assert detect_nuisance_clusters(spread) == []                                    # not within 30 minutes
    broken = [al(i, "M1", s(minutes=i * 4), "LOW", "ADEF-0007", clear=(600 if i == 3 else 30)) for i in range(1, 7)]
    assert detect_nuisance_clusters(broken) == []                                    # one slow raise breaks the run
    other_machine = [al(i, "M1" if i % 2 else "M2", s(minutes=i * 2), "LOW", "ADEF-0007", clear=30) for i in range(1, 9)]
    assert detect_nuisance_clusters(other_machine) == []


def test_repeating_low_value_alarm_per_day():
    day = [al(i, "M1", s(minutes=i * 30), "MEDIUM", "ADEF-0009", clear=900) for i in range(1, 21)]
    (c,) = detect_nuisance_clusters(day)
    assert c.rules == {"N2"}
    high = [al(i, "M1", s(minutes=i * 30), "HIGH", "ADEF-0009", clear=900) for i in range(1, 21)]
    assert detect_nuisance_clusters(high) == []                                      # severe alarms are not nuisance
    assert detect_nuisance_clusters(day[:19]) == []


def test_repeated_alarm_pattern():
    rep = [al(i, "M1", s(hours=i * 6), dfn="ADEF-0003") for i in range(1, 4)]
    (p,) = detect_repeated_alarms(rep)
    assert p.max_in_window == 3 and len(p.alarm_ids) == 3
    assert detect_repeated_alarms([al(i, "M1", s(hours=i * 13), dfn="ADEF-0003") for i in range(1, 4)]) == []


def test_day_based_rules_use_utc_whatever_the_input_offset():
    """20 alarms on one UTC day, expressed in +05:30: the UTC day must decide, not the local calendar day."""
    local = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)            # 20:00 UTC = 01:30 next day local time
    day = [al(i, "M1", start + timedelta(minutes=i * 10), "LOW", "ADEF-0009", clear=900) for i in range(1, 21)]
    shifted = [dict(a, raised_at=a["raised_at"].astimezone(local), cleared_at=a["cleared_at"].astimezone(local)) for a in day]
    assert [c.rules for c in detect_nuisance_clusters(day)] == [c.rules for c in detect_nuisance_clusters(shifted)]
    # 19 alarms before midnight UTC + 1 after: only 19 fall on the first UTC day -> no N2 cluster in either representation
    split = [al(i, "M1", datetime(2026, 10, 3, 23, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 3), "LOW", "ADEF-0010", clear=900)
             for i in range(1, 21)]
    both = [dict(a, raised_at=a["raised_at"].astimezone(local), cleared_at=a["cleared_at"].astimezone(local)) for a in split]
    assert detect_nuisance_clusters(split) == [] == detect_nuisance_clusters(both)
