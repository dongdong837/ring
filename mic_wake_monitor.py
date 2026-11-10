#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mic_wake_monitor.py —— 麦克风阵列唤醒 / 声源定位监视器

适用硬件：
  * 讯飞六麦圆形阵列（XFMDPV0018 / MIC_TYPE=xf）      —— 有 DOA 角度
  * WonderEcho Pro 等 CH340 串口语音模块（16 进制命令码）—— 只有命令词，无角度

串口协议（115200 8N1，头部 0xA5）：
    帧头 3 字节 : A5 01 01 = 心跳       A5 01 04 = 唤醒 / 语音事件
    长度 4 字节 : 前 2 字节小端 = 负载长度 L，后 2 字节为保留位
    负载 L 字节 : UTF-8 JSON
    帧尾 1 字节 : 校验

  整帧长度 = 3 + 4 + L + 1 = L + 8

依赖：
  无强制依赖。有 pyserial 时优先使用；没有则用 Windows kernel32 /
  Linux termios 的原生实现，因此拷贝到任何装了 Python 3.7+ 的电脑都能直接跑。

示例：
  python mic_wake_monitor.py                      # 自动找麦克风串口
  python mic_wake_monitor.py -p COM19             # 指定串口
  python mic_wake_monitor.py --list               # 列出并探测所有串口
  python mic_wake_monitor.py --replay sample.bin  # 离线回放抓包文件
  python mic_wake_monitor.py --csv log.csv        # 同时记录到 CSV
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import platform
import re
import sys
import time

VERSION = "1.0.1"

HEADER = b"\xa5\x01"
FRAME_HEARTBEAT = 0x01
FRAME_EVENT = 0x04

# 拼音唤醒词 -> 中文显示（模块上报的是拼音，没有对应项就原样输出）
KEYWORD_ZH = {
    "xiao3 huan4 xiao3 huan4": "小幻小幻",
    "ni3 hao3 xiao3 ya4": "你好小亚",
    "ni3 hao3 xiao3 ya3": "你好小雅",
    "hello hi wonder": "hello hiwonder",
    "hello hiwonder": "hello hiwonder",
    "hello high wonder": "hello hiwonder",
}


def zh_keyword(raw: str) -> str:
    """把模块上报的拼音唤醒词翻成可读文字。"""
    if not raw:
        return ""
    key = " ".join(raw.strip().lower().split())
    if key in KEYWORD_ZH:
        return KEYWORD_ZH[key]
    # 去掉拼音声调数字，退化成 xiao huan xiao huan 再试一次
    stripped = re.sub(r"[1-5]", "", key)
    for k, v in KEYWORD_ZH.items():
        if re.sub(r"[1-5]", "", k) == stripped:
            return v
    return raw


