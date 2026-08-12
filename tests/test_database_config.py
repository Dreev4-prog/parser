import os
import subprocess
import sys
import unittest

from db_url import normalize_database_url


class DatabaseConfigTests(unittest.TestCase):
    def _run(self, env_overrides):
        env = os.environ.copy()
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-c", "import db; print(db.DATABASE_URL); print(db.DATABASE_BACKEND)"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            text=True,
            capture_output=True,
        )

    def test_railway_requires_database_url(self):
        result = self._run({"RAILWAY_PROJECT_ID": "test-project", "DATABASE_URL": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL is required on Railway", result.stderr)

    def test_postgresql_url_is_normalized(self):
        self.assertEqual(
            normalize_database_url("postgresql://user:pass@host:5432/db"),
            "postgresql+asyncpg://user:pass@host:5432/db",
        )
        self.assertEqual(
            normalize_database_url("postgres://user:pass@host:5432/db"),
            "postgresql+asyncpg://user:pass@host:5432/db",
        )

    def test_sslmode_is_normalized_for_asyncpg(self):
        self.assertEqual(
            normalize_database_url("postgresql://u:p@host/db?sslmode=require"),
            "postgresql+asyncpg://u:p@host/db?ssl=require",
        )

    def test_sqlite_is_rejected_on_railway(self):
        result = self._run({
            "RAILWAY_PROJECT_ID": "test-project",
            "DATABASE_URL": "sqlite+aiosqlite:///./bad.db",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires PostgreSQL on Railway", result.stderr)


if __name__ == "__main__":
    unittest.main()
