#!/usr/bin/env python3
"""Boot tests, run on a machine that has KVM.

    boot-host.ini           host = <kvm-host>, scratch = <path there>
    kitchen test x.iso --kernel     ...and the boot happens there, not here

WHY. Every boot in this toolkit goes through one funnel -- `kitchen test` ->
lib/build.sh:_boot_run -> tests/boot/qemu_boot.py -- and qemu runs wherever the checkout
is. A container without /dev/kvm boots under TCG, ten to twenty times slower, and one
without qemu at all cannot boot anything. The machine with the KVM is often not the
machine with the checkout.

So this file is the one place where "run it over there" lives. Nothing else in the tree
learns about ssh: `kitchen test` asks whether a boot host is configured and, if so, hands
the boot half over here. That covers `kitchen build`'s profile tests, ci/tier-c.sh and CI
by construction, because all three call `kitchen test`.

ONE FILE, TWO ROLES. The driver runs here; the same file runs on the boot host as the
agent (`python3 boot_host.py agent <scratch>`), sent by rsync and verified by sha256
before it is executed. Two files would be two things to keep in step across a machine
boundary, and the agent side is exactly the half that cannot be debugged by reading the
local tree. Stdlib only, for the same reason: the boot host is someone's desktop, not a
machine anyone should have to prepare with pip.

NOTHING ABOUT ANY REAL HOST IS IN THIS REPOSITORY. boot-host.ini is gitignored, refused by
ci/checks/10-no-dnc.sh, and refused here if it is tracked -- a fork that committed one
would send every cloner's ISOs to the fork author's machine. boot-host.example.ini is the
committed template and names nothing.

KEY-BASED LOGIN ONLY. ssh runs with BatchMode=yes throughout: it never prompts, and there
is nowhere in the configuration to put a username or a password. A host that would prompt
is reported as a host that must accept key-based login.
"""
from __future__ import annotations

import configparser
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_NAME = "boot-host.ini"
EXAMPLE_NAME = "boot-host.example.ini"
SECTION = "boot-host"
# The only keys there are. Named here rather than inline in load() so that
# tests/unit/test_boot_host.py can check the committed template against THIS list
# instead of against a second copy of it -- a drift guard that keeps its own copy is
# the drift.
KEYS = ("host", "scratch", "vnc", "vnc_public")
MARKER = ".slax-kitchen-boot-host"

# Every message from the agent carries this prefix. The remote login shell is entitled to
# print things -- a profile that echoes, a package manager notice -- and an unprefixed
# line would then be parsed as a protocol message or, worse, silently eaten. Anything
# without the prefix is host noise and is shown as such.
SENTINEL = "#bh#"

# Exit codes. 0 and 1 are the boot test's own, unchanged, so a caller cannot tell a remote
# run from a local one by its status. 2 and 3 are this file's, and they are different
# claims: "your configuration is wrong" is answerable by editing a file, "it could not be
# run" is not. ci/tier-c.sh stops on either WITHOUT writing a ledger, because a ledger row
# is a claim about a boot that happened.
EXIT_CONFIG = 2
EXIT_UNAVAILABLE = 3

# How long the agent waits for a sign of life before it assumes the driver is gone and
# kills the boot. The driver pings every HEARTBEAT_S; six missed pings is the wait.
HEARTBEAT_S = 5
WATCHDOG_S = 30

# A run directory nobody holds a lock on belongs to a session that died. It is removed at
# once unless it was deliberately kept, and a kept one expires; a cached ISO expires when
# nothing has used it for a fortnight.
KEEP_DAYS = 7
CACHE_DAYS = 14

# qemu_boot.py puts its QMP socket in tempfile.mkdtemp(prefix="qb-") under $TMPDIR, and
# AF_UNIX caps a socket path at 108 bytes including the terminator -- "AF_UNIX path too
# long" is how a deep -o directory broke the harness once already. The agent points TMPDIR
# at the run directory, so the scratch path chosen here decides whether that fits.
AF_UNIX_MAX = 107
QMP_TAIL = "/qb-XXXXXXXX/q"          # mkdtemp's 8 random characters, plus the socket


class ConfigError(Exception):
    """boot-host.ini says something this cannot act on. Exit 2, before any ssh."""


class Unavailable(Exception):
    """The boot host could not be used. Exit 3, and never a fallback to a local boot."""


# --------------------------------------------------------------------- config ----

class Config:
    def __init__(self, host, scratch, vnc_ip, vnc_port, vnc_public, path):
        self.host = host
        self.scratch = scratch
        self.vnc_ip = vnc_ip
        self.vnc_port = vnc_port
        self.vnc_public = vnc_public
        self.path = path

    @property
    def base(self) -> str:
        """Everything this feature writes lives under one directory, and only there."""
        return self.scratch.rstrip("/") + "/boot-host"

    @property
    def vnc(self) -> str:
        return f"[{self.vnc_ip}]:{self.vnc_port}" if ":" in self.vnc_ip \
            else f"{self.vnc_ip}:{self.vnc_port}"

    @property
    def tunnelled(self) -> bool:
        """Loopback on the boot host is only reachable from the boot host, so a viewer
        here needs an ssh tunnel. Any other address is reachable directly."""
        return ipaddress.ip_address(self.vnc_ip).is_loopback


