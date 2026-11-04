import asyncio
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

import rate_limiter as rate_limiter_mod
import mock_providers
from mock_providers import primary_app, secondary_app
from rate_limiter import init_db, check_rate_limit, TOKENS_PER_MINUTE

# Use a temp DB for each test to avoid cross-test contamination
TEST_DB = "test_rate_limiter.db"

# Patch DB_PATH at import time so the router's lifespan uses it too
rate_limiter_mod.DB_PATH = TEST_DB


@pytest.fixture(autouse=True)
async def setup_db():
    """Create a fresh DB before each test and clean up after."""
    rate_limiter_mod.DB_PATH = TEST_DB
    await init_db(TEST_DB)
    yield
    # Clean out all rows so tests don't leak state
    import aiosqlite
    async with aiosqlite.connect(TEST_DB) as db:
        await db.execute("DELETE FROM token_usage")
        await db.commit()


def cleanup_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

import atexit
atexit.register(cleanup_db)


@pytest.fixture(autouse=True)
def reset_mock_providers():
    """Reset mock provider flags before each test."""
    mock_providers.primary_return_429 = False
    mock_providers.primary_timeout_seconds = 0
    yield


def auth_header(api_key: str = "tenant-123") -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def make_request(content: str = "Hello, how are you?") -> dict:
    return {"messages": [{"role": "user", "content": content}]}


# Patch the router to use in-process mock providers instead of real HTTP
@pytest.fixture(autouse=True)
def patch_router(monkeypatch):
    import router

    monkeypatch.setattr(router, "PRIMARY_TIMEOUT", 3.0)
    monkeypatch.setattr("rate_limiter.DB_PATH", TEST_DB)

    async def patched_call_provider(url: str, body: dict, timeout: float):
        """Route to in-process mock apps instead of real HTTP."""
        if "8001" in url or "primary" in url.lower():
            transport = ASGITransport(app=primary_app)
        else:
            transport = ASGITransport(app=secondary_app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat/completions",
                json=body,
                timeout=timeout,
            )

    # For timeout tests, we need a version that respects the timeout
    original_call = patched_call_provider

    async def timeout_aware_call(url: str, body: dict, timeout: float):
        if ("8001" in url or "primary" in url.lower()) and mock_providers.primary_timeout_seconds > 0:
            import httpx
            try:
                result = await asyncio.wait_for(
                    original_call(url, body, timeout),
                    timeout=timeout,
                )
                return result
            except asyncio.TimeoutError:
                raise httpx.TimeoutException("Timed out")
        return await original_call(url, body, timeout)

    monkeypatch.setattr(router, "call_provider", timeout_aware_call)


# ============================================================
# 1. Rate Limiter — sliding window + token tracking
# ============================================================

class TestRateLimiter:

    @pytest.mark.anyio
    async def test_allows_under_limit(self):
        allowed, remaining = await check_rate_limit("key-a", 1000, TEST_DB)
        assert allowed is True
        assert remaining == TOKENS_PER_MINUTE - 1000

    @pytest.mark.anyio
    async def test_rejects_over_limit(self):
        # Use up the budget
        await check_rate_limit("key-a", 45_000, TEST_DB)
        # This should push over
        allowed, remaining = await check_rate_limit("key-a", 10_000, TEST_DB)
        assert allowed is False

    @pytest.mark.anyio
    async def test_separate_tenants_independent(self):
        await check_rate_limit("key-a", 45_000, TEST_DB)
        # Different tenant should still have full budget
        allowed, _ = await check_rate_limit("key-b", 45_000, TEST_DB)
        assert allowed is True

    @pytest.mark.anyio
    async def test_window_eviction(self):
        """Old entries outside the 60s window should be evicted."""
        import aiosqlite
        import rate_limiter

        # Manually insert an old entry (90 seconds ago)
        async with aiosqlite.connect(TEST_DB) as db:
            old_time = time.time() - 90
            await db.execute(
                "INSERT INTO token_usage (api_key, tokens, timestamp) VALUES (?, ?, ?)",
                ("key-a", 40_000, old_time),
            )
            await db.commit()

        # This should succeed because the old entry is outside the window
        allowed, _ = await check_rate_limit("key-a", 40_000, TEST_DB)
        assert allowed is True

    @pytest.mark.anyio
    async def test_exact_limit_allowed(self):
        """Requesting exactly the remaining budget should be allowed."""
        await check_rate_limit("key-a", 30_000, TEST_DB)
        allowed, remaining = await check_rate_limit("key-a", 20_000, TEST_DB)
        assert allowed is True
        assert remaining == 0

    @pytest.mark.anyio
    async def test_one_token_over_rejected(self):
        """One token over the limit should be rejected."""
        await check_rate_limit("key-a", TOKENS_PER_MINUTE, TEST_DB)
        allowed, _ = await check_rate_limit("key-a", 1, TEST_DB)
        assert allowed is False


