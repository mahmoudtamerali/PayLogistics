import datetime
import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.abspath(os.path.join(ROOT, "scripts"))
sys.path.insert(0, SCRIPTS)
import image_gate as ig  # noqa: E402

TODAY = datetime.date(2026, 11, 11)


def vuln(vid, sev="HIGH", fixed="", pkg="zlib1g"):
    return {"VulnerabilityID": vid, "Severity": sev, "PkgName": pkg, "InstalledVersion": "1.0", "FixedVersion": fixed}


def report(*vulns):
    return {"Results": [{"Target": "img (debian 12)", "Vulnerabilities": list(vulns)}]}


# ------------------------------------------------------------ image gate (pure)
def test_unfixable_findings_do_not_block():
    b, a, u = ig.evaluate(report(vuln("CVE-1"), vuln("CVE-2", "CRITICAL")))
    assert (len(b), len(a), len(u)) == (0, 0, 2)


def test_fixable_high_and_critical_block():
    b, _, _ = ig.evaluate(report(vuln("CVE-1", "HIGH", "1.2"), vuln("CVE-2", "CRITICAL", "2.0"), vuln("CVE-3", "LOW", "9")))
    assert sorted(f["id"] for f in b) == ["CVE-1", "CVE-2"]


def test_empty_or_missing_results_pass():
    assert ig.evaluate({}) == ([], [], [])
    assert ig.evaluate({"Results": [{"Target": "x"}]}) == ([], [], [])


def test_accept_file_valid_and_expired():
    live, expired = ig.parse_accept("# comment\nCVE-1 2026-12-31 waiting for base image\nCVE-2 2026-01-01 old\n", TODAY)
    assert "CVE-1" in live and "CVE-2" in expired
    b, a, _ = ig.evaluate(report(vuln("CVE-1", "HIGH", "1"), vuln("CVE-2", "HIGH", "1")), live, expired)
    assert [f["id"] for f in a] == ["CVE-1"]
    assert b[0]["id"] == "CVE-2" and b[0]["expired"] is True


def test_accept_file_needs_reason_and_real_date():
    with pytest.raises(ValueError):
        ig.parse_accept("CVE-1 2026-12-31", TODAY)
    with pytest.raises(ValueError):
        ig.parse_accept("CVE-1 tomorrow because", TODAY)


def test_main_exit_codes(tmp_path, capsys):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(report(vuln("CVE-1"))))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(report(vuln("CVE-1", "HIGH", "1.1"))))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert ig.main(["--report", str(ok)], TODAY) == 0
    assert ig.main(["--report", str(bad)], TODAY) == 1
    assert ig.main(["--report", str(broken)], TODAY) == 2
    assert ig.main(["--report", str(tmp_path / "missing.json")], TODAY) == 2
    out = tmp_path / "gate.txt"
    ig.main(["--report", str(bad), "--out", str(out)], TODAY)
    assert "RESULT: BLOCKED" in out.read_text()


def test_scan_failure_is_not_a_pass():
    class P:
        returncode, stdout, stderr = 1, "", "boom"

    with pytest.raises(SystemExit):
        ig.scan("img", runner=lambda *a, **k: P())

    def missing(*a, **k):
        raise FileNotFoundError

    with pytest.raises(SystemExit):
        ig.scan("img", runner=missing)


# ------------------------------------------------------------ runner with fake CLIs
def script(path, body):
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def make_env(tmp_path, taskdef_user="10001", readonly="true", igw="[]", open_sg=False, running="", alb_scheme="internal",
             curl_ok=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    taskdef = json.dumps([{"name": "app", "user": taskdef_user, "readonlyRootFilesystem": readonly == "true",
                           "environment": [{"name": "LOG_LEVEL", "value": "info"}],
                           "secrets": [{"name": "TOKEN", "valueFrom": "arn"}], "caps": {"drop": ["ALL"]}}])
    sg = json.dumps([{"id": "sg-1", "inbound": [{"from": 80, "to": 80, "v4": ["0.0.0.0/0" if open_sg else "10.0.0.0/16"], "v6": []}]}])
    alb = json.dumps([{"name": "securecare-alb", "scheme": alb_scheme, "dns": "internal-x.eu-west-1.elb.amazonaws.com"}])
    script(bin_dir / "aws", f"""
        a="$1 $2"
        case "$a" in
          "sts get-caller-identity") echo '{{"Account":"123456789012"}}' ;;
          "ecs describe-task-definition") echo '{taskdef}' ;;
          "elbv2 describe-load-balancers")
              if printf '%s ' "$@" | grep -q -- '--names'; then echo '{alb}'; else echo '{running}'; fi ;;
          "ec2 describe-security-groups") echo '{sg}' ;;
          "ec2 describe-internet-gateways") echo '{igw}' ;;
          "ec2 describe-vpc-endpoints") echo '' ;;
          "ec2 describe-nat-gateways") echo '' ;;
          "ecs list-tasks") echo '{running}' ;;
          "cloudwatch get-dashboard") echo '{{"widgets":[{{"account":"123456789012"}}]}}' ;;
          "cloudwatch list-metrics") echo '["ScansInfected","DeniedRequests"]' ;;
          *) echo "unexpected: $*" >&2; exit 9 ;;
        esac
    """)
    script(bin_dir / "curl", "exit 0\n" if curl_ok else "echo 'timed out' >&2; exit 28\n")
    env_file = tmp_path / "gate3.env"
    env_file.write_text('REGION_CHECK=eu-west-1\nVPC_ID=vpc-1\nALB_NAME=securecare-alb\nCLUSTER=securecare\n'
                        'DASHBOARD=securecare\nTASKDEFS="securecare-records"\nLOCAL_IMAGES=""\nREAL_IMAGES="good:1"\n')
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GATE3_ENV=str(env_file), GATE3_OUT=str(tmp_path / "out"))
    return env


