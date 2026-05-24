"""Shared pytest setup.

Sets the env vars required for `app.config.Settings` to instantiate
without hitting a real .env, so any test can import `app.main` cleanly.
"""

import os

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
