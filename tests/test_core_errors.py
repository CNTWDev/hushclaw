from __future__ import annotations

import unittest

from hushclaw.core.errors import classify_error
from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import _with_retry


class TestCoreErrorClassification(unittest.TestCase):
    def test_ssl_unexpected_eof_is_retryable(self):
        exc = ProviderError(
            "Request failed: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] "
            "EOF occurred in violation of protocol (_ssl.c:1032)>"
        )
        recovery = classify_error(exc)
        self.assertTrue(recovery.retryable)
        self.assertFalse(recovery.is_auth_failure)
        self.assertFalse(recovery.should_compress)


class TestProviderRetry(unittest.IsolatedAsyncioTestCase):
    async def test_with_retry_retries_unexpected_eof(self):
        attempts = 0

        async def _fn():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ProviderError(
                    "Request failed: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] "
                    "EOF occurred in violation of protocol (_ssl.c:1032)>"
                )
            return "ok"

        result = await _with_retry(_fn, max_retries=2, base_delay=0)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)