def run_runner(env, *checks):
    return subprocess.run(["bash", os.path.join(SCRIPTS, "gate3_runner.sh"), *checks], env=env, capture_output=True, text=True)


def test_runner_list_runs_nothing():
    r = subprocess.run(["bash", os.path.join(SCRIPTS, "gate3_runner.sh"), "--list"], capture_output=True, text=True)
    assert r.returncode == 0 and "EICAR" in r.stdout


def test_runner_refuses_without_config(tmp_path):
    env = dict(os.environ, GATE3_ENV=str(tmp_path / "nope.env"))
    r = run_runner(env)
    assert r.returncode == 2 and "gate3.env" in r.stderr


def test_runner_refuses_wrong_region_guard(tmp_path):
    env = make_env(tmp_path)
    open(env["GATE3_ENV"], "a").write("REGION_CHECK=us-east-1\n")
    assert run_runner(env, "3").returncode == 2


def test_checks_3_4_6_7_pass_on_good_stack(tmp_path):
    env = make_env(tmp_path)
    r = run_runner(env, "3", "4", "6", "7")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "out"
    assert "PASS  3" in (out / "00-summary.txt").read_text()
    for name in ("03-taskdef.json", "04-alb-scheme.txt", "04-sg-rules.txt", "04-no-igw.txt", "06-dashboard.json", "07-nothing-running.txt"):
        assert (out / name).exists(), name


def test_account_id_is_redacted(tmp_path):
    env = make_env(tmp_path)
    run_runner(env, "6")
    saved = (tmp_path / "out" / "06-dashboard.json").read_text()
    assert "123456789012" not in saved and "<ACCOUNT>" in saved


def test_check3_fails_on_root_or_writable(tmp_path):
    assert run_runner(make_env(tmp_path, taskdef_user=""), "3").returncode == 1


def test_check3_fails_on_writable_fs(tmp_path):
    assert run_runner(make_env(tmp_path, readonly="false"), "3").returncode == 1


def test_check4_fails_on_open_rule(tmp_path):
    assert run_runner(make_env(tmp_path, open_sg=True), "4").returncode == 1


def test_check4_fails_on_internet_gateway(tmp_path):
    assert run_runner(make_env(tmp_path, igw='["igw-1"]'), "4").returncode == 1


def test_check4_fails_when_internet_facing_or_curl_works(tmp_path):
    assert run_runner(make_env(tmp_path, alb_scheme="internet-facing"), "4").returncode == 1
    sub = tmp_path / "b"
    sub.mkdir()
    assert run_runner(make_env(sub, curl_ok=True), "4").returncode == 1


def test_check7_fails_when_something_runs(tmp_path):
    r = run_runner(make_env(tmp_path, running="arn:aws:ecs:task/abc"), "7")
    assert r.returncode == 1 and "destroy" in r.stdout


def test_check2_blocks_old_and_passes_good(tmp_path):
    env = make_env(tmp_path)
    script(tmp_path / "bin" / "trivy", f"""
        for a in "$@"; do last="$a"; done
        if [ "$last" = "python:3.8-slim" ]; then
          echo '{json.dumps(report(vuln("CVE-9", "HIGH", "1.1")))}'
        else
          echo '{json.dumps(report(vuln("CVE-8", "HIGH", "")))}'
        fi
    """)
    r = run_runner(env, "2")
    assert r.returncode == 0, r.stdout + r.stderr
    text = (tmp_path / "out" / "02-trivy-output.txt").read_text()
    assert "RESULT: BLOCKED" in text and "RESULT: PASS" in text


# ------------------------------------------------------------ gate3.env generator
def test_make_gate3_env_from_terraform_output():
    import make_gate3_env as m
    out = {"bucket_names": {"value": {"uploads": "securecare-uploads-1", "quarantine": "securecare-quarantine-1", "clean": "c"}},
           "vpc_id": {"value": "vpc-1"}, "probe_lambda": {"value": "securecare-probe"}}
    text = m.build(out)
    assert "UPLOADS_BUCKET=securecare-uploads-1" in text and "QUARANTINE_BUCKET=securecare-quarantine-1" in text
    assert "VPC_ID=vpc-1" in text and "PROBE_LAMBDA=securecare-probe" in text and "REGION_CHECK=eu-west-1" in text
    assert "PROBE_LAMBDA" not in m.build({"vpc_id": {"value": "vpc-1"}, "probe_lambda": {"value": ""}})
