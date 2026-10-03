"""Ozone automation socket transport. Never resets, halts or resumes the target."""
import json
import math
import re
import socket
import struct
import time
import uuid

TYPES = {"u32": 4, "i32": 4, "float": 4, "bool": 1, "u8": 1}
EXPRESSION = re.compile(r"[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*|\[\d+\])*")


class OzoneError(Exception):
    pass


def validate_config(config):
    if config.get("host") not in ("localhost", "127.0.0.1", "::1"):
        raise OzoneError("只允许连接本机Ozone；远程控制不属于当前范围。")
    if not 1 <= int(config["port"]) <= 65535 or not 0.2 <= float(config["timeout"]) <= 30:
        raise OzoneError("端口或超时范围无效。")
    if not config.get("variables"):
        raise OzoneError("变量绑定不能为空。")
    for name, binding in config["variables"].items():
        if not EXPRESSION.fullmatch(binding["expression"]):
            raise OzoneError(f"{name}: 仅支持变量、成员、数字数组下标表达式。")
        if binding["type"] not in TYPES or not isinstance(binding.get("writable", False), bool):
            raise OzoneError(f"{name}: 类型或写入权限无效。")
    return config


def encode(value, kind):
    if kind == "float":
        number = float(value)
        if not math.isfinite(number):
            raise OzoneError("浮点值必须有限。")
        try:
            return struct.unpack("<I", struct.pack("<f", number))[0]
        except (OverflowError, struct.error) as exc:
            raise OzoneError("超出float32范围。") from exc
    if kind == "bool":
        if str(value).lower() not in ("0", "1", "true", "false"):
            raise OzoneError("bool仅接受0/1或true/false。")
        return int(str(value).lower() in ("1", "true"))
    number = int(str(value), 0)
    low, high = {"u8": (0, 255), "u32": (0, 0xFFFFFFFF), "i32": (-0x80000000, 0x7FFFFFFF)}[kind]
    if not low <= number <= high:
        raise OzoneError("整数超出变量类型范围。")
    return number & 0xFFFFFFFF


def decode(raw, kind):
    if kind == "float":
        return struct.unpack("<f", struct.pack("<I", raw))[0]
    if kind == "i32":
        return struct.unpack("<i", struct.pack("<I", raw))[0]
    return bool(raw) if kind == "bool" else raw


