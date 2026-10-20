"""The parts of an old Linux computer that live outside the home folder but are still yours: apps installed from
downloaded .deb files that no package source has, the system services you made (with what they run from
/usr/local and /opt), and Docker servers with their images and data. Cherry OS puts them back after a move.

Both Cherry Move senders use this file: Cherry OS's directly, and the Go app (move/, which keeps a copy) by
running it with the old computer's python3:
    python3 system_extras.py OUT_DIR
prints what it found as JSON; the files it lists are in OUT_DIR. Nothing here may need anything but Python's
standard library.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile

# Packages that come with any Linux or that Cherry OS provides itself: never rebuilt from the old computer.
NOT_REPACKED = re.compile(r"^(linux-.*|grub.*|shim.*|nvidia-.*|ubuntu-.*|cherry-.*|calamares.*|base-files|"
                          r"libc6|systemd.*|dpkg|apt)$")
UNIT_KINDS = (".service", ".timer", ".socket", ".path")
DPKG_INFO = "/var/lib/dpkg/info"
IMAGE_CAP = 4 << 30


def run(cmd, timeout=120, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def owned_by_packages(paths):
    """The paths that some installed package owns."""
    owned = set()
    for i in range(0, len(paths), 200):
        try:
            r = subprocess.run(["dpkg", "-S", *paths[i:i + 200]], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return set(paths)  # can't tell: treat everything as the system's
        for line in r.stdout.splitlines():
            _pkgs, sep, path = line.partition(": ")
            if sep:
                owned.add(path.strip())
    return owned


# ------------------------------------------------------------------ packages no package source has

def unavailable_packages():
    """Apps installed by hand (apt-mark showmanual) that no package source offers: installed from a downloaded
    .deb, or from a source that's gone. The new computer couldn't download them, so they're rebuilt here."""
    manual = (run(["apt-mark", "showmanual"]) or "").split()
    manual = [p for p in manual if not NOT_REPACKED.match(p.split(":")[0])]
    if not manual:
        return []
    policy = run(["apt-cache", "policy", *manual]) or ""
    out, name, sources = [], None, []
    for line in policy.splitlines() + [""]:
        if line and not line.startswith(" "):
            if name and sources and all(s == "/var/lib/dpkg/status" for s in sources):
                out.append(name)
            name, sources = line.rstrip(":"), []
        else:
            m = re.match(r"^\s+(?:\*\*\*\s+)?-?\d+\s+(\S+)", line)
            if m and not re.match(r"^\s+(?:\*\*\*\s+)?\S+\s+-?\d+$", line):
                sources.append(m.group(1))
    if name and sources and all(s == "/var/lib/dpkg/status" for s in sources):
        out.append(name)
    return out


def repack(pkg, out_dir):
    """Rebuilds an installed package as a .deb from its files on disk (what dpkg-repack does). None if a file
    can't be read or dpkg-deb fails."""
    base = pkg
    if not os.path.exists(f"{DPKG_INFO}/{base}.list"):
        arch = (run(["dpkg-query", "-W", "-f=${Architecture}", pkg]) or "").strip()
        base = f"{pkg}:{arch}"
        if not os.path.exists(f"{DPKG_INFO}/{base}.list"):
            return None
    status = run(["dpkg-query", "-s", pkg])
    if not status:
        return None
    control, skipping = [], False
    for line in status.splitlines():
        if line.startswith((" ", "\t")):
            if not skipping:
                control.append(line)
            continue
        key = line.split(":", 1)[0]
        skipping = key in ("Status", "Conffiles", "Config-Version")
        if not skipping:
            control.append(line)
    root = os.path.join(out_dir, "build", pkg)
    shutil.rmtree(root, ignore_errors=True)
    debian = os.path.join(root, "DEBIAN")
    os.makedirs(debian)
    with open(os.path.join(debian, "control"), "w") as f:
        f.write("\n".join(control) + "\n")
    for part in ("preinst", "postinst", "prerm", "postrm", "config", "templates", "triggers", "shlibs", "symbols"):
        src = f"{DPKG_INFO}/{base}.{part}"
        if os.path.exists(src):
            shutil.copy(src, os.path.join(debian, part))
            os.chmod(os.path.join(debian, part), 0o755 if part in ("preinst", "postinst", "prerm", "postrm",
                                                                    "config") else 0o644)
    try:
        with open(f"{DPKG_INFO}/{base}.list") as f:
            paths = [p for p in f.read().splitlines() if p and p != "/."]
        unreadable = set()
        parents = {os.path.dirname(p) for p in paths}
        for p in paths:
            dest = root + p
            if os.path.islink(p) and p in parents:  # /lib → usr/lib on merged-/usr systems: a folder to the package
                os.makedirs(dest, exist_ok=True)
            elif os.path.islink(p):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.symlink(os.readlink(p), dest)
            elif os.path.isdir(p):
                os.makedirs(dest, exist_ok=True)
                os.chmod(dest, os.stat(p).st_mode & 0o7777)
            elif os.path.isfile(p):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    shutil.copy2(p, dest)
                except PermissionError:
                    unreadable.add(p)  # root-only settings files: the package's own defaults come back instead
        if os.path.exists(f"{DPKG_INFO}/{base}.conffiles"):
            with open(f"{DPKG_INFO}/{base}.conffiles") as f:
                keep = [c for c in f.read().splitlines() if c.strip() and c.split()[-1] not in unreadable
                        and os.path.lexists(root + c.split()[-1])]
            if keep:
                with open(os.path.join(debian, "conffiles"), "w") as f:
                    f.write("\n".join(keep) + "\n")
        deb = os.path.join(out_dir, "packages", f"{pkg}.deb")
        os.makedirs(os.path.dirname(deb), exist_ok=True)
        ok = run(["dpkg-deb", "--root-owner-group", "-Zgzip", "--build", root, deb], timeout=1800) is not None
        return deb if ok else None
    except OSError:
        return None
    finally:
        shutil.rmtree(os.path.join(out_dir, "build"), ignore_errors=True)


