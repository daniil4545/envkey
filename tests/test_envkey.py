import json
import os
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVKEY = os.path.join(ROOT, "bin", "envkey")
GUARD = os.path.join(ROOT, "hooks", "guard.py")
SECRET = "FAKE_SECRET_123456"


class EnvkeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.work = os.path.join(self.root, "repo")
        os.mkdir(self.work)
        subprocess.run(["git", "init", "-q", self.work], check=True)
        self.write(".env\n", ".gitignore")
        self.stub_dir = os.path.join(self.root, "stubs")
        os.mkdir(self.stub_dir)
        self.env = dict(os.environ, PATH=self.stub_dir + os.pathsep + os.environ["PATH"])

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name=".env"):
        return os.path.join(self.work, name)

    def write(self, text, name=".env"):
        with open(self.path(name), "w", newline="") as handle:
            handle.write(text)

    def read(self):
        with open(self.path(), newline="") as handle:
            return handle.read()

    def envkey(self, *args, stdin="", cwd=None):
        return subprocess.run([ENVKEY, *args], input=stdin, capture_output=True, text=True,
                              cwd=cwd or self.work, env=self.env, timeout=30)

    def stub_osascript(self, script):
        stub = os.path.join(self.stub_dir, "osascript")
        with open(stub, "w") as handle:
            handle.write("#!/bin/sh\n" + script + "\n")
        os.chmod(stub, stat.S_IRWXU)

    # ls

    def test_ls_shows_public_values_only(self):
        self.write(f"# public\nPORT=8080\nTOKEN={SECRET}\n")
        result = self.envkey("ls")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PORT\t8080\nTOKEN\t<secret>\n")
        self.assertNotIn(SECRET, result.stderr)

    def test_search_goes_up_to_git_root_only(self):
        self.write("A=1\n")
        sub = os.path.join(self.work, "a", "b")
        os.makedirs(sub)
        self.assertEqual(self.envkey("-C", sub, "ls").stdout, "A\t<secret>\n")
        with open(os.path.join(self.root, ".env"), "w") as handle:
            handle.write("OUTSIDE=1\n")
        os.remove(self.path())
        self.assertEqual(self.envkey("ls").returncode, 2)

    # run

    def test_run_masks_secrets_keeps_public_and_exit_code(self):
        self.write(f"# public\nHOST=localhost\nTOKEN={SECRET}\n")
        result = self.envkey("run", "--", "sh", "-c", 'echo "$HOST $TOKEN"; echo "$TOKEN" >&2; exit 7')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "localhost [hidden TOKEN]\n")
        self.assertEqual(result.stderr, "[hidden TOKEN]\n")

    def test_run_missing_command_is_127(self):
        self.write("A=1\n")
        self.assertEqual(self.envkey("run", "--", "no-such-command-xyz").returncode, 127)

    def test_run_without_file_is_2(self):
        self.assertEqual(self.envkey("run", "--", "true").returncode, 2)

    # set

    def test_set_secret_from_stdin(self):
        result = self.envkey("set", "TOKEN", stdin=SECRET + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read(), f"TOKEN={SECRET}\n")
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)

    def test_set_secret_without_stdin_uses_dialog(self):
        self.stub_osascript(f"echo {SECRET}")
        result = self.envkey("set", "TOKEN")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read(), f"TOKEN={SECRET}\n")

    def test_dialog_cancel_leaves_file(self):
        self.write("A=1\n")
        self.stub_osascript("exit 1")
        self.assertEqual(self.envkey("set", "TOKEN").returncode, 1)
        self.assertEqual(self.read(), "A=1\n")

    def test_secret_value_not_accepted_as_argument(self):
        self.assertEqual(self.envkey("set", "TOKEN", SECRET).returncode, 2)

    def test_set_public_and_update_in_place(self):
        self.write("# comment\r\nA=1\r\nB=2\r\n")
        self.assertEqual(self.envkey("set", "--public", "B", "debug").returncode, 0)
        self.assertEqual(self.read(), "# comment\r\nA=1\r\n# public\r\nB=debug\r\n")
        self.assertEqual(self.envkey("set", "B", stdin="secret-77\n").returncode, 0)
        self.assertEqual(self.read(), "# comment\r\nA=1\r\nB=secret-77\r\n")

    def test_mark_existing_public_keeps_value(self):
        self.write("PORT=8080\n")
        self.assertEqual(self.envkey("set", "--public", "PORT").returncode, 0)
        self.assertEqual(self.read(), "# public\nPORT=8080\n")
        self.assertEqual(self.envkey("set", "--public", "MISSING").returncode, 2)

    def test_set_rejects_values_readers_parse_differently(self):
        for value in ["a$b", "a #b", '"q"', " p", "a\\", "x\ny"]:
            result = self.envkey("set", "K", stdin=value + "\n")
            self.assertEqual(result.returncode, 2, value)
        self.assertFalse(os.path.exists(self.path()))

    def test_set_refuses_file_not_ignored_by_git(self):
        os.remove(self.path(".gitignore"))
        self.assertEqual(self.envkey("set", "K", stdin=SECRET).returncode, 2)
        self.assertFalse(os.path.exists(self.path()))
        self.write("A=1\n")
        self.assertEqual(self.envkey("set", "K", stdin=SECRET).returncode, 2)
        self.assertEqual(self.read(), "A=1\n")

    # rm, mv

    def test_rm_and_mv_carry_the_public_mark(self):
        self.write("# public\nA=1\nB=2\n")
        self.assertEqual(self.envkey("mv", "A", "C").returncode, 0)
        self.assertEqual(self.read(), "# public\nC=1\nB=2\n")
        self.assertEqual(self.envkey("rm", "C").returncode, 0)
        self.assertEqual(self.read(), "B=2\n")
        self.assertEqual(self.envkey("rm", "C").returncode, 2)

    def test_duplicate_names_are_not_edited(self):
        self.write("A=1\nA=2\n")
        self.assertEqual(self.envkey("rm", "A").returncode, 2)
        self.assertEqual(self.read(), "A=1\nA=2\n")


class GuardTest(unittest.TestCase):
    def decide(self, tool, **tool_input):
        event = json.dumps({"tool_name": tool, "tool_input": tool_input})
        result = subprocess.run(["python3", GUARD], input=event, capture_output=True, text=True)
        return "deny" in result.stdout

    def test_file_tools(self):
        self.assertTrue(self.decide("Read", file_path="/p/.env"))
        self.assertTrue(self.decide("Edit", file_path="/p/src/.env"))
        self.assertTrue(self.decide("Read", file_path="/p/secrets/partner-db.env"))
        self.assertFalse(self.decide("Read", file_path="/p/.env.example"))
        self.assertFalse(self.decide("Read", file_path="/p/.gitignore"))

    def test_bash(self):
        for command in ["cat .env", "grep TOKEN src/.env", "awk -F= '{print $2}' ./.env", "source .env",
                        "cat secrets/partner-db.env | sed 's/=.*PASS.*/=<>/'", "grep KEY accounts/anastasia.env"]:
            self.assertTrue(self.decide("Bash", command=command), command)
        for command in ["envkey ls", "envkey run -- make test", "cp .env.example tmp",
                        "grep -q '^\\.env$' .gitignore", "ls -la", "cat .envrc", "make env-check"]:
            self.assertFalse(self.decide("Bash", command=command), command)


if __name__ == "__main__":
    unittest.main()