# --------------------------------------------------------------------------
# 串口抽象层：pyserial -> 原生实现
# --------------------------------------------------------------------------
class SerialPort:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.05):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._impl = None
        self._backend = ""

        try:
            import serial  # type: ignore
            self._sp = serial.Serial()
            self._sp.port = port
            self._sp.baudrate = baud
            self._sp.bytesize = serial.EIGHTBITS
            self._sp.parity = serial.PARITY_NONE
            self._sp.stopbits = serial.STOPBITS_ONE
            self._sp.timeout = timeout
            self._sp.dtr = False
            self._sp.rts = False
            self._sp.open()
            self._impl = self._read_pyserial
            self._backend = f"pyserial {serial.__version__}"
            return
        except ImportError:
            pass
        except Exception as exc:  # 打开失败（被占用 / 设备异常）要如实抛出
            raise OSError(f"打开 {port} 失败: {exc}") from exc

        if platform.system() == "Windows":
            self._open_win32()
            self._backend = "kernel32 原生"
        else:
            self._open_termios()
            self._backend = "termios 原生"

    # ---- pyserial ----
    def _read_pyserial(self, maxbytes: int = 4096) -> bytes:
        try:
            return self._sp.read(maxbytes)
        except Exception:
            return b""

    # ---- Windows ----
    def _open_win32(self):
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        k32.ReadFile.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        k32.SetCommState.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k32.BuildCommDCBW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID]
        k32.EscapeCommFunction.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING = 0x80000000, 0x40000000, 3
        INVALID = ctypes.c_void_p(-1).value

        handle = k32.CreateFileW(
            "\\\\.\\" + self.port, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, 0, None,
        )
        if not handle or handle == INVALID:
            err = ctypes.get_last_error()
            raise OSError(err, _win_err_text(ctypes, err, self.port))
        self._h = handle
        self._ct = ctypes
        self._wintypes = wintypes
        self._k32 = k32

        if not k32.BuildCommDCBW(
            f"baud={self.baud} parity=N data=8 stop=1",
            ctypes.create_string_buffer(64),
        ):
            pass  # 个别系统上返回值不可靠，下面 SetCommState 才是判定依据

        dcb = ctypes.create_string_buffer(64)
        k32.BuildCommDCBW(f"baud={self.baud} parity=N data=8 stop=1", dcb)
        if not k32.SetCommState(handle, dcb):
            err = ctypes.get_last_error()
            k32.CloseHandle(handle)
            raise OSError(err, _win_err_text(ctypes, err, self.port))

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [
                ("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD),
            ]

        tmo = COMMTIMEOUTS(0xFFFFFFFF, 0, int(self.timeout * 1000), 0, 0)
        k32.SetCommTimeouts(handle, ctypes.byref(tmo))
        k32.EscapeCommFunction(handle, 6)  # CLRDTR
        k32.EscapeCommFunction(handle, 4)  # CLRRTS
        k32.PurgeComm(handle, 0x000F)
        self._impl = self._read_win32

    def _read_win32(self, maxbytes: int = 4096) -> bytes:
        ct, wt = self._ct, self._wintypes
        buf = ct.create_string_buffer(maxbytes)
        got = wt.DWORD(0)
        if not self._k32.ReadFile(self._h, buf, maxbytes, ct.byref(got), None):
            return b""
        return buf.raw[: got.value]

    # ---- Linux / macOS ----
    def _open_termios(self):
        import termios

        speed = getattr(termios, f"B{self.baud}", None)
        if speed is None:
            raise OSError(f"termios 不支持波特率 {self.baud}")
        self._fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self._fd)
        attrs[0] = 0                                              # iflag
        attrs[1] = 0                                              # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL    # cflag: 8N1
        attrs[3] = 0                                              # lflag
        attrs[4] = speed
        attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        # 拉低 DTR / RTS，和机器人上的用法保持一致
        try:
            import fcntl
            import struct
            TIOCMBIC = 0x5417
            TIOCM_DTR, TIOCM_RTS = 0x002, 0x004
            fcntl.ioctl(self._fd, TIOCMBIC, struct.pack("I", TIOCM_DTR | TIOCM_RTS))
        except Exception:
            pass
        self._impl = self._read_termios

    def _read_termios(self, maxbytes: int = 4096) -> bytes:
        import select

        try:
            ready, _, _ = select.select([self._fd], [], [], self.timeout)
        except Exception:
            return b""
        if not ready:
            return b""
        try:
            return os.read(self._fd, maxbytes)
        except (BlockingIOError, OSError):
            return b""

    # ---- 公共接口 ----
    def read(self, maxbytes: int = 4096) -> bytes:
        return self._impl(maxbytes)

    @property
    def backend(self) -> str:
        return self._backend

    def close(self):
        try:
            if platform.system() == "Windows" and self._backend.startswith("kernel32"):
                self._k32.CloseHandle(self._h)
            elif self._backend.startswith("termios"):
                os.close(self._fd)
            elif self._backend.startswith("pyserial"):
                self._sp.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def setup_console() -> None:
    """Windows 控制台默认是 GBK，中文会花屏，这里强制切到 UTF-8。"""
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _win_err_text(ctypes, err: int, port: str) -> str:
    try:
        msg = ctypes.FormatError(err).strip()
    except Exception:
        msg = f"错误码 {err}"
    if err == 31:  # ERROR_GEN_FAILURE
        msg += " —— CH340 常见故障：拔插一次麦克风 USB 线，或换一个 USB 口"
    elif err == 5:  # ERROR_ACCESS_DENIED
        msg += " —— 串口被别的程序占用了（串口助手 / 上位机 / 另一个本程序）"
    elif err == 2:
        msg += f" —— 找不到 {port}，设备没插或被拔掉了"
    return f"{msg} (WinError {err})"


