#!/usr/bin/env python3
"""Cross-platform host discovery and telemetry for the benchmark suite.

The public functions in this module are intentionally inference-free. They
inspect the local host and sample operating-system/GPU counters, but never call
an Ollama generation endpoint or load/stop a model.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path


SUITE_VERSION = "0.6.0"
TELEMETRY_FIELDS = (
    "t",
    "timestamp",
    "cpu_usage_pct",
    "gpu_usage_pct",
    "cpu_temp_c",
    "gpu_temp_c",
    "soc_temp_c",
    "host_temp_c",
    "cpu_power_w",
    "gpu_power_w",
    "system_power_w",
    "total_power_w",
)


def prepend_standard_paths() -> None:
    """Expose common Homebrew and Linux binary locations without duplicates."""
    existing = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    preferred = ["/usr/local/bin", "/usr/bin", "/bin"]
    if platform.system() == "Darwin":
        preferred.insert(0, "/opt/homebrew/bin")
    os.environ["PATH"] = os.pathsep.join(
        list(dict.fromkeys([*preferred, *existing]))
    )


def number(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "[N/A]", "NA", "-", "NONE"}:
        return None
    try:
        return float(text.rstrip("%"))
    except (TypeError, ValueError):
        return None


def human_size(value) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "—"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, UnicodeError):
        return ""


def _cmd_text(command, timeout=10) -> str:
    try:
        proc = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout, check=False
        )
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _proc_cpu_times(path: Path = Path("/proc/stat")):
    line = _read_text(path).splitlines()
    if not line or not line[0].startswith("cpu "):
        return None
    try:
        values = [int(value) for value in line[0].split()[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    # Linux user/nice counters already include guest/guest_nice time. Summing
    # only the first eight fields avoids counting those guest fields twice.
    return sum(values[:8]), idle


def cpu_usage_percent(previous, current):
    if not previous or not current:
        return None
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 3)


def _max_host_thermal_zone_temp(root: Path = Path("/sys/class/thermal")):
    values = []
    try:
        zones = root.glob("thermal_zone*")
    except OSError:
        return None
    for zone in zones:
        raw = _read_text(zone / "temp")
        value = number(raw)
        if value is None:
            continue
        if value > 1000:
            value /= 1000.0
        if 0 < value < 150:
            values.append(value)
    return round(max(values), 3) if values else None


def parse_nvidia_smi_line(line: str):
    """Parse one row emitted by the suite's persistent nvidia-smi query."""
    parts = [part.strip() for part in (line or "").split(",")]
    if len(parts) < 6:
        return None
    gpu_index = number(parts[1])
    return {
        "timestamp": parts[0] or None,
        "gpu_index": int(gpu_index) if gpu_index is not None else None,
        "gpu_usage_pct": number(parts[2]),
        "gpu_temp_c": number(parts[3]),
        "gpu_power_w": number(parts[4]),
        "gpu_clock_mhz": number(parts[5]),
    }


class BaseSampler:
    backend = "none"
    description = "telemetry unavailable"

    def __init__(self, enabled=True, interval_ms=1000):
        self.enabled = bool(enabled)
        self.interval_ms = max(int(interval_ms), 250)
        self.samples = []
        self.lock = threading.Lock()
        self.error = ""

    def start(self):
        return None

    def stop(self):
        return None

    def snapshot_len(self):
        with self.lock:
            return len(self.samples)

    def get_since(self, start_index):
        with self.lock:
            return list(self.samples[start_index:])


class NullSampler(BaseSampler):
    backend = "none"
    description = "telemetry disabled or unavailable"

    def __init__(self, reason="telemetry disabled"):
        super().__init__(enabled=False)
        self.error = reason


