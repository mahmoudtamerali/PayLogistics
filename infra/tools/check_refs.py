"""Offline sanity checker for the Terraform in this repo (no Terraform binary needed).

It is NOT a replacement for `terraform validate`. It catches the typo class of mistakes:
  - HCL syntax errors
  - var./local./resource/data/module references that point at nothing
  - module calls that miss a required variable or pass an unknown one
  - module.<name>.<output> that the module does not declare
Run:  python3 tools/check_refs.py
"""
import glob
import os
import re
import sys

import hcl2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []


def q(x):
    """Newer python-hcl2 keeps the quotes around block labels; drop them."""
    return x.strip('"') if isinstance(x, str) else x


def unq(d):
    return {q(k): v for k, v in d.items()}


def parse_dir(path):
    decl = dict(variables={}, locals=set(), resources=set(), data=set(), outputs=set(), modules={}, text="")
    for f in sorted(glob.glob(os.path.join(path, "*.tf"))):
        src = open(f).read()
        try:
            d = hcl2.loads(src)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{f}: syntax error: {str(e)[:200]}")
            continue
        decl["text"] += "\n" + src
        for v in d.get("variable", []):
            (name, body), = unq(v).items()
            decl["variables"][name] = "default" in body
        for l in d.get("locals", []):
            decl["locals"].update(k for k in l if k != "__is_block__")
        for r in d.get("resource", []):
            (rt, rest), = unq(r).items()
            for rn in unq(rest):
                decl["resources"].add(f"{rt}.{rn}")
        for r in d.get("data", []):
            (rt, rest), = unq(r).items()
            for rn in unq(rest):
                decl["data"].add(f"{rt}.{rn}")
        for o in d.get("output", []):
            decl["outputs"].update(q(k) for k in o)
        for m in d.get("module", []):
            (mn, body), = unq(m).items()
            decl["modules"][mn] = {k: (q(v) if isinstance(v, str) else v) for k, v in body.items()}
    return decl


def strip(text):
    text = re.sub(r"<<-?(\w+)\n.*?\n\s*\1", "", text, flags=re.S)   # heredocs
    text = re.sub(r"(?m)^\s*(#|//).*$", "", text)                   # comment lines
    text = re.sub(r"\s#.*$", "", text, flags=re.M)                  # trailing comments
    return text


def check_dir(path, label):
    d = parse_dir(path)
    t = strip(d["text"])
    for m in set(re.findall(r"\bvar\.(\w+)", t)):
        if m not in d["variables"]:
            errors.append(f"{label}: var.{m} is not declared")
    for m in set(re.findall(r"\blocal\.(\w+)", t)):
        if m not in d["locals"]:
            errors.append(f"{label}: local.{m} is not declared")
    for rt, rn in set(re.findall(r"(?<![\w.])(aws_\w+)\.(\w+)", t)):
        if f"{rt}.{rn}" not in d["resources"]:
            errors.append(f"{label}: reference to {rt}.{rn} but no such resource")
    for rt, rn in set(re.findall(r"\bdata\.(aws_\w+)\.(\w+)", t)):
        if f"{rt}.{rn}" not in d["data"]:
            errors.append(f"{label}: reference to data.{rt}.{rn} but no such data source")
    return d


mods = {}
for p in sorted(glob.glob(os.path.join(ROOT, "modules", "*"))):
    mods[os.path.basename(p)] = check_dir(p, f"modules/{os.path.basename(p)}")

for p in sorted(glob.glob(os.path.join(ROOT, "examples", "*"))):
    label = f"examples/{os.path.basename(p)}"
    d = check_dir(p, label)
    t = strip(d["text"])
    for mn, body in d["modules"].items():
        src = body.get("source", "")
        target = os.path.basename(src.rstrip("/"))
        if target not in mods:
            errors.append(f"{label}: module {mn} source {src} not found")
            continue
        md = mods[target]
        passed = {k for k in body if k not in ("source", "providers", "depends_on", "count", "for_each", "__is_block__", "__comments__")}
        for k in passed:
            if k not in md["variables"]:
                errors.append(f"{label}: module {mn} passes unknown variable {k}")
        for k, has_default in md["variables"].items():
            if not has_default and k not in passed:
                errors.append(f"{label}: module {mn} misses required variable {k}")
    for mn, out in set(re.findall(r"\bmodule\.(\w+)\.(\w+)", t)):
        if mn not in d["modules"]:
            errors.append(f"{label}: module.{mn} is not called")
            continue
        target = os.path.basename(d["modules"][mn].get("source", "").rstrip("/"))
        if target in mods and out not in mods[target]["outputs"]:
            errors.append(f"{label}: module.{mn}.{out} but module {target} has no such output")

n_files = len(glob.glob(os.path.join(ROOT, "**", "*.tf"), recursive=True))
if errors:
    print("PROBLEMS:")
    print("\n".join(" - " + e for e in errors))
    sys.exit(1)
print(f"OK: {n_files} .tf files parsed, all references resolve")