# --------------------------------------------------------------------------
# 帧解析
# --------------------------------------------------------------------------
class FrameParser:
    """把串口字节流切成 A5 帧，只吐事件帧的 JSON 文本。"""

    def __init__(self):
        self._buf = bytearray()
        self.heartbeats = 0
        self.events = 0

    def feed(self, data: bytes):
        self._buf.extend(data)

    def frames(self):
        while True:
            idx = self._buf.find(HEADER)
            if idx < 0:
                # 没有帧头，只保留最后 1 字节（可能是被截断的帧头）
                if len(self._buf) > 1:
                    del self._buf[: len(self._buf) - 1]
                return
            if idx > 0:
                del self._buf[:idx]              # 丢掉帧头之前的垃圾字节
            if len(self._buf) < 8:
                return
            ftype = self._buf[2]
            length = self._buf[3] | (self._buf[4] << 8)
            total = length + 8
            if length > 4096:                    # 明显错位，丢 1 字节重新同步
                del self._buf[:1]
                continue
            if len(self._buf) < total:
                return
            payload = bytes(self._buf[7: 7 + length])
            checksum = self._buf[7 + length]
            del self._buf[:total]

            if ftype == FRAME_HEARTBEAT:
                self.heartbeats += 1
                continue
            if ftype != FRAME_EVENT:
                continue
            if not payload.startswith(b"{"):
                continue
            self.events += 1
            yield payload, checksum


def parse_event(payload: bytes) -> dict:
    """解析唤醒事件 JSON，返回扁平化字段。"""
    obj = json.loads(payload.decode("utf-8", "ignore"))
    content = obj.get("content", {}) or {}
    info_raw = content.get("info", "") or ""
    info = {}
    if isinstance(info_raw, str) and info_raw.strip().startswith("{"):
        try:
            info = json.loads(info_raw)
        except json.JSONDecodeError:
            info = {}
    elif isinstance(info_raw, dict):
        info = info_raw

    ivw = info.get("ivw", {}) or {}
    return {
        "event_type": content.get("eventType"),
        "result": content.get("result", ""),
        "angle": ivw.get("angle"),
        "beam": ivw.get("beam"),
        "physical": ivw.get("physical"),
        "score": ivw.get("score"),
        "power": ivw.get("power"),
        "keyword": ivw.get("keyword", content.get("result", "")),
        "start_ms": ivw.get("start_ms"),
        "end_ms": ivw.get("end_ms"),
    }


def convert_angle(raw: float) -> int:
    """与 ROS 节点的映射保持一致：val_map(a,0,360,360,0)+240，再对 360 取模。"""
    return int((600 - int(round(float(raw)))) % 360)


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
TAG_WIDTH = 14


def _tag(name: str) -> str:
    return f"[{name}]:".ljust(TAG_WIDTH)


