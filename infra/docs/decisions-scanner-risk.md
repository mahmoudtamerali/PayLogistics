# Decision: accept the remaining vulnerabilities in the scanner image

Date: 2026-11-21. Owner: Container and monitoring (Saif). Status: **accepted** for the demo and the Gate 3 and Gate 4 reviews.

Sign-off: accepted by the project owner on 2026-11-21. The team lead's own sign-off is treated as given by the project owner; add the lead's name and date here when the lead confirms.

## What was found
- Trivy scan of the scanner image on 2026-11-11: 112 HIGH or CRITICAL findings (109 HIGH, 3 CRITICAL), all in Debian OS packages (examples: libsqlite3-0 CVE-2025-7458, libxml2 CVE-2026-6653, zlib1g CVE-2023-45853). None has a fixed version, so rebuilding cannot remove them. The Python dependencies have no findings.
- The image gate (`scripts/image_gate.py`) passed on 2026-11-20: an old image with fixable findings is blocked, and the real images pass. The gate blocks only HIGH or CRITICAL findings that have a fix.
- ClamAV links libxml2 and zlib (checked with `ldd`), so a crafted upload could reach those two libraries.

## Why we accept it
The scanner is the one component that must open untrusted files, so some exposure cannot be avoided. The damage is limited by how it runs, and these limits were checked on AWS on 2026-11-20:
- non-root user 10001, read-only root filesystem, all Linux capabilities dropped (check 3, task definition);
- no internet and no NAT (check 4: internal load balancer, no internet gateway, no 0.0.0.0/0);
- no inbound connections, a task role limited to the uploads, clean and quarantine buckets and its own queue;
- files over 25 MB are rejected; synthetic data only.

## What could still go wrong
A file that exploits the scanner could make it call an infected file clean, or crash it. A crash is visible (the message goes to the dead-letter queue and an alarm fires). A wrong "clean" is not visible. In a real clinic that would be serious, so this acceptance is for a demo with synthetic data only.

## Controls and review
- The image gate blocks any HIGH or CRITICAL finding that has a fix.
- The scanner image is rebuilt every week so fixes and new ClamAV signatures arrive (`.github/workflows/scanner-weekly-rebuild.yml`; DevSecOps owns the schedule).
- The image is rescanned before the final demo. This acceptance is reviewed at that rescan and at Gate 4.
- Before any real use: a distroless or newer base image, a second scanner engine, and a human check of the clean bucket.
