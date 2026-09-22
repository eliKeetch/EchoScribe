"""EchoScribe desktop application for Windows."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "EchoScribe"
ORGANIZATION_NAME = "Thrice Agency"
PROJECT_DIRECTORY = Path(__file__).resolve().parent
TRANSCRIBE_SCRIPT = PROJECT_DIRECTORY / "transcribe.py"

PROGRESS_PATTERN = re.compile(
    r"^\[(?P<current>\d+)/(?P<total>\d+)] (?P<action>Transcribing:|SKIPPED)"
)
SUMMARY_PATTERNS = {
    "discovered": re.compile(r"^Videos discovered: (?P<value>\d+)$"),
    "succeeded": re.compile(r"^Successfully transcribed: (?P<value>\d+)$"),
    "skipped": re.compile(r"^Already completed/skipped: (?P<value>\d+)$"),
    "failed": re.compile(r"^Failed: (?P<value>\d+)$"),
}


STYLE_SHEET = """
QWidget {
    background: #0b1118;
    color: #e8eef5;
    font-family: "Segoe UI";
    font-size: 14px;
}
QMainWindow { background: #0b1118; }
QFrame#Header {
    background: #0e1620;
    border-bottom: 1px solid #233141;
}
QFrame#Panel {
    background: #111a24;
    border: 1px solid #233141;
    border-radius: 12px;
}
QLabel#BrandMark {
    background: #38d6bd;
    color: #07110f;
    border-radius: 18px;
    font-size: 17px;
    font-weight: 800;
}
QLabel#BrandName { font-size: 21px; font-weight: 700; }
QLabel#BrandSubtle, QLabel#HelpText, QLabel#MetaLabel {
    color: #8ea0b2;
}
QLabel#SectionTitle { font-size: 16px; font-weight: 700; }
QLabel#CurrentFile {
    background: #0c131b;
    border: 1px solid #233141;
    border-radius: 8px;
    color: #d8e3ed;
    padding: 10px 12px;
}
QLabel#StatusBadge {
    background: #172432;
    color: #a9bac9;
    border-radius: 11px;
    padding: 4px 10px;
    font-weight: 600;
}
QLabel#StatusBadge[running="true"] {
    background: #12342f;
    color: #66e3ce;
}
QLabel#StatusBadge[error="true"] {
    background: #3b2025;
    color: #ff9aa7;
}
QLineEdit, QComboBox {
    background: #0c131b;
    border: 1px solid #2a3a4b;
    border-radius: 8px;
    min-height: 38px;
    padding: 0 11px;
    selection-background-color: #238b7d;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #38d6bd; }
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox QAbstractItemView {
    background: #111a24;
    border: 1px solid #2a3a4b;
    selection-background-color: #1a695f;
    color: #e8eef5;
}
QPushButton {
    background: #192532;
    border: 1px solid #2c3d4f;
    border-radius: 8px;
    min-height: 38px;
    padding: 0 15px;
    font-weight: 600;
}
QPushButton:hover { background: #213142; border-color: #40566d; }
QPushButton:pressed { background: #14202b; }
QPushButton:disabled { color: #586878; background: #121b24; border-color: #202d3a; }
QPushButton#PrimaryButton {
    background: #38d6bd;
    border-color: #38d6bd;
    color: #07110f;
    font-weight: 750;
}
QPushButton#PrimaryButton:hover { background: #57e3cd; border-color: #57e3cd; }
QPushButton#StopButton { color: #ffabb5; border-color: #6d343d; }
QPushButton#StopButton:hover { background: #3a2025; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator {
    width: 18px; height: 18px;
    border: 1px solid #3a4d60;
    border-radius: 5px;
    background: #0c131b;
}
QCheckBox::indicator:checked {
    background: #38d6bd;
    border-color: #38d6bd;
}
QProgressBar {
    background: #0c131b;
    border: 0;
    border-radius: 5px;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
}
QProgressBar::chunk { background: #38d6bd; border-radius: 5px; }
QPlainTextEdit {
    background: #090f15;
    border: 1px solid #233141;
    border-radius: 9px;
    color: #b9c8d6;
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 13px;
    selection-background-color: #256e66;
}
QScrollBar:vertical {
    background: #0c131b; width: 11px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e4052; min-height: 28px; border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class Metric(QWidget):
    def __init__(self, label: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.value = QLabel("0")
        self.value.setStyleSheet("font-size: 23px; font-weight: 700;")
        caption = QLabel(label)
        caption.setObjectName("MetaLabel")
        layout.addWidget(self.value)
        layout.addWidget(caption)

    def set_value(self, value: int) -> None:
        self.value.setText(str(value))


class EchoScribeWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORGANIZATION_NAME, APP_NAME)
        self.process = QProcess(self)
        self.output_buffer = ""
        self.expecting_video_path = False
        self.stop_requested = False
        self._build_window()
        self._configure_process()
        self._restore_settings()
        self._update_output_hint()

    def _build_window(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(940, 640)
        self.resize(1140, 760)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 15, 24, 15)
        header_layout.setSpacing(12)

        mark = QLabel("ES")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(36, 36)
        header_layout.addWidget(mark)

        brand_stack = QVBoxLayout()
        brand_stack.setSpacing(0)
        brand_name = QLabel(APP_NAME)
        brand_name.setObjectName("BrandName")
        brand_subtle = QLabel("Local video transcription")
        brand_subtle.setObjectName("BrandSubtle")
        brand_stack.addWidget(brand_name)
        brand_stack.addWidget(brand_subtle)
        header_layout.addLayout(brand_stack)
        header_layout.addStretch()

        self.status_badge = QLabel("Ready")
        self.status_badge.setObjectName("StatusBadge")
        self.status_badge.setProperty("running", False)
        self.status_badge.setProperty("error", False)
        header_layout.addWidget(self.status_badge)
        root_layout.addWidget(header)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(24, 22, 24, 24)
        content_layout.setSpacing(18)
        root_layout.addWidget(content, 1)

        settings_panel = self._build_settings_panel()
        settings_panel.setFixedWidth(370)
        content_layout.addWidget(settings_panel)
        content_layout.addWidget(self._build_activity_panel(), 1)

    def _build_settings_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Transcription setup")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        source_label = QLabel("Video folder")
        source_label.setObjectName("MetaLabel")
        layout.addWidget(source_label)
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText(r"D:\path\to\videos")
        self.source_input.setAccessibleName("Video folder")
        self.source_input.textChanged.connect(self._update_output_hint)
        source_row.addWidget(self.source_input, 1)
        source_button = QPushButton("Browse")
        source_button.setAccessibleName("Choose video folder")
        source_button.clicked.connect(self._choose_source)
        source_row.addWidget(source_button)
        layout.addLayout(source_row)

        output_label = QLabel("Transcript folder")
        output_label.setObjectName("MetaLabel")
        layout.addWidget(output_label)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_input = QLineEdit()
        self.output_input.setAccessibleName("Transcript folder")
        self.output_input.setToolTip("Leave blank to use <video folder>\\_transcripts")
        output_row.addWidget(self.output_input, 1)
        output_button = QPushButton("Browse")
        output_button.setAccessibleName("Choose transcript folder")
        output_button.clicked.connect(self._choose_output)
        output_row.addWidget(output_button)
        layout.addLayout(output_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #233141; margin: 5px 0;")
        layout.addWidget(separator)

        options = QGridLayout()
        options.setHorizontalSpacing(12)
        options.setVerticalSpacing(7)
        model_label = QLabel("Model")
        model_label.setObjectName("MetaLabel")
        device_label = QLabel("Device")
        device_label.setObjectName("MetaLabel")
        options.addWidget(model_label, 0, 0)
        options.addWidget(device_label, 0, 1)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(["large-v3", "medium", "small", "base", "tiny"])
        self.model_combo.setAccessibleName("Whisper model")
        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic", "auto")
        self.device_combo.addItem("NVIDIA CUDA", "cuda")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.setAccessibleName("Processing device")
        options.addWidget(self.model_combo, 1, 0)
        options.addWidget(self.device_combo, 1, 1)
        layout.addLayout(options)

        self.force_checkbox = QCheckBox("Retranscribe completed videos")
        self.force_checkbox.setAccessibleName("Force retranscription")
        self.force_checkbox.setToolTip("Replaces transcripts that are already complete")
        layout.addWidget(self.force_checkbox)

        help_text = QLabel(
            "Automatic uses NVIDIA CUDA when available and falls back to CPU. "
            "Completed videos are skipped unless retranscription is enabled."
        )
        help_text.setObjectName("HelpText")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addItem(QSpacerItem(1, 1, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        self.start_button = QPushButton("Start transcription")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setAccessibleName("Start transcription")
        self.start_button.clicked.connect(self._start_transcription)
        layout.addWidget(self.start_button)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.stop_button = QPushButton("Stop safely")
        self.stop_button.setObjectName("StopButton")
        self.stop_button.setAccessibleName("Stop transcription")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_transcription)
        button_row.addWidget(self.stop_button)

        self.open_output_button = QPushButton("Open transcripts")
        self.open_output_button.setAccessibleName("Open transcript folder")
        self.open_output_button.clicked.connect(self._open_output_directory)
        button_row.addWidget(self.open_output_button)
        layout.addLayout(button_row)
        return panel

    def _build_activity_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading_row = QHBoxLayout()
        title = QLabel("Activity")
        title.setObjectName("SectionTitle")
        heading_row.addWidget(title)
        heading_row.addStretch()
        self.progress_label = QLabel("0 of 0 videos")
        self.progress_label.setObjectName("MetaLabel")
        heading_row.addWidget(self.progress_label)
        layout.addLayout(heading_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("Batch progress")
        layout.addWidget(self.progress_bar)

        current_label = QLabel("Current video")
        current_label.setObjectName("MetaLabel")
        layout.addWidget(current_label)
        self.current_file = QLabel("Waiting to start")
        self.current_file.setObjectName("CurrentFile")
        self.current_file.setWordWrap(True)
        self.current_file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.current_file)

        metric_row = QHBoxLayout()
        metric_row.setSpacing(28)
        self.discovered_metric = Metric("Discovered")
        self.succeeded_metric = Metric("Completed")
        self.skipped_metric = Metric("Skipped")
        self.failed_metric = Metric("Failed")
        for metric in (
            self.discovered_metric,
            self.succeeded_metric,
            self.skipped_metric,
            self.failed_metric,
        ):
            metric_row.addWidget(metric)
        metric_row.addStretch()
        layout.addLayout(metric_row)

        log_header = QHBoxLayout()
        log_label = QLabel("Live log")
        log_label.setObjectName("MetaLabel")
        log_header.addWidget(log_label)
        log_header.addStretch()
        clear_button = QPushButton("Clear")
        clear_button.setFixedHeight(30)
        clear_button.clicked.connect(self._clear_log)
        log_header.addWidget(clear_button)
        layout.addLayout(log_header)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(5000)
        self.log_output.setPlaceholderText("Transcription output will appear here.")
        self.log_output.setAccessibleName("Transcription log")
        layout.addWidget(self.log_output, 1)
        return panel

    def _configure_process(self) -> None:
        self.process.setWorkingDirectory(str(PROJECT_DIRECTORY))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.started.connect(self._process_started)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

    def _restore_settings(self) -> None:
        self.source_input.setText(self.settings.value("source", "", str))
        self.output_input.setText(self.settings.value("output", "", str))
        self.model_combo.setCurrentText(self.settings.value("model", "large-v3", str))
        device = self.settings.value("device", "auto", str)
        index = self.device_combo.findData(device)
        self.device_combo.setCurrentIndex(max(0, index))
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        self.settings.setValue("source", self.source_input.text().strip())
        self.settings.setValue("output", self.output_input.text().strip())
        self.settings.setValue("model", self.model_combo.currentText().strip())
        self.settings.setValue("device", self.device_combo.currentData())
        self.settings.setValue("geometry", self.saveGeometry())

    def _choose_source(self) -> None:
        initial = self.source_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose video folder", initial)
        if selected:
            self.source_input.setText(str(Path(selected)))

    def _choose_output(self) -> None:
        initial = self.output_input.text().strip() or str(self._effective_output_directory())
        selected = QFileDialog.getExistingDirectory(
            self, "Choose transcript folder", initial
        )
        if selected:
            self.output_input.setText(str(Path(selected)))

    def _update_output_hint(self) -> None:
        source_text = self.source_input.text().strip()
        hint = (
            str(Path(source_text) / "_transcripts")
            if source_text
            else r"Default: <video folder>\_transcripts"
        )
        self.output_input.setPlaceholderText(hint)

    def _effective_output_directory(self) -> Path:
        explicit_output = self.output_input.text().strip()
        if explicit_output:
            return Path(explicit_output).expanduser()
        source = self.source_input.text().strip()
        return Path(source).expanduser() / "_transcripts" if source else Path.home()

    def _validate_inputs(self) -> tuple[Path, str] | None:
        source_text = self.source_input.text().strip()
        model = self.model_combo.currentText().strip()
        if not source_text:
            QMessageBox.warning(self, APP_NAME, "Choose a folder containing your videos.")
            self.source_input.setFocus()
            return None
        source = Path(source_text).expanduser()
        if not source.is_dir():
            QMessageBox.warning(
                self, APP_NAME, f"The video folder does not exist:\n\n{source}"
            )
            self.source_input.setFocus()
            return None
        if not model:
            QMessageBox.warning(self, APP_NAME, "Enter or select a Whisper model.")
            self.model_combo.setFocus()
            return None
        if not TRANSCRIBE_SCRIPT.is_file():
            QMessageBox.critical(
                self, APP_NAME, f"The transcription engine is missing:\n\n{TRANSCRIBE_SCRIPT}"
            )
            return None
        return source.resolve(), model

    def _start_transcription(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        validated = self._validate_inputs()
        if validated is None:
            return
        source, model = validated
        self._save_settings()
        self._reset_run_state()

        arguments = [
            "-u",
            str(TRANSCRIBE_SCRIPT),
            str(source),
            "--model",
            model,
            "--device",
            str(self.device_combo.currentData()),
        ]
        output = self.output_input.text().strip()
        if output:
            arguments.extend(["--output", str(Path(output).expanduser())])
        if self.force_checkbox.isChecked():
            arguments.append("--force")

        self._append_log(f"Starting EchoScribe for:\n{source}\n\n")
        self._set_status("Preparing", running=True)
        self.current_file.setText("Scanning for videos…")
        self.progress_bar.setRange(0, 0)
        self._set_controls_running(True)
        self.process.start(sys.executable, arguments)

    def _stop_transcription(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.stop_requested = True
        self._set_status("Stopping", running=True)
        self.stop_button.setEnabled(False)
        self._append_log("\nStop requested. The current process is being closed…\n")
        self.process.terminate()
        QTimer.singleShot(5000, self._kill_if_still_running)

    def _kill_if_still_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _process_started(self) -> None:
        self._set_status("Running", running=True)

    def _read_process_output(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not data:
            return
        self._append_log(data)
        self.output_buffer += data
        lines = self.output_buffer.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.output_buffer = lines.pop()
        else:
            self.output_buffer = ""
        for line in lines:
            self._parse_output_line(line.strip())

    def _parse_output_line(self, line: str) -> None:
        if not line:
            return
        found = re.match(r"^Found (\d+) videos\.$", line)
        if found:
            total = int(found.group(1))
            self.discovered_metric.set_value(total)
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(0)
            self.progress_label.setText(f"0 of {total} videos")
            return

        progress = PROGRESS_PATTERN.match(line)
        if progress:
            current = int(progress.group("current"))
            total = int(progress.group("total"))
            action = progress.group("action")
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(current)
            self.progress_label.setText(f"{current} of {total} videos")
            self.expecting_video_path = True
            if action.startswith("SKIPPED"):
                self._set_status("Skipping completed", running=True)
            else:
                self._set_status("Transcribing", running=True)
            return

        if self.expecting_video_path:
            self.current_file.setText(line)
            self.expecting_video_path = False

        for name, pattern in SUMMARY_PATTERNS.items():
            match = pattern.match(line)
            if not match:
                continue
            value = int(match.group("value"))
            metric = getattr(self, f"{name}_metric")
            metric.set_value(value)
            return

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self.output_buffer.strip():
            self._parse_output_line(self.output_buffer.strip())
        self.output_buffer = ""
        self._set_controls_running(False)
        if self.stop_requested:
            self._set_status("Stopped")
            self.current_file.setText("Stopped. Run again to resume safely.")
        elif exit_code == 0:
            self._set_status("Complete")
            self.current_file.setText("All available videos processed.")
            if self.progress_bar.maximum() > 0:
                self.progress_bar.setValue(self.progress_bar.maximum())
        else:
            failed = int(self.failed_metric.value.text())
            label = f"Complete · {failed} failed" if failed else "Could not complete"
            self._set_status(label, error=True)
            if not failed:
                self.current_file.setText("Check the log for details.")
        self.stop_requested = False

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._append_log(
                f"\nCould not start Python: {self.process.errorString()}\n"
            )
            self._set_controls_running(False)
            self._set_status("Could not start", error=True)

    def _reset_run_state(self) -> None:
        self.output_buffer = ""
        self.expecting_video_path = False
        self.stop_requested = False
        for metric in (
            self.discovered_metric,
            self.succeeded_metric,
            self.skipped_metric,
            self.failed_metric,
        ):
            metric.set_value(0)
        self.progress_label.setText("0 of 0 videos")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    def _set_controls_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        for control in (
            self.source_input,
            self.output_input,
            self.model_combo,
            self.device_combo,
            self.force_checkbox,
        ):
            control.setEnabled(not running)

    def _set_status(
        self, text: str, *, running: bool = False, error: bool = False
    ) -> None:
        self.status_badge.setText(text)
        self.status_badge.setProperty("running", running)
        self.status_badge.setProperty("error", error)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

    def _append_log(self, text: str) -> None:
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible()

    def _clear_log(self) -> None:
        self.log_output.clear()

    def _open_output_directory(self) -> None:
        directory = self._effective_output_directory().resolve()
        if not directory.exists():
            QMessageBox.information(
                self,
                APP_NAME,
                "The transcript folder has not been created yet. Start a transcription first.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        if self.process.state() != QProcess.ProcessState.NotRunning:
            response = QMessageBox.question(
                self,
                "Close EchoScribe?",
                "A transcription is still running. Closing now will stop it; the next run "
                "will safely resume incomplete videos.\n\nClose EchoScribe?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.process.kill()
            self.process.waitForFinished(3000)
        self._save_settings()
        event.accept()


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE_SHEET)
    application.setFont(QFont("Segoe UI", 10))
    window = EchoScribeWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
