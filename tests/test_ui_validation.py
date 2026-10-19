"""Unit tests for ui/validation.py. No root, no Flask, no containers.

Run: python3 -m unittest tests/test_ui_validation.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui"))
import validation as v  # noqa: E402

# Strings an attacker might send. Fields that are not a command must reject all of them.
INJECTIONS = [
    "; rm -rf /",
    "$(id)",
    "`id`",
    "a\nb",
    "a\rb",
    "a\x00b",
    "../../etc/passwd",
    "/etc/passwd",
    "a b",
    "a&&b",
    "a|b",
    "--rootfs=/",
    "-rf",
    "name\n",
    "'; DROP TABLE x; --",
    "<script>alert(1)</script>",
]


class NameTests(unittest.TestCase):
    def test_valid(self):
        for name in ["web", "a", "my-box_1.2", "0abc", "a" * 31]:
            self.assertEqual(v.validate_name(name), name)

    def test_empty_means_no_name(self):
        self.assertEqual(v.validate_name(""), "")
        self.assertEqual(v.validate_name(None), "")

    def test_rejects_bad_names(self):
        for name in INJECTIONS + ["A", "Web", "_x", ".x", "-x", "a" * 32, "é", 5, ["a"], {}]:
            with self.assertRaises(v.ValidationError, msg=repr(name)):
                v.validate_name(name)

    def test_rejects_name_that_looks_like_an_id(self):
        with self.assertRaises(v.ValidationError):
            v.validate_name("0123456789ab")


class HostnameTests(unittest.TestCase):
    def test_valid(self):
        for host in ["box", "my-host", "a.b.c", "A1", "x" * 63]:
            self.assertEqual(v.validate_hostname(host), host)
        self.assertEqual(v.validate_hostname(""), "")

    def test_rejects(self):
        for host in INJECTIONS + ["-a", "a-", ".a", "a.", "a..b", "x" * 64, "a_b", 1]:
            with self.assertRaises(v.ValidationError, msg=repr(host)):
                v.validate_hostname(host)


class MemoryTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.validate_memory("64M"), "64M")
        self.assertEqual(v.validate_memory("64m"), "64M")
        self.assertEqual(v.validate_memory("8192K"), "8192K")
        self.assertEqual(v.validate_memory("1G"), "1G")
        self.assertEqual(v.validate_memory("16G"), "16G")
        self.assertEqual(v.validate_memory("4194304"), "4194304")
        self.assertEqual(v.validate_memory(""), "")

    def test_rejects_out_of_range(self):
        for text in ["0", "1M", "3M", "17G", "999999999999G", "99999999999999999999"]:
            with self.assertRaises(v.ValidationError, msg=text):
                v.validate_memory(text)

    def test_rejects_garbage(self):
        for text in INJECTIONS + ["-5M", "-1", "1.5G", "M", "64MB", "64 M", "0x40M", "1e9", "64M\n",
                                  True, 1.5, [], {}]:
            with self.assertRaises(v.ValidationError, msg=repr(text)):
                v.validate_memory(text)


class CpusTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.validate_cpus("0.5", 8), "0.5")
        self.assertEqual(v.validate_cpus(0.1, 8), "0.1")
        self.assertEqual(v.validate_cpus(8, 8), "8")
        self.assertEqual(v.validate_cpus("2", 8), "2")
        self.assertEqual(v.validate_cpus("", 8), "")

    def test_rejects_out_of_range(self):
        for value in [0, 0.05, "0.09", 9, "8.5", -1, "-0.5", 1e9, "999999"]:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_cpus(value, 8)

    def test_rejects_garbage(self):
        for value in INJECTIONS + [float("nan"), float("inf"), "nan", "inf", "1e1", "0x1", "1,5", True, [], {}]:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_cpus(value, 8)

    def test_default_max_is_machine_core_count(self):
        v.validate_cpus(os.cpu_count(), None)
        with self.assertRaises(v.ValidationError):
            v.validate_cpus(os.cpu_count() + 1, None)


class PidsTests(unittest.TestCase):
    def test_valid(self):
        for value, text in [(1, "1"), ("32", "32"), (4096, "4096")]:
            self.assertEqual(v.validate_pids(value), text)
        self.assertEqual(v.validate_pids(""), "")

    def test_rejects(self):
        for value in INJECTIONS + [0, -1, "-1", 4097, 10 ** 12, "99999999999", "1.5", 1.5, True, "5\n", [], {}]:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_pids(value)


class NetTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.validate_net("none"), "none")
        self.assertEqual(v.validate_net("veth"), "veth")
        self.assertEqual(v.validate_net(""), "none")
        self.assertEqual(v.validate_net(None), "none")

    def test_rejects(self):
        for value in INJECTIONS + ["host", "bridge", "NONE", "veth ", 1, ["veth"]]:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_net(value)


class CommandTests(unittest.TestCase):
    def test_splits_like_a_shell_would_quote_it(self):
        self.assertEqual(v.validate_command("sleep 300"), ["sleep", "300"])
        self.assertEqual(v.validate_command("sh -c 'echo hi; sleep 5'"), ["sh", "-c", "echo hi; sleep 5"])

    def test_shell_metacharacters_stay_literal(self):
        # No shell runs these: they are just odd-looking arguments for execvp.
        self.assertEqual(v.validate_command("; rm -rf /"), [";", "rm", "-rf", "/"])
        self.assertEqual(v.validate_command("echo $(id)"), ["echo", "$(id)"])
        self.assertEqual(v.validate_command("echo `id`"), ["echo", "`id`"])
        self.assertEqual(v.validate_command("a && b | c > d"), ["a", "&&", "b", "|", "c", ">", "d"])

    def test_rejects_newlines_and_control_characters(self):
        for text in ["echo hi\nrm -rf /", "echo\rhi", "echo\x00hi", "echo\x1b[31m", "a\tb"]:
            with self.assertRaises(v.ValidationError, msg=repr(text)):
                v.validate_command(text)

    def test_rejects_empty(self):
        for text in ["", "   ", "''", '""']:
            with self.assertRaises(v.ValidationError, msg=repr(text)):
                v.validate_command(text)

    def test_rejects_unbalanced_quotes(self):
        for text in ["echo 'hi", 'echo "hi', "echo \\"]:
            with self.assertRaises(v.ValidationError, msg=text):
                v.validate_command(text)

    def test_argument_count_limit(self):
        self.assertEqual(len(v.validate_command(" ".join(["a"] * 32))), 32)
        with self.assertRaises(v.ValidationError):
            v.validate_command(" ".join(["a"] * 33))

    def test_argument_length_limit(self):
        self.assertEqual(len(v.validate_command("x" * 256)[0]), 256)
        with self.assertRaises(v.ValidationError):
            v.validate_command("echo " + "x" * 257)

    def test_rejects_non_strings(self):
        for value in [None, 5, ["ls"], {"a": 1}, True]:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_command(value)


class ContainerIdTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.validate_container_id("0123456789ab"), "0123456789ab")

    def test_rejects(self):
        bad = ["", "0123456789a", "0123456789abc", "0123456789AB", "0123456789ag", "../../etc/pass",
               "0123456789ab\n", "0123456789ab/..", "-rf", "$(id)", None, 12, ["a"]]
        for value in bad + INJECTIONS:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_container_id(value)


class TailTests(unittest.TestCase):
    def test_default_and_valid(self):
        self.assertEqual(v.validate_tail(None), 200)
        self.assertEqual(v.validate_tail(""), 200)
        self.assertEqual(v.validate_tail("50"), 50)
        self.assertEqual(v.validate_tail("1000"), 1000)

    def test_rejects(self):
        for value in ["0", "-5", "1001", "99999999999999", "abc", "1.5", "5\n", "1; ls", "$(id)", "-1"]:
            with self.assertRaises(v.ValidationError, msg=value):
                v.validate_tail(value)


class InitTests(unittest.TestCase):
    def test_booleans_accepted(self):
        self.assertIs(v.validate_init(True), True)
        self.assertIs(v.validate_init(False), False)

    def test_everything_else_rejected(self):
        for value in ["true", "false", "True", "yes", "on", "1", "", 1, 0, 1.0, None, [], [True],
                      {}, {"a": 1}] + INJECTIONS:
            with self.assertRaises(v.ValidationError, msg=repr(value)):
                v.validate_init(value)


class LaunchPayloadTests(unittest.TestCase):
    def test_full_payload(self):
        opts = v.validate_launch({"command": "sleep 300", "name": "web", "hostname": "box",
                                  "memory": "64M", "cpus": "0.5", "pids": 32, "net": "veth",
                                  "init": True}, 8)
        self.assertEqual(opts, {"argv": ["sleep", "300"], "name": "web", "hostname": "box",
                                "memory": "64M", "cpus": "0.5", "pids": "32", "net": "veth",
                                "init": True})

    def test_minimal_payload(self):
        opts = v.validate_launch({"command": "ls"}, 8)
        self.assertEqual(opts["argv"], ["ls"])
        self.assertEqual(opts["net"], "none")
        self.assertEqual(opts["name"], "")
        self.assertIs(opts["init"], False)

    def test_init_is_passed_through_as_a_boolean(self):
        self.assertIs(v.validate_launch({"command": "ls", "init": True}, 8)["init"], True)
        self.assertIs(v.validate_launch({"command": "ls", "init": False}, 8)["init"], False)

    def test_bad_init_is_reported_on_the_init_field(self):
        with self.assertRaises(v.ValidationError) as ctx:
            v.validate_launch({"command": "ls", "init": "true"}, 8)
        self.assertEqual(ctx.exception.field, "init")

    def test_requires_command(self):
        with self.assertRaises(v.ValidationError):
            v.validate_launch({"name": "web"}, 8)

    def test_rejects_unknown_fields_such_as_rootfs(self):
        for extra in ["rootfs", "rm", "shell", "env", "user"]:
            with self.assertRaises(v.ValidationError, msg=extra):
                v.validate_launch({"command": "ls", extra: "/"}, 8)

    def test_rejects_non_objects(self):
        for payload in [None, [], "ls", 5, [{"command": "ls"}]]:
            with self.assertRaises(v.ValidationError, msg=repr(payload)):
                v.validate_launch(payload, 8)

    def test_error_names_the_field(self):
        with self.assertRaises(v.ValidationError) as ctx:
            v.validate_launch({"command": "ls", "memory": "-5M"}, 8)
        self.assertEqual(ctx.exception.field, "memory")


class ExecPayloadTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.validate_exec({"command": "ps aux"}), ["ps", "aux"])

    def test_rejects(self):
        for payload in [{}, {"command": ""}, {"command": "ps", "x": 1}, [], None, {"command": "a\nb"}]:
            with self.assertRaises(v.ValidationError, msg=repr(payload)):
                v.validate_exec(payload)


if __name__ == "__main__":
    unittest.main()
