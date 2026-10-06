"""Тесты обёртки ключей: временные каталоги, фиктивные значения, фейк `security` в PATH."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

REAL_GIT = shutil.which("git")

import envfile  # noqa: E402


def load_script(name):
    """Скрипт без расширения как модуль, чтобы подменять os.replace внутри процесса."""
    loader = importlib.machinery.SourceFileLoader("keys_" + name, os.path.join(HERE, name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

FAKE_SECURITY = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_SEC_DIR"]
argv = sys.argv[1:]
entry = {"argv": argv}
if argv[:1] == ["add-generic-password"]:
    entry["stdin"] = sys.stdin.read()
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps(entry) + "\n")
with open(os.path.join(d, "items.json")) as f:
    items = json.load(f)
cmd = argv[0] if argv else ""
if cmd == "dump-keychain":
    for key in items:
        svce, acct = key.split("|", 1)
        print('    "acct"<blob>="%s"' % acct)
        print('    "svce"<blob>="%s"' % svce)
elif cmd == "find-generic-password":
    svce, acct = argv[argv.index("-s") + 1], argv[argv.index("-a") + 1]
    key = svce + "|" + acct
    if key not in items:
        sys.exit(44)
    print(items[key])
sys.exit(0)
'''

FAKE_GIT = '''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GIT_LOG"
exec "$REAL_GIT" "$@"
'''


class Sandbox(unittest.TestCase):
    """Каталог теста, фейки `security` и `git` в PATH, запуск скриптов среза."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="keys-test-"))
        self.sec = os.path.join(self.root, "sec")
        self.bin = os.path.join(self.root, "bin")
        self.work = os.path.join(self.root, "work")
        for d in (self.sec, self.bin, self.work):
            os.mkdir(d)
        self.set_keychain({})
        self.fake(os.path.join(self.bin, "security"), FAKE_SECURITY)
        self.fake(os.path.join(self.bin, "git"), FAKE_GIT)
        self.git_log = os.path.join(self.root, "git.log")
        self.env = dict(
            os.environ,
            PATH=self.bin + os.pathsep + os.environ["PATH"],
            FAKE_SEC_DIR=self.sec,
            FAKE_GIT_LOG=self.git_log,
            REAL_GIT=REAL_GIT,
        )

    def fake(self, path, text):
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)

    def set_keychain(self, items):
        """items: {(svce, acct): значение}"""
        with open(os.path.join(self.sec, "items.json"), "w") as f:
            json.dump({"%s|%s" % k: v for k, v in items.items()}, f)

    def security_log(self):
        path = os.path.join(self.sec, "log.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def run_cmd(self, script, *args, stdin=b"", env=None):
        return subprocess.run(
            [os.path.join(HERE, script), *args],
            input=stdin, capture_output=True, env=env or self.env, timeout=30,
        )

    def path(self, name=".env"):
        return os.path.join(self.work, name)

    def write(self, data, name=".env"):
        if isinstance(data, str):
            data = data.encode()
        with open(self.path(name), "wb") as f:
            f.write(data)

    def read(self, name=".env"):
        with open(self.path(name), "rb") as f:
            return f.read()

    def git_init(self, ignore=True):
        subprocess.run([REAL_GIT, "init", "-q", self.work], check=True)
        if ignore:
            self.write(".env\n", ".gitignore")


class LegacyKeychainMode(Sandbox):
    def test_legacy_withkey_keychain_mode_unchanged(self):
        self.set_keychain({("ctx/x/svc", "TOKEN_ABC"): "value-123456"})
        r = self.run_cmd(
            "withkey", "ctx/x", "--", "sh", "-c", 'echo "$TOKEN_ABC"; echo err >&2; exit 3'
        )
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stdout.decode(), "[скрыто TOKEN_ABC]\n")
        self.assertTrue(r.stderr.decode().endswith("err\n"), r.stderr)

    def test_legacy_keyls_prefix_output(self):
        self.set_keychain({
            ("ctx/x/svc", "B_KEY"): "1", ("ctx/x/svc", "A_KEY"): "2",
            ("ctx/y/svc", "C_KEY"): "3", ("plain", "D_KEY"): "4",
        })
        r = self.run_cmd("keyls", "ctx/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode(), "%-40s %s\n%-40s %s\n" % (
            "ctx/x/svc", "A_KEY", "ctx/x/svc", "B_KEY"))
        r = self.run_cmd("keyls")
        self.assertEqual(len(r.stdout.decode().splitlines()), 3)