# ============================================================
# 2. Fallback Mechanics
# ============================================================

class TestFallback:

    @pytest.mark.anyio
    async def test_primary_success(self):
        """Normal case — primary returns a response."""
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["model"] == "primary-model"

    @pytest.mark.anyio
    async def test_fallback_on_429(self):
        """Primary returns 429 — should fallback to secondary."""
        mock_providers.primary_return_429 = True
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["model"] == "secondary-model"

    @pytest.mark.anyio
    async def test_fallback_on_timeout(self):
        """Primary times out — should fallback to secondary."""
        mock_providers.primary_timeout_seconds = 5  # longer than the 3s timeout
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
                timeout=15.0,  # give the test itself enough time
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["model"] == "secondary-model"


# ============================================================
# 3. Error Sanitization
# ============================================================

class TestErrorSanitization:

    @pytest.mark.anyio
    async def test_missing_api_key(self):
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/completions", json=make_request())
        data = resp.json()
        assert resp.status_code == 401
        assert data["error"]["type"] == "gateway_error"
        assert "API key" in data["error"]["message"]

    @pytest.mark.anyio
    async def test_rate_limit_error_format(self):
        """Rate limit errors use the standardized format."""
        import router
        # Exhaust the budget
        for _ in range(10):
            await check_rate_limit("tenant-123", 10_000, TEST_DB)

        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
            )
        data = resp.json()
        assert resp.status_code == 429
        assert data["error"]["type"] == "gateway_error"
        assert "Rate limit" in data["error"]["message"]

    @pytest.mark.anyio
    async def test_error_does_not_leak_internals(self):
        """Error responses must not contain stack traces or upstream details."""
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/completions", json=make_request())
        body = resp.text
        assert "Traceback" not in body
        assert "localhost" not in body
        assert "8001" not in body
        assert "httpx" not in body

    @pytest.mark.anyio
    async def test_invalid_json_body(self):
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                content=b"not json",
                headers={**auth_header(), "Content-Type": "application/json"},
            )
        data = resp.json()
        assert resp.status_code == 400
        assert data["error"]["type"] == "gateway_error"


# ============================================================
# Evaluation Criteria Tests
# ============================================================


class TestAsyncConcurrencyAndTimeouts:
    """Criterion 1: Async concurrency handling and timeout race conditions."""

    @pytest.mark.anyio
    async def test_concurrent_requests_different_tenants(self):
        """Multiple tenants making concurrent requests should not interfere."""
        import router
        transport = ASGITransport(app=router.app)

        async def make_call(api_key: str):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/v1/chat/completions",
                    json=make_request(),
                    headers={"Authorization": f"Bearer {api_key}"},
                )

        results = await asyncio.gather(
            make_call("tenant-a"),
            make_call("tenant-b"),
            make_call("tenant-c"),
        )
        for resp in results:
            assert resp.status_code == 200
            assert resp.json()["model"] == "primary-model"

    @pytest.mark.anyio
    async def test_concurrent_requests_same_tenant_within_limit(self):
        """Same tenant making concurrent requests that fit within the limit."""
        import router
        transport = ASGITransport(app=router.app)

        async def make_call():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/v1/chat/completions",
                    json=make_request("short"),
                    headers=auth_header(),
                )

        results = await asyncio.gather(*[make_call() for _ in range(5)])
        success_count = sum(1 for r in results if r.status_code == 200)
        assert success_count >= 1

    @pytest.mark.anyio
    async def test_timeout_does_not_block_other_requests(self):
        """A timing-out primary should not block a subsequent request."""
        mock_providers.primary_timeout_seconds = 5
        import router
        transport = ASGITransport(app=router.app)

        start = time.perf_counter()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
                timeout=15.0,
            )
        elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        assert resp.json()["model"] == "secondary-model"
        assert elapsed < 4.5, f"Took {elapsed:.2f}s — timeout not enforced"

    @pytest.mark.anyio
    async def test_timeout_measured_in_seconds(self):
        """Verify the timeout is actually ~3 seconds, not instant or way too long."""
        mock_providers.primary_timeout_seconds = 10
        import router
        transport = ASGITransport(app=router.app)

        start = time.perf_counter()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
                timeout=15.0,
            )
        elapsed = time.perf_counter() - start

        assert resp.json()["model"] == "secondary-model"
        assert 2.5 < elapsed < 4.5, f"Elapsed {elapsed:.2f}s — expected ~3s timeout"


