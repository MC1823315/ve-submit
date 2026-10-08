import os
from pathlib import Path
import stat
import tempfile
import unittest

from ve_submit.secrets import (
    SecretFileError,
    VE_API_KEY,
    YUKON_API_TOKEN,
    configured_values,
    dotenv_path,
    has_secret,
    lookup,
    parse_dotenv,
)


class ParseTests(unittest.TestCase):
    def test_assignments_comments_and_quotes(self):
        values = parse_dotenv(
            "\n".join([
                "# comment",
                "export YUKON_API_TOKEN=yukon-token",
                "VE_API_KEY=\"ve key\"",
                "IGNORED",
                "EMPTY=",
            ])
        )
        self.assertEqual(values["YUKON_API_TOKEN"], "yukon-token")
        self.assertEqual(values["VE_API_KEY"], "ve key")
        self.assertEqual(values["EMPTY"], "")

    def test_environment_wins_over_file_and_blank_is_missing(self):
        file_values = {YUKON_API_TOKEN: "from-file", VE_API_KEY: "ve"}
        self.assertEqual(lookup(YUKON_API_TOKEN, {YUKON_API_TOKEN: "from-env"}, file_values), "from-env")
        self.assertEqual(lookup(YUKON_API_TOKEN, {YUKON_API_TOKEN: "  "}, file_values), "from-file")
        self.assertTrue(has_secret(VE_API_KEY, {}, file_values))
        self.assertFalse(has_secret(VE_API_KEY, {}, {}))


class FileTests(unittest.TestCase):
    def test_default_file_is_outside_the_checkout(self):
        home = Path("/tmp/ve-submit-home")
        path, explicit = dotenv_path({}, home)
        self.assertFalse(explicit)
        self.assertEqual(path, home / ".config" / "ve-submit" / ".env")
        self.assertNotEqual(path.parent, Path.cwd())

    def test_missing_default_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(configured_values({}, Path(directory)), {})

    def test_private_file_is_read_and_world_readable_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw).resolve()
            path = directory / "keys.env"
            path.write_text("YUKON_API_TOKEN=yukon\nVE_API_KEY=ve\n", encoding="utf-8")
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            values = configured_values({"VE_SUBMIT_ENV": str(path)}, directory)
            self.assertEqual(values[YUKON_API_TOKEN], "yukon")
            self.assertEqual(values[VE_API_KEY], "ve")
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
            with self.assertRaises(SecretFileError):
                configured_values({"VE_SUBMIT_ENV": str(path)}, directory)

    def test_missing_explicit_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.env"
            with self.assertRaises(SecretFileError):
                configured_values({"VE_SUBMIT_ENV": str(missing)}, Path(directory))


if __name__ == "__main__":
    unittest.main()
