import io
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import prove_isolation as pi  # noqa: E402
import recovery_time as rt  # noqa: E402
import probe_lambda  # noqa: E402


# ---------------------------------------------------------------- isolation
def test_open_cidrs_finds_ipv4_and_ipv6():
    sgs = [{"GroupId": "sg-1", "IpPermissions": [{"IpRanges": [{"CidrIp": "10.0.0.0/16"}], "Ipv6Ranges": []}]},
           {"GroupId": "sg-2", "IpPermissions": [{"IpRanges": [{"CidrIp": "0.0.0.0/0"}], "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]}]
    assert pi.open_cidrs(sgs) == [("sg-2", "0.0.0.0/0"), ("sg-2", "::/0")]


class FakeEc2:
    def __init__(self, open_rule=False, igw=False):
        self.open_rule, self.igw = open_rule, igw

    def describe_security_groups(self, **kw):
        cidr = "0.0.0.0/0" if self.open_rule else "10.0.0.0/16"
        return {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [{"IpRanges": [{"CidrIp": cidr}]}]}]}

    def describe_internet_gateways(self, **kw):
        return {"InternetGateways": [{"InternetGatewayId": "igw-1"}] if self.igw else []}


class FakeElb:
    def __init__(self, scheme="internal"):
        self.scheme = scheme

    def describe_load_balancers(self, **kw):
        return {"LoadBalancers": [{"Scheme": self.scheme, "DNSName": "internal-x.eu-west-1.elb.amazonaws.com"}]}


class FakeLambda:
    def __init__(self, body, fn_error=False):
        self.body, self.fn_error = body, fn_error

    def invoke(self, **kw):
        out = {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(self.body).encode())}
        if self.fn_error:
            out["FunctionError"] = "Unhandled"
        return out


def args(probe=None):
    return types.SimpleNamespace(vpc_id="vpc-1", alb_name="securecare-records", probe_lambda=probe)


def status(results):
    return {r["check"]: r["status"] for r in results}


def test_isolation_all_pass():
    r = pi.run(args("probe"), FakeEc2(), FakeElb(), FakeLambda({"status": 200}), call=lambda url: (False, "timeout"))
    assert set(status(r).values()) == {"PASS"}


def test_isolation_detects_public_alb_open_sg_igw_and_reachable():
    r = pi.run(args("probe"), FakeEc2(open_rule=True, igw=True), FakeElb("internet-facing"),
               FakeLambda({"status": 200}), call=lambda url: (True, "HTTP 200"))
    s = status(r)
    assert s["alb_scheme_internal"] == s["no_inbound_from_internet"] == s["no_internet_gateway"] == s["outside_call_fails"] == "FAIL"


def test_isolation_without_probe_is_skipped_not_passed():
    r = pi.run(args(), FakeEc2(), FakeElb(), call=lambda url: (False, "timeout"))
    assert status(r)["inside_call_works"] == "SKIPPED"


def test_inside_call_function_error_fails():
    r = pi.run(args("probe"), FakeEc2(), FakeElb(), FakeLambda({"status": 200}, fn_error=True), call=lambda url: (False, "x"))
    assert status(r)["inside_call_works"] == "FAIL"


def test_outside_call_refused_counts_as_not_reachable():
    assert pi.outside_call("http://127.0.0.1:9/health", timeout=1)[0] is False


def test_probe_lambda_reports_connection_error():
    out = probe_lambda.handler({"url": "http://127.0.0.1:9/"})
    assert out["status"] == 0


# ---------------------------------------------------------------- probe_lambda: authenticated mode

class _FakeSecretsClient:
    def __init__(self, values):
        self.values = values  # {(secret_id, stage): secret_string}
        self.calls = []

    def get_secret_value(self, SecretId, VersionStage):  # noqa: N803 - boto3's own casing
        self.calls.append((SecretId, VersionStage))
        if (SecretId, VersionStage) not in self.values:
            raise KeyError(f"no fake secret for {(SecretId, VersionStage)}")
        return {"SecretString": self.values[(SecretId, VersionStage)]}


class _FakeHTTPResponse:
    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self._body = body

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_old_url_only_mode_is_unchanged(monkeypatch):
    """The exact payload check 4 / prove_isolation.py sends must still take the plain-GET path."""
    seen = []

    def fake_urlopen(url_or_req, timeout=None):
        seen.append(url_or_req)
        assert isinstance(url_or_req, str)  # old mode: bare url string, not a Request
        return _FakeHTTPResponse(200, b"ok-body")

    monkeypatch.setattr(probe_lambda.urllib.request, "urlopen", fake_urlopen)
    out = probe_lambda.handler({"url": "http://x/health"})
    assert out == {"status": 200, "body": "ok-body"}
    assert seen == ["http://x/health"]


def test_probe_sends_token_as_header_and_never_returns_it(monkeypatch):
    fake_secrets = _FakeSecretsClient({("tok-arn", "AWSCURRENT"): "super-secret-value"})
    monkeypatch.setattr(probe_lambda.boto3, "client", lambda name: fake_secrets)

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return _FakeHTTPResponse(201, b"created")

    monkeypatch.setattr(probe_lambda.urllib.request, "urlopen", fake_urlopen)

    out = probe_lambda.handler(
        {
            "url": "http://x/patients/p1/notes",
            "method": "POST",
            "json_body": {"visit_date": "2026-01-01", "text": "t"},
            "token_secret_id": "tok-arn",
            "caller_id": "doc-1",
            "caller_role": "doctor",
        }
    )

    assert out == {"status": 201, "body": "created"}
    assert "super-secret-value" not in json.dumps(out)  # the result never carries the token
    assert captured["headers"]["X-internal-token"] == "super-secret-value"
    assert captured["headers"]["X-caller-id"] == "doc-1"
    assert captured["headers"]["X-caller-role"] == "doctor"
    assert captured["method"] == "POST"
    assert json.loads(captured["data"]) == {"visit_date": "2026-01-01", "text": "t"}
    assert fake_secrets.calls == [("tok-arn", "AWSCURRENT")]


def test_probe_uses_requested_token_stage(monkeypatch):
    fake_secrets = _FakeSecretsClient({("tok-arn", "AWSPREVIOUS"): "old-secret"})
    monkeypatch.setattr(probe_lambda.boto3, "client", lambda name: fake_secrets)
    monkeypatch.setattr(
        probe_lambda.urllib.request, "urlopen", lambda req, timeout=None: _FakeHTTPResponse(200, b"ok")
    )

    probe_lambda.handler(
        {"url": "http://x/health", "token_secret_id": "tok-arn", "token_stage": "AWSPREVIOUS"}
    )
    assert fake_secrets.calls == [("tok-arn", "AWSPREVIOUS")]


def test_probe_wrong_stage_is_handled_not_raised(monkeypatch):
    """Asking for a stage the fake secret doesn't have must come back as a handled error, not a crash."""
    fake_secrets = _FakeSecretsClient({("tok-arn", "AWSCURRENT"): "v"})
    monkeypatch.setattr(probe_lambda.boto3, "client", lambda name: fake_secrets)

    out = probe_lambda.handler(
        {"url": "http://x/health", "token_secret_id": "tok-arn", "token_stage": "AWSPREVIOUS"}
    )
    assert out["status"] == 0
    assert "error" in out
    assert "v" not in json.dumps(out)


# ---------------------------------------------------------------- recovery
def test_guard_refuses_other_clusters_and_services():
    with pytest.raises(SystemExit):
        rt.guard("paylogistics-ecs-cluster", "securecare-records")
    with pytest.raises(SystemExit):
        rt.guard("securecare", "other-service")
    rt.guard("securecare", "securecare-records")


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeEcs:
    def __init__(self, clock, new_after=40):
        self.clock, self.new_after, self.stopped = clock, new_after, None
        self.stop_at = None

    def list_tasks(self, **kw):
        if self.stop_at is not None and self.clock.t - self.stop_at >= self.new_after:
            return {"taskArns": ["arn:t/new"]}
        return {"taskArns": ["arn:t/old"]}

    def stop_task(self, **kw):
        self.stopped = kw["task"]
        self.stop_at = self.clock.t

    def describe_tasks(self, **kw):
        return {"tasks": [{"lastStatus": "RUNNING"}]}


class FakeTg:
    def __init__(self, clock, healthy_after=75):
        self.clock, self.healthy_after = clock, healthy_after

    def describe_target_health(self, **kw):
        old = {"Target": {"Id": "10.0.1.5"}, "TargetHealth": {"State": "healthy" if self.clock.t < 20 else "draining"}}
        out = [old]
        if self.clock.t >= 30:  # the replacement registers only after the old task is stopped
            state = "healthy" if self.clock.t >= self.healthy_after else "initial"
            out.append({"Target": {"Id": "10.0.1.9"}, "TargetHealth": {"State": state}})
        return {"TargetHealthDescriptions": out}


def test_recovery_times_are_measured():
    c = Clock()
    ecs = FakeEcs(c)
    a = types.SimpleNamespace(cluster="securecare", service="securecare-records", target_group_arn="arn:tg",
                              timeout=300, interval=5)
    r = rt.run(a, ecs, FakeTg(c), clock=c.now, sleep=c.sleep)
    assert ecs.stopped == "arn:t/old"
    assert 40 <= r["new_task_running_seconds"] <= 46
    assert 75 <= r["target_healthy_seconds"] <= 85


def test_recovery_timeout_gives_none():
    c = Clock()
    ecs = FakeEcs(c, new_after=10_000)
    a = types.SimpleNamespace(cluster="securecare", service="securecare-records", target_group_arn=None,
                              timeout=30, interval=5)
    assert rt.run(a, ecs, clock=c.now, sleep=c.sleep)["new_task_running_seconds"] is None


def test_dry_run_stops_nothing(capsys):
    assert rt.main(["--cluster", "securecare", "--service", "securecare-records"]) == 0
    assert "dry run" in capsys.readouterr().out