class MactopSampler(BaseSampler):
    backend = "mactop"
    description = "Apple SoC telemetry via mactop"

    def __init__(self, enabled=True, interval_ms=1000, executable="mactop"):
        super().__init__(enabled=enabled, interval_ms=interval_ms)
        self.executable = executable
        self.proc = None
        self.thread = None
        self.err_thread = None
        self.stop_flag = threading.Event()

    def start(self):
        if not self.enabled:
            return
        command = [
            self.executable,
            "--headless",
            "--format",
            "json",
            "--count",
            "0",
            "--interval",
            str(self.interval_ms),
        ]
        try:
            self.proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
            )
        except OSError as exc:
            self.enabled = False
            self.error = f"unable to start mactop: {exc}"
            return
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.err_thread = threading.Thread(target=self._err_reader, daemon=True)
        self.thread.start()
        self.err_thread.start()
        time.sleep(2.5)

    def _err_reader(self):
        try:
            for line in self.proc.stderr:
                if line and len(self.error) < 2000:
                    self.error += line
        except Exception:
            pass

    def _reader(self):
        decoder = json.JSONDecoder()
        buffer = ""
        while not self.stop_flag.is_set():
            try:
                chunk = self.proc.stdout.read(4096)
            except Exception as exc:
                self.error += f" read_error={exc}"
                break
            if not chunk:
                if self.proc and self.proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            buffer += chunk
            while True:
                candidate = buffer.lstrip()
                if not candidate:
                    buffer = ""
                    break
                if candidate.startswith(","):
                    candidate = candidate[1:].lstrip()
                if candidate.startswith("["):
                    candidate = candidate[1:].lstrip()
                if candidate.startswith("]"):
                    buffer = candidate[1:]
                    break
                try:
                    obj, offset = decoder.raw_decode(candidate)
                except json.JSONDecodeError:
                    buffer = candidate[-2_000_000:]
                    break
                buffer = candidate[offset:]
                try:
                    soc = obj.get("soc_metrics") or {}
                    sample = {
                        "t": time.monotonic(),
                        "timestamp": obj.get("timestamp"),
                        "cpu_usage_pct": number(obj.get("cpu_usage")),
                        "gpu_usage_pct": number(obj.get("gpu_usage")),
                        "cpu_temp_c": number(soc.get("cpu_temp")),
                        "gpu_temp_c": number(soc.get("gpu_temp")),
                        "soc_temp_c": number(soc.get("soc_temp")),
                        "cpu_power_w": number(soc.get("cpu_power")),
                        "gpu_power_w": number(soc.get("gpu_power")),
                        "system_power_w": number(soc.get("system_power")),
                        "total_power_w": number(soc.get("total_power")),
                    }
                    with self.lock:
                        self.samples.append(sample)
                except Exception as exc:
                    if len(self.error) < 2000:
                        self.error += f" sample_error={exc}"

    def stop(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.thread:
            self.thread.join(timeout=3)
        if self.err_thread:
            self.err_thread.join(timeout=1)


class NvidiaSmiSampler(BaseSampler):
    backend = "nvidia-smi"
    description = "NVIDIA GPU telemetry plus Linux /proc CPU utilization"

    def __init__(self, enabled=True, interval_ms=1000, executable="nvidia-smi"):
        super().__init__(enabled=enabled, interval_ms=interval_ms)
        self.executable = executable
        self.proc = None
        self.thread = None
        self.err_thread = None
        self.stop_flag = threading.Event()
        self._cpu_previous = None

    def start(self):
        if not self.enabled:
            return
        command = [
            self.executable,
            "--query-gpu=timestamp,index,utilization.gpu,temperature.gpu,power.draw,clocks.current.graphics",
            "--format=csv,noheader,nounits",
            f"--loop-ms={self.interval_ms}",
        ]
        self._cpu_previous = _proc_cpu_times()
        try:
            self.proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.enabled = False
            self.error = f"unable to start nvidia-smi: {exc}"
            return
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.err_thread = threading.Thread(target=self._err_reader, daemon=True)
        self.thread.start()
        self.err_thread.start()
        time.sleep(max(1.2, min(2.5, self.interval_ms / 1000 * 1.5)))

    def _err_reader(self):
        try:
            for line in self.proc.stderr:
                if line and len(self.error) < 2000:
                    self.error += line
        except Exception:
            pass

    def _reader(self):
        pending_timestamp = None
        pending_rows = []

        def emit_sample(timestamp, rows):
            if not rows:
                return
            current = _proc_cpu_times()
            cpu_usage = cpu_usage_percent(self._cpu_previous, current)
            self._cpu_previous = current
            gpu_usage = [row.get("gpu_usage_pct") for row in rows if row.get("gpu_usage_pct") is not None]
            gpu_temps = [row.get("gpu_temp_c") for row in rows if row.get("gpu_temp_c") is not None]
            gpu_power = [row.get("gpu_power_w") for row in rows if row.get("gpu_power_w") is not None]
            gpu_clocks = [row.get("gpu_clock_mhz") for row in rows if row.get("gpu_clock_mhz") is not None]
            sample = {
                "t": time.monotonic(),
                "timestamp": timestamp,
                "cpu_usage_pct": cpu_usage,
                # For multi-GPU hosts, utilization/temperature use the busiest
                # card while power is summed across cards for this timestamp.
                "gpu_usage_pct": max(gpu_usage) if gpu_usage else None,
                "cpu_temp_c": None,
                "gpu_temp_c": max(gpu_temps) if gpu_temps else None,
                "soc_temp_c": None,
                "host_temp_c": _max_host_thermal_zone_temp(),
                "cpu_power_w": None,
                "gpu_power_w": round(sum(gpu_power), 3) if gpu_power else None,
                "system_power_w": None,
                "total_power_w": None,
                "gpu_indexes": [row.get("gpu_index") for row in rows],
                "gpu_clock_mhz": max(gpu_clocks) if gpu_clocks else None,
            }
            with self.lock:
                self.samples.append(sample)

        try:
            for line in self.proc.stdout:
                if self.stop_flag.is_set():
                    break
                parsed = parse_nvidia_smi_line(line)
                if not parsed:
                    if line.strip() and len(self.error) < 2000:
                        self.error += f" unparsed_nvidia_smi={line.strip()[:160]}"
                    continue
                timestamp = parsed.get("timestamp")
                if pending_timestamp is not None and timestamp != pending_timestamp:
                    emit_sample(pending_timestamp, pending_rows)
                    pending_rows = []
                pending_timestamp = timestamp
                pending_rows.append(parsed)
        except Exception as exc:
            if len(self.error) < 2000:
                self.error += f" read_error={exc}"
        finally:
            emit_sample(pending_timestamp, pending_rows)

    def stop(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.thread:
            self.thread.join(timeout=3)
        if self.err_thread:
            self.err_thread.join(timeout=1)


def create_sampler(mode="auto", interval_ms=1000, system_name=None, which=None):
    """Return a normalized telemetry sampler without starting it."""
    selected = (mode or "auto").strip().lower()
    if selected not in {"auto", "mactop", "nvidia-smi", "none"}:
        raise ValueError(f"unsupported telemetry backend: {mode}")
    if selected == "none":
        return NullSampler("telemetry disabled")
    system_name = system_name or platform.system()
    which = which or shutil.which
    if selected == "mactop" or (selected == "auto" and system_name == "Darwin"):
        executable = which("mactop")
        if executable:
            return MactopSampler(interval_ms=interval_ms, executable=executable)
        if selected == "mactop":
            return NullSampler("mactop was requested but is not installed")
    if selected == "nvidia-smi" or (selected == "auto" and system_name == "Linux"):
        executable = which("nvidia-smi")
        if executable:
            return NvidiaSmiSampler(interval_ms=interval_ms, executable=executable)
        if selected == "nvidia-smi":
            return NullSampler("nvidia-smi was requested but is not installed")
    return NullSampler(f"no supported telemetry backend found for {system_name or 'unknown OS'}")


def host_product_name() -> str:
    for path in (
        Path("/sys/devices/virtual/dmi/id/product_name"),
        Path("/sys/class/dmi/id/product_name"),
    ):
        value = _read_text(path)
        if value:
            return value.replace("_", " ")
    if platform.system() == "Darwin":
        return _cmd_text(["sysctl", "-n", "hw.model"])
    return ""


def local_host_label() -> str:
    override = os.environ.get("LLM_BENCHMARK_HOST_LABEL", "").strip()
    if override:
        return override
    node = platform.node().split(".", 1)[0]
    product = host_product_name()
    combined = f"{node} {product}".lower().replace("-", " ").replace("_", " ")
    if "dgx" in combined and "spark" in combined:
        return "NVIDIA DGX Spark"
    if "studio" in combined and platform.system() == "Darwin":
        return "Mac Studio"
    if "mini" in combined and platform.system() == "Darwin":
        return "Mac Mini"
    if node:
        return node
    return "Local Linux Host" if platform.system() == "Linux" else "Local Host"


def _linux_lscpu():
    raw = _cmd_text(["lscpu", "-J"])
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    out = {}
    for item in data.get("lscpu") or []:
        key = str(item.get("field") or "").rstrip(":")
        out[key] = str(item.get("data") or "")
    return out


def _linux_memory_bytes():
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                return 0
    return 0


def collect_system_specs():
    """Return dashboard-ready host specifications on macOS or Linux."""
    system = platform.system()
    arch = platform.machine() or _cmd_text(["uname", "-m"])
    product = host_product_name()
    if system == "Darwin":
        cpu = _cmd_text(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Unknown CPU"
        physical = _cmd_text(["sysctl", "-n", "hw.physicalcpu"])
        logical = _cmd_text(["sysctl", "-n", "hw.logicalcpu"])
        memory_raw = number(_cmd_text(["sysctl", "-n", "hw.memsize"])) or 0
        gpu = "Unknown GPU"
        gpu_small = "—"
        try:
            displays = json.loads(
                _cmd_text(["system_profiler", "SPDisplaysDataType", "-json"], timeout=45)
            )
            display = (displays.get("SPDisplaysDataType") or [{}])[0]
            gpu = display.get("sppci_model") or display.get("_name") or gpu
            cores = display.get("sppci_cores")
            metal_raw = display.get("spdisplays_mtlgpufamilysupport") or ""
            metal = {
                "spdisplays_metal4": "Metal 4",
                "spdisplays_metal3": "Metal 3",
                "spdisplays_metal2": "Metal 2",
            }.get(metal_raw, metal_raw.replace("spdisplays_", "").replace("_", " ").title())
            gpu_small = " · ".join(
                value for value in (f"{cores} GPU cores" if cores else "", metal) if value
            ) or "—"
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        cpu_small = " · ".join(
            value
            for value in (
                f"{physical} CPU cores" if physical else "",
                f"{logical} threads" if logical and logical != physical else "",
                arch,
            )
            if value
        )
        memory_label = "Unified memory"
        memory_small = "Apple unified memory" + (f" · {product}" if product else "")
    elif system == "Linux":
        lscpu = _linux_lscpu()
        cpu = lscpu.get("Model name") or platform.processor() or "Unknown CPU"
        logical = lscpu.get("CPU(s)") or str(os.cpu_count() or "")
        cores = lscpu.get("Core(s) per socket")
        sockets = lscpu.get("Socket(s)")
        physical = ""
        try:
            physical = str(int(cores) * int(sockets)) if cores and sockets else ""
        except ValueError:
            pass
        memory_raw = _linux_memory_bytes()
        gpu_row = _cmd_text(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ]
        ).splitlines()
        gpu = "Unknown GPU"
        gpu_small = "—"
        if gpu_row:
            values = [value.strip() for value in gpu_row[0].split(",")]
            gpu = values[0] or gpu
            details = []
            if len(values) > 1 and values[1]:
                details.append(f"driver {values[1]}")
            if len(values) > 2 and values[2] and values[2].upper() != "N/A":
                details.append(f"compute {values[2]}")
            gpu_small = " · ".join(details) or "—"
        cpu_small = " · ".join(
            value
            for value in (
                f"{physical} CPU cores" if physical else "",
                f"{logical} threads" if logical else "",
                arch,
            )
            if value
        )
        is_spark = "dgx" in product.lower() and "spark" in product.lower()
        memory_label = "Unified memory" if is_spark else "System memory"
        memory_small = (
            "CPU/GPU coherent unified memory" if is_spark else "Linux system memory"
        ) + (f" · {product}" if product else "")
    else:
        cpu = platform.processor() or "Unknown CPU"
        cpu_small = arch
        memory_raw = 0
        gpu = "Unknown GPU"
        gpu_small = "—"
        memory_label = "System memory"
        memory_small = system or "Unknown operating system"

    try:
        disk = shutil.disk_usage("/")
        disk_free = human_size(disk.free)
        disk_total = human_size(disk.total)
    except OSError:
        disk_free = disk_total = "—"
    return {
        "cpu": cpu,
        "cpu_small": cpu_small or arch,
        "gpu": gpu,
        "gpu_small": gpu_small,
        "memory": human_size(memory_raw) if memory_raw else "—",
        "memory_label": memory_label,
        "memory_small": memory_small,
        "disk_free": disk_free,
        "disk_small": f"free on local / · {disk_total} total",
        "os": f"{system} {platform.release()}".strip(),
        "architecture": arch,
        "product": product,
    }


def ollama_version(base_url="http://127.0.0.1:11434") -> str:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/version", timeout=10) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        return str(data.get("version") or "")
    except Exception:
        return ""


def run_metadata(telemetry_backend="none", base_url="http://127.0.0.1:11434"):
    return {
        "suite_version": SUITE_VERSION,
        "host": platform.node().split(".", 1)[0] or "unknown",
        "host_label": local_host_label(),
        "platform": platform.system().lower() or "unknown",
        "os_version": platform.platform(),
        "architecture": platform.machine() or "unknown",
        "telemetry_backend": telemetry_backend,
        "ollama_version": ollama_version(base_url),
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


prepend_standard_paths()
