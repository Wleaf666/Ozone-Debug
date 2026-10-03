"""Run: python app.py. Python standard library only; no firmware changes."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ozone_client import Ozone, OzoneError, validate_config

BASE = Path(__file__).resolve().parent


class Panel:
    def __init__(self, root):
        self.root = root
        self.root.title("Ozone · 机构调试台")
        self.root.geometry("1080x790")
        self.root.minsize(880, 650)
        self.requests = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.working = False
        self.client = None
        self.values = {}
        self.closed = False
        self.config = json.loads((BASE / (".local.json" if (BASE / ".local.json").exists() else "config.json")).read_text(encoding="utf-8"))
        self.status = tk.StringVar(value="未连接 · 控制写入关闭")
        self.arm = tk.BooleanVar(value=False)
        self.distance = tk.StringVar(value="20")
        self.custom_value = tk.StringVar(value="0")
        self.variable = tk.StringVar()
        self.build()
        threading.Thread(target=self.worker, daemon=True).start()
        self.root.after(50, self.pump)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 8))
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="机构调试台 / OZONE", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="只通过本机Socket读写RAM · 不自动暂停、复位或烧录 · 软件停止不是硬件急停").pack(anchor="w", pady=(4, 12))
        connection = ttk.Frame(shell)
        connection.pack(fill="x")
        ttk.Button(connection, text="连接 / 只读诊断", command=self.connect).pack(side="left")
        ttk.Button(connection, text="断开", command=lambda: self.submit("disconnect", None)).pack(side="left", padx=8)
        ttk.Button(connection, text="刷新状态", command=lambda: self.submit("snapshot", None)).pack(side="left")
        ttk.Checkbutton(connection, text="确认目标运行中，允许写入", variable=self.arm, command=self.arm_changed).pack(side="right")
        ttk.Label(shell, textvariable=self.status, wraplength=980).pack(anchor="w", pady=10)
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill="both", expand=True)
        controls = ttk.Frame(self.notebook, padding=14)
        settings = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(controls, text="当前机构")
        self.notebook.add(settings, text="变量绑定 / 配置")
        self.write_buttons = []
        top = ttk.Frame(controls)
        top.pack(fill="x")
        for axis, label in (("lift", "升降 / 相对位移 mm"), ("flip", "翻转 / 绝对角度 deg")):
            box = ttk.LabelFrame(top, text=label, padding=12)
            box.pack(side="left", fill="both", expand=True, padx=(0, 8))
            row = ttk.Frame(box)
            row.pack(fill="x")
            for text, code in (("设零", 1), ("使能保持", 2), ("失能", 3), ("停止保持", 5)):
                self.write_button(row, text, lambda a=axis, c=code: self.submit("action", (a, c, None)))
            moves = ttk.Frame(box)
            moves.pack(fill="x", pady=(12, 0))
            if axis == "lift":
                ttk.Entry(moves, textvariable=self.distance, width=10).pack(side="left", padx=(0, 8))
                self.write_button(moves, "上升", lambda: self.move_lift(1))
                self.write_button(moves, "下降", lambda: self.move_lift(-1))
            else:
                self.write_button(moves, "到 180°", lambda: self.submit("action", ("flip", 4, 180)))
                self.write_button(moves, "回 0°", lambda: self.submit("action", ("flip", 4, 0)))
        grip = ttk.LabelFrame(controls, text="夹爪 / 输出命令，不是到位反馈", padding=12)
        grip.pack(fill="x", pady=12)
        for label, key in (("固定爪", "fixed_gripper"), ("移动爪", "moving_gripper")):
            for state, text in ((1, "闭合"), (0, "打开")):
                self.write_button(grip, label + text, lambda k=key, s=state: self.submit("gripper", (k, s)))
        self.tree = ttk.Treeview(controls, columns=("expression", "value"), show="headings", height=9)
        self.tree.heading("expression", text="变量表达式")
        self.tree.heading("value", text="最近读取值（按刷新更新，非实时示波器）")
        self.tree.column("expression", width=360)
        self.tree.column("value", width=430)
        self.tree.pack(fill="both", expand=True)
        custom = ttk.Frame(settings)
        custom.pack(fill="x", pady=(0, 10))
        self.var_box = ttk.Combobox(custom, textvariable=self.variable, state="readonly", width=25)
        self.var_box.pack(side="left")
        ttk.Button(custom, text="读取选中变量", command=lambda: self.submit("read", self.variable.get())).pack(side="left", padx=6)
        ttk.Entry(custom, textvariable=self.custom_value, width=15).pack(side="left")
        self.write_button(custom, "写入选中变量", self.custom_write)
        ttk.Label(settings, text="编辑JSON：expression支持变量/成员/数字下标；type为u32、i32、float、bool、u8。\n保持固件ELF匹配；对象复位后地址会重新解析。不接受任意脚本或外设地址。", wraplength=940).pack(anchor="w")
        self.config_editor = tk.Text(settings, height=17, font=("Consolas", 10), undo=True)
        self.config_editor.pack(fill="both", expand=True, pady=8)
        self.config_editor.insert("1.0", json.dumps(self.config, ensure_ascii=False, indent=2))
        ttk.Button(settings, text="保存本地配置并断开（重新连接生效）", command=self.save_config).pack(anchor="w")
        self.log_text = tk.Text(shell, height=7, state="disabled", font=("Consolas", 9), background="#152333", foreground="#dce6ed")
        self.log_text.pack(fill="x", pady=(12, 0))
        self.update_bindings()
        self.button_states()

    def write_button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=(0, 5))
        self.write_buttons.append(button)

    def update_bindings(self):
        keys = list(self.config["variables"])
        self.var_box["values"] = keys
        self.variable.set(keys[0] if keys else "")

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 400:
            self.log_text.delete("1.0", "100.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def button_states(self):
        allowed = bool(self.client and self.client.ready and self.arm.get() and not self.working)
        for button in self.write_buttons:
            button.configure(state="normal" if allowed else "disabled")

    def submit(self, operation, data):
        if self.working:
            self.log("上一操作仍在执行，不排队重复运动命令。")
            return
        self.working = True
        self.button_states()
        self.requests.put_nowait((operation, data, self.arm.get()))

    def connect(self):
        self.arm.set(False)
        self.submit("connect", self.config)

    def arm_changed(self):
        if self.arm.get() and not (self.client and self.client.ready):
            self.arm.set(False)
            self.log("需要先通过只读诊断。")
        self.button_states()

    def move_lift(self, sign):
        try:
            distance = abs(float(self.distance.get())) * sign
            self.submit("action", ("lift", 4, distance))
        except ValueError:
            messagebox.showerror("输入错误", "请输入有效的毫米数。")

    def custom_write(self):
        key = self.variable.get()
        if messagebox.askyesno("写入确认", f"直接写入 {key} = {self.custom_value.get()}？\n通用写入不会执行设备动作的顺序检查。"):
            self.submit("write", (key, self.custom_value.get()))

    def save_config(self):
        if self.working:
            return
        try:
            config = validate_config(json.loads(self.config_editor.get("1.0", "end")))
            (BASE / ".local.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.config = config
            self.arm.set(False)
            self.update_bindings()
            self.submit("disconnect", None)
        except (ValueError, KeyError, TypeError, OzoneError) as exc:
            messagebox.showerror("配置错误", str(exc))

    def worker(self):
        while True:
            operation, data, armed = self.requests.get()
            try:
                if operation == "connect":
                    if self.client:
                        self.client.close()
                    self.client = Ozone(data, lambda message: self.events.put(("log", message)))
                    result = self.client.connect()
                elif operation == "disconnect":
                    if self.client:
                        self.client.close()
                    result = "已断开，未发送任何目标停止/复位指令。"
                else:
                    if not self.client or not self.client.ready:
                        raise OzoneError("先连接并通过只读诊断。")
                    self.client.armed = armed
                    if operation == "snapshot": result = self.client.snapshot()
                    elif operation == "action": result = self.client.action(*data)
                    elif operation == "gripper": result = self.client.gripper(*data)
                    elif operation == "read": result = {data: self.client.read(data)}
                    elif operation == "write":
                        self.client.write(*data)
                        result = "写入完成（无自动重试）。"
                    else: raise OzoneError("未知操作。")
                self.events.put(("done", result))
            except Exception as exc:
                if self.client:
                    self.client.armed = False
                self.events.put(("error", str(exc)))

    def pump(self):
        if self.closed:
            return
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(data)
                continue
            self.working = False
            if kind == "error":
                self.arm.set(False)
                self.status.set("操作失败 · " + data)
                self.log(data)
            else:
                if isinstance(data, dict):
                    self.values.update(data)
                    self.tree.delete(*self.tree.get_children())
                    for key, value in self.values.items():
                        binding = self.config["variables"].get(key)
                        if binding:
                            self.tree.insert("", "end", values=(binding["expression"], str(value)))
                    self.status.set("只读诊断/读取通过 · 版本 " + self.client.version + " · 写入需手动授权")
                else:
                    self.status.set(str(data))
            self.button_states()
        self.root.after(50, self.pump)

    def close(self):
        # Disconnecting the GUI NEVER implies motor stop; operators must stop first.
        if self.client:
            self.client.close()
        self.closed = True
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Panel(root)
    print("Ozone control panel ready", flush=True)
    root.mainloop()
