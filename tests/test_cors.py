"""Test for dev CORS configuration (audit item D5).

In development the app must reflect the request Origin (not return a literal "*") so that
allow_credentials=True is valid: browsers reject "*" combined with credentials. The tests
run with PRODUCTION unset, i.e. the development CORS branch.
"""


def test_dev_cors_reflects_origin_with_credentials(test_client):
    resp = test_client.get("/health", headers={"Origin": "http://localhost:3000"})

    # Origin is reflected, not "*", so credentials are allowed per the CORS spec.
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp.headers.get("access-control-allow-credentials") == "true"