class Ozone:
    def __init__(self, config, log=lambda message: None):
        self.config = validate_config(config)
        self.log = log
        self.sock = None
        self.banner = ""
        self.version = "未知"
        self.ready = False
        self.armed = False

    def close(self):
        self.ready = self.armed = False
        if self.sock:
            self.sock.close()
            self.sock = None

    def connect(self):
        self.close()
        try:
            self.sock = socket.create_connection((self.config["host"], int(self.config["port"])),
                                                 float(self.config["timeout"]))
            self.sock.settimeout(0.3)
            try:
                self.banner = self.sock.recv(8192).decode("utf-8", "replace")
            except socket.timeout:
                self.banner = ""
            match = re.search(r"\bV(\d+\.\d+[a-z]?)", self.banner, re.I)
            self.version = match.group(1) if match else "未知（欢迎信息无版本，按能力检测）"
            self.log("欢迎信息: " + (self.banner.strip() or "未收到，继续只读接口探测"))
            # This checks the exact APIs needed, not an invented minimum version.
            if self.evaluate('Elf.GetExprValue("sizeof(unsigned int)")') != 4:
                raise OzoneError("表达式求值不支持或目标不是32位；禁止写入。")
            cpuid = self.evaluate("Target.ReadU32(0xE000ED00)")
            if (cpuid >> 4) & 0xFFF != 0xC24:
                raise OzoneError(f"未读取到Cortex-M4 CPUID: 0x{cpuid:08X}，检查目标连接。")
            snapshot = self.snapshot()
            self.ready = True
            self.log(f"版本 {self.version}；Socket、符号解析、目标RAM读取通过。尚未验证实物动作。")
            return snapshot
        except OSError as exc:
            self.close()
            raise OzoneError(
                f"无法连接本机Ozone {self.config['host']}:{self.config['port']}：{exc}。"
                "确认Ozone已启动且该版本提供Automation Socket；不自动启动或升级调试器。"
            ) from exc
        except Exception:
            self.close()
            raise

    def evaluate(self, expression):
        if not self.sock:
            raise OzoneError("未连接Ozone。")
        token = "OZP_" + uuid.uuid4().hex
        text = f'Util.LogHex("{token}=", {expression});\n'
        self.log("> " + text.strip())
        deadline = time.monotonic() + float(self.config["timeout"])
        response = ""
        try:
            self.sock.sendall(text.encode("ascii"))
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = self.sock.recv(8192)
                if not chunk:
                    raise OzoneError("Ozone关闭连接。")
                response += chunk.decode("utf-8", "replace")
                if len(response) > 262144:
                    raise OzoneError("返回数据过多；协议不同步。")
                # A command echo contains quotes before '=', and is NOT a result.
                match = re.search(re.escape(token) + r"=[ \t]*(?:0x)?([0-9a-fA-F]{1,8})(?=[ \t]*\r?\n)", response)
                if match:
                    self.log("< " + match.group(0))
                    return int(match.group(1), 16)
                if re.search(r"\b(?:error|unknown command|syntax error)\b", response, re.I):
                    raise OzoneError("Ozone命令错误: " + response[-1500:])
            raise OzoneError("Ozone回复超时。检查19200接口、版本及Util.LogHex支持；不会重试写入。")
        except (OSError, OzoneError) as exc:
            self.log("< " + response[-1500:])
            self.close()  # Do not consume a stale reply for the next operation.
            if isinstance(exc, OzoneError):
                raise
            raise OzoneError(f"连接超时/中断: {exc}；写入结果可能不确定，不自动重发。") from exc

    def binding(self, key):
        try:
            return self.config["variables"][key]
        except KeyError as exc:
            raise OzoneError(f"缺少绑定 {key}；请在配置中添加。") from exc

    def address(self, key):
        binding = self.binding(key)
        expression = binding["expression"]
        # Root pointer is read EVERY TIME. No stale heap addresses after reset.
        if "->" in expression:
            root = expression.split("->", 1)[0]
            pointer_address = self.evaluate(f'Elf.GetExprValue("&{root}")')
            self.validate_address(pointer_address, 4)
            pointer = self.evaluate(f"Target.ReadU32(0x{pointer_address:X})")
            self.validate_address(pointer, 4)
        address = self.evaluate(f'Elf.GetExprValue("&{expression}")')
        self.validate_address(address, TYPES[binding["type"]])
        return address

    @staticmethod
    def validate_address(address, width):
        # STM32F407 SRAM1/2 and CCM. Never permit flash/peripheral writes here.
        valid = (0x20000000 <= address and address + width <= 0x20020000) or (
            0x10000000 <= address and address + width <= 0x10010000)
        if not valid or address % width:
            raise OzoneError(f"无效RAM地址0x{address:08X}：对象未初始化、符号不匹配或解析失败。")

    def read(self, key):
        binding = self.binding(key)
        address = self.address(key)
        width = TYPES[binding["type"]]
        raw = self.evaluate(f"Target.ReadU{width * 8}(0x{address:X})")
        return decode(raw, binding["type"])

    def write(self, key, value):
        if not self.ready or not self.armed:
            raise OzoneError("尚未通过诊断或未允许写入。")
        binding = self.binding(key)
        if not binding.get("writable", False):
            raise OzoneError(f"{key}为只读绑定。")
        raw = encode(value, binding["type"])
        address = self.address(key)
        result = self.evaluate(f"Target.WriteU{TYPES[binding['type']] * 8}(0x{address:X}, 0x{raw:X})")
        if result != 0:
            self.armed = False
            raise OzoneError(f"写入失败 {key}；可能已部分执行，禁止自动重试。")

    def snapshot(self):
        values = {}
        for key in self.config["variables"]:
            value = self.read(key)
            if key in ("lift_root", "flip_root"):
                self.validate_address(value, 4)
            values[key] = value
        return values

    def wait_clear(self, key):
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.read(key) == 0:
                return
            time.sleep(0.03)
        self.armed = False
        raise OzoneError("命令未被消费：目标可能暂停或任务未运行。不要自动重发运动命令。")

    def action(self, axis, command, value=None):
        if self.read(axis + "_command") != 0:
            raise OzoneError("上一条命令尚未消费。")
        if command in (1, 2, 4) and self.read(axis + "_busy"):
            raise OzoneError("设备正在运动，拒绝提交此操作。")
        if command in (2, 4) and not self.read(axis + "_zeroed"):
            raise OzoneError("设备尚未设零。")
        if value is not None:
            self.write("lift_distance" if axis == "lift" else "flip_target", value)
        self.write(axis + "_command", command)  # Command is ALWAYS written last.
        self.wait_clear(axis + "_command")
        result = self.read(axis + "_result")
        if result:
            raise OzoneError(f"固件拒绝命令，result={result}（1离线/未设零，2未使能，3非法值，4忙，5规划失败）。")
        return "命令已接受，不代表运动完成；刷新查看busy和位置。"

    def gripper(self, key, closed):
        self.write(key, int(closed))
        self.write("gripper_trigger", 1)
        self.wait_clear("gripper_trigger")
        return "GPIO输出请求已消费；无气缸到位传感器，不能确认夹紧。"
