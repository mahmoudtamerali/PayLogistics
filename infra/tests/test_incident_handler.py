import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules", "incident_automation", "lambda"))
import handler  # noqa: E402


def event(name="securecare-scan-infected-file", value="ALARM"):
    return {"time": "2026-11-11T10:00:00Z", "region": "eu-west-1",
            "detail": {"alarmName": name, "state": {"value": value, "reason": "Threshold crossed"}}}


class FakeSns:
    def __init__(self):
        self.calls = []

    def publish(self, **kw):
        self.calls.append(kw)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TOPIC_ARN", "arn:aws:sns:eu-west-1:000000000000:t")


def test_known_alarm_is_high_with_first_step():
    sns = FakeSns()
    out = handler.handler(event(), client=sns)
    assert out["published"] is True
    call = sns.calls[0]
    assert call["TopicArn"].endswith(":t")
    assert call["Subject"].startswith("[HIGH]")
    assert "quarantine" in call["Message"].lower()
    assert "Region: eu-west-1" in call["Message"]


def test_unknown_alarm_uses_default_text():
    sns = FakeSns()
    handler.handler(event("securecare-new-thing"), client=sns)
    assert "[MEDIUM]" in sns.calls[0]["Subject"]
    assert "none (default text used)" in sns.calls[0]["Message"]


def test_ok_transition_publishes_nothing():
    sns = FakeSns()
    out = handler.handler(event(value="OK"), client=sns)
    assert out["published"] is False and sns.calls == []


def test_subject_is_cut_to_100_chars():
    subject, _ = handler.build_message(event("securecare-" + "x" * 200))
    assert len(subject) <= 100


def test_every_alarm_in_monitoring_has_a_playbook_entry():
    import re
    root = os.path.join(os.path.dirname(__file__), "..", "modules")
    names = []
    for mod in ("monitoring", "waf_alarm"):
        text = open(os.path.join(root, mod, "main.tf")).read()
        names += re.findall(r'alarm_name\s*=\s*"\$\{var\.name\}-([\w-]+)"', text)
    assert names, "no alarms found"
    missing = [n for n in names if handler.classify("securecare-" + n)[3] is None]
    assert missing == []


def test_message_is_json_safe_text():
    _, msg = handler.build_message(event())
    json.dumps(msg)