# Private, listed explicitly rather than taken from ipaddress.is_private, which is a
# broader question than the one being asked: it counts 0.0.0.0/8 and 100.64.0.0/10 as
# private, and "this network" and a carrier-grade NAT range are not addresses anyone
# means to bind a VNC server to without being asked. RFC 1918 plus loopback plus unique
# local, and nothing else.
def vnc_is_private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)          # ValueError for a hostname: callers catch it
    if a.version == 4:
        return any(a in ipaddress.ip_network(n) for n in
                   ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    return a == ipaddress.ip_address("::1") or a in ipaddress.ip_network("fc00::/7")


def parse_vnc(value: str) -> tuple:
    """`IP`, `IP:PORT` or `[v6]:PORT` -> (ip, port).

    AN IP LITERAL, NOT A HOSTNAME. The address is what qemu binds and what decides
    whether the viewer here needs a tunnel; a name would have to be resolved on the boot
    host to answer either question, and a name that resolves differently on the two
    machines answers them inconsistently.
    """
    text = value.strip()
    port = 5900
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        if not _:
            raise ConfigError(f"vnc: unclosed '[' in {value!r}")
        if rest.startswith(":"):
            port = _port(rest[1:], value)
        elif rest:
            raise ConfigError(f"vnc: trailing {rest!r} in {value!r}")
    elif text.count(":") == 1:
        host, _, p = text.partition(":")
        port = _port(p, value)
    else:
        host = text
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise ConfigError(
            f"vnc: {host!r} is not an IP address. Give the address qemu should bind on "
            f"the boot host, e.g. 127.0.0.1:5900 (tunnelled) or 172.17.0.1:5900 (direct)")
    return host, port


def _port(text: str, whole: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ConfigError(f"vnc: {text!r} is not a port number, in {whole!r}")
    return int(text)


def local_reason(env=None) -> str | None:
    """Why a boot is staying here, or None. KITCHEN_BOOT_HOST=local wins over the file."""
    env = os.environ if env is None else env
    if env.get("KITCHEN_BOOT_HOST", "") == "local":
        return "KITCHEN_BOOT_HOST=local"
    return None


def load(root: str | None = None, env=None) -> Config | None:
    """The boot host, or None when boots stay here. Raises ConfigError, never guesses.

    A configured host that cannot be used FAILS THE COMMAND. It never falls back to a
    local boot: the reason to configure one is that a boot here is slow or impossible, so
    a silent fallback would turn "my tests got slower" into the symptom of a broken ssh
    key, which is a bad trade for anyone trying to produce evidence.
    """
    if local_reason(env) is not None:
        return None
    root = REPO_ROOT if root is None else root
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.exists(path):
        return None

    st = os.stat(path)
    if st.st_mode & 0o022:
        raise ConfigError(
            f"{CONFIG_NAME} is writable by group or others (mode {st.st_mode & 0o777:04o}).\n"
            f"  It names the machine your ISOs are sent to: chmod 600 {path}")
    # Tracked is refused rather than warned about. This file is gitignored by design; a
    # tree where it is tracked is either a fork that committed one -- so every clone would
    # boot on the fork author's machine -- or an accident heading for the same place.
    if _git(root, "ls-files", "--error-unmatch", "--", CONFIG_NAME)[0] == 0:
        raise ConfigError(
            f"{CONFIG_NAME} is tracked by git. It names YOUR machine and YOUR account:\n"
            f"  git rm --cached {CONFIG_NAME}   (it is gitignored; the template is "
            f"{EXAMPLE_NAME})")

    cp = configparser.ConfigParser(
        strict=True,                       # a duplicate section or key raises
        interpolation=None,                # a path with a % in it is a path, not a format
        default_section="_kitchen_has_no_default_section_")
    try:
        with open(path, encoding="utf-8") as f:
            cp.read_file(f, source=CONFIG_NAME)
    except configparser.Error as e:
        raise ConfigError(f"{CONFIG_NAME}: {e}")

    extra = [s for s in cp.sections() if s != SECTION]
    if extra:
        raise ConfigError(f"{CONFIG_NAME}: unknown section [{extra[0]}] "
                          f"(the only section is [{SECTION}])")
    if not cp.has_section(SECTION):
        raise ConfigError(f"{CONFIG_NAME}: no [{SECTION}] section (see {EXAMPLE_NAME})")

    for key in cp.options(SECTION):
        if key not in KEYS:
            raise ConfigError(f"{CONFIG_NAME}: unknown key {key!r} in [{SECTION}] "
                              f"(known keys: {', '.join(KEYS)})")
    for key in ("host", "scratch"):
        if not cp.get(SECTION, key, fallback="").strip():
            raise ConfigError(f"{CONFIG_NAME}: [{SECTION}] needs {key} = ... "
                              f"(see {EXAMPLE_NAME})")
    # The template ships <kvm-host> and <absolute path on that host>. Copied and not
    # filled in, those reach ssh as a hostname and a directory, and the error comes back
    # from ssh instead of from the file that is actually wrong.
    for key in cp.options(SECTION):
        v = cp.get(SECTION, key).strip()
        if v.startswith("<") and v.endswith(">"):
            raise ConfigError(f"{CONFIG_NAME}: {key} is still the template's "
                              f"placeholder {v!r} -- fill it in")

    host = cp.get(SECTION, "host").strip()
    if host.startswith("-"):
        raise ConfigError(f"{CONFIG_NAME}: host {host!r} starts with '-', which ssh would "
                          f"read as an option")
    if any(c.isspace() for c in host):
        raise ConfigError(f"{CONFIG_NAME}: host {host!r} contains whitespace")

    scratch = cp.get(SECTION, "scratch").strip()
    if not scratch.startswith("/"):
        raise ConfigError(f"{CONFIG_NAME}: scratch {scratch!r} must be an absolute path "
                          f"on {host} -- a relative one would depend on the login "
                          f"directory of an account this never looks at")

    vnc_ip, vnc_port = parse_vnc(cp.get(SECTION, "vnc", fallback="127.0.0.1:5900"))
    try:
        vnc_public = cp.getboolean(SECTION, "vnc_public", fallback=False)
    except ValueError:
        raise ConfigError(f"{CONFIG_NAME}: vnc_public must be yes or no")
    if not vnc_is_private(vnc_ip) and not vnc_public:
        raise ConfigError(
            f"{CONFIG_NAME}: vnc = {vnc_ip} is NOT a private address.\n"
            f"  A VNC server there is reachable from outside your network, and this one "
            f"has no password: anyone who can reach it has the keyboard of the machine "
            f"under test.\n"
            f"  Private means 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16 or fc00::/7.\n"
            f"  If you really mean it, add vnc_public = yes to [{SECTION}] -- every "
            f"launch will say so.")

    cfg = Config(host, scratch, vnc_ip, vnc_port, vnc_public, path)
    room = AF_UNIX_MAX - len(QMP_TAIL) - len(_run_rel("x" * RUN_ID_LEN)) - len("/boot-host")
    if len(scratch) > room:
        raise ConfigError(
            f"{CONFIG_NAME}: scratch is {len(scratch)} characters and the longest that "
            f"works is {room}.\n"
            f"  qemu's QMP socket lives under it, and a unix socket path is capped at "
            f"{AF_UNIX_MAX + 1} bytes. A longer path fails mid-boot with 'AF_UNIX path "
            f"too long'; this is the same length check, before anything is copied.")
    return cfg


def _git(root: str, *args: str) -> tuple:
    try:
        r = subprocess.run(["git", "-C", root, *args],
                           capture_output=True, text=True)
    except OSError:
        return 1, "", "git is not installed"
    return r.returncode, r.stdout, r.stderr


# ---------------------------------------------------------------------- paths ----

RUN_ID_LEN = 20                                   # 20260919-150405-a1b2


def _run_rel(run_id: str) -> str:
    return f"/runs/{run_id}/tmp"


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


# --------------------------------------------------------------------- driver ----

def ssh_argv(cfg: Config, tty: bool = False, tunnel: tuple | None = None) -> list:
    """The ssh command line every connection shares.

    BatchMode=yes is the load-bearing option: it turns every prompt -- password,
    passphrase, unknown host key -- into an immediate failure instead of a command that
    hangs waiting for a terminal that a build script does not have. That is also why
    nothing here has a username or password field to fill in.
    """
    a = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]
    a += ["-tt"] if tty else ["-T"]
    if tunnel:
        lport, rhost, rport = tunnel
        # ExitOnForwardFailure: without it ssh reports the busy port on stderr and carries
        # on, so the viewer here quietly attaches to whatever already owns that port --
        # which, for a second launch, is the PREVIOUS boot.
        a += ["-o", "ExitOnForwardFailure=yes", "-L", f"{lport}:{rhost}:{rport}"]
    return a


def rsync_e(cfg: Config) -> str:
    return " ".join(shlex.quote(x) for x in ssh_argv(cfg))


def agent_command(cfg: Config, apath: str, digest: str) -> str:
    """What the boot host is asked to run: verify the agent, then become it.

    The sha256 is checked ON THE BOOT HOST, immediately before the file runs as that
    account. rsync exiting 0 is a statement about a transfer; this is a statement about
    the bytes that are about to be executed -- including on a second run, which reuses
    an agent directory written by an earlier session.

    `exec` so the agent replaces the shell: a shell in the middle would swallow the
    signals that ssh's disconnect delivers, which is the mechanism the boot host uses to
    notice that the driver is gone.
    """
    return (f"printf '%s  %s\\n' {shlex.quote(digest)} {shlex.quote(apath)} "
            f"| sha256sum -c --quiet && "
            f"exec python3 {shlex.quote(apath)} agent {shlex.quote(cfg.scratch)}")


def agent_bytes() -> bytes:
    with open(os.path.abspath(__file__), "rb") as f:
        return f.read()


class Session:
    """One conversation with the boot host: send the agent, run something, copy back.

    The agent is started over a single ssh connection whose stdin stays open for the life
    of the session. That is not incidental -- it is how the boot host learns that the
    driver has gone. EOF on stdin, or thirty seconds without a heartbeat, and the agent
    kills the boot's process group. Without it a Ctrl-C here, or a dropped network, leaves
    a qemu running on someone else's machine with nobody to notice.
    """

    def __init__(self, cfg: Config, echo=print, verbose: bool = False):
        self.cfg = cfg
        self.echo = echo
        self.verbose = verbose
        self.proc: subprocess.Popen | None = None
        self.run_dir: str | None = None
        self.run_id: str | None = None
        self.facts: dict = {}
        self.keep_on_exit = False
        self._wlock = threading.Lock()
        self._beat: threading.Thread | None = None
        self._stop = threading.Event()
        self._errs: list = []

    # -- lifecycle ------------------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        # KEEP IS FOR EVIDENCE THAT DID NOT MAKE IT HERE, and nothing else. `any exception`
        # was the first rule and it is wrong: a Ctrl-C leaves a run directory on someone
        # else's machine, where it survives the stale sweep for a week because `.keep`
        # says somebody wanted it -- and nobody did. The only case worth keeping is a
        # copy-back that failed, where the boot host holds the only copy of the evidence.
        self.close(keep=self.keep_on_exit)
        return False

    def start(self) -> None:
        blob = agent_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        adir = f"{self.cfg.base}/agent/{digest[:12]}"
        apath = f"{adir}/boot_host.py"
        self._send_agent(adir, apath)

        # "--" before the host name, so a host that somehow begins with a dash is a host
        # and not an option. load() refuses one, and this is the second lock on the door.
        argv = ssh_argv(self.cfg) + ["--", self.cfg.host,
                                     agent_command(self.cfg, apath, digest)]
        if self.verbose:
            self.echo("  " + " ".join(shlex.quote(x) for x in argv))
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            raise Unavailable(f"could not run ssh: {e}")
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            hello = self._expect("hello")
        except Unavailable:
            # Nothing has entered the `with` block yet, so nothing will call __exit__ to
            # tidy up. An ssh left running here is an ssh nobody closes.
            self.close()
            raise
        self.facts = hello.get("facts", {})
        self._beat = threading.Thread(target=self._heartbeat, daemon=True)
        self._beat.start()

    def _send_agent(self, adir: str, apath: str) -> None:
        # --rsync-path carries the mkdir, so this is one connection rather than two. The
        # base directory is created 0700 the first time anything is sent: it holds images
        # under test and their evidence, and on a shared machine that is nobody else's
        # business. An existing directory keeps whatever mode it has, and the pre-run
        # check refuses it if that mode is loose.
        mk = (f"mkdir -p -m 700 {shlex.quote(self.cfg.base)} && "
              f"mkdir -p {shlex.quote(adir)} && rsync")
        r = self._rsync(["-a", "--rsync-path", mk,
                         os.path.abspath(__file__), f"{self.cfg.host}:{apath}"])
        if r.returncode != 0:
            err = r.stderr.strip() or f"rsync exit {r.returncode}"
            # THE MKDIR FAILING IS NOT THE LOGIN FAILING, and both say "Permission
            # denied". Measured: an unwritable scratch path gives rsync exit 12 with
            # "mkdir: cannot create directory '/root': Permission denied", which the
            # login-failure rule below matched happily -- so a wrong `scratch =` line
            # was reported as "hydra refused the key", sending the reader to run
            # ssh-copy-id against a host whose key already worked. The login DID work;
            # that is how the mkdir got far enough to fail.
            if "mkdir:" in err or "No such file or directory" in err:
                raise Unavailable(
                    f"{self.cfg.host} accepted the login, but this account cannot create "
                    f"{self.cfg.base}:\n    "
                    + "\n    ".join(err.splitlines()[:2])
                    + f"\n  Set scratch in {CONFIG_NAME} to a directory you can write "
                      f"on {self.cfg.host}.")
            if "rsync: command not found" in err or "rsync: not found" in err:
                raise Unavailable(f"{self.cfg.host} has no rsync: "
                                  f"sudo apt-get install rsync")
            raise Unavailable(self._ssh_hint(err))

    def _ssh_hint(self, err: str) -> str:
        """ssh's own failures, translated into the thing to go and do.

        Matched on ssh's signatures, not on a word. "Permission denied" alone is said by
        every program on the far side of the connection; ssh's own refusal names the
        methods it tried, in brackets.
        """
        low = err.lower()
        host = self.cfg.host
        if "permission denied (" in low or "no supported authentication" in low \
                or "too many authentication failures" in low:
            return (f"{host} refused the key.\n"
                    f"  This never asks for a password: it needs key-based login.\n"
                    f"    ssh-copy-id {host}   then   ssh {host} true")
        if "host key verification failed" in low or "known_hosts" in low:
            return (f"{host}'s host key is not known here.\n"
                    f"  Connect once by hand to check and accept it:  ssh {host} true")
        if "could not resolve" in low or "name or service not known" in low:
            return (f"{host} does not resolve. Check the name, or give ssh an alias for "
                    f"it in ~/.ssh/config")
        if "connection refused" in low or "connection timed out" in low \
                or "no route to host" in low:
            return f"{host} is not reachable: {err.splitlines()[0]}"
        return f"{host}: {err}"

    def _rsync(self, args: list, stdin: bytes | None = None):
        argv = ["rsync", "-e", rsync_e(self.cfg)] + args
        if self.verbose:
            self.echo("  " + " ".join(shlex.quote(x) for x in argv))
        try:
            return subprocess.run(argv, input=stdin, capture_output=True,
                                  text=stdin is None)
        except OSError as e:
            raise Unavailable(f"could not run rsync: {e}")

    # -- protocol -------------------------------------------------------------
    def send(self, **msg) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise Unavailable("the session is not open")
        line = json.dumps(msg) + "\n"
        with self._wlock:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                raise Unavailable(self._lost())

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self.send(op="ping")
            except Exception:                      # noqa: BLE001 -- the reader reports it
                return

    def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            line = line.rstrip("\n")
            if not line:
                continue
            self._errs.append(line)
            del self._errs[:-20]
            if self.verbose:
                self.echo(f"  ssh: {line}")

    def _lost(self) -> str:
        rc = self.proc.poll() if self.proc else None
        err = "\n  ".join(self._errs[-5:])
        if rc == 255 or (self._errs and rc not in (0, None)):
            return self._ssh_hint(err or f"ssh exited {rc}")
        return (f"the connection to {self.cfg.host} ended"
                + (f" (ssh exit {rc})" if rc not in (0, None) else "")
                + (f"\n  {err}" if err else ""))

    def _expect(self, kind: str, on_out=None) -> dict:
        """Pump the agent's stream until the awaited message arrives.

        Single-threaded on purpose: the only other thing that writes to the connection is
        the heartbeat, so there is no queue and no second reader to race with. Output from
        the run is relayed through `on_out` as it arrives rather than collected, because
        a boot test's value is partly in watching it.
        """
        assert self.proc is not None and self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            if not line.startswith(SENTINEL):
                # The login shell's own output. Shown, never parsed.
                if line.strip():
                    self.echo(f"  {self.cfg.host}: {line}")
                continue
            try:
                msg = json.loads(line[len(SENTINEL):])
            except ValueError:
                self.echo(f"  {self.cfg.host}: {line}")
                continue
            t = msg.get("t")
            if t == "out" and on_out is not None:
                on_out(msg.get("line", ""))
                continue
            if t == "note":
                self.echo(msg.get("line", ""))
                continue
            if t == "fatal":
                raise Unavailable(f"{self.cfg.host}: {msg.get('why', 'agent failed')}")
            if t == kind:
                return msg
            if self.verbose:
                self.echo(f"  (ignored {t})")
        raise Unavailable(self._lost())

    def close(self, keep: bool = False) -> None:
        self._stop.set()
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                self.send(op="done", keep=keep)
                # Waited for, not fired and forgotten. This is the only report that the
                # boot host is back as it was found, and a run directory that survived a
                # clean exit is a fact worth hearing rather than one to discover a week
                # later in `boot-host check`.
                bye = self._expect("bye")
                left = bye.get("dir") or self.run_dir
                if self.run_dir and not bye.get("removed"):
                    if bye.get("kept"):
                        self.echo(f"  {Y}kept on {self.cfg.host}: {left}{O}")
                    else:
                        self.echo(f"  {Y}could not remove {left} on {self.cfg.host}; "
                                  f"the next run sweeps it{O}")
                self.proc.stdin.close()
        except Exception:                          # noqa: BLE001 -- closing anyway
            pass
        # Drained before waiting. The agent still has a `bye` to write, and a child that
        # cannot finish writing to a full pipe is a child that never exits -- a deadlock
        # in the one code path that runs after every failure.
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.read()
        except Exception:                          # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc = None

    def request(self, kind: str, **msg) -> dict:
        """Send one message and wait for its answer."""
        self.send(**msg)
        return self._expect(kind)

    # -- steps ----------------------------------------------------------------
    def check(self, uefi: bool = False) -> dict:
        self.send(op="check", uefi=uefi)
        return self._expect("check")

    def sweep(self) -> dict:
        self.send(op="sweep")
        return self._expect("swept")

    def open_run(self) -> dict:
        self.send(op="open", run_id=new_run_id())
        msg = self._expect("run")
        self.run_dir, self.run_id = msg["dir"], msg["id"]
        return msg

    def push_tree(self) -> int:
        """The working tree, exactly as a local run would see it.

        `git ls-files --cached --others --exclude-standard` rather than a copy of the
        directory: it is the same set a local `kitchen test` reads, it skips out/, work/
        and isos/ (hundreds of megabytes of build output that the boot host has no use
        for), and -- because boot-host.ini is gitignored -- it can never send the
        configuration file naming the machine it is being sent to.
        """
        files = tree_files(REPO_ROOT)
        if not files:
            raise Unavailable("git listed no files to send -- is this a checkout?")
        payload = b"".join(p.encode() + b"\0" for p in files)
        r = self._rsync(["-a", "--from0", "--files-from=-", REPO_ROOT + "/",
                         f"{self.cfg.host}:{self.run_dir}/tree/"], stdin=payload)
        if r.returncode != 0:
            raise Unavailable("could not send the tree: "
                              + r.stderr.decode(errors="replace").strip())
        return len(files)

    def push_iso(self, iso: str) -> tuple:
        """Send the image once per content, not once per run.

        Content-addressed because the name is not the fact that matters: two runs of
        `kitchen build` produce out/<same name>.iso with different bytes, and a cache keyed
        on the name would boot the first one forever. The sha256 is computed here, checked
        there, and the file only becomes visible under its real name once it matches.
        """
        digest = sha256_file(iso)
        name = os.path.basename(iso)
        self.send(op="cache", sha256=digest, name=name)
        msg = self._expect("cache")
        sent = 0
        if not msg.get("have"):
            r = self._rsync(["-a", "--partial", iso,
                             f"{self.cfg.host}:{msg['partial']}"])
            if r.returncode != 0:
                raise Unavailable("could not send the image: " + r.stderr.strip())
            sent = os.path.getsize(iso)
        self.send(op="stage", sha256=digest, name=name)
        st = self._expect("staged")
        if not st.get("ok"):
            raise Unavailable(st.get("why", "the image did not verify on the boot host"))
        return st["path"], sent

    def push_file(self, local: str, remote: str) -> None:
        r = self._rsync(["-a", local, f"{self.cfg.host}:{remote}"])
        if r.returncode != 0:
            raise Unavailable(f"could not send {local}: " + r.stderr.strip())

    def pull(self, remote: str, local: str, extra: list | None = None,
             optional: bool = False) -> None:
        # --ignore-missing-args (rsync >= 3.1) for a file the run MIGHT have produced.
        # Two of them are optional by nature: runs.jsonl exists only if a boot recorded
        # one, and a golden only if the boot had serial output to write it from. Without
        # this, asking for one that is not there fails with rsync exit 23 at the very end
        # of an otherwise successful run -- after the evidence is already home.
        if optional:
            extra = (extra or []) + ["--ignore-missing-args"]
        # --no-owner --no-group: `-a` implies -o -g, and a driver running as root then
        # chowns the evidence to the BOOT HOST's numeric uid. Observed: a serial log and
        # a screenshot landing here as uid 1000 because that is fullaxx over there, in a
        # directory belonging to the person who asked for the test. The boot happened
        # elsewhere; the evidence is theirs.
        os.makedirs(os.path.dirname(local.rstrip("/")) or ".", exist_ok=True)
        r = self._rsync(["-a", "--no-owner", "--no-group", "--sparse"] + (extra or [])
                        + [f"{self.cfg.host}:{remote}", local])
        if r.returncode != 0:
            raise Unavailable(f"could not copy back {remote}: " + r.stderr.strip())

    def listing(self) -> list:
        self.send(op="list")
        return self._expect("list").get("files", [])

    def run(self, argv: list, on_out) -> dict:
        self.send(op="run", argv=argv)
        return self._expect("rc", on_out=on_out)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_files(root: str) -> list:
    """Tracked plus untracked-but-not-ignored, minus submodules and deleted files.

    -z throughout: git quotes paths containing spaces or non-ASCII in its normal output,
    and a quoted path handed to rsync names a file that does not exist. Gitlinks (mode
    160000) are directory pointers, not files; rsync cannot send one and the boot test
    does not read vendor/linux-live.
    """
    rc, out, err = _git(root, "ls-files", "-s", "-z")
    if rc != 0:
        raise Unavailable(f"git ls-files failed: {err.strip()}")
    files = []
    for ent in out.split("\0"):
        if not ent:
            continue
        meta, _, path = ent.partition("\t")
        if meta.split(" ", 1)[0] != "160000":
            files.append(path)
    _rc, out, _ = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    files += [p for p in out.split("\0") if p]
    _rc, out, _ = _git(root, "ls-files", "--deleted", "-z")
    gone = {p for p in out.split("\0") if p}
    return sorted(set(files) - gone)


# ------------------------------------------------------------------- commands ----

def _echo(line: str = "") -> None:
    print(line, flush=True)


def _paths_home(text: str, pairs: list) -> str:
    """Rewrite the boot host's paths into local ones.

    Everything this run produced is copied back here, so a line saying where something
    landed must say where it landed HERE. A path under the remote run directory is a path
    nobody can open -- and the first version rewrote only the image and the evidence
    directory, so the golden line read "20260919-153351-825f/golden/g.testkit" instead of
    the /tmp/g.testkit it had just written.

    `pairs` is longest-first: the run directory itself is the last resort, after every
    path inside it that has a local counterpart has had its turn.
    """
    for remote, local in pairs:
        if remote:
            text = text.replace(remote, local)
    return text


def cmd_test(cfg: Config, args: list) -> int:
    """The boot half of `kitchen test`, run on the boot host.

    lib/build.sh has already parsed the flags and decided the modes; this receives the
    paths it must translate (--iso, --out, --golden, --record) and passes everything else
    through untouched, so a new boot flag needs no change here.
    """
    paths = {"--iso": "", "--out": "", "--golden": "", "--record": ""}
    tail = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in paths:
            # BOUNDS-CHECKED. `args[i + 1]` on a trailing "--golden" is an IndexError and
            # a traceback, which is the failure mode this project spent four commits
            # removing from the commands that read an image.
            if i + 1 >= len(args):
                print(f"boot-host: {a} needs a value", file=sys.stderr)
                return EXIT_CONFIG
            paths[a] = args[i + 1]; i += 2
        elif a == "--":
            tail += args[i + 1:]; break
        else:
            tail.append(a); i += 1
    iso, out = paths["--iso"], paths["--out"]
    golden, record = paths["--golden"], paths["--record"]
    if not iso:
        print("boot-host: no --iso", file=sys.stderr)
        return EXIT_CONFIG
    out = out or os.path.join(os.path.dirname(iso) or ".", "boot-tests")
    os.makedirs(out, exist_ok=True)

    # BOTH SPELLINGS. lib/build.sh accepts `--uefi` and `--uefi-boot` for the same mode,
    # so matching one of them is a pre-run check that misses half the time it is needed --
    # and the symptom is a boot failing on the host for want of OVMF, which is precisely
    # what checking here is meant to prevent.
    wants_uefi = any(a in ("--uefi", "--uefi-boot") for a in tail)
    t0 = time.time()
    with Session(cfg, echo=_echo) as s:
        chk = s.check(uefi=wants_uefi)
        if not chk.get("ok"):
            raise Unavailable(_problems(cfg, chk))
        node = chk.get("facts", {}).get("node", cfg.host)
        _echo(f"  {os.path.basename(iso)} -> {cfg.host}"
              f"  ({node}, qemu {chk['facts'].get('qemu', '?')}, "
              f"{'KVM' if chk['facts'].get('kvm') else 'no KVM'})")
        swept = s.sweep()
        if swept.get("runs_removed") or swept.get("killed"):
            _echo(f"  {D}swept {swept['runs_removed']} abandoned run(s)"
                  + (f", killed {len(swept['killed'])} stray process(es)"
                     if swept.get("killed") else "") + O)
        run = s.open_run()
        run_dir = run["dir"]

        n = s.push_tree()
        iso_remote, sent = s.push_iso(iso)
        gold_remote = ""
        if golden:
            gold_remote = f"{run_dir}/golden/{os.path.basename(golden)}"
            if os.path.exists(golden):
                s.push_file(golden, gold_remote)
        _echo(f"  {D}sent {n} files"
              + (f" and {sent // 1048576} MiB of image" if sent
                 else " (image already cached)")
              + f", {time.time() - t0:.1f}s{O}")

        argv = ["test", iso_remote, "--out", f"{run_dir}/out"]
        if gold_remote:
            argv += ["--golden", gold_remote]
        if record:
            argv += ["--record", f"{run_dir}/runs.jsonl"]
        argv += tail

        # Specific before general: gold_remote and runs.jsonl both live UNDER run_dir, so
        # the catch-all for the run directory has to come last or it would shadow them.
        pairs = [(gold_remote, golden),
                 (f"{run_dir}/runs.jsonl", record),
                 (iso_remote, iso),
                 (f"{run_dir}/out", out.rstrip("/")),
                 (f"{run_dir}/tree/", ""),
                 (run_dir + "/", os.path.basename(run_dir) + "/")]

        def relay(line: str) -> None:
            _echo(_paths_home(line, pairs))

        res = s.run(argv, on_out=relay)
        # From here until the copy-back lands, the boot host holds the ONLY copy of what
        # this run produced. If anything below fails, the run directory stays there to be
        # fetched by hand rather than being swept away with the evidence in it.
        s.keep_on_exit = True
        rc = int(res.get("rc", 1))
        for p in res.get("problems", []):
            _echo(f"  {R}FAIL{O} {cfg.host}: {p}")
            rc = 1

        remote_files = s.listing()
        _drop_stale_shots(out, remote_files)
        # --exclude=/pids/, never --delete: the pid files are the boot host's own
        # bookkeeping and mean nothing here, and everything else in the evidence
        # directory belongs to whoever put it there.
        s.pull(f"{run_dir}/out/", out.rstrip("/") + "/", ["--exclude=/pids/"])
        if record:
            _append_record(s, run_dir, record)
        if golden and not os.path.exists(golden) and gold_remote:
            # Created by the run: a first sweep for an image has no golden to send, and
            # the block the boot produced is the thing worth keeping.
            s.pull(gold_remote, golden, optional=True)
            if os.path.exists(golden):
                _echo(f"  {D}golden written: {golden}{O}")
        s.keep_on_exit = False                     # it is all here now
        _echo(f"  {D}evidence in {out}{O}")
    return rc


def _append_record(s: Session, run_dir: str, record: str) -> None:
    tmp = tempfile.mkdtemp(prefix="bh-rec-")
    try:
        local = os.path.join(tmp, "runs.jsonl")
        s.pull(f"{run_dir}/runs.jsonl", local, optional=True)
        if os.path.exists(local):
            os.makedirs(os.path.dirname(os.path.abspath(record)), exist_ok=True)
            # Appended, never copied over: ci/tier-c.sh points every boot of a sweep at
            # one file and reads it afterwards, so a copy would keep only the last boot.
            with open(local, encoding="utf-8") as src, \
                    open(record, "a", encoding="utf-8") as dst:
                dst.write(src.read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _drop_stale_shots(out: str, remote_files: list) -> None:
    """A screenshot from an older run must not survive as this run's evidence.

    qemu_boot.py unlinks its own outputs before it starts, which protects a LOCAL re-run.
    It cannot protect this one: the boot happened on another machine, and a boot that
    produced no screenshot copies nothing back, leaving last week's PNG sitting beside
    this week's serial log with a fresh timestamp from rsync. That is a stale artifact
    presented as evidence, which this project treats as worse than no evidence.
    """
    produced = {os.path.basename(p) for p in remote_files}
    for name in produced:
        if not name.endswith(".serial.log"):
            continue
        png = name[:-len(".serial.log")] + ".png"
        if png not in produced:
            stale = os.path.join(out, png)
            if os.path.exists(stale):
                os.unlink(stale)
                _echo(f"  {D}removed {png}: this boot produced no screenshot{O}")


def _problems(cfg: Config, chk: dict) -> str:
    lines = [f"{cfg.host} cannot run boot tests:"]
    for p in chk.get("problems", []):
        lines.append(f"  - {p}")
    return "\n".join(lines)


def cmd_check(cfg: Config, verbose: bool = False) -> int:
    _echo(f"{B}boot host{O}  {cfg.host}")
    _echo(f"  workdir  {cfg.base}  {D}(everything this writes lives here){O}")
    where = "tunnelled through ssh" if cfg.tunnelled else "connected to directly"
    warn = "" if vnc_is_private(cfg.vnc_ip) else f"  {R}PUBLIC ADDRESS{O}"
    _echo(f"  vnc      {cfg.vnc}  ({where}){warn}")
    with Session(cfg, echo=_echo, verbose=verbose) as s:
        chk = s.check(uefi=True)
        f = chk.get("facts", {})
        _echo(f"  host     {f.get('node', '?')}, {f.get('os', '?')}")
        for line in chk.get("ok_lines", []):
            _echo(f"  {G}ok{O}   {line}")
        for p in chk.get("problems", []):
            _echo(f"  {R}FAIL{O} {p}")
        swept = s.sweep()
        _echo(f"  {D}{swept.get('runs_left', 0)} run(s) kept, "
              f"cache {swept.get('cache_mb', 0)} MiB in "
              f"{swept.get('cache_n', 0)} image(s){O}")
        if chk.get("ok"):
            _echo(f"\n{G}ready{O}  boots run on {cfg.host}")
            return 0
    _echo(f"\n{R}not ready{O}  fix the above, or set KITCHEN_BOOT_HOST=local to boot here")
    return EXIT_UNAVAILABLE


def cmd_clean(cfg: Config) -> int:
    with Session(cfg, echo=_echo) as s:
        msg = s.request("cleaned", op="clean")
    _echo(f"  removed {msg.get('runs', 0)} run(s), {msg.get('images', 0)} cached "
          f"image(s), {msg.get('agents', 0)} old agent(s) -- {msg.get('mb', 0)} MiB")
    _echo(f"  {D}kept {cfg.base}/disks (yours, not this tool's){O}")
    return 0


# ---------------------------------------------------------------------- agent ----
#
# EVERYTHING BELOW RUNS ON THE BOOT HOST, started by the driver above as
# `python3 boot_host.py agent <scratch>`. It talks JSON lines over stdin/stdout and must
# not assume anything about that machine except python3 >= 3.9 and a POSIX filesystem.

class Agent:
    def __init__(self, scratch: str):
        self.scratch = scratch
        self.base = scratch.rstrip("/") + "/boot-host"
        self.run_dir = ""
        self.run_lock = None
        self.boot_lock = None
        self.child: subprocess.Popen | None = None
        self.keep = False
        self.last = time.monotonic()
        self.out_lock = threading.Lock()
        self.done = threading.Event()

    # -- plumbing -------------------------------------------------------------
    def emit(self, **msg) -> None:
        with self.out_lock:
            sys.stdout.write(SENTINEL + json.dumps(msg) + "\n")
            sys.stdout.flush()

    def fatal(self, why: str) -> None:
        self.emit(t="fatal", why=why)
        self.cleanup(keep=True)
        os._exit(EXIT_UNAVAILABLE)

    def serve(self) -> None:
        try:
            # 0700 on the directory THIS created, and never on one that was already
            # there: the mode of a directory somebody else made is their decision, and
            # op_check reports a loose one rather than silently changing it.
            fresh = not os.path.isdir(self.base)
            os.makedirs(self.base, exist_ok=True)
            if fresh:
                os.chmod(self.base, 0o700)
            with open(os.path.join(self.base, MARKER), "w") as f:
                f.write("slax-kitchen boot host scratch area; safe to delete when idle\n")
            for sub in ("agent", "cache", "runs", "disks"):
                os.makedirs(os.path.join(self.base, sub), exist_ok=True)
        except OSError as e:
            self.emit(t="fatal", why=f"cannot use {self.base}: {e}")
            os._exit(EXIT_UNAVAILABLE)
        self.emit(t="hello", facts={"node": socket.gethostname(),
                                    "python": sys.version.split()[0],
                                    "base": self.base})
        threading.Thread(target=self._watchdog, daemon=True).start()
        for raw in sys.stdin:
            self.last = time.monotonic()
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            try:
                self.dispatch(msg)
            except Exception as e:                 # noqa: BLE001
                self.fatal(f"{type(e).__name__}: {e}")
            if self.done.is_set():
                break
        self.cleanup(keep=self.keep)

    def _watchdog(self) -> None:
        """The driver's absence is not the guest's problem to survive.

        A boot test holds a qemu with a virtual machine in it. If the driver is killed,
        or the network drops, nothing else on this machine knows that the reason for that
        guest has gone -- ssh's own keepalives tell the CLIENT the server is gone, not the
        other way round. So the agent listens for a heartbeat and, after thirty seconds of
        silence, takes the whole process group down.
        """
        while not self.done.wait(1.0):
            if time.monotonic() - self.last > WATCHDOG_S:
                self.emit(t="note", line="  boot host: no heartbeat for "
                                         f"{WATCHDOG_S}s, stopping the boot")
                self.kill_child()
                self.cleanup(keep=False)
                os._exit(EXIT_UNAVAILABLE)

    # A NAME FROM THE DRIVER IS ONE PATH COMPONENT, and this is where that is enforced.
    # Every one of them -- the run id, the cache's sha256, the image's basename -- is
    # joined onto a directory here, and os.path.join with an absolute or climbing value
    # walks straight out of the scratch area the whole design promises to stay inside.
    # The driver only ever sends well-formed values; that is an argument for this being
    # cheap, not for leaving it out.
    @staticmethod
    def _component(value, what: str) -> str:
        if not isinstance(value, str) or not value or value in (".", "..") \
                or "/" in value or "\0" in value:
            raise ValueError(f"{what} is not a single path component: {value!r}")
        return value

    def _open_run(self) -> str:
        """The run directory, or a refusal.

        Without this, an op arriving before `open` joins its name onto "" and writes a
        relative path into whatever the agent's working directory happens to be -- the
        login directory of the account on the boot host.
        """
        if not self.run_dir:
            raise RuntimeError("no run is open: the driver sent this out of order")
        return self.run_dir

    def dispatch(self, msg: dict) -> None:
        op = msg.get("op")
        if op == "ping":
            return
        fn = getattr(self, "op_" + str(op), None)
        if fn is None:
            self.fatal(f"unknown op {op!r} -- driver and agent disagree")
        fn(msg)

    # -- operations -----------------------------------------------------------
    def op_check(self, msg: dict) -> None:
        ok_lines, problems, facts = [], [], {"node": socket.gethostname()}
        facts["os"] = _os_pretty()

        st = os.stat(self.base)
        if st.st_uid != os.getuid():
            problems.append(f"{self.base} is owned by uid {st.st_uid}, not this account")
        elif st.st_mode & 0o022:
            problems.append(f"{self.base} is writable by group or others "
                            f"(mode {st.st_mode & 0o777:04o}): chmod 700 {self.base}")
        else:
            ok_lines.append(f"{self.base} is yours and private")

        # /usr/sbin appended because an ssh command runs a NON-LOGIN shell, whose PATH
        # routinely lacks it -- and that is where mkfs.ext4 lives. Reporting an installed
        # tool as missing would send somebody to install what they already have.
        path = os.environ.get("PATH", "") + ":/usr/sbin:/sbin:/usr/local/sbin"
        found = []
        for tool, pkg in (("qemu-system-x86_64", "qemu-system-x86"),
                          ("qemu-img", "qemu-utils"),
                          ("xorriso", "xorriso"),
                          ("mkfs.ext4", "e2fsprogs"),
                          ("rsync", "rsync")):
            if shutil.which(tool, path=path) is None:
                problems.append(f"{tool} is not installed: sudo apt-get install {pkg}")
            else:
                found.append(tool)
        # Named, not counted. A green line reading "5 tools ok" is the same sentence
        # whether it checked the right five or not; this one can be read back against
        # what a boot actually runs.
        if found:
            ok_lines.append("tools: " + ", ".join(found))
        if sys.version_info < (3, 9):
            problems.append(f"python3 is {sys.version.split()[0]}; this needs 3.9 or newer")
        else:
            ok_lines.append(f"python3 {sys.version.split()[0]}")

        qemu = shutil.which("qemu-system-x86_64", path=path)
        if qemu:
            facts["qemu"] = _qemu_version(qemu)
            ok_lines.append(f"qemu-system-x86_64 {facts['qemu']}")

        # A BOOT HOST IS A KVM HOST. Falling back to TCG here would defeat the entire
        # point of sending the work to another machine -- and silently, since a TCG boot
        # passes, only slowly. The ledger records accel per row, so a TCG run would also
        # quietly weaken evidence that says KVM.
        facts["kvm"] = os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.W_OK)
        if not os.path.exists("/dev/kvm"):
            problems.append("/dev/kvm does not exist: this machine has no KVM "
                            "(check virtualisation is enabled in its firmware, and "
                            "that kvm_intel or kvm_amd is loaded)")
        elif not facts["kvm"]:
            problems.append(_kvm_advice())
        else:
            ok_lines.append("/dev/kvm is writable by this account")

        if msg.get("uefi"):
            if _find_ovmf():
                ok_lines.append("OVMF firmware for --uefi")
            else:
                problems.append("no OVMF firmware, which --uefi needs: "
                                "sudo apt-get install ovmf")

        free = shutil.disk_usage(self.base).free
        facts["free_gb"] = round(free / 2**30, 1)
        if free < 3 * 2**30:
            problems.append(f"only {facts['free_gb']} GiB free under {self.scratch}; "
                            f"an image, its evidence and a persistence disk need ~3 GiB")
        else:
            ok_lines.append(f"{facts['free_gb']} GiB free under {self.scratch}")

        sample = f"{self.base}/runs/{'x' * RUN_ID_LEN}/tmp{QMP_TAIL}"
        if len(sample) > AF_UNIX_MAX:
            problems.append(f"{self.base} is too deep: qemu's QMP socket would be "
                            f"{len(sample)} bytes and a unix socket path stops at "
                            f"{AF_UNIX_MAX + 1}")

        self.emit(t="check", ok=not problems, problems=problems,
                  ok_lines=ok_lines, facts=facts)

    def op_sweep(self, msg: dict) -> None:
        removed, killed = 0, []
        runs = os.path.join(self.base, "runs")
        now = time.time()
        for name in sorted(os.listdir(runs)):
            d = os.path.join(runs, name)
            if not os.path.isdir(d):
                continue
            held = _locked(os.path.join(d, ".lock"))
            if held is None:                       # someone is using it right now
                continue
            held.close()
            if os.path.exists(os.path.join(d, ".keep")):
                if now - os.path.getmtime(d) < KEEP_DAYS * 86400:
                    continue
            killed += _kill_orphans(d)
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        cache_n, cache_b, cache_removed = 0, 0, 0
        cache = os.path.join(self.base, "cache")
        for name in sorted(os.listdir(cache)):
            d = os.path.join(cache, name)
            if not os.path.isdir(d):
                continue
            if now - os.path.getmtime(d) > CACHE_DAYS * 86400:
                shutil.rmtree(d, ignore_errors=True)
                cache_removed += 1
                continue
            cache_n += 1
            cache_b += sum(os.path.getsize(os.path.join(d, f))
                           for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        left = len([n for n in os.listdir(runs) if os.path.isdir(os.path.join(runs, n))])
        self.emit(t="swept", runs_removed=removed, killed=killed,
                  cache_removed=cache_removed, runs_left=left,
                  cache_n=cache_n, cache_mb=cache_b // 1048576)

    def op_open(self, msg: dict) -> None:
        run_id = self._component(msg.get("run_id"), "run_id")
        self.run_dir = os.path.join(self.base, "runs", run_id)
        for sub in ("", "tree", "out", "tmp", "golden"):
            os.makedirs(os.path.join(self.run_dir, sub), exist_ok=True)
        # Held for the life of the agent. A stale sweep by ANOTHER session tests exactly
        # this lock to decide whether a run directory is abandoned, so the lock must
        # outlive every operation in between -- which is why it is an open file descriptor
        # on the instance rather than a context manager around the run.
        self.run_lock = open(os.path.join(self.run_dir, ".lock"), "w")
        fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.emit(t="run", id=run_id, dir=self.run_dir)

    def op_cache(self, msg: dict) -> None:
        d = os.path.join(self.base, "cache", self._component(msg.get("sha256"), "sha256"))
        os.makedirs(d, exist_ok=True)
        final = os.path.join(d, self._component(msg.get("name"), "image name"))
        self.emit(t="cache", have=os.path.exists(final),
                  partial=final + ".partial", path=final)

    def op_stage(self, msg: dict) -> None:
        run_dir = self._open_run()
        name = self._component(msg.get("name"), "image name")
        d = os.path.join(self.base, "cache", self._component(msg.get("sha256"), "sha256"))
        final = os.path.join(d, name)
        partial = final + ".partial"
        if not os.path.exists(final):
            if not os.path.exists(partial):
                self.emit(t="staged", ok=False, why="the image did not arrive")
                return
            got = _sha256(partial)
            if got != msg["sha256"]:
                # Verified HERE, by the machine that will boot it. rsync reporting success
                # is a claim about a transfer; this is a claim about the file.
                os.unlink(partial)
                self.emit(t="staged", ok=False,
                          why=f"the image arrived corrupted (sha256 {got[:12]}, "
                              f"expected {msg['sha256'][:12]})")
                return
            os.rename(partial, final)
        os.utime(d, None)                          # last used: the cache expiry reads this
        link = os.path.join(run_dir, name)
        if os.path.exists(link):
            os.unlink(link)
        try:
            # A hard link, so a 440 MiB image is not copied per run and the run directory
            # still sees a plain file at a short path.
            os.link(final, link)
        except OSError:
            shutil.copy2(final, link)
        self.emit(t="staged", ok=True, path=link)

    def op_run(self, msg: dict) -> None:
        """Start the boot and return AT ONCE, relaying from a thread.

        The relay cannot run on the main loop. That loop is the only thing reading stdin,
        stdin is where the heartbeat arrives, and the watchdog kills the boot after thirty
        silent seconds -- so relaying inline would make every boot longer than half a
        minute kill itself, which is every boot. Found by reading the watchdog next to the
        loop it starves; a first version had exactly that.
        """
        threading.Thread(target=self._run, args=(msg,), daemon=True).start()

    def _run(self, msg: dict) -> None:
        try:
            self._run_locked(msg)
        except Exception as e:                     # noqa: BLE001 -- a thread's last chance
            # In a thread, so dispatch()'s try/except cannot see this. An unreported
            # exception here would leave the driver waiting for an `rc` that never comes,
            # which reads as a hung boot rather than a broken agent.
            self.emit(t="fatal", why=f"{type(e).__name__}: {e}")
            self.cleanup(keep=True)
            os._exit(EXIT_UNAVAILABLE)

    def _run_locked(self, msg: dict) -> None:
        run_dir = self._open_run()
        self.boot_lock = open(os.path.join(self.base, "boot.lock"), "w")
        try:
            fcntl.flock(self.boot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Serialised deliberately. The menu modes measure keystroke timing against a
            # countdown, so two boots sharing this machine's CPU do not merely go slower,
            # they miss the menu and assert against an entry nobody selected.
            self.emit(t="note", line="  boot host: waiting for another run to finish")
            fcntl.flock(self.boot_lock, fcntl.LOCK_EX)

        env = dict(os.environ)
        env["KITCHEN_BOOT_HOST"] = "local"         # the tree over there must not recurse
        # ...and it should not ANNOUNCE that, either. The copy of `kitchen` running here
        # is nested inside a remote boot that has already said where it is; its "this boot
        # stays here" note, relayed home, reads as though the boot never left.
        env["KITCHEN_BOOT_HOST_AGENT"] = "1"
        env["TMPDIR"] = os.path.join(run_dir, "tmp")
        env["PATH"] = env.get("PATH", "") + ":/usr/sbin:/sbin"
        env["NO_COLOR"] = "1"
        argv = ["sh", os.path.join(run_dir, "tree", "kitchen")] + list(msg["argv"])
        # start_new_session, so qemu and everything else the test starts share ONE process
        # group that can be signalled as a unit. Killing the shell alone leaves the guest.
        self.child = subprocess.Popen(
            argv, cwd=os.path.join(run_dir, "tree"), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True)
        for line in self.child.stdout:
            self.emit(t="out", line=line.rstrip("\n"))
        rc = self.child.wait()
        self.child = None
        problems = self._leftovers(run_dir)
        fcntl.flock(self.boot_lock, fcntl.LOCK_UN)
        self.boot_lock.close()
        self.boot_lock = None
        self.emit(t="rc", rc=rc, problems=problems)

    def _leftovers(self, run_dir: str) -> list:
        """What the run left behind. Named, and failing the run.

        TMPDIR pointed at this run's own directory is what makes this possible: a leaked
        QMP socket directory is normally invisible among everyone else's /tmp, and
        ci/tier-c.sh's leak count could only ever be a count. Here the whole directory
        belongs to one boot, so anything still in it has a name.
        """
        problems = []
        tmp = os.path.join(run_dir, "tmp")
        left = sorted(os.listdir(tmp)) if os.path.isdir(tmp) else []
        if left:
            problems.append("the run left " + ", ".join(left[:5])
                            + (f" and {len(left) - 5} more" if len(left) > 5 else "")
                            + " in its temporary directory")
        piddir = os.path.join(run_dir, "out", "pids")
        for pf in sorted(os.listdir(piddir)) if os.path.isdir(piddir) else []:
            full = os.path.join(piddir, pf)
            try:
                pid = int(open(full).read().strip())
            except (OSError, ValueError):
                continue
            # qemu_boot.py removes its own pid file on the way out, so one still here is
            # a guest nobody stopped.
            problems.append(f"a guest was left running ({pf}); it has been killed")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            os.unlink(full)
        return problems

    def op_list(self, msg: dict) -> None:
        out = os.path.join(self._open_run(), "out")
        files = []
        for dirpath, _dirs, names in os.walk(out):
            for n in names:
                files.append(os.path.relpath(os.path.join(dirpath, n), out))
        self.emit(t="list", files=sorted(files))

    def op_clean(self, msg: dict) -> None:
        runs = images = agents = 0
        freed = 0
        for sub, kind in (("runs", "runs"), ("cache", "images"), ("agent", "agents")):
            d = os.path.join(self.base, sub)
            for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                full = os.path.join(d, name)
                if not os.path.isdir(full):
                    continue
                if sub == "runs":
                    held = _locked(os.path.join(full, ".lock"))
                    if held is None:
                        continue                   # in use by another session
                    held.close()
                if sub == "agent" and full == os.path.dirname(os.path.abspath(__file__)):
                    continue                       # the agent running this very command
                freed += _du(full)
                shutil.rmtree(full, ignore_errors=True)
                if sub == "runs":
                    runs += 1
                elif sub == "cache":
                    images += 1
                else:
                    agents += 1
        # B/disks is never touched: tools/qemu/boot.py's relative --disk paths land there,
        # so it holds work somebody asked for, not this tool's bookkeeping.
        self.emit(t="cleaned", runs=runs, images=images, agents=agents,
                  mb=freed // 1048576)

    def op_done(self, msg: dict) -> None:
        """Tear down HERE, and say what happened, before the connection goes.

        Teardown used to run after the driver had already walked away, in serve()'s tail.
        Whatever it did -- or failed to do -- was then unobservable from the other end: a
        run directory survived a clean exit once during the first end-to-end runs and
        there was nothing to say why, because the only witness had already exited. Doing
        it inside the dispatch means an exception is caught and reported like any other,
        and the answer travels back on the connection that asked.
        """
        self.keep = bool(msg.get("keep"))
        left = self.run_dir
        self.cleanup(keep=self.keep)
        removed = bool(left) and not os.path.exists(left)
        # serve()'s tail cleans up too, for the case this message never arrives -- a
        # driver that was killed. Having done it here, forget the directory, so that
        # second call cannot touch a path this one has already dealt with.
        self.run_dir = ""
        self.done.set()
        self.emit(t="bye", removed=removed, kept=self.keep, dir=left or "")

    # -- shutdown -------------------------------------------------------------
    def kill_child(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        try:
            os.killpg(self.child.pid, signal.SIGTERM)
        except OSError:
            return
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except OSError:
                pass

    def cleanup(self, keep: bool) -> None:
        self.kill_child()
        if not self.run_dir:
            return
        if keep:
            try:
                open(os.path.join(self.run_dir, ".keep"), "w").close()
            except OSError:
                pass
            return
        if self.run_lock is not None:
            try:
                self.run_lock.close()
            except OSError:
                pass
            self.run_lock = None
        shutil.rmtree(self.run_dir, ignore_errors=True)


def _locked(path: str):
    """Open `path` and take its lock, or None if somebody already holds it.

    The caller closes what it gets back. This is how an abandoned run is told from a
    live one without a pid file, a timestamp or a guess: the lock is held by an open
    file descriptor in the agent process, so the kernel releases it exactly when that
    process ends, however it ends.
    """
    try:
        f = open(path, "a+")
    except OSError:
        return None
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _kill_orphans(run_dir: str) -> list:
    """Kill what this abandoned run left running -- and only that.

    /proc/<pid>/cmdline must name the run directory. Never pkill qemu: a boot host is
    somebody's machine, and a sweep that takes out their other virtual machines is a
    far worse bug than a leaked guest.
    """
    killed = []
    piddir = os.path.join(run_dir, "out", "pids")
    for pf in sorted(os.listdir(piddir)) if os.path.isdir(piddir) else []:
        try:
            pid = int(open(os.path.join(piddir, pf)).read().strip())
        except (OSError, ValueError):
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if run_dir in cmd:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass
    return killed


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _du(path: str) -> int:
    total = 0
    for dirpath, _d, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


def _qemu_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    m = re.search(r"\bversion (\d+(?:\.\d+)+)", out)
    return m.group(1) if m else "unknown"


def _os_pretty() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return os.uname().sysname


def _find_ovmf() -> bool:
    if os.environ.get("OVMF_CODE"):
        return os.path.isfile(os.environ["OVMF_CODE"])
    for d in ("/usr/share/OVMF", "/usr/share/ovmf", "/usr/share/edk2/ovmf",
              "/usr/share/qemu"):
        for n in ("OVMF_CODE_4M.fd", "OVMF_CODE.fd", "OVMF.fd"):
            if os.path.isfile(os.path.join(d, n)):
                return True
    return False


def _kvm_advice() -> str:
    """Why /dev/kvm is there and still not writable, and what to do about it.

    Three different causes with three different fixes, and `kitchen doctor` learned the
    same lesson locally: telling someone to add themselves to a group they are already in
    wastes an hour. Read the permission bits before advising.
    """
    try:
        st = os.stat("/dev/kvm")
    except OSError as e:
        return f"/dev/kvm cannot be read: {e}"
    import grp
    import pwd
    user = pwd.getpwuid(os.getuid()).pw_name
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    if st.st_mode & 0o060 == 0:
        return (f"/dev/kvm is mode {st.st_mode & 0o777:04o}, so group membership cannot "
                f"help. Its udev rule is wrong on this machine.")
    members = []
    try:
        members = grp.getgrgid(st.st_gid).gr_mem
    except KeyError:
        pass
    if user in members or os.getgid() == st.st_gid:
        return (f"{user} is in group {group}, which owns /dev/kvm, but this session was "
                f"not granted it -- the group was added after the session started. Log in "
                f"again, and if ssh is reusing a connection: ssh -O exit <host>")
    return (f"{user} cannot write /dev/kvm (owned by group {group}):\n"
            f"      sudo usermod -aG {group} {user}\n"
            f"    then log in again (ssh -O exit <host> first if you use ControlMaster)")


# ------------------------------------------------------------------------ cli ----

if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
    B, G, Y, R, D, O = ("\033[1m", "\033[32m", "\033[33m", "\033[31m", "\033[2m",
                        "\033[0m")
else:
    B = G = Y = R = D = O = ""

USAGE = """kitchen boot-host -- run boot tests on a machine that has KVM

  kitchen boot-host check     can this machine's boot host run the tests?
  kitchen boot-host clean     remove cached images and finished runs there
  kitchen boot-host show      what boot-host.ini says, without connecting

Configured by boot-host.ini in the repository root, which is gitignored. Copy
boot-host.example.ini to start. Set KITCHEN_BOOT_HOST=local to boot here instead."""


def main(argv: list) -> int:
    args = argv[1:]
    if args and args[0] == "agent":                # the far side; never used locally
        Agent(args[1]).serve()
        return 0
    cmd = args[0] if args else "check"
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    rest = args[1:]
    verbose = "--verbose" in rest
    rest = [a for a in rest if a != "--verbose"]

    try:
        cfg = load()
    except ConfigError as e:
        print(f"boot-host: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # `active` is the question lib/build.sh asks before every boot: 0 a host is
    # configured, 1 boots stay here, 2 the configuration is broken. A shell caller needs
    # a status, not a parse.
    if cmd == "active":
        # STDOUT CARRIES THE ANSWER, STDERR CARRIES THE NOTE. lib/build.sh reads this in a
        # `$(...)`, so anything else printed to stdout becomes part of the host name --
        # and the "booting here" note would have been swallowed by the substitution that
        # discards it, which is the opposite of saying so.
        if cfg is None:
            why = local_reason()
            if why and not os.environ.get("KITCHEN_BOOT_HOST_AGENT"):
                print(f"  {D}{why}: this boot stays here{O}", file=sys.stderr)
            return 1
        print(cfg.host)
        return 0
    if cfg is None:
        why = local_reason() or f"no {CONFIG_NAME} (template: {EXAMPLE_NAME})"
        print(f"boot-host: {why}", file=sys.stderr)
        return EXIT_CONFIG
    if cmd == "show":
        print(f"host     {cfg.host}")
        print(f"scratch  {cfg.scratch}")
        print(f"base     {cfg.base}")
        print(f"vnc      {cfg.vnc}  "
              f"({'tunnelled' if cfg.tunnelled else 'direct'}"
              f"{', PUBLIC' if not vnc_is_private(cfg.vnc_ip) else ''})")
        return 0

    try:
        if cmd == "check":
            return cmd_check(cfg, verbose=verbose)
        if cmd == "clean":
            return cmd_clean(cfg)
        if cmd == "test":
            return cmd_test(cfg, rest)
        print(USAGE, file=sys.stderr)
        return EXIT_CONFIG
    except Unavailable as e:
        print(f"\n{R}boot host unavailable{O}  {e}", file=sys.stderr)
        print(f"  {D}nothing was run. To boot here instead: KITCHEN_BOOT_HOST=local{O}",
              file=sys.stderr)
        return EXIT_UNAVAILABLE
    except BrokenPipeError:
        # `kitchen test ... | head` closes the pipe under us. Python then fails to flush
        # stdout at shutdown, prints "BrokenPipeError ignored" and exits 120 -- a code
        # that looks like a real failure and is unique in this toolkit (the other
        # commands give 1 or 141 here). Point the remaining output at /dev/null and
        # report what a program killed by SIGPIPE reports.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
    except KeyboardInterrupt:
        # The session's __exit__ has already told the agent to stop, which is what takes
        # the remote qemu down. Saying so matters: a Ctrl-C that leaves a guest running on
        # someone else's machine is the failure this whole heartbeat exists to prevent.
        print(f"\n  {Y}interrupted; the boot host was told to stop{O}", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
