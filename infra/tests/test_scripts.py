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
