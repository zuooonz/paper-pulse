#!/usr/bin/env python3
"""
Paper Pulse 服务管理 (Windows & Linux)
用法:
    python scripts/server.py start    启动前后端（隐藏窗口，自动清理端口）
    python scripts/server.py stop     停止
    python scripts/server.py restart  重启
"""
import logging

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
PID_FILE = PROJECT_ROOT / ".service_pids"
_SRC_DIR = PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
from tools.storage import get_temp_dir

WINDOWS_UVICORN = shutil.which("uvicorn") or "uvicorn"


def _api_host() -> str:
    return os.environ.get("API_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _api_port() -> int:
    return int(os.environ.get("API_PORT", "8000"))


def _frontend_port() -> int:
    return int(os.environ.get("FRONTEND_PORT", "3000"))


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((_api_host(), port)) == 0


def _kill_by_port(port: int):
    """根据端口杀死占用进程"""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
            pids = set()
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 5 and fields[0].upper() == "TCP":
                    local_address = fields[1].rsplit(":", 1)
                    if len(local_address) == 2 and local_address[1] == str(port) and fields[3].upper() == "LISTENING":
                        pids.add(fields[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, check=False)
        except Exception:
            logger.warning('处理失败，已跳过该项（_kill_by_port）', exc_info=True)
            pass
    else:
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False)
            for pid in result.stdout.split():
                if pid.isdigit():
                    subprocess.run(["kill", "-9", pid], capture_output=True, check=False)
        except Exception:
            logger.warning('处理失败，已跳过该项（_kill_by_port）', exc_info=True)
            pass


def _popen_hidden(cmd, cwd, log_name: str = "", env: dict | None = None):
    """启动子进程并隐藏窗口（Windows），输出重定向到日志文件"""
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not log_name:
        log_name = "backend.log" if "uvicorn" in str(cmd[0]).lower() else "frontend.log"
    log_path = log_dir / log_name
    log_file = open(log_path, "a", encoding="utf-8")
    kwargs = {
        "cwd": cwd,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if env is not None:
        kwargs["env"] = env
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    proc = subprocess.Popen(cmd, **kwargs)
    proc._log_file = log_file  # 保持引用避免被GC关闭
    return proc


def _save_pids(backend_pid: int, frontend_pid: int):
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(f"{backend_pid}\n{frontend_pid}\n")


def _read_pids() -> list[int]:
    if not PID_FILE.exists():
        return []
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return [int(line.strip()) for line in f if line.strip()]
    except Exception:
        logger.warning("读取服务进程号失败", exc_info=True)
        return []


def _clear_pids():
    PID_FILE.unlink(missing_ok=True)


def _kill_pid(pid: int):
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except Exception:
        logger.warning('处理失败，已跳过该项（_kill_pid）', exc_info=True)
        pass


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------
def _clear_pyc_cache():
    """清理所有 Python 字节码缓存，确保加载最新代码"""
    for pyc_dir in PROJECT_ROOT.rglob("__pycache__"):
        try:
            for pyc_file in pyc_dir.glob("*.pyc"):
                pyc_file.unlink()
        except Exception:
            logger.warning('处理失败，已跳过该项（_clear_pyc_cache）', exc_info=True)
            pass


def cmd_start():
    print("=" * 40)
    print("  启动 Paper Pulse")
    print("=" * 40)

    # 清理 Python 缓存
    _clear_pyc_cache()

    # 自动清理端口占用
    api_port, frontend_port = _api_port(), _frontend_port()
    for port in (api_port, frontend_port):
        if _is_port_in_use(port):
            print(f"  端口 {port} 被占用，正在清理...")
            _kill_by_port(port)
            time.sleep(1)

    # 后端
    print(f"  [1/2] 启动后端 API  ({_api_host()}:{api_port})")
    uvicorn = WINDOWS_UVICORN if platform.system() == "Windows" else "uvicorn"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["BACKEND_API_URL"] = os.environ.get("BACKEND_API_URL", f"http://127.0.0.1:{api_port}")
    backend = _popen_hidden(
        [uvicorn, "src.api.main:app", "--host", _api_host(), "--port", str(api_port),
         "--workers", os.environ.get("UVICORN_WORKERS", "1"),
         # Keep idle connections open longer than the frontend proxy reuses them.
         "--timeout-keep-alive", os.environ.get("UVICORN_KEEP_ALIVE", "75")],
        PROJECT_ROOT,
        env=env,
    )
    print(f"        PID {backend.pid}")

    time.sleep(4)

    # 前端
    print(f"  [2/2] 启动前端页面 (http://localhost:{frontend_port})")
    node = shutil.which("node") or "node"
    next_bin = FRONTEND_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    if not next_bin.exists():
        next_bin = FRONTEND_DIR / "node_modules" / ".bin" / "next.cmd"
    frontend = _popen_hidden(
        [node, str(next_bin), "dev", "-p", str(frontend_port)],
        FRONTEND_DIR,
        env=env,
    )
    print(f"        PID {frontend.pid}")

    _save_pids(backend.pid, frontend.pid)

    print("\n" + "=" * 40)
    print("  启动完成!")
    print("=" * 40)
    print(f"  后端: http://{_api_host()}:{api_port}")
    print(f"  前端: http://localhost:{frontend_port}")
    print("\n  停止: python scripts/server.py stop")
    print("=" * 40)


def cmd_stop(silent: bool = False):
    if not silent:
        print("=" * 40)
        print("  停止 Paper Pulse")
        print("=" * 40)

    # 通过 PID 停止
    for pid in _read_pids():
        _kill_pid(pid)
        if not silent:
            print(f"  已停止 PID {pid}")

    _clear_pids()

    # 兜底：通过端口再清一次
    for port in (_api_port(), _frontend_port()):
        _kill_by_port(port)

    if not silent:
        print("=" * 40)
        print("  已停止")
        print("=" * 40)


def _wait_for_ports_free(ports: list[int], timeout: float = 10.0) -> bool:
    """等待端口完全释放，超时返回 False"""
    start = time.time()
    while time.time() - start < timeout:
        if not any(_is_port_in_use(p) for p in ports):
            return True
        time.sleep(0.5)
    return False


def cmd_restart():
    print("=" * 40)
    print("  重启 Paper Pulse")
    print("=" * 40)

    cmd_stop(silent=True)

    print("  等待端口释放...")
    if not _wait_for_ports_free([_api_port(), _frontend_port()], timeout=10.0):
        print("  警告: 端口未完全释放，继续尝试启动...")

    time.sleep(1)
    _clear_pyc_cache()
    cmd_start()


def cmd_disk():
    """查看服务器端硬盘占用情况"""
    print("=" * 50)
    print("  Paper Pulse — 硬盘占用情况")
    print("=" * 50)

    # 临时下载目录
    temp_dir = get_temp_dir(PROJECT_ROOT)
    if temp_dir.exists():
        total_size = sum(f.stat().st_size for f in temp_dir.glob("*") if f.is_file())
        file_count = len(list(temp_dir.glob("*")))
        print(f"\n  临时下载目录: {temp_dir}")
        print(f"  文件数量: {file_count}")
        print(f"  占用空间: {total_size / 1024 / 1024:.2f} MB")

        # 显示详细信息
        for f in temp_dir.glob("*"):
            if f.is_file():
                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime))
                size_mb = f.stat().st_size / 1024 / 1024
                print(f"    {f.name[:40]:<40} {size_mb:>8.2f} MB  {mtime}")
    else:
        print(f"\n  临时下载目录不存在: {temp_dir}")

    # data 目录
    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        print(f"\n  data 目录: {data_dir}")
        for subdir in ["pdf", "images", "temp_downloads"]:
            subpath = data_dir / subdir
            if subpath.exists():
                total_size = sum(f.stat().st_size for f in subpath.rglob("*") if f.is_file())
                file_count = len(list(subpath.rglob("*")))
                print(f"    {subdir}: {file_count} 文件, {total_size / 1024 / 1024:.2f} MB")

    print("\n" + "=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Paper Pulse 服务管理")
    parser.add_argument("action", choices=["start", "stop", "restart", "disk"],
                        help="start=启动, stop=停止, restart=重启, disk=查看硬盘占用")
    args = parser.parse_args()

    if args.action == "start":
        cmd_start()
    elif args.action == "stop":
        cmd_stop()
    elif args.action == "disk":
        cmd_disk()
    else:
        cmd_restart()


if __name__ == "__main__":
    main()
