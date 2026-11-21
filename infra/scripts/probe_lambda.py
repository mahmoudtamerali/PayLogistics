"""Throwaway probe for T56. Deploy it as a Lambda in the PRIVATE subnets, with a security group the ALB allows.
It does one GET and returns the status. Delete the function after the test.
Event: {"url": "http://<alb-dns>/health"}   (health needs no token)"""
import json
import urllib.error
import urllib.request


def handler(event, context=None):
    try:
        with urllib.request.urlopen(event["url"], timeout=5) as r:  # noqa: S310
            return {"status": r.status, "body": r.read(200).decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "error": type(e).__name__}
