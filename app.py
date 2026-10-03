"""Ozone Control Panel: native desktop UI, packaged as OzonePanel.exe."""
import json
import math
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
BG, CARD, EDGE = "#F5F5F4", "#FFFFFF", "#E5E5E3"
TEXT, MUTED, ACCENT = "#202322", "#686D6B", "#345F50"
GOOD, BAD, WARN = "#346538", "#9F2F2D", "#856323"
FONT, MONO = "Microsoft YaHei UI", "Consolas"


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
        root.geometry("1240x860")
        root.minsize(1080, 790)
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
                           font=kwargs.pop("font", (FONT, size, "bold" if bold else "normal")), **kwargs)

    def button(self, parent, text, command, write=False, primary=False, danger=False, **kwargs):
        color = "#FDEBEC" if danger else TEXT if primary else "#F5F5F4"
        foreground = BAD if danger else "#FFFFFF" if primary else TEXT
        button = ctk.CTkButton(
            parent, text=text, command=command, height=38,
            fg_color=color, hover_color="#F5DADB" if danger else "#383E3B" if primary else "#E9EBE8",
            text_color=foreground, text_color_disabled="#929691", corner_radius=5,
            border_width=0 if primary or danger else 1, border_color=EDGE,
            font=(FONT, 13), **kwargs)
        # Native keyboard focus, without animated motion around machine controls.
        button._canvas.configure(takefocus=1)
        button._canvas.bind("<FocusIn>", lambda event: button.configure(border_width=2, border_color=ACCENT))
        button._canvas.bind("<FocusOut>", lambda event: button.configure(border_width=0 if primary or danger else 1, border_color=EDGE))
        button._canvas.bind("<Return>", lambda event: button.invoke())
        if write:
            self.write_buttons.append(button)
        return button

    def divider(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=EDGE, corner_radius=0).pack(fill="x")

    def build(self):
        shell = ctk.CTkFrame(self.root, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=28, pady=(22, 14))
        masthead = ctk.CTkFrame(shell, fg_color="transparent")
        masthead.pack(fill="x")
        self.label(masthead, "Ozone", 23, TEXT, True).pack(side="left")
        self.label(masthead, "机构调试", 15, MUTED).pack(side="left", padx=16)
        self.label(masthead, "STM32F407 · 双轴 / 气动夹爪", 12, MUTED).pack(side="right")
        ctk.CTkFrame(shell, height=1, fg_color=EDGE, corner_radius=0).pack(fill="x", pady=(16, 0))
        toolbar = ctk.CTkFrame(shell, fg_color="transparent")
        toolbar.pack(fill="x", pady=16)
        self.connection_badge = self.label(toolbar, "未连接", 13, MUTED, True)
        self.connection_badge.pack(side="left", padx=(0, 14))
        self.endpoint = self.label(toolbar, "", 12, MUTED, font=(MONO, 12))
        self.endpoint.pack(side="left")
        self.button(toolbar, "连接诊断", self.connect, primary=True, width=100).pack(side="left", padx=(22, 8))
        self.button(toolbar, "断开", lambda: self.submit("disconnect", None, "断开连接"), width=70).pack(side="left")
        self.button(toolbar, "刷新状态", lambda: self.submit("snapshot", None, "读取设备状态"), width=100).pack(side="left", padx=8)
        ctk.CTkSwitch(toolbar, text="确认目标运行，允许写入", variable=self.arm,
                      command=self.arm_changed, progress_color=ACCENT, button_color="#FFFFFF",
                      fg_color="#DADDD8", font=(FONT, 12)).pack(side="right")
        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.label(footer, "位置为手动刷新快照 · 命令接受不代表到位", 11, MUTED).pack(side="left")
        self.label(footer, "停止保持不是硬件急停 · 断开不会停止机构", 11, MUTED).pack(side="right")
        body = ctk.CTkFrame(shell, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3, uniform="workspace")
        body.grid_columnconfigure(1, weight=2, uniform="workspace")
        body.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(
            body, fg_color=CARD, corner_radius=8, border_width=1, border_color=EDGE,
            segmented_button_fg_color=BG, segmented_button_selected_color=TEXT,
            segmented_button_selected_hover_color="#383E3B",
            segmented_button_unselected_color=BG, segmented_button_unselected_hover_color="#E9EBE8",
            text_color=TEXT, anchor="nw")
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.tabs.add("机构控制")
        self.tabs.add("变量与配置")
        self.tabs.add("变量快照")
        # Selected tab must have white text on the charcoal selection.
        self.tabs._segmented_button.configure(text_color=("#FFFFFF", "#FFFFFF"))
        for name, button in self.tabs._segmented_button._buttons_dict.items():
            if name != self.tabs.get():
                button.configure(text_color=TEXT)
        self.tabs.configure(command=self.update_tab_colors)
        controls = self.tabs.tab("机构控制")
        controls.grid_columnconfigure(0, weight=1)
        self.metrics = {}
        for index, (axis, title, subtitle, unit) in enumerate((
            ("lift", "升降", "相对位移", "mm"), ("flip", "翻转", "绝对角度", "deg"))):
            section = ctk.CTkFrame(controls, fg_color="transparent")
            section.pack(fill="x", padx=18, pady=(8 if index == 0 else 12, 10))
            heading = ctk.CTkFrame(section, fg_color="transparent")
            heading.pack(fill="x")
            titles = ctk.CTkFrame(heading, fg_color="transparent")
            titles.pack(side="left", anchor="n")
            self.label(titles, title, 23, bold=True).pack(anchor="w")
            self.label(titles, subtitle + " · " + unit, 12, MUTED).pack(anchor="w", pady=(2, 0))
            position = self.label(heading, "未读取", 22, MUTED, font=(MONO, 22))
            position.pack(side="right")
            state = self.label(section, "尚无设备反馈快照", 12, MUTED)
            state.pack(anchor="w", pady=(2, 6))
            self.metrics[axis] = (position, state, unit)
            move = ctk.CTkFrame(section, fg_color="transparent")
            move.pack(fill="x", pady=(0, 8))
            if axis == "lift":
                field = ctk.CTkFrame(move, fg_color="transparent")
                field.pack(side="left", padx=(0, 10))
                self.label(field, "移动距离 / mm", 11, MUTED).pack(anchor="w", pady=(0, 4))
                ctk.CTkEntry(field, textvariable=self.distance, width=135, height=40,
                            font=(MONO, 17), fg_color=CARD, border_width=1, border_color="#C9CEC8",
                            text_color=TEXT, corner_radius=5).pack()
                self.button(move, "上升", lambda: self.move_lift(1), write=True, primary=True, width=108).pack(side="left", anchor="s", padx=4)
                self.button(move, "下降", lambda: self.move_lift(-1), write=True, width=108).pack(side="left", anchor="s", padx=4)
            else:
                self.button(move, "翻转到 180°", lambda: self.submit("action", ("flip", 4, 180), "翻转 · 前往180°"),
                            write=True, primary=True, width=170).pack(side="left", padx=(0, 10))
                self.button(move, "返回 0°", lambda: self.submit("action", ("flip", 4, 0), "翻转 · 返回0°"),
                            write=True, width=125).pack(side="left")
            row = ctk.CTkFrame(section, fg_color="transparent")
            row.pack(fill="x")
            for text, code in (("设零", 1), ("使能", 2), ("失能", 3), ("停止保持", 5)):
                self.button(row, text, lambda a=axis, c=code, t=text: self.submit(
                    "action", (a, c, None), ("升降" if a == "lift" else "翻转") + " · " + t),
                    write=True, danger=code == 5, width=78 if code != 5 else 108).pack(side="left", padx=(0, 8))
            self.divider(controls)
        grip = ctk.CTkFrame(controls, fg_color="transparent")
        grip.pack(fill="x", padx=18, pady=(10, 6))
        heading = ctk.CTkFrame(grip, fg_color="transparent")
        heading.pack(fill="x", pady=(0, 6))
        self.label(heading, "夹爪", 19, bold=True).pack(side="left")
        self.label(heading, "仅发送输出命令", 11, MUTED).pack(side="right")
        groups = ctk.CTkFrame(grip, fg_color="transparent")
        groups.pack(fill="x")
        groups.grid_columnconfigure((0, 1), weight=1)
        for index, (label, key) in enumerate((("固定夹爪", "fixed_gripper"), ("移动夹爪", "moving_gripper"))):
            group = ctk.CTkFrame(groups, fg_color="transparent")
            group.grid(row=0, column=index, sticky="ew", padx=(0, 12 if index == 0 else 0))
            self.label(group, label, 12, MUTED, anchor="w").pack(anchor="w", pady=(0, 4))
            row = ctk.CTkFrame(group, fg_color="transparent")
            row.pack(fill="x")
            row.grid_columnconfigure((0, 1), weight=1)
            for column, (closed, text) in enumerate(((1, "闭合"), (0, "打开"))):
                self.button(row, text, lambda k=key, c=closed, t=label + " · " + text: self.submit("gripper", (k, c), t),
                            write=True, width=75).grid(row=0, column=column, sticky="ew", padx=(0, 6 if column == 0 else 0))
        self.build_config(self.tabs.tab("变量与配置"))
        self.build_snapshot(self.tabs.tab("变量快照"))

        results = ctk.CTkFrame(body, fg_color=CARD, corner_radius=8, border_width=1, border_color=EDGE)
        results.grid(row=0, column=1, sticky="nsew")
        result_top = ctk.CTkFrame(results, fg_color="transparent")
        result_top.pack(fill="x", padx=22, pady=(24, 8))
        self.label(result_top, "执行结果", 16, bold=True).pack(side="left")
        self.result_time = self.label(result_top, "尚未发送", 11, MUTED, font=(MONO, 11))
        self.result_time.pack(side="right")
        self.result_heading = self.label(results, "等待连接", 29, TEXT, True, anchor="w")
        self.result_heading.pack(fill="x", padx=22, pady=(8, 4))
        self.result_action = self.label(results, "准备就绪", 12, MUTED, anchor="w")
        self.result_action.pack(fill="x", padx=22, pady=(0, 12))
        self.result_body = ctk.CTkTextbox(results, height=175, font=(FONT, 15), wrap="word",
                                         fg_color=CARD, text_color=TEXT, corner_radius=0,
                                         border_width=0, scrollbar_button_color="#C9CEC8", spacing3=9)
        self.result_body.pack(fill="x", padx=18, pady=(0, 18))
        self.set_result("等待连接", "1. 在Ozone中加载匹配的ELF并连接目标。\n2. 点击连接诊断，确认符号和RAM可读取。\n3. 确认目标运行，授权后开始操作。", MUTED)
        self.divider(results)
        journal = ctk.CTkTabview(results, fg_color=CARD, corner_radius=0,
            segmented_button_fg_color=CARD, segmented_button_selected_color="#E9EDE8",
            segmented_button_selected_hover_color="#DDE4DB", segmented_button_unselected_color=CARD,
            segmented_button_unselected_hover_color=BG, text_color=TEXT, anchor="nw")
        journal.pack(fill="both", expand=True, padx=15, pady=(12, 14))
        journal.add("操作记录")
        journal.add("协议详情")
        self.history = ctk.CTkTextbox(journal.tab("操作记录"), font=(FONT, 13), wrap="word",
                                      fg_color=CARD, text_color=TEXT, corner_radius=0, spacing3=8)
        self.history.pack(fill="both", expand=True)
        self.history.insert("1.0", "暂无操作记录\n\n操作时间、设备命令和返回信息会显示在这里。\n底层Socket命令请切换到协议详情。")
        self.history.configure(state="disabled")
        self.history_empty = True
        self.protocol = ctk.CTkTextbox(journal.tab("协议详情"), font=(MONO, 12), wrap="word",
                                       fg_color=CARD, text_color=TEXT, corner_radius=0)
        self.protocol.pack(fill="both", expand=True)
        self.protocol.configure(state="disabled")
        self.update_bindings()
        self.button_states()

    def update_tab_colors(self):
        for name, button in self.tabs._segmented_button._buttons_dict.items():
            button.configure(text_color="#FFFFFF" if name == self.tabs.get() else TEXT)

    def build_config(self, parent):
        self.label(parent, "变量与连接配置", 20, bold=True).pack(anchor="w", padx=16, pady=(16, 4))
        self.label(parent, "保存后重新连接生效。通用写入不检查机构动作顺序。", 12, MUTED).pack(anchor="w", padx=16, pady=(0, 18))
        fields = ctk.CTkFrame(parent, fg_color="transparent")
        fields.pack(fill="x", padx=16)
        self.label(fields, "变量绑定", 11, MUTED).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.label(fields, "写入值", 11, MUTED).grid(row=0, column=2, sticky="w", pady=(0, 4))
        self.var_box = ctk.CTkComboBox(fields, variable=self.variable, state="readonly", width=180,
            fg_color=CARD, border_color=EDGE, button_color=BG, button_hover_color="#E9EBE8",
            text_color=TEXT, dropdown_fg_color=CARD, dropdown_text_color=TEXT, dropdown_hover_color=BG)
        self.var_box.grid(row=1, column=0, padx=(0, 6))
        self.button(fields, "读取", lambda: self.submit("read", self.variable.get(), "读取 · " + self.variable.get()), width=65).grid(row=1, column=1, padx=(0, 14))
        ctk.CTkEntry(fields, textvariable=self.custom_value, width=90, fg_color=CARD,
                     border_color=EDGE, text_color=TEXT, font=(MONO, 14)).grid(row=1, column=2, padx=(0, 6))
        self.button(fields, "写入", self.custom_write, write=True, width=65).grid(row=1, column=3)
        self.label(parent, "JSON配置", 12, MUTED).pack(anchor="w", padx=16, pady=(20, 6))
        self.config_editor = ctk.CTkTextbox(parent, font=(MONO, 12), wrap="none",
            fg_color=BG, text_color=TEXT, border_width=1, border_color=EDGE, corner_radius=5)
        self.config_editor.pack(fill="both", expand=True, padx=16)
        self.config_editor.insert("1.0", json.dumps(self.config, ensure_ascii=False, indent=2))
        self.button(parent, "保存配置并断开", self.save_config, primary=True, width=170).pack(anchor="w", padx=16, pady=16)

    def build_snapshot(self, parent):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, foreground=TEXT, fieldbackground=CARD,
                         rowheight=34, font=(MONO, 12), borderwidth=0)
        style.configure("Treeview.Heading", background=BG, foreground=MUTED, font=(FONT, 12))
        style.map("Treeview", background=[("selected", "#E9EDE8")], foreground=[("selected", TEXT)])
        self.label(parent, "变量快照", 20, bold=True).pack(anchor="w", padx=16, pady=(16, 4))
        self.label(parent, "点击刷新状态更新全部变量，不是连续采样。", 12, MUTED).pack(anchor="w", padx=16, pady=(0, 16))
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=16)
        self.tree = ttk.Treeview(frame, columns=("expression", "value"), show="headings")
        self.tree.heading("expression", text="表达式")
        self.tree.heading("value", text="读取值")
        self.tree.column("expression", width=275)
        self.tree.column("value", width=130)
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.snapshot_time = self.label(parent, "尚未读取", 11, MUTED)
        self.snapshot_time.pack(anchor="w", padx=16, pady=14)

    def update_bindings(self):
        keys = list(self.config["variables"])
        self.var_box.configure(values=keys)
        self.variable.set(keys[0] if keys else "")
        self.endpoint.configure(text=f"{self.config['host']}:{self.config['port']}")

    def set_result(self, title, detail, color=TEXT):
        self.result_heading.configure(text=title, text_color=color)
        self.result_action.configure(text=self.action_title)
        self.result_time.configure(text=datetime.now().strftime("%H:%M:%S"))
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
        if self.history_empty:
            self.history.configure(state="normal")
            self.history.delete("1.0", "end")
            self.history.configure(state="disabled")
            self.history_empty = False
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
            measured = isinstance(p, (int, float))
            position.configure(text=f"{p:.3f} {unit}" if measured else "未读取",
                               text_color=TEXT if measured else MUTED,
                               font=(MONO, 32 if measured else 22))
            b, z, r = (self.values.get(axis + suffix) for suffix in ("_busy", "_zeroed", "_result"))
            activity = "运动中" if b is True else "非忙" if b is False else "未读取"
            zero = "已设零" if z is True else "未设零" if z is False else "设零状态未读取"
            state.configure(text=f"{activity} · {zero} · result {r if r is not None else '未读取'}")
        self.snapshot_time.configure(text=("最近读取 " + datetime.now().strftime("%H:%M:%S") + " · 非连续采样")
                                     if self.values else "尚未读取")

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
                if values or operation in ("connect", "disconnect"):
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
    ctk.set_appearance_mode("light")
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
