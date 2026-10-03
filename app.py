"""Ozone native desktop panel. Qt backing-store presentation; no target reset/halt."""
import json
import math
from pathlib import Path
import queue
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QCheckBox, QLineEdit, QTabWidget, QPlainTextEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from ozone_client import Ozone, OzoneError, validate_config

BUNDLE = Path(__file__).resolve().parent
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE
COLORS = {"normal": "#202322", "success": "#346538", "error": "#9F2F2D", "waiting": "#856323"}


def load_config():
    path = BASE / ".local.json"
    if not path.exists(): path = BASE / "config.json"
    if not path.exists(): path = BUNDLE / "config.json"
    return validate_config(json.loads(path.read_text(encoding="utf-8")))


class Panel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ozone Control Panel · 机构调试台")
        self.resize(1240, 860)
        self.setMinimumSize(1080, 760)
        self.requests = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.client = None
        self.working = False
        self.values = {}
        self.config = load_config()
        self.write_buttons = []
        self.action_title = "准备就绪"
        self.history_empty = True
        self.build()
        threading.Thread(target=self.worker, daemon=True).start()
        self.pump_timer = QTimer(self)
        self.pump_timer.timeout.connect(self.pump)
        self.pump_timer.start(50)

    def label(self, text, role=""):
        widget = QLabel(text)
        if role: widget.setObjectName(role)
        return widget

    def button(self, text, callback, write=False, primary=False, danger=False):
        widget = QPushButton(text)
        widget.clicked.connect(callback)
        widget.setMinimumHeight(38)
        widget.setProperty("kind", "primary" if primary else "danger" if danger else "secondary")
        if write: self.write_buttons.append(widget)
        return widget

    @staticmethod
    def row(*widgets):
        layout = QHBoxLayout()
        layout.setSpacing(8)
        for widget in widgets: layout.addWidget(widget)
        return layout

    @staticmethod
    def text_box(readonly=True):
        widget = QPlainTextEdit()
        widget.setReadOnly(readonly)
        return widget

    def build(self):
        self.setStyleSheet("""
            QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #202322; }
            QMainWindow, #shell { background: #F5F5F4; }
            QLabel#brand { font-size: 27px; font-weight: 700; }
            QLabel#title { font-size: 23px; font-weight: 600; }
            QLabel#muted { color: #686D6B; font-size: 12px; }
            QLabel#position { font-family: Consolas; font-size: 30px; }
            QLabel#result { font-size: 29px; font-weight: 600; }
            QPushButton { border: 1px solid #E0E3DE; border-radius: 5px; padding: 8px 16px; background: #F5F5F4; }
            QPushButton:hover { background: #E9EDE8; }
            QPushButton:pressed { background: #DDE4DB; }
            QPushButton:focus { border: 2px solid #345F50; }
            QPushButton[kind='primary'] { background: #202322; color: white; border-color: #202322; }
            QPushButton[kind='primary']:hover { background: #383E3B; }
            QPushButton[kind='danger'] { background: #FDEBEC; color: #9F2F2D; border-color: #FDEBEC; }
            QPushButton:disabled { color: #8A908B; background: #F1F2EF; border-color: #E5E5E3; }
            QLineEdit, QComboBox { background: white; border: 1px solid #D1D6CE; border-radius: 4px; padding: 8px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #345F50; }
            QTabWidget::pane { background: white; border: 1px solid #E5E5E3; border-radius: 6px; }
            QTabBar::tab { padding: 10px 16px; background: #F5F5F4; color: #686D6B; }
            QTabBar::tab:selected { background: white; color: #202322; border-bottom: 2px solid #345F50; }
            QPlainTextEdit { background: white; border: none; padding: 6px; font-size: 14px; }
            QTableWidget { background: white; border: none; gridline-color: #E5E5E3; font-family: Consolas; }
            QHeaderView::section { background: #F5F5F4; border: none; padding: 10px; color: #686D6B; }
            #results { background: white; border: 1px solid #E5E5E3; border-radius: 6px; }
            QCheckBox { spacing: 8px; }
        """)
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(24, 20, 24, 14)
        layout.setSpacing(16)
        heading = self.row(self.label("Ozone", "brand"), self.label("机构调试", "muted"))
        heading.addStretch()
        heading.addWidget(self.label("STM32F407 · 双轴 / 气动夹爪", "muted"))
        layout.addLayout(heading)
        self.connection_badge = self.label("未连接 · 写入关闭")
        self.endpoint = self.label("", "muted")
        toolbar = self.row(self.connection_badge, self.endpoint,
                           self.button("连接诊断", self.connect, primary=True),
                           self.button("断开", lambda: self.submit("disconnect", None, "断开连接")),
                           self.button("刷新状态", lambda: self.submit("snapshot", None, "读取设备状态")))
        toolbar.addStretch()
        self.arm = QCheckBox("确认目标运行，允许写入")
        self.arm.toggled.connect(self.arm_changed)
        toolbar.addWidget(self.arm)
        layout.addLayout(toolbar)
        workspace = QHBoxLayout()
        workspace.setSpacing(18)
        self.tabs = QTabWidget()
        workspace.addWidget(self.tabs, 3)
        controls = QWidget()
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(22, 18, 22, 18)
        control_layout.setSpacing(14)
        self.tabs.addTab(controls, "机构控制")
        self.metrics = {}
        self.distance = QLineEdit("20")
        self.distance.setMaximumWidth(140)
        for axis, title, subtitle, unit in (("lift", "升降", "相对位移", "mm"), ("flip", "翻转", "绝对角度", "deg")):
            h = self.row(self.label(title, "title"))
            h.addStretch()
            position = self.label("未读取", "position")
            h.addWidget(position)
            control_layout.addLayout(h)
            control_layout.addWidget(self.label(subtitle + " · " + unit, "muted"))
            status = self.label("尚无设备反馈快照", "muted")
            control_layout.addWidget(status)
            self.metrics[axis] = (position, status, unit)
            if axis == "lift":
                control_layout.addWidget(self.label("移动距离 / mm", "muted"))
                control_layout.addLayout(self.row(self.distance,
                    self.button("上升", lambda: self.move_lift(1), write=True, primary=True),
                    self.button("下降", lambda: self.move_lift(-1), write=True)))
            else:
                control_layout.addLayout(self.row(
                    self.button("翻转到180°", lambda: self.submit("action", ("flip", 4, 180), "翻转 · 前往180°"), write=True, primary=True),
                    self.button("返回0°", lambda: self.submit("action", ("flip", 4, 0), "翻转 · 返回0°"), write=True)))
            commands = QHBoxLayout()
            for name, code in (("设零", 1), ("使能", 2), ("失能", 3), ("停止保持", 5)):
                commands.addWidget(self.button(name, lambda checked=False, a=axis, c=code, n=name: self.submit(
                    "action", (a, c, None), ("升降" if a == "lift" else "翻转") + " · " + n), write=True, danger=code == 5))
            control_layout.addLayout(commands)
        control_layout.addWidget(self.label("夹爪", "title"))
        grid = QGridLayout()
        for column, (name, key) in enumerate((("固定夹爪", "fixed_gripper"), ("移动夹爪", "moving_gripper"))):
            grid.addWidget(self.label(name, "muted"), 0, column * 2, 1, 2)
            for index, (closed, word) in enumerate(((1, "闭合"), (0, "打开"))):
                grid.addWidget(self.button(word, lambda checked=False, k=key, c=closed, n=name + " · " + word:
                    self.submit("gripper", (k, c), n), write=True), 1, column * 2 + index)
        control_layout.addLayout(grid)
        control_layout.addStretch()
        self.build_config()
        self.build_snapshot()
        results = QWidget()
        results.setObjectName("results")
        result_layout = QVBoxLayout(results)
        result_layout.setContentsMargins(18, 20, 18, 14)
        self.result_time = self.label("尚未发送", "muted")
        result_layout.addLayout(self.row(self.label("执行结果", "title"), self.result_time))
        self.result_heading = self.label("等待连接", "result")
        self.result_action = self.label("准备就绪", "muted")
        result_layout.addWidget(self.result_heading)
        result_layout.addWidget(self.result_action)
        self.result_body = self.text_box()
        self.result_body.setMinimumHeight(150)
        self.result_body.setMaximumHeight(205)
        result_layout.addWidget(self.result_body)
        journal = QTabWidget()
        self.history = self.text_box()
        self.history.setPlainText("暂无操作记录\n\n操作时间、设备命令和返回信息会显示在这里。")
        self.protocol = self.text_box()
        self.protocol.setStyleSheet("font-family: Consolas; font-size: 12px;")
        journal.addTab(self.history, "操作记录")
        journal.addTab(self.protocol, "协议详情")
        result_layout.addWidget(journal, 1)
        workspace.addWidget(results, 2)
        layout.addLayout(workspace, 1)
        footer = self.row(self.label("位置为手动刷新快照 · 接受不代表到位", "muted"))
        footer.addStretch()
        footer.addWidget(self.label("停止保持不是硬件急停 · 断开不会停止机构", "muted"))
        layout.addLayout(footer)
        self.update_bindings()
        self.set_result("等待连接", "先用Ozone连接目标并加载匹配的ELF，再点击连接诊断。\n诊断通过后确认目标运行，再允许写入。", "waiting")
        self.button_states()

    def build_config(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(self.label("变量与连接配置", "title"))
        layout.addWidget(self.label("保存后重新连接生效。通用写入不检查机构动作顺序。", "muted"))
        self.variable = QComboBox()
        self.custom_value = QLineEdit("0")
        self.custom_value.setMaximumWidth(110)
        layout.addLayout(self.row(self.variable,
            self.button("读取", lambda: self.submit("read", self.variable.currentText(), "读取 · " + self.variable.currentText())),
            self.custom_value, self.button("写入", self.custom_write, write=True)))
        self.config_editor = self.text_box(False)
        self.config_editor.setStyleSheet("font-family: Consolas; font-size: 12px; background: #F5F5F4;")
        self.config_editor.setPlainText(json.dumps(self.config, ensure_ascii=False, indent=2))
        layout.addWidget(self.config_editor, 1)
        layout.addWidget(self.button("保存配置并断开", self.save_config, primary=True))
        self.tabs.addTab(page, "变量与配置")

    def build_snapshot(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.label("变量快照", "title"))
        self.tree = QTableWidget(0, 2)
        self.tree.setHorizontalHeaderLabels(["表达式", "读取值"])
        self.tree.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tree, 1)
        self.snapshot_time = self.label("尚未读取", "muted")
        layout.addWidget(self.snapshot_time)
        self.tabs.addTab(page, "变量快照")

    def update_bindings(self):
        self.variable.clear()
        self.variable.addItems(list(self.config["variables"]))
        self.endpoint.setText(f"{self.config['host']}:{self.config['port']}")

    def set_result(self, title, detail, kind="normal"):
        self.result_heading.setText(title)
        self.result_heading.setStyleSheet(f"color: {COLORS[kind]}; font-size: 29px; font-weight: 600;")
        self.result_action.setText(self.action_title)
        self.result_time.setText(datetime.now().strftime("%H:%M:%S"))
        self.result_body.setPlainText(detail)

    def append(self, box, text):
        box.appendPlainText(text)
        box.document().setMaximumBlockCount(600)

    def record(self, title, detail):
        if self.history_empty:
            self.history.clear()
            self.history_empty = False
        self.append(self.history, datetime.now().strftime("%H:%M:%S") + "  " + title + "\n" + detail + "\n")

    def button_states(self):
        ready = bool(self.client and self.client.ready)
        allowed = ready and self.arm.isChecked() and not self.working
        for button in self.write_buttons: button.setEnabled(allowed)
        self.connection_badge.setText("写入已授权" if allowed else "操作执行中" if self.working else "只读已连接" if ready else "未连接 · 写入关闭")

    def submit(self, operation, data, label):
        if self.working: return
        self.working = True
        self.action_title = label
        self.set_result("执行中", "等待Ozone返回，写入不会自动重试。", "waiting")
        self.record(label, "已提交，等待返回")
        self.button_states()
        self.requests.put_nowait((operation, data, self.arm.isChecked(), label))

    def connect(self):
        if self.working: return
        self.arm.setChecked(False)
        self.submit("connect", self.config, "连接 / 只读诊断")

    def arm_changed(self, checked):
        if checked and not (self.client and self.client.ready):
            self.arm.setChecked(False)
            self.set_result("未允许写入", "请先连接并通过只读诊断。", "waiting")
        self.button_states()

    def move_lift(self, sign):
        try:
            value = abs(float(self.distance.text())) * sign
            if not math.isfinite(value): raise ValueError()
            self.submit("action", ("lift", 4, value), f"升降 · 相对位移 {value:g} mm")
        except ValueError:
            self.set_result("输入无效", "请输入有限的毫米数。", "error")

    def custom_write(self):
        key, value = self.variable.currentText(), self.custom_value.text()
        if QMessageBox.question(self, "写入确认", f"直接写入 {key} = {value}？\n不执行机构动作顺序检查。") == QMessageBox.StandardButton.Yes:
            self.submit("write", (key, value), "通用写入 · " + key)

    def save_config(self):
        if self.working: return
        try:
            config = validate_config(json.loads(self.config_editor.toPlainText()))
            (BASE / ".local.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.config = config
            self.values.clear()
            self.arm.setChecked(False)
            self.update_bindings()
            self.submit("disconnect", None, "保存配置")
        except (OSError, ValueError, KeyError, TypeError, OzoneError) as exc:
            self.set_result("配置保存失败", str(exc), "error")

    def worker(self):
        while True:
            operation, data, armed, label = self.requests.get()
            try:
                if operation == "connect":
                    if self.client: self.client.close()
                    self.client = Ozone(data, lambda message: self.events.put(("log", message)))
                    values = self.client.connect()
                    message = f"Ozone版本：{self.client.version}\nSocket、符号表达式和目标RAM读取通过。授权后才能写入。"
                    title = "诊断通过"
                elif operation == "disconnect":
                    if self.client: self.client.close()
                    values, title = {}, "已断开"
                    message = "连接已关闭，没有发送停止、复位或运行指令。"
                else:
                    if not self.client or not self.client.ready: raise OzoneError("先连接并通过只读诊断。")
                    self.client.armed = armed
                    values = {}
                    if operation == "snapshot":
                        values = self.client.snapshot()
                        message, title = "已读取变量快照。", "读取成功"
                    elif operation == "read":
                        values = {data: self.client.read(data)}
                        message, title = f"{data} = {values[data]}", "读取成功"
                    elif operation == "action":
                        message, title = self.client.action(*data), "命令已接受"
                    elif operation == "gripper":
                        message, title = self.client.gripper(*data), "输出请求已消费"
                    elif operation == "write":
                        self.client.write(*data)
                        message, title = "内存写入成功，不代表固件动作完成。", "写入成功"
                    else: raise OzoneError("未知操作。")
                self.events.put(("done", (label, title, message, values, operation)))
            except Exception as exc:
                if self.client: self.client.armed = False
                self.events.put(("error", (label, str(exc))))

    def render_values(self):
        self.tree.setRowCount(len(self.values))
        for row, (key, value) in enumerate(self.values.items()):
            binding = self.config["variables"].get(key)
            if binding:
                self.tree.setItem(row, 0, QTableWidgetItem(binding["expression"]))
                self.tree.setItem(row, 1, QTableWidgetItem(str(value)))
        for axis, (position, status, unit) in self.metrics.items():
            p = self.values.get(axis + "_position")
            position.setText(f"{p:.3f} {unit}" if isinstance(p, (float, int)) else "未读取")
            b, z, r = (self.values.get(axis + suffix) for suffix in ("_busy", "_zeroed", "_result"))
            activity = "运动中" if b is True else "非忙" if b is False else "未读取"
            status.setText(f"{activity} · 设零 {z if z is not None else '未读取'} · result {r if r is not None else '未读取'}")
        self.snapshot_time.setText("最近读取 " + datetime.now().strftime("%H:%M:%S") if self.values else "尚未读取")

    def pump(self):
        # Bound work per tick so a large protocol log cannot starve painting/input.
        for _ in range(80):
            try: kind, data = self.events.get_nowait()
            except queue.Empty: break
            if kind == "log":
                self.append(self.protocol, data)
                continue
            self.working = False
            if kind == "error":
                label, message = data
                self.action_title = label
                self.arm.setChecked(False)
                self.set_result("操作失败", message, "error")
                self.record(label + " · 失败", message)
            else:
                label, title, message, values, operation = data
                self.action_title = label
                if operation in ("connect", "disconnect"):
                    self.values.clear()
                    self.arm.setChecked(False)
                self.values.update(values)
                if values or operation in ("connect", "disconnect"): self.render_values()
                self.set_result(title, message, "success" if operation != "disconnect" else "normal")
                self.record(label + " · " + title, message)
            self.button_states()

    def closeEvent(self, event):
        if self.client: self.client.close()
        self.pump_timer.stop()
        event.accept()


def main():
    application = QApplication(sys.argv)
    try:
        panel = Panel()
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", f"配置或资源加载失败：\n{exc}")
        raise
    panel.show()
    print("Ozone control panel ready", flush=True)
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