class TestRateLimiterEvictionAndTracking:
    """Criterion 2: Accurate rate-limiter state eviction and token tracking logic."""

    @pytest.mark.anyio
    async def test_eviction_removes_old_rows(self):
        """After eviction, old rows should be gone from the database."""
        import aiosqlite

        async with aiosqlite.connect(TEST_DB) as db:
            now = time.time()
            await db.execute(
                "INSERT INTO token_usage (api_key, tokens, timestamp) VALUES (?, ?, ?)",
                ("key-a", 10_000, now - 120),
            )
            await db.execute(
                "INSERT INTO token_usage (api_key, tokens, timestamp) VALUES (?, ?, ?)",
                ("key-a", 5_000, now - 30),
            )
            await db.commit()

        await check_rate_limit("key-a", 1, TEST_DB)

        async with aiosqlite.connect(TEST_DB) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM token_usage WHERE api_key = 'key-a'")
            row = await cursor.fetchone()
            assert row[0] == 2

    @pytest.mark.anyio
    async def test_token_tracking_accuracy(self):
        """Token counts should be tracked precisely across multiple requests."""
        import aiosqlite

        await check_rate_limit("key-a", 10_000, TEST_DB)
        await check_rate_limit("key-a", 15_000, TEST_DB)
        await check_rate_limit("key-a", 5_000, TEST_DB)

        async with aiosqlite.connect(TEST_DB) as db:
            cursor = await db.execute(
                "SELECT SUM(tokens) FROM token_usage WHERE api_key = 'key-a'"
            )
            row = await cursor.fetchone()
            assert row[0] == 30_000

    @pytest.mark.anyio
    async def test_rejected_request_not_recorded(self):
        """A rejected request should NOT consume any of the budget."""
        import aiosqlite

        await check_rate_limit("key-a", 45_000, TEST_DB)
        allowed, _ = await check_rate_limit("key-a", 10_000, TEST_DB)
        assert allowed is False

        async with aiosqlite.connect(TEST_DB) as db:
            cursor = await db.execute(
                "SELECT SUM(tokens) FROM token_usage WHERE api_key = 'key-a'"
            )
            row = await cursor.fetchone()
            assert row[0] == 45_000

    @pytest.mark.anyio
    async def test_remaining_tokens_reported_accurately(self):
        """The remaining token count should reflect exact usage."""
        _, remaining1 = await check_rate_limit("key-a", 20_000, TEST_DB)
        assert remaining1 == 30_000

        _, remaining2 = await check_rate_limit("key-a", 10_000, TEST_DB)
        assert remaining2 == 20_000

        _, remaining3 = await check_rate_limit("key-a", 20_000, TEST_DB)
        assert remaining3 == 0


class TestFallbackAndErrorSanitization:
    """Criterion 3: Graceful fallback mechanics and standardized error sanitization."""

    @pytest.mark.anyio
    async def test_fallback_response_is_valid(self):
        """Fallback response should have the same structure as a primary response."""
        mock_providers.primary_return_429 = True
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
            )
        data = resp.json()
        assert "choices" in data
        assert "usage" in data
        assert data["choices"][0]["message"]["role"] == "assistant"

    @pytest.mark.anyio
    async def test_all_error_responses_have_consistent_shape(self):
        """Every error type should return the same {error: {type, message}} shape."""
        import router
        transport = ASGITransport(app=router.app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/v1/chat/completions", json=make_request())

        for _ in range(10):
            await check_rate_limit("tenant-err", 10_000, TEST_DB)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r2 = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers={"Authorization": "Bearer tenant-err"},
            )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r3 = await client.post(
                "/v1/chat/completions",
                content=b"bad",
                headers={**auth_header(), "Content-Type": "application/json"},
            )

        for resp in [r1, r2, r3]:
            data = resp.json()
            assert "error" in data
            assert data["error"]["type"] == "gateway_error"
            assert "message" in data["error"]

    @pytest.mark.anyio
    async def test_upstream_error_details_never_exposed(self):
        """Even when upstream fails, response must not contain internal details."""
        mock_providers.primary_return_429 = True
        import router
        transport = ASGITransport(app=router.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=make_request(),
                headers=auth_header(),
            )
        body = resp.text
        assert "Traceback" not in body
        assert "localhost:8001" not in body