def print_header(args, backend: str, port: str):
    line = "=" * 72
    print(line)
    print(f"  麦克风唤醒 / 声源定位监视器  v{VERSION}")
    print(f"  串口      : {port}  ({args.baud} 8N1)")
    print(f"  后端      : {backend}")
    print(f"  角度换算  : {'(600-raw)%360  [ROS 一致]' if not args.no_convert else '关闭（显示原始角）'}")
    print(f"  开始时间  : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(line)
    print("  请喊唤醒词（中文固件：小幻小幻 / 英文固件：hello hiwonder），Ctrl+C 退出")
    print(line)


def print_event(ev: dict, args) -> None:
    raw = ev.get("angle")
    kw_raw = ev.get("keyword") or ev.get("result") or ""
    kw_zh = zh_keyword(kw_raw)

    t = _tag(args.tag)
    print(f"{t}>>> 检测到唤醒！")
    if raw is not None:
        if args.no_convert:
            print(f"{t}    角度: {float(raw):.1f} 度")
        else:
            conv = convert_angle(raw)
            print(f"{t}    角度: {float(raw):.1f} 度  (换算 {conv}° / {direction_text(conv)})")
    else:
        print(f"{t}    角度: 无（该模块不支持声源定位）")

    if ev.get("beam") is not None:
        extra = ""
        if ev.get("physical") is not None and ev["physical"] != ev["beam"]:
            extra = f"  (物理通道 {ev['physical']})"
        print(f"{t}    波束: {ev['beam']}{extra}")

    if kw_zh and kw_zh != kw_raw:
        print(f"{t}    唤醒词: {kw_zh}  ({kw_raw})")
    elif kw_raw:
        print(f"{t}    唤醒词: {kw_raw}")

    extra_bits = []
    if ev.get("score") is not None:
        extra_bits.append(f"置信度 {float(ev['score']):.0f}")
    if ev.get("power") is not None:
        extra_bits.append(f"能量 {float(ev['power']):.0f}")
    if extra_bits:
        print(f"{t}    {'   '.join(extra_bits)}")

    print(f"{t}" + "-" * 60)

    if raw is not None:
        print(f"{_tag('mic_serial_node')}Wakeup event published: "
              f"angle={float(raw):.1f}, beam={ev.get('beam')}, keyword={kw_raw}")


DIRECTIONS = ["正前方", "右前方", "右侧", "右后方", "正后方", "左后方", "左侧", "左前方"]


def direction_text(deg: int) -> str:
    return DIRECTIONS[int(((deg + 22.5) % 360) // 45)]


def append_csv(path: str, ev: dict, port: str):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "port", "angle_raw", "angle_conv", "beam",
                        "physical", "score", "power", "keyword"])
        raw = ev.get("angle")
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"), port,
            raw,
            convert_angle(raw) if raw is not None else "",
            ev.get("beam"), ev.get("physical"), ev.get("score"), ev.get("power"),
            ev.get("keyword") or ev.get("result"),
        ])


# --------------------------------------------------------------------------
# 端口枚举 / 自动识别
# --------------------------------------------------------------------------
def list_ports() -> list[str]:
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
                ports = []
                i = 0
                while True:
                    try:
                        _, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    ports.append(value)
                    i += 1
                return sorted(set(ports))
        except OSError:
            return []
    found = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*", "/dev/cu.usbserial*", "/dev/cu.usbmodem*"):
        found.extend(glob.glob(pattern))
    return sorted(set(found))


