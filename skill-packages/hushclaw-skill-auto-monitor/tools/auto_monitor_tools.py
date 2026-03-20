"""Auto-Monitor tools — system health checks and alert dispatch.

Requires: psutil  (pip install psutil)
Alert webhook is optional; alerts always write to ~/.hushclaw/monitor_alerts.jsonl.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool

_ALERT_LOG = Path("~/.hushclaw/monitor_alerts.jsonl").expanduser()
_CONFIG_FILE = Path("~/.hushclaw/monitor_config.json").expanduser()

_DEFAULT_CONFIG = {
    "cpu_threshold": 90,
    "mem_threshold": 85,
    "disk_threshold": 90,
    "webhook_url": "",
}


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text())
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


def _save_config(cfg: dict) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


@tool(description=(
    "Get current system health: CPU %, memory %, disk usage %, load average, uptime. "
    "Requires psutil."
))
def monitor_health() -> ToolResult:
    """Return a snapshot of CPU, memory, disk, and load metrics."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return ToolResult(error="psutil is not installed. Run: pip install psutil")

    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime_h = (datetime.now() - boot).total_seconds() / 3600

    cfg = _load_config()
    warnings: list[str] = []
    if cpu > cfg["cpu_threshold"]:
        warnings.append(f"CPU {cpu}% > threshold {cfg['cpu_threshold']}%")
    if mem.percent > cfg["mem_threshold"]:
        warnings.append(f"Memory {mem.percent}% > threshold {cfg['mem_threshold']}%")
    if disk.percent > cfg["disk_threshold"]:
        warnings.append(f"Disk {disk.percent}% > threshold {cfg['disk_threshold']}%")

    return ToolResult(output={
        "cpu_percent": cpu,
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1e9, 2),
        "memory_total_gb": round(mem.total / 1e9, 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1e9, 2),
        "disk_total_gb": round(disk.total / 1e9, 2),
        "load_avg_1m": load[0],
        "load_avg_5m": load[1],
        "uptime_hours": round(uptime_h, 1),
        "warnings": warnings,
    })


@tool(description="List top N processes sorted by 'cpu' or 'memory'. Requires psutil.")
def monitor_processes(top_n: int = 10, sort_by: str = "cpu") -> ToolResult:
    """Return top N processes by CPU or memory usage."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return ToolResult(error="psutil is not installed. Run: pip install psutil")

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            procs.append(p.info)
        except psutil.NoSuchProcess:
            pass

    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    procs.sort(key=lambda x: x.get(key) or 0, reverse=True)
    return ToolResult(output={"processes": procs[:top_n], "sorted_by": sort_by})


@tool(description="Check whether the specified TCP ports are currently listening.")
def monitor_check_ports(ports: list[int]) -> ToolResult:
    """Return {port: listening} dict for each requested port."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return ToolResult(error="psutil is not installed. Run: pip install psutil")

    listening = {c.laddr.port for c in psutil.net_connections() if c.status == "LISTEN"}
    result = {str(p): p in listening for p in ports}
    closed = [p for p in ports if p not in listening]
    return ToolResult(output={"ports": result, "closed": closed})


@tool(description=(
    "Get disk read/write rates in bytes per second by sampling psutil twice over one second. "
    "Returns per-disk read_bytes_per_sec and write_bytes_per_sec. Requires psutil."
))
def monitor_disk_io() -> ToolResult:
    """Return disk I/O rates sampled over a 1-second interval."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return ToolResult(error="psutil is not installed. Run: pip install psutil")

    import time

    before = psutil.disk_io_counters(perdisk=True)
    time.sleep(1)
    after = psutil.disk_io_counters(perdisk=True)

    disks = {}
    for name in after:
        b = before.get(name)
        a = after[name]
        if b is None:
            continue
        disks[name] = {
            "read_bytes_per_sec": a.read_bytes - b.read_bytes,
            "write_bytes_per_sec": a.write_bytes - b.write_bytes,
        }

    return ToolResult(output={"disks": disks, "interval_sec": 1})


@tool(description="Check systemd/launchctl service status for the given service names.")
def monitor_check_services(service_names: list[str]) -> ToolResult:
    """Return status dict for each requested service (Linux systemd or macOS launchctl)."""
    import platform
    import subprocess

    results: dict[str, str] = {}
    system = platform.system()

    for svc in service_names:
        if system == "Linux":
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True,
            )
            results[svc] = r.stdout.strip() or "unknown"
        elif system == "Darwin":
            r = subprocess.run(
                ["launchctl", "list", svc],
                capture_output=True, text=True,
            )
            results[svc] = "running" if r.returncode == 0 else "stopped"
        else:
            results[svc] = "unsupported_platform"

    return ToolResult(output={"services": results})


@tool(description=(
    "Send an alert. level: INFO | WARN | CRITICAL. "
    "Always writes to ~/.hushclaw/monitor_alerts.jsonl; "
    "if webhook_url is set in config (or passed directly), also POSTs JSON."
))
def monitor_send_alert(
    title: str,
    message: str,
    level: str = "WARN",
    webhook_url: str = "",
) -> ToolResult:
    """Log alert locally and optionally POST to a webhook URL."""
    cfg = _load_config()
    url = webhook_url or cfg.get("webhook_url", "")

    record = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "level": level.upper(),
        "title": title,
        "message": message,
    }

    _ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    webhook_status = "not_configured"
    if url:
        try:
            payload = json.dumps({"text": f"[{level.upper()}] {title}\n{message}"}).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                webhook_status = f"sent ({resp.status})"
        except Exception as e:
            webhook_status = f"failed: {e}"

    return ToolResult(output={
        "logged": True,
        "alert": record,
        "webhook_status": webhook_status,
    })


@tool(description="View current monitor alert thresholds and webhook URL.")
def monitor_get_alert_config() -> ToolResult:
    """Return the current monitoring configuration."""
    return ToolResult(output=_load_config())


@tool(description=(
    "Update alert thresholds: cpu_threshold (%), mem_threshold (%), disk_threshold (%), webhook_url."
))
def monitor_set_alert_config(
    cpu_threshold: int | None = None,
    mem_threshold: int | None = None,
    disk_threshold: int | None = None,
    webhook_url: str | None = None,
) -> ToolResult:
    """Persist new threshold values to ~/.hushclaw/monitor_config.json."""
    cfg = _load_config()
    if cpu_threshold is not None:
        cfg["cpu_threshold"] = cpu_threshold
    if mem_threshold is not None:
        cfg["mem_threshold"] = mem_threshold
    if disk_threshold is not None:
        cfg["disk_threshold"] = disk_threshold
    if webhook_url is not None:
        cfg["webhook_url"] = webhook_url
    _save_config(cfg)
    return ToolResult(output={"updated": True, "config": cfg})
