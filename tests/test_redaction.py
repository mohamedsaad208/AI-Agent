"""Build output is stored and re-sent, so credential shapes must not survive capture."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.redaction import redact


class RedactionTests(unittest.TestCase):
    def test_known_credential_shapes_are_removed(self):
        gh_tok = "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
        aws_key = "AK" + "IAIOSFODNN7EXAMPLE"
        sk_key = "sk-" + "proj-abcdefghijklmnopqrstuvwx"
        rsa_key = "-----" + "BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----" + "END RSA PRIVATE KEY-----"
        cases = {
            f"AWS_ACCESS_KEY_ID={aws_key}": aws_key,
            f"remote: {gh_tok}": gh_tok,
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIx"
            "n0K.abcdefghijklMNO1234567890": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "jdbc:mysql://svc:Sup3rS3cretValue@db.internal/app": "Sup3rS3cretValue",
            f"OPENAI_API_KEY = '{sk_key}'": sk_key,
            rsa_key: "MIIEowIBAAKCAQEA",
        }
        for line, secret in cases.items():
            with self.subTest(line=line[:40]):
                cleaned = redact(line)
                self.assertNotIn(secret, cleaned)
                self.assertIn("[redacted]", cleaned)

    def test_ordinary_build_output_survives(self):
        for line in ("Tests run: 8, Failures: 0, Errors: 0, Skipped: 0",
                     "[INFO] BUILD SUCCESS",
                     "tokenCount = 12  # not a credential",
                     'password: "${VAULT_PASSWORD}"',
                     "File \"app.py\", line 12, in main",
                     "user@host:~$ mvn -B test",
                     "username = \"admin\"  # the account name, not a secret"):
            with self.subTest(line=line):
                self.assertEqual(redact(line), line)

    def test_env_var_style_names_are_scrubbed_too(self):
        """`\b` does not fire after an underscore, and build logs shout these names."""
        aws_sec = "MockSecretAccessKeyVal" + "Example123456789"
        sk_key = "sk-" + "proj-abcdefghijklmnopqrstuvwx"
        cases = {
            f"AWS_SECRET_ACCESS_KEY={aws_sec}":
                aws_sec,
            "SPRING_DATASOURCE_PASSWORD=Sup3rS3cretValue": "Sup3rS3cretValue",
            "DB_PASSWORD=hunter2hunter2": "hunter2hunter2",
            "SLACK_SIGNING_SECRET=1234567890abcdef1234567890abcdef": "1234567890abcdef1234567890abcdef",
            "MY_APP_TOKEN=abcdefghijklmno": "abcdefghijklmno",
            f"export OPENAI_API_KEY='{sk_key}'": sk_key,
        }
        for line, secret in cases.items():
            with self.subTest(line=line[:48]):
                cleaned = redact(line)
                self.assertNotIn(secret, cleaned)
                self.assertIn("[redacted]", cleaned)
                name = line.replace("export ", "").split("=")[0].split(":")[0].strip()
                self.assertIn(name, cleaned, "the name survives, the value does not")

    def test_the_shapes_no_env_name_could_carry(self):
        slack_tok = "xo" + "xb-1234567890-1234567890123-abcdefghijklmnopqrstuvwx"
        google_tok = "AI" + "zaSyA1234567890abcdefghij123456789012"
        gitlab_tok = "gl" + "pat-ABCDEFGHIJKLMNOPQRSTUV12"
        for line, secret in (
                (f"SLACK_TOKEN={slack_tok}",
                 slack_tok),
                # AIza plus exactly 35 characters, which is the shape of a real Google key.
                (f"lookup returned {google_tok}",
                 google_tok),
                (f"GITLAB_TOKEN={gitlab_tok}", gitlab_tok)):
            with self.subTest(line=line[:40]):
                self.assertNotIn(secret, redact(line))

    def test_a_qualifier_does_not_turn_ordinary_words_into_secrets(self):
        for line in ("tokenCount = 12", "token_expiry_seconds = 900", "connection_timeout = 30000",
                     "passwordless login is configured", "SecretService started",
                     "ACCESS_TOKEN_TTL_MILLIS=900000 is the default"):
            with self.subTest(line=line):
                self.assertEqual(redact(line), line)

    def test_redaction_is_idempotent_and_keeps_context(self):
        once = redact("LoginTest failure: expected 401 but password='hunter22' was accepted")
        self.assertEqual(redact(once), once)
        self.assertIn("LoginTest failure", once)
        self.assertIn("expected 401", once)
        self.assertIn("was accepted", once)
        self.assertNotIn("hunter22", once)


if __name__ == "__main__":
    unittest.main()
