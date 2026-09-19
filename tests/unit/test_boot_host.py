#!/usr/bin/env python3
"""lib/boot_host.py's refusals and its argument building -- no ssh, no qemu, no network.

WHY THIS EXISTS. boot-host.ini decides where every boot test runs and what a VNC server
there is exposed to. Both are answers that must be wrong LOUDLY: a mistyped scratch path
that reaches ssh comes back as somebody else's error message, and a VNC address outside
the private ranges is an unauthenticated keyboard on the machine under test. So every
refusal is checked here, by the file that states the rule, before any connection exists.

The other half is the argument building. `ssh -o BatchMode=yes` is what makes "key-based
login only" true rather than merely intended -- without it a missing key turns a build
into a process waiting for a password prompt nobody will see. It is one word in one list,
and this is what keeps it there.

Run directly: python3 tests/unit/test_boot_host.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import boot_host  # noqa: E402

FAILURES = []

GOOD = "[boot-host]\nhost = kvmbox\nscratch = /srv/scratch\n"


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def write_cfg(d, text, mode=0o600):
    p = os.path.join(d, "boot-host.ini")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(p, mode)
    return p


def git(repo, *args, check_rc=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r


def new_repo(parent, name):
    repo = os.path.join(parent, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "Test")
    return repo


# Every way the file can be wrong, and the words the message must carry. The message is
# part of the contract: "boot-host.ini: unknown key 'hosts'" is actionable and "could not
# resolve hostname" three connections later is not.
REFUSALS = [
    ("no section",       "host = kvmbox\n",                          None),
    ("wrong section",    "[boothost]\nhost = a\nscratch = /s\n",     "unknown section"),
    ("a DEFAULT section", "[DEFAULT]\nhost = a\n[boot-host]\nscratch = /s\nhost = b\n",
     "unknown section"),
    ("unknown key",      GOOD + "hosts = other\n",                   "unknown key"),
    ("duplicate key",    "[boot-host]\nhost = a\nhost = b\nscratch = /s\n", None),
    ("no host",          "[boot-host]\nscratch = /s\n",              "needs host"),
    ("empty host",       "[boot-host]\nhost =\nscratch = /s\n",      "needs host"),
    ("no scratch",       "[boot-host]\nhost = a\n",                  "needs scratch"),
    ("placeholder host", "[boot-host]\nhost = <kvm-host>\nscratch = /s\n", "placeholder"),
    ("placeholder scratch",
     "[boot-host]\nhost = a\nscratch = <absolute path on that host>\n", "placeholder"),
    ("host is an option", "[boot-host]\nhost = -oProxyCommand=x\nscratch = /s\n",
     "would read as an option"),
    ("host with a space", "[boot-host]\nhost = a b\nscratch = /s\n",  "whitespace"),
    ("relative scratch", "[boot-host]\nhost = a\nscratch = work/here\n", "absolute path"),
    ("scratch too deep", "[boot-host]\nhost = a\nscratch = /" + "x" * 80 + "\n",
     "AF_UNIX path too long"),
    ("vnc is a hostname", GOOD + "vnc = kvmbox\n",                   "not an IP address"),
    ("vnc port is words", GOOD + "vnc = 127.0.0.1:vnc\n",            "not a port number"),
    ("vnc port is zero",  GOOD + "vnc = 127.0.0.1:0\n",              "not a port number"),
    # qemu's VNC server takes a display number and listens on 5900+display, so a port
    # below 5900 is one it cannot offer -- and `-vnc <addr>:5900` would bind 11800.
    ("vnc port below 5900", GOOD + "vnc = 127.0.0.1:22\n",           "below 5900"),
    # `fe80::1:5900` reads as "fe80::1, port 5900" and is ALSO a valid address on its
    # own, so accepting it bare would silently pick a different machine. Refused, not
    # guessed at.
    ("a bare IPv6 address", GOOD + "vnc = fd00::1\n",                "in brackets"),
    ("an IPv6 address that looks like a port",
     GOOD + "vnc = fe80::1:5900\n",                                  "in brackets"),
    ("vnc_public is not a boolean", GOOD + "vnc_public = maybe\n",   "must be yes or no"),
]


def test_every_bad_config_is_refused():
    d = tempfile.mkdtemp(prefix="bh-cfg-")
    try:
        for name, text, phrase in REFUSALS:
            write_cfg(d, text)
            try:
                boot_host.load(root=d, env={})
                check(f"{name}: refused", "accepted", "ConfigError")
            except boot_host.ConfigError as e:
                if phrase:
                    check(f"{name}: message says why", phrase in str(e), True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_good_config_is_read_exactly():
    d = tempfile.mkdtemp(prefix="bh-cfg-")
    try:
        write_cfg(d, "[boot-host]\nhost = kvmbox\nscratch = /srv/scratch/\n"
                     "vnc = 172.17.0.1:5901\n")
        cfg = boot_host.load(root=d, env={})
        check("host", cfg.host, "kvmbox")
        check("scratch", cfg.scratch, "/srv/scratch/")
        # One directory, derived once. Everything this feature writes is under it, so a
        # trailing slash in the file must not produce a doubled one in every path.
        check("base", cfg.base, "/srv/scratch/boot-host")
        check("vnc", cfg.vnc, "172.17.0.1:5901")
        check("a routable private address is direct, not tunnelled", cfg.tunnelled, False)

        write_cfg(d, GOOD)
        cfg = boot_host.load(root=d, env={})
        check("vnc defaults to loopback", cfg.vnc, "127.0.0.1:5900")
        check("loopback is tunnelled", cfg.tunnelled, True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_world_writable_config_is_refused():
    """Anyone who can write it can redirect every boot test to a machine of their own."""
    d = tempfile.mkdtemp(prefix="bh-perm-")
    try:
        write_cfg(d, GOOD, mode=0o666)
        try:
            boot_host.load(root=d, env={})
            check("mode 0666 refused", "accepted", "ConfigError")
        except boot_host.ConfigError as e:
            check("says which mode", "0666" in str(e), True)
            check("names the fix", "chmod 600" in str(e), True)
        write_cfg(d, GOOD, mode=0o640)
        boot_host.load(root=d, env={})          # group-READABLE is not the problem
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_tracked_config_is_refused():
    """A fork that committed one would boot every cloner's ISOs on the fork's machine.

    Gitignored is not enough by itself: `git add -f` is one command, and the resulting
    clone WORKS, so nobody would look. ci/checks/10-no-dnc.sh refuses to commit one and
    this refuses to use one that is already committed, which are different moments.
    """
    tmp = tempfile.mkdtemp(prefix="bh-git-")
    try:
        repo = new_repo(tmp, "repo")
        write_cfg(repo, GOOD)
        git(repo, "add", "-f", "boot-host.ini")
        git(repo, "commit", "-qm", "oops")
        try:
            boot_host.load(root=repo, env={})
            check("a tracked config is refused", "accepted", "ConfigError")
        except boot_host.ConfigError as e:
            check("names the fix", "git rm --cached" in str(e), True)
        git(repo, "rm", "-q", "--cached", "boot-host.ini")
        git(repo, "commit", "-qm", "untracked again")
        cfg = boot_host.load(root=repo, env={})
        check("...and accepted once it is not tracked", cfg.host, "kvmbox")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# The whole table, stated once. is_private would answer "yes" for 0.0.0.0 and
# 100.64.0.0/10, and binding an unauthenticated VNC server to 0.0.0.0 is exactly the
# mistake this is here to catch -- so the ranges are listed rather than delegated.
PRIVATE = ["127.0.0.1", "127.1.2.3", "10.1.2.3", "172.16.0.1", "172.31.255.254",
           "192.168.1.5", "::1", "fd00::1", "fc00::5"]
PUBLIC = ["0.0.0.0", "::", "172.32.0.1", "172.15.0.1", "100.64.0.1", "8.8.8.8",
          "2001:db8::1", "169.254.1.1"]


def test_the_private_address_table():
    for ip in PRIVATE:
        check(f"{ip} is private", boot_host.vnc_is_private(ip), True)
    for ip in PUBLIC:
        check(f"{ip} is NOT private", boot_host.vnc_is_private(ip), False)


def vnc_spec(ip):
    """How an address is written in the file. A v6 literal is full of colons, so the
    port separator only means anything inside brackets -- and an unbracketed one is
    refused rather than read as an address nobody asked for."""
    return f"[{ip}]:5900" if ":" in ip else f"{ip}:5900"


def test_a_public_vnc_needs_saying_so_twice():
    d = tempfile.mkdtemp(prefix="bh-vnc-")
    try:
        for ip in PUBLIC:
            write_cfg(d, GOOD + f"vnc = {vnc_spec(ip)}\n")
            try:
                boot_host.load(root=d, env={})
                check(f"vnc = {ip} is refused unconfirmed", "accepted", "ConfigError")
            except boot_host.ConfigError as e:
                check(f"vnc = {ip}: says it is not private", "NOT a private" in str(e), True)
                check(f"vnc = {ip}: names the confirmation", "vnc_public" in str(e), True)
            # ...and confirmed, it is accepted and REMEMBERS that it is public, which is
            # what makes every later launch able to say so.
            write_cfg(d, GOOD + f"vnc = {vnc_spec(ip)}\nvnc_public = yes\n")
            cfg = boot_host.load(root=d, env={})
            check(f"vnc = {ip}: accepted when confirmed", cfg.vnc_ip, ip)
            check(f"vnc = {ip}: still known to be public",
                  boot_host.vnc_is_private(cfg.vnc_ip), False)
        # A v6 literal keeps its brackets, so the address and the port stay tellable apart.
        write_cfg(d, GOOD + "vnc = [fd00::1]:5902\n")
        cfg = boot_host.load(root=d, env={})
        check("v6 address", cfg.vnc_ip, "fd00::1")
        check("v6 port", cfg.vnc_port, 5902)
        check("v6 printed with brackets", cfg.vnc, "[fd00::1]:5902")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_local_beats_the_file():
    """KITCHEN_BOOT_HOST=local is the escape hatch, so it must win over a valid file."""
    d = tempfile.mkdtemp(prefix="bh-local-")
    try:
        write_cfg(d, GOOD)
        check("configured", boot_host.load(root=d, env={}).host, "kvmbox")
        check("local wins", boot_host.load(root=d, env={"KITCHEN_BOOT_HOST": "local"}), None)
        check("and says why",
              boot_host.local_reason({"KITCHEN_BOOT_HOST": "local"}), "KITCHEN_BOOT_HOST=local")
        # Only that one value. Anything else is not a way to accidentally disable the
        # boot host, because a boot that silently moved back here would be a boot that
        # silently got twenty times slower.
        check("other values do not disable it",
              boot_host.load(root=d, env={"KITCHEN_BOOT_HOST": "1"}).host, "kvmbox")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_no_config_is_not_an_error():
    d = tempfile.mkdtemp(prefix="bh-none-")
    try:
        check("no file, no boot host", boot_host.load(root=d, env={}), None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_template_and_the_parser_agree():
    """A key in the example that load() refuses makes the template a trap.

    Two lists of the same thing, in two files, in two languages -- the shape this project
    already guards for kitchen's TOOLS against need.TOOL_PKG. Someone adding a key adds it
    to the template first, because that is the file a reader sees.
    """
    import configparser
    text = open(os.path.join(ROOT, "boot-host.example.ini"), encoding="utf-8").read()
    cp = configparser.ConfigParser(interpolation=None,
                                   default_section="_no_default_")
    # The template comments out its optional keys, which is exactly where a typo would
    # hide, so they are uncommented here and checked like the rest.
    cp.read_string("\n".join(ln[1:] if ln.startswith(";vnc") else ln
                             for ln in text.splitlines()))
    check("the template has the one section", cp.sections(), [boot_host.SECTION])
    for key in cp.options(boot_host.SECTION):
        check(f"the parser knows the template's {key!r}", key in boot_host.KEYS, True)
    # ...and the other way: a key the parser accepts but the template never mentions is a
    # feature nobody can find. Read from the module, never copied here: a drift guard
    # holding its own copy of the list IS the drift it is meant to catch.
    for key in boot_host.KEYS:
        check(f"the template mentions {key!r}", key in text, True)


def test_a_missing_option_value_is_refused_not_crashed():
    """`--golden` with nothing after it used to be an IndexError and a traceback."""
    tmp = tempfile.mkdtemp(prefix="bh-args-")
    try:
        repo = new_repo(tmp, "repo")
        os.makedirs(os.path.join(repo, "lib"))
        shutil.copy2(os.path.join(ROOT, "lib", "boot_host.py"),
                     os.path.join(repo, "lib", "boot_host.py"))
        write_cfg(repo, GOOD)
        for args in (["test", "--iso", "x.iso", "--golden"],
                     ["test", "--iso"],
                     ["test", "--out", "d", "--record"]):
            r = subprocess.run([sys.executable, "lib/boot_host.py", *args], cwd=repo,
                               capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, KITCHEN_BOOT_HOST=""))
            check(f"{' '.join(args)}: refused", r.returncode, 2)
            check(f"{' '.join(args)}: no traceback", "Traceback" in r.stderr, False)
            check(f"{' '.join(args)}: says which option",
                  "needs a value" in r.stderr or "no --iso" in r.stderr, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_agent_refuses_a_name_that_is_a_path():
    """Every name from the driver is joined onto a directory on somebody else's machine.

    The driver only ever sends a basename and a hex digest. That is a reason for the check
    to be cheap, not a reason to leave it out: os.path.join with an absolute or climbing
    value walks straight out of the scratch area this whole design promises to stay in.
    """
    a = boot_host.Agent("/srv/s")
    for bad in ("../../etc", "/etc/passwd", "", ".", "..", "a/b", None, 7):
        try:
            a._component(bad, "name")
            check(f"{bad!r} refused", "accepted", "ValueError")
        except ValueError:
            pass
    check("a real basename passes", a._component("slax.iso", "name"), "slax.iso")
    check("a digest passes", a._component("a" * 64, "sha256"), "a" * 64)
    # ...and an op that needs a run refuses to act before one is open, rather than
    # writing a relative path into the login directory of the account over there.
    try:
        a._open_run()
        check("an op before `open` is refused", "accepted", "RuntimeError")
    except RuntimeError as e:
        check("...saying why", "out of order" in str(e), True)


def test_a_port_another_server_holds_is_refused():
    """...before anything is sent, which is the whole point of asking early.

    FOUND BY BEING CAUGHT BY IT. The container this was built in already runs a VNC
    desktop on 5901, bound to 0.0.0.0. A launch aimed at that port was refused -- and the
    probe checking the launch then connected to the port, got a perfectly good
    `RFB 003.008` from the OTHER server, and reported success. That is exactly the
    confusion the check exists to prevent: a viewer pointed at a port someone else owns
    looks like it worked.
    """
    import socket as sk
    held = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
    held.setsockopt(sk.SOL_SOCKET, sk.SO_REUSEADDR, 1)
    try:
        held.bind(("127.0.0.1", 0))               # whatever port the OS hands out
        port = held.getsockname()[1]
        held.listen(1)
        why = boot_host._tunnel_port_busy(port)
        check("a held port is refused", bool(why), True)
        check("...naming it", str(port) in why, True)
        check("...and saying it is in use", "already in use" in why, True)
    finally:
        held.close()
    # ...and a free one is not. Without this the check could refuse everything and the
    # test above would still pass.
    free = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
    free.bind(("127.0.0.1", 0))
    spare = free.getsockname()[1]
    free.close()
    check("a free port is allowed", boot_host._tunnel_port_busy(spare), "")


def test_the_ssh_command_never_prompts():
    cfg = boot_host.Config("kvmbox", "/srv/s", "127.0.0.1", 5900, False, "x")
    a = boot_host.ssh_argv(cfg)
    check("BatchMode is set", "BatchMode=yes" in a, True)
    # The pair that makes a dropped network end the session instead of hanging it: the
    # agent's heartbeat covers the other direction.
    check("keepalives are set", "ServerAliveInterval=10" in a, True)
    check("no tty by default", "-T" in a, True)
    check("no forward by default", any(x == "-L" for x in a), False)
    t = boot_host.ssh_argv(cfg, tty=True, tunnel=(5900, "127.0.0.1", 5900))
    check("a viewer session gets a tty", "-tt" in t, True)
    check("...and the forward", "5900:127.0.0.1:5900" in t, True)
    # Without this ssh reports a busy port and connects anyway, so a second launch would
    # show the viewer the PREVIOUS boot.
    check("...and refuses to continue without it", "ExitOnForwardFailure=yes" in t, True)


def test_a_failure_is_blamed_on_the_right_thing():
    """"Permission denied" is said by everything, and only ssh's own refusal is ssh's.

    FOUND BY RUNNING IT. A `scratch` the account cannot create gives rsync exit 12 with
    "mkdir: cannot create directory '/root': Permission denied" -- and the first version
    of the rule matched the bare words and reported "hydra refused the key", sending the
    reader to run ssh-copy-id against a host whose key had just worked. It had to work:
    that is how the mkdir got far enough to fail.

    Both strings below are what ssh 9.6 and rsync 3.2.7 actually printed.
    """
    cfg = boot_host.Config("kvmbox", "/srv/s", "127.0.0.1", 5900, False, "x")
    s = boot_host.Session.__new__(boot_host.Session)     # no connection, just the rule
    s.cfg = cfg
    auth = s._ssh_hint("nosuchuser@10.1.1.21: Permission denied (publickey,password).")
    check("ssh's refusal is a key problem", "refused the key" in auth, True)
    mkdir = s._ssh_hint("mkdir: cannot create directory ‘/root’: Permission "
                        "denied\nrsync: connection unexpectedly closed (0 bytes received "
                        "so far) [sender]")
    check("a remote mkdir failing is NOT a key problem", "refused the key" in mkdir, False)
    for err, want in (("ssh: Could not resolve hostname kvmbox: Name or service not known",
                       "does not resolve"),
                      ("ssh: connect to host kvmbox port 22: Connection refused",
                       "not reachable"),
                      ("Host key verification failed.", "host key")):
        check(f"{want!r} is recognised", want in s._ssh_hint(err), True)


def test_the_agent_is_verified_before_it_runs():
    cfg = boot_host.Config("kvmbox", "/srv/s", "127.0.0.1", 5900, False, "x")
    cmd = boot_host.agent_command(cfg, "/srv/s/boot-host/agent/abc/boot_host.py", "d" * 64)
    check("checks the digest", "sha256sum -c --quiet" in cmd, True)
    check("...before python runs", cmd.index("sha256sum") < cmd.index("python3"), True)
    check("...and only then", "&&" in cmd, True)
    check("execs, so signals reach the agent", "exec python3" in cmd, True)
    check("passes the scratch directory", cmd.endswith("agent /srv/s"), True)
    # A scratch path with a space in it is a path, not two arguments.
    spacey = boot_host.Config("kvmbox", "/srv/my scratch", "127.0.0.1", 5900, False, "x")
    cmd = boot_host.agent_command(spacey, "/a/b.py", "d" * 64)
    check("a path with a space is quoted", cmd.endswith("agent '/srv/my scratch'"), True)


def test_the_tree_sent_is_the_tree_git_sees():
    """...which is why the configuration file cannot be among it.

    boot-host.ini is gitignored, so `--others --exclude-standard` leaves it out. That is
    not a nicety: the file names the machine being sent to, and sending it would put it
    in a directory on that machine where anyone with an account could read it.
    """
    tmp = tempfile.mkdtemp(prefix="bh-tree-")
    try:
        repo = new_repo(tmp, "repo")
        for name in ("kitchen", "tracked.txt", "gone.txt"):
            with open(os.path.join(repo, name), "w") as f:
                f.write("x\n")
        with open(os.path.join(repo, ".gitignore"), "w") as f:
            f.write("/boot-host.ini\nout/\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        write_cfg(repo, GOOD)
        with open(os.path.join(repo, "untracked.txt"), "w") as f:
            f.write("new\n")
        os.makedirs(os.path.join(repo, "out"))
        with open(os.path.join(repo, "out", "big.iso"), "w") as f:
            f.write("iso\n")
        os.unlink(os.path.join(repo, "gone.txt"))

        files = boot_host.tree_files(repo)
        check("tracked files go", "tracked.txt" in files, True)
        check("untracked but not ignored goes", "untracked.txt" in files, True)
        check("the boot host config NEVER goes", "boot-host.ini" in files, False)
        check("build output does not go", "out/big.iso" in files, False)
        # rsync is given a list; a name in it that is not on disk fails the transfer.
        check("a deleted file is not sent", "gone.txt" in files, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_stale_screenshot_is_not_this_run_s_evidence():
    """The boot happened elsewhere, so qemu_boot.py's own unlink cannot reach it here.

    A boot that produces no screenshot copies none back. Last week's PNG then sits beside
    this week's serial log, with a timestamp rsync just refreshed, and reads as evidence
    of a boot that never produced it.
    """
    d = tempfile.mkdtemp(prefix="bh-png-")
    try:
        for n in ("img-bios.serial.log", "img-bios.png", "img-uefi.png"):
            open(os.path.join(d, n), "w").close()
        boot_host._drop_stale_shots(d, ["img-bios.serial.log"])
        check("the stale screenshot is gone",
              os.path.exists(os.path.join(d, "img-bios.png")), False)
        check("the serial log is untouched",
              os.path.exists(os.path.join(d, "img-bios.serial.log")), True)
        # Only the boots this run reported. A PNG belonging to a mode that did not run
        # this time is not this run's business.
        check("another mode's evidence is left alone",
              os.path.exists(os.path.join(d, "img-uefi.png")), True)

        open(os.path.join(d, "img-bios.png"), "w").close()
        boot_host._drop_stale_shots(d, ["img-bios.serial.log", "img-bios.png"])
        check("a screenshot this run DID produce survives",
              os.path.exists(os.path.join(d, "img-bios.png")), True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_printed_paths_are_paths_here():
    """A line saying where the evidence landed must name a file someone can open.

    The golden case is here because it was wrong: the first rewrite covered the image and
    the evidence directory only, so a first sweep announced its new golden as
    "20260919-153351-825f/golden/g.testkit" -- a path on a machine the reader is not on --
    rather than the /tmp/g.testkit it had just written here.
    """
    run = "/srv/s/boot-host/runs/r1"
    pairs = [(f"{run}/golden/g.testkit", "tests/boot/golden/g.testkit"),
             (f"{run}/runs.jsonl", "out/tier-c/runs.jsonl"),
             (f"{run}/x.iso", "out/x.iso"),
             (f"{run}/out", "out/boot-tests"),
             (f"{run}/tree/", ""),
             (run + "/", "r1/")]
    for name, line, want in [
            ("the evidence path is local",
             f"  wrote {run}/out/x.png", "  wrote out/boot-tests/x.png"),
            ("the image is named as it is known here",
             f"test {run}/x.iso --kernel", "test out/x.iso --kernel"),
            ("the golden is named as it is known here",
             f"  golden: 14 lines match {run}/golden/g.testkit",
             "  golden: 14 lines match tests/boot/golden/g.testkit"),
            ("...and so is the ledger it appends to",
             f"  recorded to {run}/runs.jsonl", "  recorded to out/tier-c/runs.jsonl")]:
        check(name, boot_host._paths_home(line, pairs), want)


def test_the_gate_refuses_a_committed_config():
    """ci/checks/10-no-dnc.sh, driven over a throwaway repo -- and the template passes."""
    tmp = tempfile.mkdtemp(prefix="bh-gate-")
    try:
        repo = new_repo(tmp, "repo")
        os.makedirs(os.path.join(repo, "ci", "checks"))
        shutil.copy2(os.path.join(ROOT, "ci", "lib.sh"),
                     os.path.join(repo, "ci", "lib.sh"))
        shutil.copy2(os.path.join(ROOT, "ci", "checks", "10-no-dnc.sh"),
                     os.path.join(repo, "ci", "checks", "10-no-dnc.sh"))
        shutil.copy2(os.path.join(ROOT, "boot-host.example.ini"),
                     os.path.join(repo, "boot-host.example.ini"))
        git(repo, "add", "-A")
        p = subprocess.run(["sh", "ci/checks/10-no-dnc.sh"], cwd=repo,
                           capture_output=True, text=True,
                           env=dict(os.environ, KITCHEN_SCOPE="staged"))
        check("the committed template passes", p.returncode, 0)

        write_cfg(repo, GOOD)
        git(repo, "add", "-f", "boot-host.ini")
        p = subprocess.run(["sh", "ci/checks/10-no-dnc.sh"], cwd=repo,
                           capture_output=True, text=True,
                           env=dict(os.environ, KITCHEN_SCOPE="staged"))
        out = p.stdout + p.stderr
        check("a force-added config is refused", p.returncode != 0, True)
        check("...by name", "boot-host.ini" in out, True)
        check("...saying what would happen", "send its" in out or "boot tests there" in out,
              True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_active_answers_with_a_status():
    """lib/build.sh asks this before every boot, so the three answers must be distinct."""
    tmp = tempfile.mkdtemp(prefix="bh-active-")
    try:
        repo = new_repo(tmp, "repo")
        os.makedirs(os.path.join(repo, "lib"))
        shutil.copy2(os.path.join(ROOT, "lib", "boot_host.py"),
                     os.path.join(repo, "lib", "boot_host.py"))

        def active(env_extra=None):
            env = dict(os.environ)
            env.pop("KITCHEN_BOOT_HOST", None)
            env.update(env_extra or {})
            return subprocess.run([sys.executable, "lib/boot_host.py", "active"], cwd=repo,
                                  capture_output=True, text=True, env=env)

        check("no config: boots stay here", active().returncode, 1)
        write_cfg(repo, GOOD)
        r = active()
        check("a config: boots go there", r.returncode, 0)
        check("...and it names the host", r.stdout.strip(), "kvmbox")
        r = active({"KITCHEN_BOOT_HOST": "local"})
        check("local wins", r.returncode, 1)
        # SAID OUT LOUD, and on stderr. Someone with a boot host configured who sees a
        # slow local boot needs to know the override is why; and lib/build.sh reads
        # stdout in a $(...), so a note printed there would be swallowed by the
        # substitution that discards it -- announcing nothing, to nobody.
        check("...and says so", "this boot stays here" in r.stderr, True)
        check("...on stderr, not stdout", r.stdout.strip(), "")
        # ...except when the agent set it. The copy of kitchen running inside a remote
        # boot has already said where it is; relaying "this boot stays here" from there
        # reads as though the boot never left.
        quiet = active({"KITCHEN_BOOT_HOST": "local", "KITCHEN_BOOT_HOST_AGENT": "1"})
        check("the nested agent says nothing", quiet.stderr.strip(), "")
        check("...but still answers 1", quiet.returncode, 1)
        write_cfg(repo, "[boot-host]\nhost = a\nscratch = relative\n")
        r = active()
        check("a broken config is its own answer", r.returncode, 2)
        check("...explained on stderr", "absolute path" in r.stderr, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    box = tempfile.mkdtemp(prefix="test_boot_host-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_every_bad_config_is_refused,
                   test_a_good_config_is_read_exactly,
                   test_a_world_writable_config_is_refused,
                   test_a_tracked_config_is_refused,
                   test_the_private_address_table,
                   test_a_public_vnc_needs_saying_so_twice,
                   test_local_beats_the_file,
                   test_no_config_is_not_an_error,
                   test_the_template_and_the_parser_agree,
                   test_a_missing_option_value_is_refused_not_crashed,
                   test_the_agent_refuses_a_name_that_is_a_path,
                   test_a_port_another_server_holds_is_refused,
                   test_the_ssh_command_never_prompts,
                   test_a_failure_is_blamed_on_the_right_thing,
                   test_the_agent_is_verified_before_it_runs,
                   test_the_tree_sent_is_the_tree_git_sees,
                   test_a_stale_screenshot_is_not_this_run_s_evidence,
                   test_printed_paths_are_paths_here,
                   test_the_gate_refuses_a_committed_config,
                   test_active_answers_with_a_status]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_boot_host.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