def keyset_stdin(sb, *args, value=b"secret-val-1\n"):
    return sb.run_cmd("keyset", "-f", sb.work, *args, stdin=value)


class EnvfileUnit(Sandbox):
    def test_find_walks_up_to_git_root(self):
        self.git_init()
        sub = os.path.join(self.work, "a", "b")
        os.makedirs(sub)
        self.write("K=abcdef12\n")
        path, _ = envfile.find_file(sub)
        self.assertEqual(path, self.path())
        # вне git ищется только в самом DIR, файл уровнем выше не берётся
        outside = os.path.join(self.root, "plain")
        os.makedirs(os.path.join(outside, "sub"))
        with open(os.path.join(outside, ".env"), "w") as f:
            f.write("K=abcdef12\n")
        path, searched = envfile.find_file(os.path.join(outside, "sub"))
        self.assertIsNone(path)
        self.assertEqual(searched, [os.path.join(outside, "sub", ".env")])

    def test_find_missing_exit2_path(self):
        r = self.run_cmd("keyls", "-f", self.work)
        self.assertEqual(r.returncode, 2)
        self.assertIn(self.path(), r.stderr.decode())
        r = self.run_cmd("keyls", "-f", os.path.join(self.work, "нет"))
        self.assertEqual(r.returncode, 2)

    def test_load_line_forms_and_public_marker(self):
        text = (
            "# comment\n\nexport A=1\nB=\"two words\"\nC='single'\nD=v # c\nE=\nF=a=b==\n"
            "G=  spaced  \n# public\nP=pub\n# public x\nQ=1\n# public\n\nR=1\n# public\nS=1\r\n"
        )
        got = envfile.Document("/x/.env", text).effective()
        self.assertEqual(got["A"], ("1", False))
        self.assertEqual(got["B"], ("two words", False))
        self.assertEqual(got["C"], ("single", False))
        self.assertEqual(got["D"], ("v", False))
        self.assertEqual(got["E"], ("", False))
        self.assertEqual(got["F"], ("a=b==", False))
        self.assertEqual(got["G"], ("spaced", False))
        self.assertEqual(got["P"], ("pub", True))
        self.assertEqual(got["Q"], ("1", False))  # иная строка снимает метку
        self.assertEqual(got["R"], ("1", False))  # пустая строка снимает метку
        self.assertEqual(got["S"], ("1", True))   # CRLF: \r снят
        for bad in ("K = secret88\n", "1A=secret88\n", "just secret88\n", "A-B=secret88\n"):
            with self.assertRaises(envfile.EnvError) as ctx:
                envfile.Document("/x/.env", "OK=1\n" + bad)
            self.assertIn("строка 2", str(ctx.exception))
            self.assertNotIn("secret88", str(ctx.exception))

    def test_duplicate_name_rules(self):
        original = "K=first111\nX=1\nK=second22\n"
        self.write(original)
        doc = envfile.Document.load(self.path())
        self.assertEqual(doc.effective()["K"][0], "second22")
        self.assertEqual(sorted(v for v, n in doc.secret_values() if n == "K"), ["first111", "second22"])
        for cmd in (("keyset", "K"), ("keydel", "K"), ("keymv", "K", "Z")):
            args = ("-f", self.work) + cmd[1:]
            r = self.run_cmd(cmd[0], *args, stdin=b"newvalue1\n")
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("K в строках 1, 3", r.stderr.decode())
            self.assertEqual(self.read(), original.encode())
        r = self.run_cmd("withkey", "-f", self.work, "--", "sh", "-c", "echo $K first111")
        self.assertEqual(r.stdout.decode(), "[скрыто K] [скрыто K]\n")

    def test_edit_preserves_foreign_lines_byte_exact(self):
        self.git_init()
        orig = "# top comment\n\nA=\"quoted val\"\nexport B='x'  # note\n\n# public\nP=pub\nC=3\n"
        self.write(orig)
        self.assertEqual(keyset_stdin(self, "NEW", value=b"newval11\n").returncode, 0)
        self.assertEqual(self.read(), (orig + "NEW=newval11\n").encode())
        self.assertEqual(keyset_stdin(self, "C", value=b"c333333\n").returncode, 0)
        self.assertEqual(self.run_cmd("keymv", "-f", self.work, "A", "A2").returncode, 0)
        self.assertEqual(self.run_cmd("keydel", "-f", self.work, "B").returncode, 0)
        want = ("# top comment\n\nA2=\"quoted val\"\n\n# public\nP=pub\nC=c333333\nNEW=newval11\n")
        self.assertEqual(self.read(), want.encode())
        # файл без \n в конце: чужие строки те же, запись отдельной строкой
        self.write("# c\nA=1")
        self.assertEqual(keyset_stdin(self, "NEW", value=b"newval11\n").returncode, 0)
        self.assertEqual(self.read(), b"# c\nA=1\nNEW=newval11\n")
        # CRLF: окончание сохраняется и у новой строки
        self.write("A=1\r\nB=2\r\n")
        keyset_stdin(self, "B", value=b"bbbbbb2\n")
        keyset_stdin(self, "C", value=b"cccccc3\n")
        self.assertEqual(self.read(), b"A=1\r\nB=bbbbbb2\r\nC=cccccc3\r\n")

    def test_write_failure_keeps_file(self):
        original = b"# keep\nA=1\nB=2\n"
        self.write(original)
        secret = "must-not-leak-9"
        for name, argv, stdin in (
            ("keyset", ["-f", self.work, "A"], secret + "\n"),
            ("keydel", ["-f", self.work, "A"], ""),
        ):
            module = load_script(name)
            err = io.StringIO()
            fake_stdin = io.TextIOWrapper(io.BytesIO(stdin.encode()))
            with mock.patch("envfile.os.replace", side_effect=OSError(28, "No space left")), \
                    mock.patch.object(sys, "stdin", fake_stdin), \
                    contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                module.main(argv)
            self.assertNotEqual(ctx.exception.code, 0, name)
            self.assertEqual(self.read(), original, name)
            self.assertEqual(sorted(os.listdir(self.work)), [".env"], name)
            self.assertNotIn(secret, err.getvalue(), name)

    def test_symlink_target_written(self):
        target = os.path.join(self.root, "real-keys")
        with open(target, "w") as f:
            f.write("A=1\n")
        os.symlink(target, self.path())
        self.assertEqual(keyset_stdin(self, "B", value=b"bbbbbb2\n").returncode, 0)
        self.assertTrue(os.path.islink(self.path()))
        with open(target) as f:
            self.assertEqual(f.read(), "A=1\nB=bbbbbb2\n")
        self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o600")


