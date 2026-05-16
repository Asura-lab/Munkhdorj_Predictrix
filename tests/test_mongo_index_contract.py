from pathlib import Path
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_APP_PATH = ROOT_DIR / "backend" / "app.py"


class MongoIndexContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = BACKEND_APP_PATH.read_text(encoding="utf-8")

    def test_users_email_unique_index_present(self):
        self.assertIn("_ensure_index(users_collection, 'email', name='uniq_users_email', unique=True)", self.content)

    def test_verification_indexes_present(self):
        self.assertIn(
            "_ensure_index(verification_codes, 'email', name='uniq_verification_email', unique=True)",
            self.content,
        )
        self.assertIn(
            "_ensure_index(verification_codes, 'expires_at', name='ttl_verification_expires', expireAfterSeconds=0)",
            self.content,
        )

    def test_reset_indexes_present(self):
        self.assertIn("_ensure_index(reset_codes, 'email', name='uniq_reset_email', unique=True)", self.content)
        self.assertIn(
            "_ensure_index(reset_codes, 'expires_at', name='ttl_reset_expires', expireAfterSeconds=0)",
            self.content,
        )

    def test_signal_idempotency_unique_index_present(self):
        self.assertIn("name='uniq_signals_timestamp_symbol_timeframe_auto'", self.content)
        self.assertIn("[('timestamp', 1), ('symbol', 1), ('timeframe', 1)]", self.content)
        self.assertIn("partialFilterExpression={'source': 'auto', 'signal': {'$in': ['BUY', 'SELL']}}", self.content)

    def test_auto_signal_upsert_present(self):
        self.assertIn("signals_collection.update_one(dedupe_filter, update_doc, upsert=True)", self.content)


if __name__ == "__main__":
    unittest.main()