# ------------------------------------------------------------------ your own system services

def system_services(out_dir):
    """Services and timers you added to /etc/systemd/system yourself (not from a package or snap), and the files
    in /usr/local and in folders of /opt that no package owns, which those services usually run."""
    units, dropins, files = [], [], []
    folder = "/etc/systemd/system"
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    cands = [os.path.join(folder, n) for n in names if n.endswith(UNIT_KINDS) and not n.startswith("snap")]
    cands = [p for p in cands if os.path.isfile(p) and not os.path.islink(p)]
    drops = []
    for n in names:
        d = os.path.join(folder, n)
        if n.endswith(".d") and os.path.isdir(d) and not os.path.islink(d) and not n.startswith("snap"):
            drops += [os.path.join(d, c) for c in sorted(os.listdir(d)) if c.endswith(".conf")]
    owned = owned_by_packages(cands + drops)
    for p in cands:
        if p in owned:
            continue
        try:
            text = open(p).read()
        except (OSError, UnicodeDecodeError):
            continue
        name = os.path.basename(p)
        state = (run(["systemctl", "is-enabled", name], timeout=20) or "").strip()
        units.append({"name": name, "text": text, "enabled": state == "enabled"})
    for p in drops:
        if p in owned:
            continue
        try:
            dropins.append({"path": os.path.relpath(p, folder), "text": open(p).read()})
        except (OSError, UnicodeDecodeError):
            pass
    for top, name in (("/usr/local", "usr-local.tar"), ("/opt", "opt.tar")):
        tar = _tar_unowned(top, os.path.join(out_dir, "system", name))
        if tar:
            files.append(tar)
    return {"units": units, "dropins": dropins, "files": files, "mounts": drive_mounts()}


SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var", "/tmp", "none", "swap"}
# Network filesystems and the package with their mount helper.
NETWORK_FS = {"cifs": "cifs-utils", "smb3": "cifs-utils", "nfs": "nfs-common", "nfs4": "nfs-common",
              "fuse.sshfs": "sshfs", "davfs": "davfs2"}
DRIVE_SPEC = re.compile(r"(UUID|LABEL|PARTUUID|PARTLABEL)=\S+$|/dev/disk/by-(uuid|label|partuuid|partlabel)/\S+$")