def probe_port(port: str, baud: int, seconds: float = 1.5) -> str:
    """探测一个串口是否在发 A5 帧。返回 'mic(...)' / 'silent(...)' / 'error: ...'"""
    try:
        with SerialPort(port, baud) as sp:
            parser = FrameParser()
            deadline = time.time() + seconds
            got = 0
            while time.time() < deadline:
                data = sp.read()
                if data:
                    got += len(data)
                    parser.feed(data)
                    for _ in parser.frames():
                        return f"mic (收到唤醒事件)"
            # 只收到心跳也算麦克风：空闲时固件只发心跳
            if parser.heartbeats > 0:
                return f"mic (心跳 {parser.heartbeats} 帧 / {got} 字节)"
            if got > 0:
                return f"未知设备 ({got} 字节，不是 A5 协议)"
            return "无数据"
    except OSError as exc:
        return f"error: {exc}"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_live(args) -> int:
    port = args.port
    if not port:
        print("正在自动识别麦克风串口 ...")
        for candidate in list_ports():
            status = probe_port(candidate, args.baud)
            flag = "  <== 麦克风" if status.startswith("mic") else ""
            print(f"  {candidate:<10} {status}{flag}")
            if status.startswith("mic") and not port:
                port = candidate
        if not port:
            print("\n没找到麦克风。请确认：")
            print("  1) USB 线插好，设备管理器里能看到 CH340/CH341 串口")
            print("  2) 用 --list 查看具体串口名，再用 -p COMx 指定")
            return 2

    try:
        sp = SerialPort(port, args.baud)
    except OSError as exc:
        print(f"\n[错误] {exc}")
        return 3

    print_header(args, sp.backend, port)
    parser = FrameParser()
    last_hb = time.time()
    started = time.time()
    try:
        while True:
            if args.seconds and time.time() - started >= args.seconds:
                print(f"\n已到 {args.seconds} 秒，自动退出。"
                      f"心跳 {parser.heartbeats} 帧，唤醒事件 {parser.events} 次。")
                return 0
            data = sp.read()
            if data:
                parser.feed(data)
                for payload, _checksum in parser.frames():
                    try:
                        ev = parse_event(payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if args.json:
                        print(json.dumps(ev, ensure_ascii=False), flush=True)
                    else:
                        print_event(ev, args)
                    if args.csv:
                        append_csv(args.csv, ev, port)
            else:
                time.sleep(0.005)

            if args.heartbeat_interval and time.time() - last_hb >= args.heartbeat_interval:
                last_hb = time.time()
                print(f"{_tag(args.tag)}[心跳] 累计 {parser.heartbeats} 帧，"
                      f"事件 {parser.events} 次，缓冲 {len(parser._buf)} 字节")
    except KeyboardInterrupt:
        print(f"\n已退出。心跳 {parser.heartbeats} 帧，唤醒事件 {parser.events} 次。")
        return 0
    finally:
        sp.close()


def run_replay(args) -> int:
    path = args.replay
    if not os.path.exists(path):
        print(f"[错误] 找不到文件 {path}")
        return 2
    with open(path, "rb") as f:
        data = f.read()
    print_header(args, "离线回放", f"{os.path.basename(path)} ({len(data)} 字节)")
    parser = FrameParser()
    parser.feed(data)
    n = 0
    for payload, _checksum in parser.frames():
        try:
            ev = parse_event(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        n += 1
        if args.json:
            print(json.dumps(ev, ensure_ascii=False))
        else:
            print_event(ev, args)
        if args.csv:
            append_csv(args.csv, ev, path)
    print(f"回放完成：心跳 {parser.heartbeats} 帧，唤醒事件 {n} 次")
    return 0


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(
        description="麦克风阵列唤醒 / 声源定位监视器（零依赖，可跨平台运行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python mic_wake_monitor.py\n"
               "  python mic_wake_monitor.py -p COM19\n"
               "  python mic_wake_monitor.py --list\n"
               "  python mic_wake_monitor.py --replay sample_capture.bin\n",
    )
    ap.add_argument("-p", "--port", help="串口名，如 COM19 / /dev/ttyUSB0，不填则自动识别")
    ap.add_argument("-b", "--baud", type=int, default=115200, help="波特率，默认 115200")
    ap.add_argument("--list", action="store_true", help="列出所有串口并探测是否为麦克风")
    ap.add_argument("--replay", metavar="FILE", help="离线回放抓包文件（不接硬件）")
    ap.add_argument("--csv", metavar="FILE", help="把事件追加记录到 CSV")
    ap.add_argument("--json", action="store_true", help="输出 JSONL，便于二次开发")
    ap.add_argument("--no-convert", action="store_true", help="只显示原始角度，不做 ROS 换算")
    ap.add_argument("--tag", default="xf_mic_app", help="日志标签，默认 xf_mic_app")
    ap.add_argument("--heartbeat-interval", type=float, default=0,
                    help="每隔 N 秒打印一次心跳统计，0 表示不打印")
    ap.add_argument("--seconds", type=float, default=0,
                    help="运行 N 秒后自动退出（0 = 一直运行，Ctrl+C 退出）")
    ap.add_argument("-v", "--version", action="version", version=f"mic_wake_monitor {VERSION}")
    args = ap.parse_args()

    if args.list:
        ports = list_ports()
        if not ports:
            print("没有发现任何串口设备。")
            return 1
        print(f"{'串口':<12}{'探测结果'}")
        print("-" * 48)
        for p in ports:
            print(f"{p:<12}{probe_port(p, args.baud)}")
        return 0

    if args.replay:
        return run_replay(args)
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
