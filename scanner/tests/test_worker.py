import json

import pytest

from scanner import clamd_client, config, worker

from .conftest import FakeClamd, VIRUS_MARKER, keys, s3_event


def send(sqs, body):
    sqs.send_message(QueueUrl=config.QUEUE_URL, MessageBody=body)


def queue_depth(sqs):
    a = sqs.get_queue_attributes(QueueUrl=config.QUEUE_URL, AttributeNames=["All"])["Attributes"]
    return int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])


def test_clean_file_goes_to_clean_bucket(aws, clamd):
    s3, sqs = aws
    s3.put_object(Bucket="t-uploads", Key="u/abc", Body=b"harmless synthetic file")
    send(sqs, s3_event("u/abc"))
    assert worker.run_once() == 1
    assert keys(s3, "t-clean") == ["u/abc"]
    assert keys(s3, "t-uploads") == [] and keys(s3, "t-quarantine") == []
    assert queue_depth(sqs) == 0


def test_infected_file_goes_to_quarantine(aws, clamd, capsys):
    s3, sqs = aws
    s3.put_object(Bucket="t-uploads", Key="u/bad", Body=b"xx" + VIRUS_MARKER)
    send(sqs, s3_event("u/bad"))
    worker.run_once()
    assert keys(s3, "t-quarantine") == ["u/bad"]
    assert keys(s3, "t-clean") == [] and keys(s3, "t-uploads") == []
    out = capsys.readouterr().out
    assert '"ScansInfected":1' in out and "Fake.Test.Signature" in out
    assert queue_depth(sqs) == 0


def test_url_encoded_keys_are_decoded(aws, clamd):
    s3, sqs = aws
    s3.put_object(Bucket="t-uploads", Key="u/a b+c", Body=b"ok")
    send(sqs, s3_event("u/a+b%2Bc"))
    worker.run_once()
    assert keys(s3, "t-clean") == ["u/a b+c"]


def test_too_large_goes_to_quarantine_unscanned(aws, clamd, monkeypatch):
    s3, sqs = aws
    monkeypatch.setattr(config, "MAX_BYTES", 10)
    s3.put_object(Bucket="t-uploads", Key="u/big", Body=b"x" * 11)
    send(sqs, s3_event("u/big"))
    worker.run_once()
    assert keys(s3, "t-quarantine") == ["u/big"]


def test_clamd_size_limit_reply_goes_to_quarantine(aws, monkeypatch):
    s3, sqs = aws
    server = FakeClamd(max_len=5)
    server.start()
    monkeypatch.setattr(config, "CLAMD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "CLAMD_PORT", server.port)
    s3.put_object(Bucket="t-uploads", Key="u/big", Body=b"x" * 50)
    send(sqs, s3_event("u/big"))
    worker.run_once()
    server.stop()
    assert keys(s3, "t-quarantine") == ["u/big"]


def test_clamd_failure_never_releases_file_and_keeps_message(aws, monkeypatch, capsys):
    s3, sqs = aws
    server = FakeClamd(fail=True)
    server.start()
    monkeypatch.setattr(config, "CLAMD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "CLAMD_PORT", server.port)
    s3.put_object(Bucket="t-uploads", Key="u/x", Body=b"data")
    send(sqs, s3_event("u/x"))
    worker.run_once()
    server.stop()
    assert keys(s3, "t-uploads") == ["u/x"]            # still waiting
    assert keys(s3, "t-clean") == [] and keys(s3, "t-quarantine") == []
    assert queue_depth(sqs) == 1                        # will be retried / go to DLQ
    assert '"ScansFailed":1' in capsys.readouterr().out


def test_clamd_down_is_a_failure_not_a_pass(aws, monkeypatch):
    s3, sqs = aws
    monkeypatch.setattr(config, "CLAMD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "CLAMD_PORT", 1)        # nothing listens here
    s3.put_object(Bucket="t-uploads", Key="u/x", Body=b"data")
    send(sqs, s3_event("u/x"))
    worker.run_once()
    assert keys(s3, "t-clean") == [] and keys(s3, "t-uploads") == ["u/x"]
    assert queue_depth(sqs) == 1


def test_missing_object_is_treated_as_already_handled(aws, clamd):
    s3, sqs = aws
    send(sqs, s3_event("u/gone"))
    worker.run_once()
    assert queue_depth(sqs) == 0 and keys(s3, "t-clean") == []


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"Event": "s3:TestEvent"}),
        json.dumps([1, 2]),
        json.dumps({"Records": [{"eventName": "ObjectRemoved:Delete"}]}),
        json.dumps({"Records": [{"eventName": "ObjectCreated:Put"}]}),
        s3_event("u/x", bucket="some-other-bucket"),
    ],
)
def test_unrecognised_messages_are_dropped_not_retried_forever(aws, clamd, body):
    s3, sqs = aws
    send(sqs, body)
    worker.run_once()
    assert queue_depth(sqs) == 0
    assert keys(s3, "t-clean") == []


def test_heartbeat_file_is_written(aws, clamd, tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(config, "HEARTBEAT_FILE", str(hb))
    worker.run_once()
    assert hb.exists()


def test_parse_reply():
    assert clamd_client.parse_reply("stream: OK").status == "clean"
    v = clamd_client.parse_reply("stream: Eicar-Signature FOUND")
    assert (v.status, v.signature) == ("infected", "Eicar-Signature")
    assert clamd_client.parse_reply("INSTREAM size limit exceeded. ERROR").status == "too_large"
    with pytest.raises(clamd_client.ClamdError):
        clamd_client.parse_reply("something ERROR")


def test_run_forever_survives_queue_errors(aws, monkeypatch):
    """A broken connection to SQS must not crash the worker: it backs off and retries."""
    from botocore.exceptions import EndpointConnectionError

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise EndpointConnectionError(endpoint_url="https://sqs.example")
        raise KeyboardInterrupt  # stop the loop once it has survived two failures

    monkeypatch.setattr(worker, "run_once", flaky)
    monkeypatch.setattr(config, "QUEUE_ERROR_BACKOFF", 0)
    monkeypatch.setattr(config, "HEARTBEAT_FILE", "/tmp/hb-test")
    with pytest.raises(KeyboardInterrupt):
        worker.run_forever()
    assert calls["n"] == 3


def test_queue_connection_errors_do_not_stop_the_worker(monkeypatch, tmp_path):
    from botocore.exceptions import EndpointConnectionError

    monkeypatch.setattr(config, "HEARTBEAT_FILE", str(tmp_path / "hb"))
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise EndpointConnectionError(endpoint_url="https://sqs.example")

    class Stop(Exception):
        pass

    def stop_after_two(_seconds):
        if calls["n"] >= 2:
            raise Stop()

    monkeypatch.setattr(worker, "run_once", flaky)
    monkeypatch.setattr(worker.time, "sleep", stop_after_two)
    monkeypatch.setattr(config, "QUEUE_URL", "https://sqs.example/queue")
    with pytest.raises(Stop):
        worker.run_forever()
    assert calls["n"] >= 2  # it kept trying instead of crashing on the first error
