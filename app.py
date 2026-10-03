"""Ozone Control Panel: native desktop UI, packaged as OzonePanel.exe."""
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
from ozone_client import Ozone, OzoneError, validate_config

BUNDLE = Path(__file__).resolve().parent
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE
BG, CARD, EDGE = "#101820", "#1a2632", "#304252"
TEXT, MUTED, ACCENT = "#ecf2f7", "#a8bbcb", "#24b7a0"
GOOD, BAD, WARN = "#52d6a7", "#ff8c94", "#efc371"
FONT = "Microsoft YaHei UI"


def load_config():
    path = BASE / ".local.json"
    if not path.exists():
        path = BASE / "config.json"
    if not path.exists():
        path = BUNDLE / "config.json"
    return validate_config(json.loads(path.read_text(encoding="utf-8")))


class Panel:
    def __init__(self, root):
        self.root = root
        root.title("Ozone Control Panel · 机构调试台")
        root.geometry("1280x850")
        root.minsize(1080, 760)
        root.configure(fg_color=BG)
        self.requests = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.working = False
        self.client = None
        self.values = {}
        self.closed = False
        self.config = load_config()
        self.arm = tk.BooleanVar(value=False)
        self.distance = tk.StringVar(value="20")
        self.custom_value = tk.StringVar(value="0")
        self.variable = tk.StringVar()
        self.write_buttons = []
        self.action_title = "准备就绪"
        self.build()
        threading.Thread(target=self.worker, daemon=True).start()
        root.after(50, self.pump)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def label(self, parent, text, size=14, color=TEXT, bold=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                           font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text, command, write=False, primary=False, danger=False, **kwargs):
        color = "#763842" if danger else ACCENT if primary else "#293e50"
        button = ctk.CTkButton(parent, text=text, command=command, height=40,
                              fg_color=color, hover_color="#40586c" if not primary else "#199983",
                              text_color=BG if primary else TEXT, corner_radius=8,
                              font=(FONT, 14, "bold"), **kwargs)
        if write:
            self.write_buttons.append(button)
        return button

    def build(self):
        shell = ctk.CTkFrame(self.root, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=22, pady=18)
        heading = ctk.CTkFrame(shell, fg_color="transparent")
        heading.pack(fill="x")
        self.label(heading, "OZONE / CONTROL DESK", 26, bold=True).pack(side="left")
        self.connection_badge = self.label(heading, "未连接 · 写入关闭", 14, WARN)
        self.connection_badge.pack(side="right")
        self.label(shell, "STM32F407  ·  双轴 / 气动夹爪    |    仅操作当前固件RAM，不复位、不暂停、不烧录", 13, MUTED).pack(anchor="w", pady=(2, 14))
        toolbar = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=10)
        toolbar.pack(fill="x", pady=(0, 16))
        self.button(toolbar, "连接与诊断", self.connect, primary=True, width=130).pack(side="left", padx=12, pady=12)
        self.button(toolbar, "断开", lambda: self.submit("disconnect", None, "断开连接"), width=80).pack(side="left", padx=(0, 8))
        self.button(toolbar, "刷新状态", lambda: self.submit("snapshot", None, "读取设备状态"), width=110).pack(side="left")
        self.endpoint = self.label(toolbar, "", 12, MUTED)
        self.endpoint.pack(side="left", padx=14)
        ctk.CTkSwitch(toolbar, text="目标运行中，允许写入", variable=self.arm,
                      command=self.arm_changed, progress_color=ACCENT,
                      font=(FONT, 13)).pack(side="right", padx=16)
        body = ctk.CTkFrame(shell, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(body, fg_color=CARD, corner_radius=10,
                                   segmented_button_selected_color="#236d72",
                                   segmented_button_selected_hover_color="#2c8588")
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.tabs.add("机构控制")
        self.tabs.add("变量与配置")
        self.tabs.add("变量快照")
        controls = self.tabs.tab("机构控制")
        self.metrics = {}
        for axis, title, unit in (("lift", "升降", "mm"), ("flip", "翻转", "deg")):
            frame = ctk.CTkFrame(controls, fg_color="#202f3d", corner_radius=10)
            frame.pack(fill="x", padx=6, pady=(6, 12))
            title_row = ctk.CTkFrame(frame, fg_color="transparent")
            title_row.pack(fill="x", padx=15, pady=(10, 2))
            self.label(title_row, title, 19, bold=True).pack(side="left")
            position = self.label(title_row, "— " + unit, 25, ACCENT, True)
            position.pack(side="right")
            state = self.label(frame, "未读取  /  设零 —  /  result —", 12, MUTED)
            state.pack(anchor="w", padx=15, pady=(0, 10))
            self.metrics[axis] = (position, state, unit)
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=15)
            row.grid_columnconfigure((0, 1, 2, 3), weight=1)
            for index, (text, code) in enumerate((("设零", 1), ("使能保持", 2), ("失能", 3), ("停止保持", 5))):
                self.button(row, text,
                            lambda a=axis, c=code, t=text: self.submit("action", (a, c, None), ("升降" if a == "lift" else "翻转") + " · " + t),
                            write=True, danger=code == 5, width=85).grid(row=0, column=index, sticky="ew", padx=3)
            move = ctk.CTkFrame(frame, fg_color="transparent")
            move.pack(fill="x", padx=15, pady=12)
            if axis == "lift":
                ctk.CTkEntry(move, textvariable=self.distance, width=115, height=42, font=(FONT, 16)).pack(side="left")
                self.label(move, "mm", 13, MUTED).pack(side="left", padx=8)
                self.button(move, "上升", lambda: self.move_lift(1), write=True, primary=True, width=105).pack(side="left", padx=4)
                self.button(move, "下降", lambda: self.move_lift(-1), write=True, width=105).pack(side="left", padx=4)
            else:
                self.button(move, "翻转到 180°", lambda: self.submit("action", ("flip", 4, 180), "翻转 · 前往180°"), write=True, primary=True, width=180).pack(side="left", padx=3)
                self.button(move, "返回 0°", lambda: self.submit("action", ("flip", 4, 0), "翻转 · 返回0°"), write=True, width=150).pack(side="left", padx=6)
        grippers = ctk.CTkFrame(controls, fg_color="#202f3d", corner_radius=10)
        grippers.pack(fill="x", padx=6)
        self.label(grippers, "气动夹爪", 19, bold=True).pack(anchor="w", padx=15, pady=(10, 8))
        for label, key in (("固定爪", "fixed_gripper"), ("移动爪", "moving_gripper")):
            row = ctk.CTkFrame(grippers, fg_color="transparent")
            row.pack(fill="x", padx=15, pady=(0, 10))
            self.label(row, label, 14).pack(side="left", padx=(0, 15))
            for closed, text in ((1, "闭合"), (0, "打开")):
                self.button(row, text, lambda k=key, c=closed, t=label + text: self.submit("gripper", (k, c), t),
                            write=True, width=115).pack(side="left", padx=4)
        self.label(controls, "位置为最近读取快照，需点击刷新。\n停止保持不是硬件急停；夹爪状态不是到位反馈。", 12, MUTED, justify="left").pack(anchor="w", padx=12, pady=12)
        self.build_config(self.tabs.tab("变量与配置"))
        self.build_snapshot(self.tabs.tab("变量快照"))
        results = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10)
        results.grid(row=0, column=1, sticky="nsew")
        self.label(results, "命令返回 / RESPONSE", 17, bold=True).pack(anchor="w", padx=20, pady=(22, 10))
        self.result_heading = self.label(results, "等待连接", 27, WARN, True, anchor="w")
        self.result_heading.pack(fill="x", padx=20)
        self.result_action = self.label(results, "尚未发送任何目标命令", 14, MUTED, anchor="w")
        self.result_action.pack(fill="x", padx=20, pady=(4, 10))
        self.result_body = ctk.CTkTextbox(results, height=160, font=(FONT, 16), wrap="word",
                                         fg_color="#15222d", text_color=TEXT, border_width=1, border_color=EDGE)
        self.result_body.pack(fill="x", padx=18, pady=(0, 14))
        self.set_result("等待连接", "先进行只读连接诊断。通过后确认目标运行中，再允许写入。", WARN)
        journal = ctk.CTkTabview(results, fg_color="transparent", segmented_button_selected_color="#236d72")
        journal.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        journal.add("操作记录")
        journal.add("协议详情")
        self.history = ctk.CTkTextbox(journal.tab("操作记录"), font=(FONT, 14), wrap="word", fg_color="#15222d")
        self.history.pack(fill="both", expand=True)
        self.history.configure(state="disabled")
        self.protocol = ctk.CTkTextbox(journal.tab("协议详情"), font=("Consolas", 12), wrap="word", fg_color="#15222d")
        self.protocol.pack(fill="both", expand=True)
        self.protocol.configure(state="disabled")
        self.label(shell, "READ FIRST  /  参数先写，命令最后写；接受不代表到位。断开面板不会自动停止机构。", 12, MUTED).pack(anchor="w", pady=(12, 0))
        self.update_bindings()
        self.button_states()

    def build_config(self, parent):
        self.label(parent, "变量绑定与连接设置", 19, bold=True).pack(anchor="w", pady=8)
        self.label(parent, "配置在保存后断开，重新连接生效。通用写入不执行机构顺序检查。", 12, MUTED, wraplength=550, justify="left").pack(anchor="w", pady=(0, 10))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        self.var_box = ctk.CTkComboBox(row, variable=self.variable, state="readonly", width=170)
        self.var_box.pack(side="left")
        self.button(row, "读取", lambda: self.submit("read", self.variable.get(), "读取 · " + self.variable.get()), width=65).pack(side="left", padx=6)
        ctk.CTkEntry(row, textvariable=self.custom_value, width=95).pack(side="left")
        self.button(row, "写入", self.custom_write, write=True, width=65).pack(side="left", padx=6)
        self.config_editor = ctk.CTkTextbox(parent, font=("Consolas", 12), wrap="none", fg_color="#15222d")
        self.config_editor.pack(fill="both", expand=True, pady=12)
        self.config_editor.insert("1.0", json.dumps(self.config, ensure_ascii=False, indent=2))
        self.button(parent, "保存配置并断开", self.save_config, width=170).pack(anchor="w", pady=8)

    def build_snapshot(self, parent):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#15222d", foreground=TEXT, fieldbackground="#15222d", rowheight=32, font=(FONT, 12), borderwidth=0)
        style.configure("Treeview.Heading", background="#293e50", foreground=TEXT, font=(FONT, 12, "bold"))
        style.map("Treeview", background=[("selected", "#236d72")], foreground=[("selected", "white")])
        self.label(parent, "变量快照", 19, bold=True).pack(anchor="w", pady=8)
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("expression", "value"), show="headings")
        self.tree.heading("expression", text="表达式")
        self.tree.heading("value", text="值")
        self.tree.column("expression", width=270)
        self.tree.column("value", width=130)
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.snapshot_time = self.label(parent, "尚未读取", 12, MUTED)
        self.snapshot_time.pack(anchor="w", pady=10)

    def update_bindings(self):
        keys = list(self.config["variables"])
        self.var_box.configure(values=keys)
        self.variable.set(keys[0] if keys else "")
        self.endpoint.configure(text=f"{self.config['host']}:{self.config['port']}")

    def set_result(self, title, detail, color=TEXT):
        self.result_heading.configure(text=title, text_color=color)
        self.result_action.configure(text=self.action_title)
        self.result_body.configure(state="normal")
        self.result_body.delete("1.0", "end")
        self.result_body.insert("end", detail)
        self.result_body.configure(state="disabled")

    def append(self, box, text, max_lines=600):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        if int(box.index("end-1c").split(".")[0]) > max_lines:
            box.delete("1.0", "100.0")
        box.see("end")
        box.configure(state="disabled")

    def record(self, title, detail):
        self.append(self.history, datetime.now().strftime("%H:%M:%S") + "  " + title + "\n" + detail + "\n")

    def button_states(self):
        ready = bool(self.client and self.client.ready)
        allowed = ready and self.arm.get() and not self.working
        for button in self.write_buttons:
            button.configure(state="normal" if allowed else "disabled")
        label = "写入已授权" if allowed else "操作执行中" if self.working else "只读已连接" if ready else "未连接 · 写入关闭"
        self.connection_badge.configure(text=label, text_color=GOOD if allowed else MUTED if ready else WARN)

    def submit(self, operation, data, label):
        if self.working:
            return
        self.working = True
        self.action_title = label
        self.set_result("执行中", "等待Ozone返回。运动写入不会自动重试。", WARN)
        self.record(label, "已提交，等待返回")
        self.button_states()
        self.requests.put_nowait((operation, data, self.arm.get(), label))

    def connect(self):
        if self.working:
            return
        self.arm.set(False)
        self.submit("connect", self.config, "连接 / 只读诊断")

    def arm_changed(self):
        if self.arm.get() and not (self.client and self.client.ready):
            self.arm.set(False)
            self.set_result("未允许写入", "请先连接并通过只读诊断。", WARN)
        self.button_states()

    def move_lift(self, sign):
        try:
            value = abs(float(self.distance.get())) * sign
            if not math.isfinite(value):
                raise ValueError()
            self.submit("action", ("lift", 4, value), f"升降 · 相对位移 {value:g} mm")
        except ValueError:
            self.set_result("输入无效", "请输入有限的毫米数。", BAD)

    def custom_write(self):
        key = self.variable.get()
        value = self.custom_value.get()
        if messagebox.askyesno("写入确认", f"直接写入 {key} = {value}？\n不执行机构动作顺序检查。", parent=self.root):
            self.submit("write", (key, value), "通用写入 · " + key)

    def save_config(self):
        if self.working:
            return
        try:
            config = validate_config(json.loads(self.config_editor.get("1.0", "end")))
            (BASE / ".local.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.config = config
            self.values.clear()
            self.arm.set(False)
            self.update_bindings()
            self.submit("disconnect", None, "保存配置")
        except (OSError, ValueError, KeyError, TypeError, OzoneError) as exc:
            self.set_result("配置保存失败", str(exc), BAD)

    def worker(self):
        while True:
            operation, data, armed, label = self.requests.get()
            try:
                if operation == "connect":
                    if self.client:
                        self.client.close()
                    self.client = Ozone(data, lambda message: self.events.put(("log", message)))
                    values = self.client.connect()
                    message = f"Ozone版本：{self.client.version}\nSocket、符号表达式和目标RAM读取通过。勾选授权后才能写入。"
                    title = "诊断通过"
                elif operation == "disconnect":
                    if self.client:
                        self.client.close()
                    values = {}
                    message = "连接已关闭，写入权限已解除。没有发送任何停止、复位或运行指令。"
                    title = "已断开"
                else:
                    if not self.client or not self.client.ready:
                        raise OzoneError("先连接并通过只读诊断。")
                    self.client.armed = armed
                    values = {}
                    if operation == "snapshot":
                        values = self.client.snapshot()
                        message, title = "已读取变量快照。位置与状态以本次读取为准。", "读取成功"
                    elif operation == "read":
                        values = {data: self.client.read(data)}
                        message, title = f"{data} = {values[data]}", "读取成功"
                    elif operation == "action":
                        message = self.client.action(*data)
                        title = "命令已接受"
                    elif operation == "gripper":
                        message = self.client.gripper(*data)
                        title = "输出请求已消费"
                    elif operation == "write":
                        self.client.write(*data)
                        message, title = "内存写入成功。通用写入不代表固件动作已完成。", "写入成功"
                    else:
                        raise OzoneError("未知操作。")
                self.events.put(("done", (label, title, message, values, operation)))
            except Exception as exc:
                if self.client:
                    self.client.armed = False
                self.events.put(("error", (label, str(exc))))

    def render_values(self):
        self.tree.delete(*self.tree.get_children())
        for key, value in self.values.items():
            binding = self.config["variables"].get(key)
            if binding:
                self.tree.insert("", "end", values=(binding["expression"], str(value)))
        for axis, (position, state, unit) in self.metrics.items():
            p = self.values.get(axis + "_position")
            position.configure(text=f"{p:.3f} {unit}" if isinstance(p, (int, float)) else "— " + unit)
            b, z, r = (self.values.get(axis + suffix) for suffix in ("_busy", "_zeroed", "_result"))
            activity = "运动中" if b is True else "非忙" if b is False else "未读取"
            state.configure(text=f"{activity}  /  设零 {z if z is not None else '—'}  /  result {r if r is not None else '—'}")
        self.snapshot_time.configure(text="最近读取 " + datetime.now().strftime("%H:%M:%S") + " · 非连续采样")

    def pump(self):
        if self.closed:
            return
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append(self.protocol, data)
                continue
            self.working = False
            if kind == "error":
                label, message = data
                self.action_title = label
                self.arm.set(False)
                self.set_result("操作失败", message, BAD)
                self.record(label + " · 失败", message)
            else:
                label, title, message, values, operation = data
                self.action_title = label
                if operation in ("connect", "disconnect"):
                    self.values.clear()
                    self.arm.set(False)
                self.values.update(values)
                self.render_values()
                self.set_result(title, message, GOOD if operation != "disconnect" else MUTED)
                self.record(label + " · " + title, message)
            self.button_states()
        self.root.after(50, self.pump)

    def close(self):
        if self.client:
            self.client.close()
        self.closed = True
        self.root.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    try:
        Panel(root)
    except Exception as exc:
        messagebox.showerror("启动失败", f"配置或资源加载失败：\n{exc}", parent=root)
        root.destroy()
        raise
    print("Ozone control panel ready", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
