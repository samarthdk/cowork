"""Measurements for the metrics panel and the start-up checks: memory used by
each model process, and whether the Mac is offline.

The offline check only reads local settings (is Wi-Fi switched on, is there
any network route). It never sends anything to the network.
"""

import ctypes
import os
import re
import subprocess

import psutil

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)


def footprint_mb(pid: int) -> float:
    """Physical memory footprint of one process, in MB (0 if it can't be read)."""
    buffer = (ctypes.c_uint64 * 40)()          # room for rusage_info_v4
    if _libc.proc_pid_rusage(int(pid), 0, ctypes.byref(buffer)) != 0:
        return 0.0
    return buffer[9] / 1048576                 # ri_phys_footprint (after 16 bytes of uuid and 7 counters)


GROUPS = {   # label -> does this process belong to it? (first match wins)
    "WhisperKit (speech in)": lambda name, cmd: "whisperkit-cli" in cmd and " serve" in cmd,
    "TTSKit (speech out)": lambda name, cmd: "whisperkit-cli" in cmd and " tts" in cmd,
    "Bonsai model (llama-server)": lambda name, cmd: name == "llama-server" or "/llama-server" in cmd,
    "LM Studio app": lambda name, cmd: name.startswith("LM Studio") or "/.lmstudio/" in cmd.split(" ")[0],
}
MODEL_PARTS = ("WhisperKit (speech in)", "TTSKit (speech out)", "Bonsai model (llama-server)",
               "Second Voice app + camera")   # what counts towards the model memory budget


def memory() -> dict:
    """Memory in MB per part of the system, plus the total for the models."""
    usage = {label: 0.0 for label in GROUPS}
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = process.info["name"] or ""
            cmd = " ".join(process.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for label, belongs in GROUPS.items():
            if belongs(name, cmd):
                usage[label] += footprint_mb(process.info["pid"])
                break
    usage["Second Voice app + camera"] = footprint_mb(os.getpid())
    result = {label: round(mb) for label, mb in usage.items()}
    result["models_total"] = round(sum(usage[label] for label in MODEL_PARTS))
    result["total"] = round(sum(usage.values()))
    return result


def system_memory() -> dict:
    v = psutil.virtual_memory()
    return {"total_mb": round(v.total / 1048576), "available_mb": round(v.available / 1048576),
            "used_percent": v.percent, "swap_used_mb": round(psutil.swap_memory().used / 1048576)}


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def wifi_is_on() -> bool | None:
    """True/False, or None if it can't be told."""
    ports = _run(["networksetup", "-listallhardwareports"])
    found = re.search(r"Hardware Port: Wi-Fi\nDevice: (\w+)", ports)
    if not found:
        return None
    state = _run(["networksetup", "-getairportpower", found.group(1)])
    return state.strip().endswith("On") if state else None


def has_network_route() -> bool:
    """Is any network (Wi-Fi, cable, phone tethering) currently usable?"""
    info = _run(["scutil", "--nwi"])
    return bool(re.search(r"Network interfaces: \S", info)) and "No IPv4 states found" not in info.split("IPv6")[0]


def offline_status() -> dict:
    wifi, route = wifi_is_on(), has_network_route()
    return {"offline": wifi is False and not route, "wifi_on": wifi, "network_route": route}


if __name__ == "__main__":
    # .venv/bin/python sysinfo.py
    for label, mb in memory().items():
        print(f"{label:28} {mb:6} MB")
    print(system_memory())
    print(offline_status())