class WithkeyFile(Sandbox):
    def test_withkey_masks_secret_in_stdout_and_stderr(self):
        self.write("K=abcdef12\nK2=abcdef12xyz\nK3=abcde\n# public\nPUB=publicval1\n")
        script = 'echo "$K $K2 $K3 $PUB"; echo "$K2 $K" >&2'
        r = self.run_cmd("withkey", "-f", self.work, "--", "sh", "-c", script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode(), "[скрыто K] [скрыто K2] abcde publicval1\n")
        self.assertEqual(r.stderr.decode(), "[скрыто K2] [скрыто K]\n")

    def test_withkey_keychain_overrides_file_and_masks(self):
        self.write("K=filevalue1\nOTHER=otherval11\n")
        self.set_keychain({("ctx/x/svc", "K"): "kcvalue77"})
        r = self.run_cmd("withkey", "-f", self.work, "ctx/x", "--", "sh", "-c",
                         'echo "$K $OTHER"; echo filevalue1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode(), "[скрыто K] [скрыто OTHER]\n[скрыто K]\n")

    def test_withkey_exit_code_and_127(self):
        self.write("K=abcdef12\n")
        r = self.run_cmd("withkey", "-f", self.work, "--", "sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        r = self.run_cmd("withkey", "-f", self.work, "--", "no-such-command-xyz")
        self.assertEqual(r.returncode, 127)

    def test_withkey_prod_needs_reader(self):
        self.set_keychain({("ctx/x/svc", "K"): "kcvalue77"})
        env = {k: v for k, v in self.env.items() if k != "WITHKEY_PROD_READER"}
        r = self.run_cmd("withkey", "--prod", "ctx/x", "--", "true", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"WITHKEY_PROD_READER", r.stderr)

    def test_withkey_prod_with_f_exit2(self):
        self.write("K=abcdef12\n")
        r = self.run_cmd("withkey", "--prod", "-f", self.work, "ctx/x", "--", "true")
        self.assertEqual(r.returncode, 2)

    def test_file_mode_never_calls_security(self):
        self.git_init()
        steps = [
            ("keyset", ["-f", self.work, "K"], b"abcdef12\n"),
            ("keyset", ["-f", self.work, "--public", "P", "pubvalue1"], b""),
            ("keyls", ["-f", self.work], b""),
            ("keymv", ["-f", self.work, "K", "K2"], b""),
            ("withkey", ["-f", self.work, "--", "true"], b""),
            ("keydel", ["-f", self.work, "K2"], b""),
        ]
        for name, args, stdin in steps:
            r = self.run_cmd(name, *args, stdin=stdin)
            self.assertEqual(r.returncode, 0, (name, r.stderr))
        self.assertEqual(self.security_log(), [])

    def test_bad_line_exit2_no_value(self):
        for bad in ("BROKEN секрет99\n", "1BAD=секрет99\n"):
            self.write("OK=1\n" + bad)
            for name, args in (
                ("keyls", ["-f", self.work]),
                ("withkey", ["-f", self.work, "--", "true"]),
                ("keyset", ["-f", self.work, "K"]),
            ):
                r = self.run_cmd(name, *args, stdin=b"newvalue1\n")
                err = r.stderr.decode()
                self.assertEqual(r.returncode, 2, (name, bad, err))
                self.assertIn("строка 2", err)
                self.assertNotIn("секрет99", err)
                self.assertNotIn("секрет99", r.stdout.decode())


class KeysetKeyls(Sandbox):
    def test_keyls_f_public_and_secret(self):
        self.write("# public\nPUB=hello\nSEC=hidden-1\nEMPTY=\n")
        r = self.run_cmd("keyls", "-f", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode(), "PUB\thello\nSEC\t<секрет>\nEMPTY\t<секрет>\n")
        self.assertIn(self.path(), r.stderr.decode())

    def test_keyset_stdin_value_not_in_argv_mode_0600(self):
        self.git_init()
        value = "argv-probe-77"
        script = os.path.join(HERE, "keyset")
        proc = subprocess.Popen([script, "-f", self.work, "K"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        ps = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(proc.pid)],
                            capture_output=True, text=True).stdout
        proc.communicate((value + "\n").encode(), timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(value, ps)
        with open(self.git_log) as log:
            self.assertNotIn(value, log.read())
        self.assertEqual(self.read(), ("K=%s\n" % value).encode())
        self.assertEqual(oct(os.stat(self.path()).st_mode & 0o777), "0o600")

    def test_keyset_updates_in_place(self):
        self.write("A=1\nB=2\nC=3\n")
        self.assertEqual(keyset_stdin(self, "B", value=b"bbbbbb2\n").returncode, 0)
        self.assertEqual(self.read(), b"A=1\nB=bbbbbb2\nC=3\n")

    def test_keyset_new_file_needs_gitignore(self):
        self.git_init(ignore=False)
        r = keyset_stdin(self, "K")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.path()))
        self.write(".env\n", ".gitignore")
        self.assertEqual(keyset_stdin(self, "K").returncode, 0)
        self.assertEqual(oct(os.stat(self.path()).st_mode & 0o777), "0o600")
        # вне git проверки нет
        plain = os.path.join(self.root, "plain")
        os.mkdir(plain)
        r = self.run_cmd("keyset", "-f", plain, "K", stdin=b"abcdef12\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(plain, ".env")))

    def test_keyset_refuses_tracked_file(self):
        self.git_init(ignore=False)
        self.write("A=1\n")
        r = keyset_stdin(self, "K")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.read(), b"A=1\n")

    def test_keyset_public_marker(self):
        self.git_init()
        r = self.run_cmd("keyset", "-f", self.work, "--public", "K", "pubvalue1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(), b"# public\nK=pubvalue1\n")
        keyset_stdin(self, "K", value=b"secret-val-1\n")
        self.assertEqual(self.read(), b"K=secret-val-1\n")
        self.run_cmd("keyset", "-f", self.work, "--public", "K", "pubvalue2")
        self.assertEqual(self.read(), b"# public\nK=pubvalue2\n")

    def test_marker_does_not_leak_to_neighbor(self):
        self.write("# public\nA=1\nB=секрет\n")
        self.run_cmd("keydel", "-f", self.work, "A")
        self.assertEqual(self.read(), "B=секрет\n".encode())
        self.write("# public\nA=1\nB=секрет\n")
        self.run_cmd("keymv", "-f", self.work, "A", "C")
        r = self.run_cmd("keyls", "-f", self.work)
        self.assertEqual(r.stdout.decode(), "C\t1\nB\t<секрет>\n")
        for tail in ("A=1\n# public", "A=1\n# public\n"):
            self.write(tail)
            self.assertEqual(keyset_stdin(self, "NEW", value=b"newsecret\n").returncode, 0)
            r = self.run_cmd("keyls", "-f", self.work)
            self.assertEqual(r.stdout.decode(), "A\t<секрет>\nNEW\t<секрет>\n")
            r = self.run_cmd("withkey", "-f", self.work, "--", "sh", "-c", "echo $NEW")
            self.assertEqual(r.stdout.decode(), "[скрыто NEW]\n")

    def test_keyset_value_rules(self):
        accepted = ["a=b==", "x y"]
        rejected = ['"q"', "a #b", " p", "p ", "a$b", "a\nb", "a\rb", ""]
        for value in accepted:
            self.write("A=1\n")
            r = keyset_stdin(self, "K", value=value.encode() + b"\n")
            self.assertEqual(r.returncode, 0, (value, r.stderr))
            out = os.path.join(self.work, "out")
            r = self.run_cmd("withkey", "-f", self.work, "--", "sh", "-c", 'printf %s "$K" > "$0"', out)
            with open(out) as f:
                self.assertEqual(f.read(), value)
        for value in rejected:
            self.write("A=1\n")
            r = keyset_stdin(self, "K", value=value.encode() + b"\n")
            self.assertEqual(r.returncode, 2, repr(value))
            self.assertEqual(self.read(), b"A=1\n", repr(value))
            if value.strip():
                self.assertNotIn(value, r.stderr.decode())
        r = keyset_stdin(self, "K", value=b"abcdef12\r\n")
        self.assertEqual(self.read(), b"A=1\nK=abcdef12\n")
        r = self.run_cmd("keyset", "-f", self.work, "--public", "P", "a #b")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.read(), b"A=1\nK=abcdef12\n")

    def test_keyset_value_in_argv_without_public_exit2(self):
        r = self.run_cmd("keyset", "-f", self.work, "K", "значение77")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.path()))
        self.assertNotIn("значение77", r.stderr.decode())
        self.write("A=1\n")
        r = self.run_cmd("keyset", "-f", self.work, "K", "значение77")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.read(), b"A=1\n")
        self.assertNotIn("значение77", r.stderr.decode())

    def test_keyset_tty_hidden_input(self):
        value = b"ttysecret1"
        pid, fd = pty.fork()
        if pid == 0:
            script = os.path.join(HERE, "keyset")
            os.execve(script, [script, "-f", self.work, "K"], self.env)
        out, deadline, sent = b"", time.time() + 20, False
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(fd, 1024)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            if not sent and b"\xd0\x92\xd0\xb2\xd0\xb5\xd0\xb4\xd0\xb8\xd1\x82\xd0\xb5" in out:
                time.sleep(0.2)
                os.write(fd, value + b"\n")
                sent = True
        _, status = os.waitpid(pid, 0)
        self.assertTrue(sent, out)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, out)
        self.assertNotIn(value, out)
        self.assertEqual(self.read(), b"K=ttysecret1\n")

    def test_keyset_keychain_mode_argv(self):
        r = self.run_cmd("keyset", "ctx/x/svc", "K", stdin=b"topsecret1\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [c for c in self.security_log() if c["argv"][0] == "add-generic-password"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"], ["add-generic-password", "-U", "-s", "ctx/x/svc", "-a", "K", "-w"])
        self.assertNotIn("topsecret1", json.dumps(calls[0]["argv"]))
        r = self.run_cmd("keyset", "ctx/x/y/z", "K")
        self.assertEqual(r.returncode, 2)

    def test_from_keychain_exact_service_only(self):
        self.git_init()
        self.set_keychain({
            ("ctx/x", "A"): "avalue11", ("ctx/x", "B"): "bvalue22",
            ("ctx/xy", "C"): "cvalue33", ("ctx/x/y", "D"): "dvalue44",
        })
        self.write("# public\nA=old\nX=1\n")
        r = self.run_cmd("keyset", "-f", self.work, "--from-keychain", "ctx/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode(), "A\nB\n")
        self.assertEqual(self.read(), b"A=avalue11\nX=1\nB=bvalue22\n")
        self.assertTrue(all(c["argv"][0] in ("dump-keychain", "find-generic-password")
                            for c in self.security_log()))

    def test_from_keychain_zero_or_newline_exit2(self):
        self.git_init()
        original = b"X=1\n"
        self.write(original)
        self.set_keychain({("ctx/x", "A"): "avalue11"})
        r = self.run_cmd("keyset", "-f", self.work, "--from-keychain", "ctx/none")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.read(), original)
        for bad in ("bvalue\nline2", "b$value22", '"quoted"'):
            self.set_keychain({("ctx/x", "A"): "avalue11", ("ctx/x", "B"): bad})
            r = self.run_cmd("keyset", "-f", self.work, "--from-keychain", "ctx/x")
            self.assertEqual(r.returncode, 2, repr(bad))
            self.assertEqual(self.read(), original, repr(bad))
            self.assertEqual(r.stdout, b"")
            self.assertNotIn("avalue11", r.stderr.decode())
        os.remove(self.path())
        r = self.run_cmd("keyset", "-f", self.work, "--from-keychain", "ctx/x")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.path()))


class DelMv(Sandbox):
    def test_keydel_removes_line_and_marker(self):
        self.write("A=1\n# public\nP=pub\nB=2\n")
        self.assertEqual(self.run_cmd("keydel", "-f", self.work, "P").returncode, 0)
        self.assertEqual(self.read(), b"A=1\nB=2\n")
        r = self.run_cmd("keydel", "-f", self.work, "NOPE")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.read(), b"A=1\nB=2\n")

    def test_keymv_moves_public_marker(self):
        self.write("A=1\n# public\nP=pub # c\nB=2\n")
        self.assertEqual(self.run_cmd("keymv", "-f", self.work, "P", "Q").returncode, 0)
        self.assertEqual(self.read(), b"A=1\n# public\nQ=pub # c\nB=2\n")
        r = self.run_cmd("keyls", "-f", self.work)
        self.assertIn("Q\tpub\n", r.stdout.decode())

    def test_keymv_target_taken_exit2(self):
        original = b"A=1\nB=2\n"
        self.write(original)
        for args in (("A", "B"), ("NOPE", "C"), ("A", "1bad")):
            r = self.run_cmd("keymv", "-f", self.work, *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(self.read(), original)


if __name__ == "__main__":
    unittest.main()
