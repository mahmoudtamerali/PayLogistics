#!/usr/bin/env python3
"""Image gate: fail the build only on HIGH or CRITICAL findings that have a fix available.

Why: a finding with no fixed version cannot be repaired by rebuilding, so failing on it blocks every build
for nothing. A finding that has a fix is a real, avoidable risk, so it blocks. Unfixable findings are still
counted and printed, so they stay visible and go into the risk decision.

Use it in a pipeline step (it runs Trivy for you):

  python3 scripts/image_gate.py --image securecare-records:test
  python3 scripts/image_gate.py --image securecare-scanner:dev --out evidence/gate3/02-trivy-output.txt

or on a report you already have (trivy image --format json --output report.json IMAGE):

  python3 scripts/image_gate.py --report report.json

Exit code: 0 pass, 1 blocked by fixable findings, 2 could not run (no trivy, bad report). A broken scan
never counts as a pass.

Accepting a fixable finding on purpose (for example a fix that is not released for our base image yet):
put one line per finding in an accept file and pass --accept-file. Format:

  CVE-2025-1234  2026-12-31  reason in plain words

The line only works until the date; after that the finding blocks again. This stops a quick fix from
becoming permanent. Findings with no fix never need a line.
"""
import argparse
import datetime
import json
import subprocess
import sys

BLOCKING = ("HIGH", "CRITICAL")


def parse_accept(text, today):
    """Return {vuln_id: (expiry_date, reason)} for lines that have not expired; expired ids are returned
    in the second value so the report can say why a finding blocks again."""
    live, expired = {}, {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            raise ValueError(f"accept file line {n}: need '<id> <YYYY-MM-DD> <reason>'")
        vid, date_s, reason = parts
        try:
            until = datetime.date.fromisoformat(date_s)
        except ValueError:
            raise ValueError(f"accept file line {n}: '{date_s}' is not a date like 2026-12-31") from None
        (live if until >= today else expired)[vid] = (until, reason)
    return live, expired


def findings(report):
    """Flatten a Trivy JSON report into a list of dicts for HIGH and CRITICAL findings."""
    out = []
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            if v.get("Severity") in BLOCKING:
                out.append({
                    "id": v.get("VulnerabilityID", "?"),
                    "pkg": v.get("PkgName", "?"),
                    "installed": v.get("InstalledVersion", "?"),
                    "fixed": (v.get("FixedVersion") or "").strip(),
                    "severity": v["Severity"],
                    "target": result.get("Target", ""),
                })
    return out


def evaluate(report, accept=None, expired=None):
    """Split findings into blocking, accepted and unfixable. Pure function, easy to test."""
    accept, expired = accept or {}, expired or {}
    blocking, accepted, unfixable = [], [], []
    for f in findings(report):
        if not f["fixed"]:
            unfixable.append(f)
        elif f["id"] in accept:
            accepted.append(f)
        else:
            f = dict(f, expired=f["id"] in expired)
            blocking.append(f)
    return blocking, accepted, unfixable


def render(image, blocking, accepted, unfixable):
    lines = [f"Image gate for {image}",
             f"  blocking (fixable HIGH/CRITICAL): {len(blocking)}",
             f"  accepted on purpose (until their date): {len(accepted)}",
             f"  not fixable today (listed, do not block): {len(unfixable)}"]
    for f in blocking:
        note = "  [acceptance expired]" if f.get("expired") else ""
        lines.append(f"  BLOCK {f['severity']:<8} {f['id']}  {f['pkg']} {f['installed']} -> fix {f['fixed']}{note}")
    if unfixable:
        crit = sum(1 for f in unfixable if f["severity"] == "CRITICAL")
        lines.append(f"  note: {len(unfixable)} unfixable findings ({crit} CRITICAL). Rebuild weekly and re-check; "
                     "see the risk acceptance in the project docs.")
    lines.append("RESULT: " + ("BLOCKED" if blocking else "PASS"))
    return "\n".join(lines)


def scan(image, runner=subprocess.run):
    cmd = ["trivy", "image", "--quiet", "--format", "json", "--severity", ",".join(BLOCKING),
           "--scanners", "vuln", image]
    try:
        proc = runner(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("trivy is not installed; the gate cannot run (this is not a pass)") from None
    if proc.returncode != 0:
        raise SystemExit(f"trivy failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def main(argv=None, today=None, runner=subprocess.run):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image")
    src.add_argument("--report")
    p.add_argument("--accept-file")
    p.add_argument("--out")
    args = p.parse_args(argv)
    today = today or datetime.date.today()
    try:
        accept, expired = {}, {}
        if args.accept_file:
            with open(args.accept_file) as fh:
                accept, expired = parse_accept(fh.read(), today)
        if args.report:
            with open(args.report) as fh:
                report = json.load(fh)
        else:
            report = scan(args.image, runner)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"gate could not run: {exc}", file=sys.stderr)
        return 2
    blocking, accepted, unfixable = evaluate(report, accept, expired)
    text = render(args.image or args.report, blocking, accepted, unfixable)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