def drive_mounts(fstab="/etc/fstab"):
    """Other drives and network shares mounted at startup (/etc/fstab): a second disk, a NAS. Not the system's
    own (/, /boot, swap…), which the new computer has its own of."""
    try:
        with open(fstab, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[0].startswith("#"):
            continue
        spec, where, kind = fields[:3]
        if where in SYSTEM_MOUNTS or kind == "swap" or not (DRIVE_SPEC.match(spec) or kind in NETWORK_FS):
            continue
        out.append({"spec": spec, "where": where, "type": kind, "options": fields[3] if len(fields) > 3 else "defaults",
                    "pass": fields[5] if len(fields) > 5 else "0"})
    return out


def _tar_unowned(top, out):
    """Packs the files under `top` that no package owns (and that this user can read)."""
    found = []
    for root, dirs, names in os.walk(top):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.islink(p) or os.path.isfile(p):
                found.append(p)
    owned = owned_by_packages(found)
    keep = [p for p in found if p not in owned and (os.path.islink(p) or os.access(p, os.R_OK))]
    if not keep:
        return None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tarfile.open(out, "w") as t:
        for p in keep:
            try:
                t.add(p, arcname=p.lstrip("/"), recursive=False)
            except OSError:
                pass
    return out


# ------------------------------------------------------------------ Docker servers

def docker_servers(out_dir):
    """Your Docker servers: Compose projects that are running, and containers set to come back by themselves
    (restart always / unless-stopped), with each one's data volumes and any image that was built here and can't
    be downloaded."""
    if not shutil.which("docker"):
        return {}
    ids = run(["docker", "ps", "-q"], timeout=30)
    if ids is None:
        return {"note": "Docker is installed but this user can't use it"}
    ids = ids.split()
    if not ids:
        return {}
    try:
        info = json.loads(run(["docker", "inspect", *ids], timeout=60) or "[]")
    except ValueError:
        return {}
    compose, containers, images, volumes = {}, [], [], []
    saved = {}
    for c in info:
        name = c["Name"].lstrip("/")
        labels = c["Config"].get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        restart = (c["HostConfig"].get("RestartPolicy") or {}).get("Name", "")
        if not project and restart not in ("always", "unless-stopped"):
            continue
        for m in c.get("Mounts") or []:
            if m.get("Type") == "volume":
                tar = os.path.join(out_dir, "docker", f"volume-{name}-{len(volumes)}.tar")
                os.makedirs(os.path.dirname(tar), exist_ok=True)
                with open(tar, "wb") as f:
                    try:
                        ok = subprocess.run(["docker", "cp", f"{name}:{m['Destination']}", "-"], stdout=f,
                                            stderr=subprocess.DEVNULL, timeout=3600).returncode == 0
                    except (OSError, subprocess.SubprocessError):
                        ok = False
                if ok:
                    volumes.append({"container": name, "path": m["Destination"], "file": tar})
                else:
                    os.unlink(tar)
        if project:
            compose.setdefault(project, {
                "name": project, "dir": labels.get("com.docker.compose.project.working_dir", ""),
                "files": [f for f in labels.get("com.docker.compose.project.config_files", "").split(",") if f],
                "containers": [], "running": []})
            compose[project]["containers"].append(name)
            compose[project]["running"].append(c)
            continue
        try:
            image = json.loads(run(["docker", "image", "inspect", c["Image"]], timeout=30) or "[]")[0]
        except (ValueError, IndexError):
            image = {}
        icfg = image.get("Config") or {}
        cfg, host = c["Config"], c["HostConfig"]
        tag = cfg.get("Image") or c["Image"]
        # Images built on this computer have RepoDigests too under Docker's containerd store, so that can't tell
        # what's downloadable: save every image that isn't huge, and huge ones only when nothing could download them.
        if tag not in saved and (image.get("Size", 0) <= IMAGE_CAP or not image.get("RepoDigests")):
            tar = os.path.join(out_dir, "docker", f"image-{len(saved)}.tar")
            os.makedirs(os.path.dirname(tar), exist_ok=True)
            if run(["docker", "save", "-o", tar, tag], timeout=3600) is not None:
                saved[tag] = tar
                images.append({"tag": tag, "file": tar})
        network = host.get("NetworkMode") or "bridge"
        containers.append({
            "name": name, "image": tag, "restart": restart,
            "network": network if network in ("host", "bridge", "none") else "bridge",
            "ports": [[b.get("HostIp", ""), b.get("HostPort", ""), port]
                      for port, binds in (host.get("PortBindings") or {}).items() for b in binds or []],
            "binds": [[m["Source"], m["Destination"], bool(m.get("RW"))] for m in c.get("Mounts") or []
                      if m.get("Type") == "bind"],
            "volumes": [m["Destination"] for m in c.get("Mounts") or [] if m.get("Type") == "volume"],
            "env": [e for e in cfg.get("Env") or [] if e not in (icfg.get("Env") or [])],
            "cmd": cfg.get("Cmd") if cfg.get("Cmd") != icfg.get("Cmd") else None,
            "entrypoint": cfg.get("Entrypoint") if cfg.get("Entrypoint") != icfg.get("Entrypoint") else None,
            "user": cfg.get("User") if cfg.get("User") != icfg.get("User") else "",
            "workdir": cfg.get("WorkingDir") if cfg.get("WorkingDir") != icfg.get("WorkingDir") else "",
            "cap_add": host.get("CapAdd") or [], "privileged": bool(host.get("Privileged")),
            "devices": [f"{d['PathOnHost']}:{d['PathInContainer']}" for d in host.get("Devices") or []],
            "extra_hosts": host.get("ExtraHosts") or [],
        })
    for p in compose.values():
        p["env"] = compose_env(p["dir"], p["files"], p.pop("running"))
    return {"compose": list(compose.values()), "containers": containers, "images": images, "volumes": volumes}


COMPOSE_VAR = re.compile(r"\$\{([A-Za-z_]\w*)|\$([A-Za-z_]\w*)")


def read_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    for line in lines:
        line = line.strip()
        key, sep, value = (line[7:] if line.startswith("export ") else line).partition("=")
        key, value = key.strip(), value.strip()
        if sep and re.fullmatch(r"[A-Za-z_]\w*", key):
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[key] = value
    return out


def compose_env(folder, files, running):
    """Values for the ${VARIABLES} a Compose project's files use that Compose wouldn't find by itself in the
    project's .env, because they came from somewhere only the old computer had: another env file next to them
    that a script loads first, or the shell. Found in those env files, the running containers' environment, and
    the folders mounted where a ${VARIABLE} was the mount's source."""
    texts = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                texts.append(fh.read().replace("$$", ""))
        except (OSError, UnicodeDecodeError):
            pass
    names = {a or b for t in texts for a, b in COMPOSE_VAR.findall(t)}
    found = {}
    try:
        env_files = sorted(e for e in os.listdir(folder) if e.endswith(".env") and e != ".env")
    except OSError:
        env_files = []
    for e in env_files:
        for k, v in read_env_file(os.path.join(folder, e)).items():
            if k in names:
                found.setdefault(k, v)
    for c in running:
        for e in c["Config"].get("Env") or []:
            k, _, v = e.partition("=")
            if k in names:
                found.setdefault(k, v)
    mounts = {m["Destination"]: m["Source"] for c in running for m in c.get("Mounts") or [] if m.get("Type") == "bind"}
    for t in texts:
        for name, dest in re.findall(r"""^\s*-\s*["']?\$\{(\w+)[^}]*\}:(/[^:\s"']*)""", t, re.M):
            if dest in mounts:
                found.setdefault(name, mounts[dest])
    dotenv = read_env_file(os.path.join(folder, ".env"))
    return {k: v for k, v in found.items() if k not in dotenv}


def docker_run_args(c):
    """`docker create` arguments that make the container again (the inverse of docker_servers())."""
    args = ["--name", c["name"], "--restart", c["restart"], "--network", c["network"]]
    for ip, port, inside in c.get("ports", []):
        args += ["-p", f"{ip + ':' if ip else ''}{port}:{inside}"]
    for src, dest, rw in c.get("binds", []):
        args += ["-v", f"{src}:{dest}{'' if rw else ':ro'}"]
    for dest in c.get("volumes", []):
        args += ["-v", dest]
    for e in c.get("env", []):
        args += ["-e", e]
    for cap in c.get("cap_add", []):
        args += ["--cap-add", cap]
    for d in c.get("devices", []):
        args += ["--device", d]
    for h in c.get("extra_hosts", []):
        args += ["--add-host", h]
    if c.get("privileged"):
        args.append("--privileged")
    if c.get("user"):
        args += ["--user", c["user"]]
    if c.get("workdir"):
        args += ["--workdir", c["workdir"]]
    entry = c.get("entrypoint")
    if entry:
        args += ["--entrypoint", entry[0]]
    args.append(c["image"])
    if entry:
        args += entry[1:]
    if c.get("cmd"):
        args += c["cmd"]
    return args


def collect(out_dir):
    """Everything above. File paths in the result are relative to out_dir, which holds the files."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    debs = [{"name": pkg, "file": repack(pkg, out_dir),
             "homepage": (run(["dpkg-query", "-W", "-f=${Homepage}", pkg]) or "").strip()}
            for pkg in unavailable_packages()]
    found = {"debs": debs, "system": system_services(out_dir), "docker": docker_servers(out_dir)}

    def relative(v):
        if isinstance(v, dict):
            return {k: relative(x) for k, x in v.items()}
        if isinstance(v, list):
            return [relative(x) for x in v]
        if isinstance(v, str) and v.startswith(out_dir + "/"):
            return v[len(out_dir) + 1:]
        return v
    return relative(found)


if __name__ == "__main__":
    print(json.dumps(collect(sys.argv[1])))
