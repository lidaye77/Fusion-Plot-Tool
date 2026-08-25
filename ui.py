import csv
import itertools
import os
import re
from datetime import datetime

from matplotlib import rcParams
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib import dates as mdates
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.transforms import Bbox
from matplotlib.ticker import FuncFormatter, MaxNLocator
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome
except Exception:
    qtawesome = None

from merger import FileMerger

rcParams.update(
    {
        "agg.path.chunksize": 10000,
        "path.simplify": True,
    }
)

_FILE_ROWS_CACHE = {}


def _iter_whitespace_rows(source):

    for raw_line in source:

        stripped = raw_line.strip()

        if not stripped:
            continue

        timestamp = _extract_datetime_token(stripped)
        if timestamp:
            collapsed = timestamp.replace(" ", "T")
            stripped = _DATETIME_PATTERN.sub(collapsed, stripped, count=1)

        yield re.split(r"\s+", stripped)


def _create_data_reader(source):

    sample = source.read(4096)
    source.seek(0)

    if not sample:
        return iter(())

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        source.seek(0)
        return csv.reader(source, dialect)
    except csv.Error:
        source.seek(0)
        return _iter_whitespace_rows(source)


def _file_signature(file_path):

    stat_result = os.stat(file_path)
    return (stat_result.st_mtime_ns, stat_result.st_size)


def _get_cached_file_rows(file_path):

    signature = _file_signature(file_path)
    cached = _FILE_ROWS_CACHE.get(file_path)

    if cached and cached[0] == signature:
        return cached[1]

    rows = []

    with open(
        file_path,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as source:

        reader = _create_data_reader(source)

        for row in reader:
            rows.append(tuple(cell.strip() for cell in row))

    _FILE_ROWS_CACHE[file_path] = (signature, tuple(rows))
    return _FILE_ROWS_CACHE[file_path][1]


def _get_cached_file_data(file_path):

    rows = _get_cached_file_rows(file_path)
    if not rows:
        return (), (), (), False

    header_row = rows[0]
    has_header = _row_looks_like_header(header_row)
    data_rows = rows[1:] if has_header else rows

    return rows, data_rows, header_row, has_header


def _clear_cached_file_rows(file_path=None):

    if file_path is None:
        _FILE_ROWS_CACHE.clear()
        return

    _FILE_ROWS_CACHE.pop(file_path, None)


def _enable_dialog_resize(dialog):

    dialog.setSizeGripEnabled(True)
    dialog.setWindowFlags(
        dialog.windowFlags()
        | Qt.WindowMinMaxButtonsHint
        | Qt.WindowMaximizeButtonHint
    )
    return dialog


_NUMBER_PATTERN = re.compile(
    r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"
)

_DATETIME_PATTERN = re.compile(
    r"\[?((?:19|20)\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2})\]?"
)

_MEASUREMENT_KV_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9_\- ]{0,40})\s*:\s*"
    r"([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)"
)


def _extract_numeric_tokens(text):

    return _NUMBER_PATTERN.findall(text or "")


def _extract_datetime_token(text):

    match = _DATETIME_PATTERN.search(text or "")

    if not match:
        return None

    # Normalize date separator so downstream CSV/text output is consistent.
    return match.group(1).replace("/", "-")


def _extract_clean_row_tokens(text):

    raw_text = (text or "").strip()

    if not raw_text:
        return []

    timestamp = _extract_datetime_token(raw_text)

    if timestamp:
        stripped_text = _DATETIME_PATTERN.sub(" ", raw_text, count=1)

        # Preferred for device logs: extract only key:value numeric metrics.
        kv_numeric_values = [
            value
            for _name, value in _MEASUREMENT_KV_PATTERN.findall(stripped_text)
        ]
        if kv_numeric_values:
            return [timestamp] + kv_numeric_values

        # Timestamped CSV/TSV rows: keep strict numeric cells only.
        if any(delim in stripped_text for delim in [",", "\t", ";", "|"]):
            strict_cells = []
            for cell in re.split(r"[,\t;|]", stripped_text):
                cleaned = cell.strip()
                if cleaned and _is_numeric_cell(cleaned):
                    strict_cells.append(cleaned)

            if strict_cells:
                return [timestamp] + strict_cells

        # Timestamp-only status lines (e.g., waiting/show/serial) are ignored.
        return []

    return _extract_numeric_tokens(raw_text)


def _parse_datetime_value(value):

    if value is None:
        return None

    normalized = str(value).strip().strip("[]")

    if not normalized:
        return None

    normalized = normalized.replace("T", " ").replace("_", " ").replace("/", "-")

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    return None


def _parse_datetime_seconds(value):

    dt_value = _parse_datetime_value(value)
    if dt_value is None:
        return None

    return dt_value.timestamp()


def _coerce_plot_x_value(value):

    dt_value = _parse_datetime_value(value)
    if dt_value is not None:
        return dt_value

    return _coerce_numeric_value(value)


def _series_has_datetime(values):

    for value in values:
        if isinstance(value, datetime):
            return True

    return False


def _apply_datetime_x_axis(axis):

    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(formatter)


def _format_numeric_time_value(value, unit):

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""

    if unit == "h":
        converted = seconds / 3600.0
    elif unit == "min":
        converted = seconds / 60.0
    elif unit == "ms":
        converted = seconds * 1000.0
    else:
        converted = seconds

    return str(int(round(converted)))


def _apply_time_unit_x_axis(axis, unit, has_datetime_x):

    choice = (unit or "auto").strip().lower()

    if choice not in {"auto", "h", "min", "s", "ms"}:
        choice = "auto"

    if choice == "auto":
        if has_datetime_x:
            _apply_datetime_x_axis(axis)
        return

    if has_datetime_x:
        axis.xaxis.set_major_locator(mdates.AutoDateLocator())

        if choice == "h":
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%H"))
            return

        if choice == "min":
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%M"))
            return

        if choice == "s":
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%S"))
            return

        axis.xaxis.set_major_formatter(
            FuncFormatter(
                lambda raw_value, _pos: str(
                    int(
                        round(
                            (
                                mdates.num2date(raw_value).second
                                + (mdates.num2date(raw_value).microsecond / 1_000_000.0)
                            )
                            * 1000.0
                        )
                    )
                )
            )
        )
        return

    axis.xaxis.set_major_formatter(
        FuncFormatter(
            lambda raw_value, _pos: _format_numeric_time_value(raw_value, choice)
        )
    )


def _source_unit_factor_to_seconds(source_unit):

    unit = (source_unit or "s").strip().lower()

    if unit == "ms":
        return 0.001

    if unit == "min":
        return 60.0

    if unit == "h":
        return 3600.0

    return 1.0


def _normalize_x_values_to_seconds(values, source_unit):

    factor = _source_unit_factor_to_seconds(source_unit)

    if abs(factor - 1.0) < 1e-12:
        return values

    normalized = []
    for value in values:
        if isinstance(value, datetime):
            normalized.append(value)
            continue

        try:
            normalized.append(float(value) * factor)
        except (TypeError, ValueError):
            normalized.append(value)

    return normalized


def _compact_legend_label_text(label):

    text = (label or "").strip()

    if not text:
        return text

    if text.startswith("Sensor "):
        return "S" + text[len("Sensor "):]

    if text.startswith("Avg "):
        return text.replace("Avg ", "", 1)

    return text


def _x_value_to_axis_number(value):

    if isinstance(value, datetime):
        return mdates.date2num(value)

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_axis_bounds(current_bounds, values):

    lower, upper = current_bounds

    for value in values:
        numeric_value = _x_value_to_axis_number(value)

        if numeric_value is None:
            continue

        if lower is None or numeric_value < lower:
            lower = numeric_value

        if upper is None or numeric_value > upper:
            upper = numeric_value

    return lower, upper


def _coerce_numeric_value(value):

    raw = (value or "").strip()

    if not raw:
        return None

    dt_seconds = _parse_datetime_seconds(raw)
    if dt_seconds is not None:
        return dt_seconds

    try:
        return float(raw)
    except ValueError:
        pass

    if ":" in raw:
        tail = raw.split(":")[-1].strip()
        if tail:
            dt_seconds = _parse_datetime_seconds(tail)
            if dt_seconds is not None:
                return dt_seconds

            try:
                return float(tail)
            except ValueError:
                pass

            tail_tokens = _NUMBER_PATTERN.findall(tail)
            if tail_tokens:
                try:
                    return float(tail_tokens[-1])
                except ValueError:
                    pass

    tokens = _NUMBER_PATTERN.findall(raw)
    if not tokens:
        return None

    try:
        return float(tokens[-1])
    except ValueError:
        return None


def _is_numeric_cell(value):

    return bool(_NUMBER_PATTERN.fullmatch((value or "").strip()))


def _row_looks_like_header(row):

    if not row:
        return False

    non_empty = [cell.strip() for cell in row if cell and cell.strip()]

    if not non_empty:
        return False

    numeric_count = sum(1 for cell in non_empty if _is_numeric_cell(cell))

    return numeric_count < len(non_empty)


def _create_data_row_iterator(source):

    reader = _create_data_reader(source)
    first_row = next(reader, None)

    if first_row is None:
        return iter(())

    if _row_looks_like_header(first_row):
        return reader

    return itertools.chain([first_row], reader)


def _fit_window_to_screen(
    window,
    width_ratio=0.94,
    height_ratio=0.90,
    min_width=900,
    min_height=620,
):

    screen = window.screen() or QGuiApplication.primaryScreen()

    if screen is None:
        return

    rect = screen.availableGeometry()

    target_width = int(rect.width() * width_ratio)
    target_height = int(rect.height() * height_ratio)

    target_width = max(min_width, min(target_width, rect.width()))
    target_height = max(min_height, min(target_height, rect.height()))

    target_x = rect.x() + max(0, (rect.width() - target_width) // 2)
    target_y = rect.y() + max(0, (rect.height() - target_height) // 2)

    window.setGeometry(target_x, target_y, target_width, target_height)


def _safe_save_file_name(parent, title, default_name, file_filter):

    try:
        file_path, _ = QFileDialog.getSaveFileName(
            parent,
            title,
            default_name,
            file_filter,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        return file_path
    except Exception as error:
        if hasattr(parent, "log"):
            try:
                parent.log(f"Save file dialog failed: {error}")
            except Exception:
                pass

        QMessageBox.critical(
            parent,
            "Save File Error",
            "Failed to open save file dialog."
        )
        return ""


class DropListWidget(QListWidget):

    filesDropped = Signal(list)

    def __init__(self):
        super().__init__()

        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)

    def dragEnterEvent(self, event):

        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):

        event.acceptProposedAction()

    def dropEvent(self, event):

        paths = []

        for url in event.mimeData().urls():

            path = url.toLocalFile()

            if os.path.isdir(path):

                for root, dirs, files in os.walk(path):

                    for file in files:

                        paths.append(
                            os.path.join(root, file)
                        )

            else:

                paths.append(path)

        self.filesDropped.emit(paths)


class FileColumnConfigDialog(QDialog):

    def __init__(self, file_paths, default_sensor_cols, chart_index, parent=None):

        super().__init__(parent)
        _enable_dialog_resize(self)

        self.file_paths = file_paths
        self.default_sensor_cols = default_sensor_cols
        self.chart_index = chart_index
        self._time_unit_choices = ["auto", "h", "min", "s", "ms"]
        self._time_unit_labels = {
            "auto": "Auto",
            "h": "h",
            "min": "min",
            "s": "s",
            "ms": "ms",
        }
        self._source_unit_choices = ["s", "ms", "min", "h"]
        self._source_unit_labels = {
            "s": "s",
            "ms": "ms",
            "min": "min",
            "h": "h",
        }
        self._x_time_unit = "auto"
        self._x_source_unit = "s"

        self.setWindowTitle("Configure Chart")
        self.resize(1080, 620)

        self._rows = []

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.95,
                height_ratio=0.82,
                min_width=1080,
                min_height=560,
            ),
        )

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        tip = QLabel(
            "Configure one chart: file columns, axis mapping, sensor analysis, and color strategy."
        )
        tip.setObjectName("dialogSubtitle")
        layout.addWidget(tip)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(10)

        meta_layout.addWidget(QLabel("Chart Name"))
        self.chartTitleEdit = QLineEdit(f"Chart {self.chart_index}")
        meta_layout.addWidget(self.chartTitleEdit, 2)

        meta_layout.addWidget(QLabel("X Axis Name"))
        self.xAxisNameEdit = QLineEdit("X values")
        meta_layout.addWidget(self.xAxisNameEdit, 2)

        self.xTimeUnitButton = QPushButton()
        self.xTimeUnitButton.clicked.connect(self._cycle_x_time_unit)
        meta_layout.addWidget(self.xTimeUnitButton)

        self.xSourceUnitButton = QPushButton()
        self.xSourceUnitButton.clicked.connect(self._cycle_x_source_unit)
        meta_layout.addWidget(self.xSourceUnitButton)

        self.singleYAxisLabel = QLabel("Y Axis Name")
        meta_layout.addWidget(self.singleYAxisLabel)

        self.singleYAxisNameEdit = QLineEdit("Y Axis")
        meta_layout.addWidget(self.singleYAxisNameEdit, 2)

        self.multiYAxisCheck = QCheckBox("Enable Multi-Y Axis")
        self.multiYAxisCheck.setObjectName("switchToggle")
        self.multiYAxisCheck.setChecked(False)
        self.multiYAxisCheck.stateChanged.connect(self._refresh_toggle_state)
        meta_layout.addWidget(self.multiYAxisCheck)

        layout.addLayout(meta_layout)

        option_card = QFrame()
        option_card.setObjectName("card")
        option_layout = QVBoxLayout(option_card)
        option_layout.setContentsMargins(12, 12, 12, 12)
        option_layout.setSpacing(8)

        option_title = QLabel("Analysis Options")
        option_title.setObjectName("sectionTitle")
        option_layout.addWidget(option_title)

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(22, 0, 0, 0)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(QLabel("Render Mode"))

        self.plotModeGroup = QButtonGroup(self)
        self.lineModeRadio = QRadioButton("Line")
        self.pointModeRadio = QRadioButton("Point")
        self.lineModeRadio.setChecked(True)
        self.lineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.pointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.plotModeGroup.addButton(self.lineModeRadio, 1)
        self.plotModeGroup.addButton(self.pointModeRadio, 2)
        mode_layout.addWidget(self.lineModeRadio)
        mode_layout.addWidget(self.pointModeRadio)
        mode_layout.addStretch(1)
        option_layout.addLayout(mode_layout)

        self.lineWidthFrame = QFrame()
        line_width_layout = QHBoxLayout(self.lineWidthFrame)
        line_width_layout.setContentsMargins(22, 0, 0, 0)
        line_width_layout.setSpacing(10)
        line_width_layout.addWidget(QLabel("Line Width"))
        self.lineWidthSpin = QDoubleSpinBox()
        self.lineWidthSpin.setRange(0.5, 8.0)
        self.lineWidthSpin.setSingleStep(0.1)
        self.lineWidthSpin.setDecimals(1)
        self.lineWidthSpin.setValue(1.8)
        line_width_layout.addWidget(self.lineWidthSpin)
        line_width_layout.addStretch(1)
        option_layout.addWidget(self.lineWidthFrame)

        self.pointSizeFrame = QFrame()
        point_size_layout = QHBoxLayout(self.pointSizeFrame)
        point_size_layout.setContentsMargins(22, 0, 0, 0)
        point_size_layout.setSpacing(10)
        point_size_layout.addWidget(QLabel("Point Size"))
        self.pointSizeSpin = QSpinBox()
        self.pointSizeSpin.setRange(1, 220)
        self.pointSizeSpin.setSingleStep(2)
        self.pointSizeSpin.setValue(24)
        point_size_layout.addWidget(self.pointSizeSpin)
        point_size_layout.addStretch(1)
        option_layout.addWidget(self.pointSizeFrame)

        self.sensorModeCheck = QCheckBox("Sensor No (Master)")
        self.sensorModeCheck.setObjectName("switchToggle")
        self.sensorModeCheck.setChecked(False)
        self.sensorModeCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.sensorModeCheck)

        self.sensorChildFrame = QFrame()
        sensor_child_layout = QHBoxLayout(self.sensorChildFrame)
        sensor_child_layout.setContentsMargins(22, 0, 0, 0)
        sensor_child_layout.setSpacing(12)

        self.sensorBandEnabledCheck = QCheckBox("Enable Avg Range Bands")
        self.sensorBandEnabledCheck.setObjectName("switchToggle")
        self.sensorBandEnabledCheck.setChecked(False)
        self.sensorBandEnabledCheck.setEnabled(False)
        self.sensorBandEnabledCheck.stateChanged.connect(self._refresh_toggle_state)
        sensor_child_layout.addWidget(self.sensorBandEnabledCheck)

        sensor_child_layout.addWidget(QLabel("Default Band Rules"))
        self.sensorBandDefaultEdit = QLineEdit("15%,30%")
        self.sensorBandDefaultEdit.setPlaceholderText("e.g. 15%,30% or 3,0.5")
        self.sensorBandDefaultEdit.setEnabled(False)
        sensor_child_layout.addWidget(self.sensorBandDefaultEdit, 1)

        sensor_child_layout.addStretch(1)
        option_layout.addWidget(self.sensorChildFrame)

        self.bandAlphaFrame = QFrame()
        alpha_layout = QHBoxLayout(self.bandAlphaFrame)
        alpha_layout.setContentsMargins(22, 0, 0, 0)
        alpha_layout.setSpacing(12)

        alpha_layout.addWidget(QLabel("Band Alpha (%)"))
        self.bandAlphaSpin = QSpinBox()
        self.bandAlphaSpin.setRange(5, 60)
        self.bandAlphaSpin.setValue(20)
        self.bandAlphaSpin.setEnabled(False)
        alpha_layout.addWidget(self.bandAlphaSpin)

        alpha_layout.addStretch(1)
        option_layout.addWidget(self.bandAlphaFrame)

        self.manualColorCheck = QCheckBox("Custom Colors")
        self.manualColorCheck.setObjectName("switchToggle")
        self.manualColorCheck.setChecked(False)
        self.manualColorCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.manualColorCheck)

        self.colorChildFrame = QFrame()
        color_child_layout = QHBoxLayout(self.colorChildFrame)
        color_child_layout.setContentsMargins(22, 0, 0, 0)
        color_child_layout.setSpacing(18)

        self.sameFamilyColorCheck = QCheckBox("Same Family by TXT")
        self.sameFamilyColorCheck.setObjectName("switchToggle")
        self.sameFamilyColorCheck.setChecked(True)
        color_child_layout.addWidget(self.sameFamilyColorCheck)
        color_child_layout.addStretch(1)
        option_layout.addWidget(self.colorChildFrame)

        layout.addWidget(option_card)

        table = QTableWidget(len(self.file_paths), 7)
        table.setHorizontalHeaderLabels([
            "TXT File",
            "X Column (index)",
            "Y Columns (comma)",
            "Sensor Column (index, 0=off)",
            "Y Axis ID",
            "Y Axis Name(s)",
            "Band Rules",
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(46)
        table.horizontalHeader().setMinimumSectionSize(110)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            5,
            QHeaderView.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            6,
            QHeaderView.Stretch
        )

        for row, file_path in enumerate(self.file_paths):

            file_item = QTableWidgetItem(os.path.basename(file_path))
            file_item.setToolTip(file_path)
            table.setItem(row, 0, file_item)

            x_spin = QSpinBox()
            x_spin.setRange(1, 999)
            x_spin.setValue(1)
            table.setCellWidget(row, 1, x_spin)

            y_cols_edit = QLineEdit("3")
            y_cols_edit.setPlaceholderText("e.g. 3,4,6")
            table.setCellWidget(row, 2, y_cols_edit)

            sensor_spin = QSpinBox()
            sensor_spin.setRange(0, 999)
            sensor_spin.setValue(self.default_sensor_cols[file_path])
            table.setCellWidget(row, 3, sensor_spin)

            axis_spin = QSpinBox()
            axis_spin.setRange(1, len(self.file_paths))
            axis_spin.setValue(1)
            table.setCellWidget(row, 4, axis_spin)

            axis_name_edit = QLineEdit("Y Axis")
            table.setCellWidget(row, 5, axis_name_edit)

            band_rule_edit = QLineEdit("")
            band_rule_edit.setPlaceholderText("e.g. 15%,3,0.5 (max 3)")
            table.setCellWidget(row, 6, band_rule_edit)

            self._rows.append(
                {
                    "file_path": file_path,
                    "x_col": x_spin,
                    "y_cols": y_cols_edit,
                    "sensor_col": sensor_spin,
                    "axis_id": axis_spin,
                    "axis_name": axis_name_edit,
                    "band_rules": band_rule_edit,
                }
            )

        self.table = table

        table_guide = QLabel(
            "Guide: Band Rules supports custom percent or absolute values, max 3 per box, e.g. 15%,3,0.5."
        )
        table_guide.setObjectName("dialogSubtitle")
        layout.addWidget(table_guide)

        layout.addWidget(self.table, 1)

        self._refresh_toggle_state()
        self._update_x_time_unit_button_text()
        self._update_x_source_unit_button_text()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_configs(self):

        configs = []

        for row_data in self._rows:

            y_cols = self._parse_y_columns(
                row_data["y_cols"].text()
            )

            configs.append(
                {
                    "file_path": row_data["file_path"],
                    "x_col": row_data["x_col"].value(),
                    "y_cols": y_cols,
                    "y_col": y_cols[0],
                    "sensor_col": row_data["sensor_col"].value(),
                    "axis_id": (
                        row_data["axis_id"].value()
                        if self.multiYAxisCheck.isChecked()
                        else 1
                    ),
                    "axis_name": (
                        row_data["axis_name"].text().strip() or "Y Axis"
                    ),
                    "sensor_band_rules": row_data["band_rules"].text().strip(),
                }
            )

        return configs

    def _parse_y_columns(self, text):

        columns = []

        for token in (text or "").split(","):
            value = token.strip()

            if not value:
                continue

            if not value.isdigit():
                continue

            col_index = int(value)
            if col_index < 1:
                continue

            if col_index not in columns:
                columns.append(col_index)

        return columns or [3]


    def get_meta(self):

        chart_title = self.chartTitleEdit.text().strip() or f"Chart {self.chart_index}"
        x_axis_name = self.xAxisNameEdit.text().strip() or "X values"
        multi_y = self.multiYAxisCheck.isChecked()

        if multi_y:
            y_axis_names = {}

            for row_data in self._rows:
                axis_id = row_data["axis_id"].value()
                y_cols = self._parse_y_columns(
                    row_data["y_cols"].text()
                )

                raw_axis_name = row_data["axis_name"].text().strip()
                axis_names = [
                    token.strip()
                    for token in raw_axis_name.split(",")
                    if token.strip()
                ]

                if len(y_cols) <= 1:
                    axis_name = axis_names[0] if axis_names else f"Y Axis {axis_id}"
                    y_axis_names[axis_id] = axis_name
                    continue

                for idx, y_col in enumerate(y_cols):
                    target_axis_id = axis_id + idx

                    if idx < len(axis_names):
                        axis_name = axis_names[idx]
                    elif axis_names:
                        axis_name = f"{axis_names[0]} (Y{y_col})"
                    else:
                        axis_name = f"Y Axis {target_axis_id}"

                    y_axis_names[target_axis_id] = axis_name

            if 1 not in y_axis_names:
                y_axis_names[1] = "Y Axis 1"
        else:
            y_axis_names = {
                1: self.singleYAxisNameEdit.text().strip() or "Y Axis"
            }

        return {
            "chart_title": chart_title,
            "x_axis_name": x_axis_name,
            "x_time_unit": self._x_time_unit,
            "x_source_unit": self._x_source_unit,
            "multi_y": multi_y,
            "y_axis_names": y_axis_names,
            "sensor_mode": self.sensorModeCheck.isChecked(),
            "plot_mode": "point" if self.pointModeRadio.isChecked() else "line",
            "line_width": self.lineWidthSpin.value(),
            "point_size": self.pointSizeSpin.value(),
            "sensor_band_enabled": self.sensorBandEnabledCheck.isChecked(),
            "sensor_band_default_rules": self.sensorBandDefaultEdit.text().strip(),
            "sensor_band_alpha": self.bandAlphaSpin.value() / 100.0,
            "manual_color_mode": self.manualColorCheck.isChecked(),
            "same_family_colors": self.sameFamilyColorCheck.isChecked(),
        }

    def _update_x_time_unit_button_text(self):

        label = self._time_unit_labels.get(self._x_time_unit, "Auto")
        self.xTimeUnitButton.setText(f"X Tick Unit: {label}")

    def _update_x_source_unit_button_text(self):

        label = self._source_unit_labels.get(self._x_source_unit, "s")
        self.xSourceUnitButton.setText(f"X Source Unit: {label}")

    def _cycle_x_time_unit(self):

        idx = self._time_unit_choices.index(self._x_time_unit)
        self._x_time_unit = self._time_unit_choices[(idx + 1) % len(self._time_unit_choices)]
        self._update_x_time_unit_button_text()

    def _cycle_x_source_unit(self):

        idx = self._source_unit_choices.index(self._x_source_unit)
        self._x_source_unit = self._source_unit_choices[(idx + 1) % len(self._source_unit_choices)]
        self._update_x_source_unit_button_text()

    def _refresh_toggle_state(self):

        multi_y_enabled = self.multiYAxisCheck.isChecked()
        sensor_enabled = self.sensorModeCheck.isChecked()
        manual_color_enabled = self.manualColorCheck.isChecked()

        self.table.setColumnHidden(3, not sensor_enabled)
        self.table.setColumnHidden(4, not multi_y_enabled)
        self.table.setColumnHidden(5, not multi_y_enabled)
        self.table.setColumnHidden(6, not sensor_enabled)

        if not sensor_enabled:
            self.sensorBandEnabledCheck.setChecked(False)

        self.sensorChildFrame.setVisible(sensor_enabled)
        self.sensorBandEnabledCheck.setEnabled(sensor_enabled)
        band_controls_enabled = sensor_enabled and self.sensorBandEnabledCheck.isChecked()
        self.sensorBandDefaultEdit.setEnabled(band_controls_enabled)
        self.bandAlphaFrame.setVisible(sensor_enabled)
        self.bandAlphaSpin.setEnabled(band_controls_enabled)

        point_mode_enabled = self.pointModeRadio.isChecked()
        self.lineWidthFrame.setVisible(not point_mode_enabled)
        self.pointSizeFrame.setVisible(point_mode_enabled)

        self.colorChildFrame.setVisible(manual_color_enabled)
        self.sameFamilyColorCheck.setEnabled(manual_color_enabled)

        self.singleYAxisLabel.setVisible(not multi_y_enabled)
        self.singleYAxisNameEdit.setVisible(not multi_y_enabled)

        for row_data in self._rows:
            row_data["sensor_col"].setEnabled(sensor_enabled)
            row_data["axis_id"].setEnabled(multi_y_enabled)
            row_data["axis_name"].setEnabled(multi_y_enabled)
            row_data["band_rules"].setEnabled(band_controls_enabled)


class XYZFileColumnConfigDialog(QDialog):

    def __init__(self, file_paths, default_sensor_cols, chart_index, parent=None):

        super().__init__(parent)
        _enable_dialog_resize(self)

        self.file_paths = file_paths
        self.default_sensor_cols = default_sensor_cols
        self.chart_index = chart_index

        self.setWindowTitle("Configure XYZ Chart")
        self.resize(980, 560)

        self._rows = []

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.94,
                height_ratio=0.80,
                min_width=980,
                min_height=540,
            ),
        )

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        tip = QLabel(
            "Configure one XYZ chart: choose X/Y/Z columns and optionally split lines by Sensor No."
        )
        tip.setObjectName("dialogSubtitle")
        layout.addWidget(tip)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(10)

        meta_layout.addWidget(QLabel("Chart Name"))
        self.chartTitleEdit = QLineEdit(f"XYZ Chart {self.chart_index}")
        meta_layout.addWidget(self.chartTitleEdit, 2)

        meta_layout.addWidget(QLabel("X Axis Name"))
        self.xAxisNameEdit = QLineEdit("X values")
        meta_layout.addWidget(self.xAxisNameEdit, 2)

        meta_layout.addWidget(QLabel("Y Axis Name"))
        self.yAxisNameEdit = QLineEdit("Y values")
        meta_layout.addWidget(self.yAxisNameEdit, 2)

        meta_layout.addWidget(QLabel("Z Axis Name"))
        self.zAxisNameEdit = QLineEdit("Z values")
        meta_layout.addWidget(self.zAxisNameEdit, 2)

        layout.addLayout(meta_layout)

        option_card = QFrame()
        option_card.setObjectName("card")
        option_layout = QVBoxLayout(option_card)
        option_layout.setContentsMargins(12, 12, 12, 12)
        option_layout.setSpacing(8)

        option_title = QLabel("Analysis Options")
        option_title.setObjectName("sectionTitle")
        option_layout.addWidget(option_title)

        render_title = QLabel("Render Settings")
        render_title.setObjectName("dialogSubtitle")
        option_layout.addWidget(render_title)

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(22, 0, 0, 0)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(QLabel("Render Mode"))

        self.plotModeGroup = QButtonGroup(self)
        self.lineModeRadio = QRadioButton("Line")
        self.pointModeRadio = QRadioButton("Point")
        self.lineModeRadio.setChecked(True)
        self.lineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.pointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.plotModeGroup.addButton(self.lineModeRadio, 1)
        self.plotModeGroup.addButton(self.pointModeRadio, 2)
        mode_layout.addWidget(self.lineModeRadio)
        mode_layout.addWidget(self.pointModeRadio)
        mode_layout.addStretch(1)
        option_layout.addLayout(mode_layout)

        self.lineWidthFrame = QFrame()
        line_width_layout = QHBoxLayout(self.lineWidthFrame)
        line_width_layout.setContentsMargins(22, 0, 0, 0)
        line_width_layout.setSpacing(10)
        line_width_layout.addWidget(QLabel("Line Width"))
        self.lineWidthSpin = QDoubleSpinBox()
        self.lineWidthSpin.setRange(0.5, 8.0)
        self.lineWidthSpin.setSingleStep(0.1)
        self.lineWidthSpin.setDecimals(1)
        self.lineWidthSpin.setValue(1.8)
        line_width_layout.addWidget(self.lineWidthSpin)
        line_width_layout.addStretch(1)
        option_layout.addWidget(self.lineWidthFrame)

        self.pointSizeFrame = QFrame()
        point_size_layout = QHBoxLayout(self.pointSizeFrame)
        point_size_layout.setContentsMargins(22, 0, 0, 0)
        point_size_layout.setSpacing(10)
        point_size_layout.addWidget(QLabel("Point Size"))
        self.pointSizeSpin = QSpinBox()
        self.pointSizeSpin.setRange(1, 220)
        self.pointSizeSpin.setSingleStep(2)
        self.pointSizeSpin.setValue(24)
        point_size_layout.addWidget(self.pointSizeSpin)
        point_size_layout.addStretch(1)
        option_layout.addWidget(self.pointSizeFrame)

        sensor_title = QLabel("Sensor Settings")
        sensor_title.setObjectName("dialogSubtitle")
        option_layout.addWidget(sensor_title)

        self.sensorModeCheck = QCheckBox("Sensor No (Split Lines)")
        self.sensorModeCheck.setObjectName("switchToggle")
        self.sensorModeCheck.setChecked(False)
        self.sensorModeCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.sensorModeCheck)

        layout.addWidget(option_card)

        table = QTableWidget(len(self.file_paths), 5)
        table.setHorizontalHeaderLabels([
            "Data File",
            "X Column (0=Index)",
            "Y Column",
            "Z Column",
            "Sensor Column (0=off)",
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(46)
        table.horizontalHeader().setMinimumSectionSize(120)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

        for row, file_path in enumerate(self.file_paths):

            file_item = QTableWidgetItem(os.path.basename(file_path))
            file_item.setToolTip(file_path)
            table.setItem(row, 0, file_item)

            x_spin = QSpinBox()
            x_spin.setRange(0, 999)
            x_spin.setValue(0)
            table.setCellWidget(row, 1, x_spin)

            y_spin = QSpinBox()
            y_spin.setRange(1, 999)
            y_spin.setValue(2)
            table.setCellWidget(row, 2, y_spin)

            z_spin = QSpinBox()
            z_spin.setRange(1, 999)
            z_spin.setValue(3)
            table.setCellWidget(row, 3, z_spin)

            sensor_spin = QSpinBox()
            sensor_spin.setRange(0, 999)
            sensor_spin.setValue(self.default_sensor_cols[file_path])
            table.setCellWidget(row, 4, sensor_spin)

            self._rows.append(
                {
                    "file_path": file_path,
                    "x_col": x_spin,
                    "y_col": y_spin,
                    "z_col": z_spin,
                    "sensor_col": sensor_spin,
                }
            )

        self.table = table

        table_guide = QLabel(
            "Guide: set X Column to 0 to use the point index as X values."
        )
        table_guide.setObjectName("dialogSubtitle")
        layout.addWidget(table_guide)

        layout.addWidget(self.table, 1)

        self._refresh_toggle_state()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_configs(self):

        return [
            {
                "file_path": row_data["file_path"],
                "x_col": row_data["x_col"].value(),
                "y_col": row_data["y_col"].value(),
                "z_col": row_data["z_col"].value(),
                "sensor_col": row_data["sensor_col"].value(),
            }
            for row_data in self._rows
        ]

    def get_meta(self):

        return {
            "chart_title": self.chartTitleEdit.text().strip() or f"XYZ Chart {self.chart_index}",
            "x_axis_name": self.xAxisNameEdit.text().strip() or "X values",
            "y_axis_name": self.yAxisNameEdit.text().strip() or "Y values",
            "z_axis_name": self.zAxisNameEdit.text().strip() or "Z values",
            "sensor_mode": self.sensorModeCheck.isChecked(),
            "plot_mode": "point" if self.pointModeRadio.isChecked() else "line",
            "line_width": self.lineWidthSpin.value(),
            "point_size": self.pointSizeSpin.value(),
        }

    def _refresh_toggle_state(self):

        sensor_enabled = self.sensorModeCheck.isChecked()
        point_mode_enabled = self.pointModeRadio.isChecked()

        self.table.setColumnHidden(4, not sensor_enabled)

        self.lineWidthFrame.setVisible(not point_mode_enabled)
        self.pointSizeFrame.setVisible(point_mode_enabled)

        for row_data in self._rows:
            row_data["sensor_col"].setEnabled(sensor_enabled)


class SingleFileBatchExportConfigDialog(QDialog):

    def __init__(
        self,
        file_path,
        column_count,
        default_sensor_col,
        header_names=None,
        parent=None
    ):

        super().__init__(parent)
        _enable_dialog_resize(self)

        self.file_path = file_path
        self.column_count = max(1, int(column_count))
        self.default_sensor_col = min(
            max(1, int(default_sensor_col)),
            self.column_count
        )
        self._time_unit_choices = ["auto", "h", "min", "s", "ms"]
        self._time_unit_labels = {
            "auto": "Auto",
            "h": "h",
            "min": "min",
            "s": "s",
            "ms": "ms",
        }
        self._source_unit_choices = ["s", "ms", "min", "h"]
        self._source_unit_labels = {
            "s": "s",
            "ms": "ms",
            "min": "min",
            "h": "h",
        }
        self._x_time_unit = "auto"
        self._secondary_x_time_unit = "auto"
        self._x_source_unit = "s"
        self._secondary_x_source_unit = "s"
        self.header_names = header_names or {}
        self._row_controls = []
        self._palette = [
            "#36d2ff",
            "#54f4a5",
            "#ffbc42",
            "#ff6b6b",
            "#b388ff",
            "#5be7ff",
            "#8ac926",
            "#1982c4",
            "#f72585",
            "#ffd166",
            "#06d6a0",
            "#ef476f",
        ]

        self.setWindowTitle("Single File Batch PNG Export")
        self.resize(980, 620)

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.95,
                height_ratio=0.82,
                min_width=980,
                min_height=560,
            ),
        )

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        tip = QLabel(
            "Select one file, set X axis, then enable Y columns to export one PNG per column."
        )
        tip.setObjectName("dialogSubtitle")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        file_info = QLabel(
            f"File: {os.path.basename(self.file_path)} | Detected Columns: {self.column_count}"
        )
        file_info.setObjectName("dialogSubtitle")
        file_info.setWordWrap(True)
        layout.addWidget(file_info)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(10)

        meta_layout.addWidget(QLabel("X Column (0=Index)"))
        self.xColumnSpin = QSpinBox()
        self.xColumnSpin.setRange(0, self.column_count)
        self.xColumnSpin.setValue(1 if self.column_count >= 1 else 0)
        self.xColumnSpin.valueChanged.connect(self._on_x_column_changed)
        meta_layout.addWidget(self.xColumnSpin)

        meta_layout.addWidget(QLabel("X Axis Name"))
        default_x_name = self.header_names.get(1, "X values")
        self.xAxisNameEdit = QLineEdit(default_x_name)
        meta_layout.addWidget(self.xAxisNameEdit, 2)

        self.xTimeUnitButton = QPushButton()
        self.xTimeUnitButton.clicked.connect(self._cycle_x_time_unit)
        meta_layout.addWidget(self.xTimeUnitButton)

        self.xSourceUnitButton = QPushButton()
        self.xSourceUnitButton.clicked.connect(self._cycle_x_source_unit)
        meta_layout.addWidget(self.xSourceUnitButton)

        meta_layout.addWidget(QLabel("Export Prefix"))
        base_name = os.path.splitext(os.path.basename(self.file_path))[0]
        self.exportPrefixEdit = QLineEdit(base_name)
        meta_layout.addWidget(self.exportPrefixEdit, 2)

        meta_layout.addWidget(QLabel("Overall Title"))
        self.figureTitleEdit = QLineEdit(base_name)
        meta_layout.addWidget(self.figureTitleEdit, 3)

        layout.addLayout(meta_layout)

        secondary_card = QFrame()
        secondary_card.setObjectName("card")
        secondary_layout = QVBoxLayout(secondary_card)
        secondary_layout.setContentsMargins(12, 12, 12, 12)
        secondary_layout.setSpacing(8)

        secondary_title = QLabel("Chart 2 (Bottom Subplot)")
        secondary_title.setObjectName("sectionTitle")
        secondary_layout.addWidget(secondary_title)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(22, 0, 0, 0)
        secondary_row.setSpacing(10)

        self.secondaryPlotCheck = QCheckBox("Include Chart 2 below each exported image")
        self.secondaryPlotCheck.setObjectName("switchToggle")
        self.secondaryPlotCheck.setChecked(False)
        self.secondaryPlotCheck.stateChanged.connect(self._refresh_toggle_state)
        secondary_row.addWidget(self.secondaryPlotCheck)
        secondary_row.addStretch(1)
        secondary_layout.addLayout(secondary_row)

        secondary_data_hint = QLabel("Chart 2 Data Setup")
        secondary_data_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_data_hint)

        secondary_file_row = QHBoxLayout()
        secondary_file_row.setContentsMargins(22, 0, 0, 0)
        secondary_file_row.setSpacing(10)

        secondary_file_row.addWidget(QLabel("Chart 2 File"))
        self.secondaryFileEdit = QLineEdit("")
        self.secondaryFileEdit.setReadOnly(True)
        self.secondaryFileEdit.setPlaceholderText("Select a second file")
        secondary_file_row.addWidget(self.secondaryFileEdit, 3)

        self.secondaryFileBrowseButton = QPushButton("Browse")
        self.secondaryFileBrowseButton.clicked.connect(self._choose_secondary_file)
        secondary_file_row.addWidget(self.secondaryFileBrowseButton)

        secondary_layout.addLayout(secondary_file_row)

        secondary_meta = QHBoxLayout()
        secondary_meta.setContentsMargins(22, 0, 0, 0)
        secondary_meta.setSpacing(10)

        secondary_meta.addWidget(QLabel("Chart 2 X Column (0=Index)"))
        self.secondaryXColumnSpin = QSpinBox()
        self.secondaryXColumnSpin.setRange(0, self.column_count)
        self.secondaryXColumnSpin.setValue(1 if self.column_count >= 1 else 0)
        self.secondaryXColumnSpin.valueChanged.connect(self._on_secondary_x_column_changed)
        secondary_meta.addWidget(self.secondaryXColumnSpin)

        secondary_meta.addWidget(QLabel("Chart 2 X Axis Name"))
        self.secondaryXAxisNameEdit = QLineEdit("X values")
        secondary_meta.addWidget(self.secondaryXAxisNameEdit, 2)

        self.secondaryXTimeUnitButton = QPushButton()
        self.secondaryXTimeUnitButton.clicked.connect(self._cycle_secondary_x_time_unit)
        secondary_meta.addWidget(self.secondaryXTimeUnitButton)

        self.secondaryXSourceUnitButton = QPushButton()
        self.secondaryXSourceUnitButton.clicked.connect(self._cycle_secondary_x_source_unit)
        secondary_meta.addWidget(self.secondaryXSourceUnitButton)

        secondary_layout.addLayout(secondary_meta)

        secondary_y_row = QHBoxLayout()
        secondary_y_row.setContentsMargins(22, 0, 0, 0)
        secondary_y_row.setSpacing(10)

        secondary_y_row.addWidget(QLabel("Chart 2 Y Columns"))
        self.secondaryYColumnsEdit = QLineEdit("2")
        self.secondaryYColumnsEdit.setPlaceholderText("e.g. 2,3,5")
        self.secondaryYColumnsEdit.editingFinished.connect(self._sync_secondary_control_state)
        secondary_y_row.addWidget(self.secondaryYColumnsEdit, 2)

        secondary_y_row.addWidget(QLabel("Y Axis Names"))
        self.secondaryYAxisNamesEdit = QLineEdit("Y2")
        self.secondaryYAxisNamesEdit.setPlaceholderText("e.g. Y2,Temp,Pressure")
        secondary_y_row.addWidget(self.secondaryYAxisNamesEdit, 2)

        secondary_y_row.addWidget(QLabel("Line Colors"))
        self.secondaryColorsEdit = QLineEdit("#ef4444")
        self.secondaryColorsEdit.setPlaceholderText("e.g. #ef4444,#0ea5e9,#22c55e")
        secondary_y_row.addWidget(self.secondaryColorsEdit, 2)

        self.secondaryPickColorsButton = QPushButton("Pick Colors")
        self.secondaryPickColorsButton.clicked.connect(self._pick_secondary_colors)
        secondary_y_row.addWidget(self.secondaryPickColorsButton)

        secondary_layout.addLayout(secondary_y_row)

        secondary_title_row = QHBoxLayout()
        secondary_title_row.setContentsMargins(22, 0, 0, 0)
        secondary_title_row.setSpacing(10)
        secondary_title_row.addWidget(QLabel("Chart 2 Title"))
        self.secondaryTitleEdit = QLineEdit("Chart 2")
        secondary_title_row.addWidget(self.secondaryTitleEdit, 2)
        secondary_title_row.addStretch(1)
        secondary_layout.addLayout(secondary_title_row)

        secondary_color_hint = QLabel("Colors map to Y columns in order (first color -> first Y column).")
        secondary_color_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_color_hint)

        secondary_style_hint = QLabel("Chart 2 Style (applies to all Chart 2 Y columns)")
        secondary_style_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_style_hint)

        secondary_axis_row = QHBoxLayout()
        secondary_axis_row.setContentsMargins(22, 0, 0, 0)
        secondary_axis_row.setSpacing(10)

        self.secondaryMultiYAxisCheck = QCheckBox("Chart 2 Multi-Y Axis")
        self.secondaryMultiYAxisCheck.setObjectName("switchToggle")
        self.secondaryMultiYAxisCheck.setChecked(True)
        secondary_axis_row.addWidget(self.secondaryMultiYAxisCheck)
        secondary_axis_row.addStretch(1)
        secondary_layout.addLayout(secondary_axis_row)

        secondary_style_row = QHBoxLayout()
        secondary_style_row.setContentsMargins(22, 0, 0, 0)
        secondary_style_row.setSpacing(10)

        secondary_style_row.addWidget(QLabel("Render Mode"))
        self.secondaryPlotModeGroup = QButtonGroup(self)
        self.secondaryLineModeRadio = QRadioButton("Line")
        self.secondaryPointModeRadio = QRadioButton("Point")
        self.secondaryLineModeRadio.setChecked(True)
        self.secondaryLineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.secondaryPointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.secondaryPlotModeGroup.addButton(self.secondaryLineModeRadio, 1)
        self.secondaryPlotModeGroup.addButton(self.secondaryPointModeRadio, 2)
        secondary_style_row.addWidget(self.secondaryLineModeRadio)
        secondary_style_row.addWidget(self.secondaryPointModeRadio)

        self.secondaryLineWidthFrame = QFrame()
        secondary_line_width_layout = QHBoxLayout(self.secondaryLineWidthFrame)
        secondary_line_width_layout.setContentsMargins(0, 0, 0, 0)
        secondary_line_width_layout.setSpacing(8)
        secondary_line_width_layout.addWidget(QLabel("Line Width"))
        self.secondaryLineWidthSpin = QDoubleSpinBox()
        self.secondaryLineWidthSpin.setRange(0.5, 8.0)
        self.secondaryLineWidthSpin.setSingleStep(0.1)
        self.secondaryLineWidthSpin.setDecimals(1)
        self.secondaryLineWidthSpin.setValue(1.8)
        secondary_line_width_layout.addWidget(self.secondaryLineWidthSpin)
        secondary_style_row.addWidget(self.secondaryLineWidthFrame)

        self.secondaryPointSizeFrame = QFrame()
        secondary_point_size_layout = QHBoxLayout(self.secondaryPointSizeFrame)
        secondary_point_size_layout.setContentsMargins(0, 0, 0, 0)
        secondary_point_size_layout.setSpacing(8)
        secondary_point_size_layout.addWidget(QLabel("Point Size"))
        self.secondaryPointSizeSpin = QSpinBox()
        self.secondaryPointSizeSpin.setRange(1, 220)
        self.secondaryPointSizeSpin.setSingleStep(2)
        self.secondaryPointSizeSpin.setValue(24)
        secondary_point_size_layout.addWidget(self.secondaryPointSizeSpin)
        secondary_style_row.addWidget(self.secondaryPointSizeFrame)
        secondary_style_row.addStretch(1)

        secondary_layout.addLayout(secondary_style_row)
        layout.addWidget(secondary_card)

        option_card = QFrame()
        option_card.setObjectName("card")
        option_layout = QVBoxLayout(option_card)
        option_layout.setContentsMargins(12, 12, 12, 12)
        option_layout.setSpacing(8)

        option_title = QLabel("Analysis Options")
        option_title.setObjectName("sectionTitle")
        option_layout.addWidget(option_title)

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(22, 0, 0, 0)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(QLabel("Render Mode"))

        self.plotModeGroup = QButtonGroup(self)
        self.lineModeRadio = QRadioButton("Line")
        self.pointModeRadio = QRadioButton("Point")
        self.lineModeRadio.setChecked(True)
        self.lineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.pointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.plotModeGroup.addButton(self.lineModeRadio, 1)
        self.plotModeGroup.addButton(self.pointModeRadio, 2)
        mode_layout.addWidget(self.lineModeRadio)
        mode_layout.addWidget(self.pointModeRadio)
        mode_layout.addStretch(1)
        option_layout.addLayout(mode_layout)

        self.lineWidthFrame = QFrame()
        line_width_layout = QHBoxLayout(self.lineWidthFrame)
        line_width_layout.setContentsMargins(22, 0, 0, 0)
        line_width_layout.setSpacing(10)
        line_width_layout.addWidget(QLabel("Line Width"))
        self.lineWidthSpin = QDoubleSpinBox()
        self.lineWidthSpin.setRange(0.5, 8.0)
        self.lineWidthSpin.setSingleStep(0.1)
        self.lineWidthSpin.setDecimals(1)
        self.lineWidthSpin.setValue(1.8)
        line_width_layout.addWidget(self.lineWidthSpin)
        line_width_layout.addStretch(1)
        option_layout.addWidget(self.lineWidthFrame)

        self.pointSizeFrame = QFrame()
        point_size_layout = QHBoxLayout(self.pointSizeFrame)
        point_size_layout.setContentsMargins(22, 0, 0, 0)
        point_size_layout.setSpacing(10)
        point_size_layout.addWidget(QLabel("Point Size"))
        self.pointSizeSpin = QSpinBox()
        self.pointSizeSpin.setRange(1, 220)
        self.pointSizeSpin.setSingleStep(2)
        self.pointSizeSpin.setValue(24)
        point_size_layout.addWidget(self.pointSizeSpin)
        point_size_layout.addStretch(1)
        option_layout.addWidget(self.pointSizeFrame)

        self.sensorModeCheck = QCheckBox("Sensor No (Split Lines)")
        self.sensorModeCheck.setObjectName("switchToggle")
        self.sensorModeCheck.setChecked(False)
        self.sensorModeCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.sensorModeCheck)

        sensor_row = QHBoxLayout()
        sensor_row.setContentsMargins(22, 0, 0, 0)
        sensor_row.setSpacing(10)
        sensor_row.addWidget(QLabel("Sensor Column (0=off)"))
        self.sensorColumnSpin = QSpinBox()
        self.sensorColumnSpin.setRange(0, self.column_count)
        self.sensorColumnSpin.setValue(self.default_sensor_col)
        self.sensorColumnSpin.valueChanged.connect(self._refresh_row_state)
        sensor_row.addWidget(self.sensorColumnSpin)
        sensor_row.addStretch(1)
        option_layout.addLayout(sensor_row)

        self.sensorChildFrame = QFrame()
        sensor_child_layout = QHBoxLayout(self.sensorChildFrame)
        sensor_child_layout.setContentsMargins(22, 0, 0, 0)
        sensor_child_layout.setSpacing(12)

        self.sensorBandEnabledCheck = QCheckBox("Enable Avg Range Bands")
        self.sensorBandEnabledCheck.setObjectName("switchToggle")
        self.sensorBandEnabledCheck.setChecked(False)
        self.sensorBandEnabledCheck.setEnabled(False)
        self.sensorBandEnabledCheck.stateChanged.connect(self._refresh_toggle_state)
        sensor_child_layout.addWidget(self.sensorBandEnabledCheck)

        sensor_child_layout.addWidget(QLabel("Default Band Rules"))
        self.sensorBandDefaultEdit = QLineEdit("15%,30%")
        self.sensorBandDefaultEdit.setPlaceholderText("e.g. 15%,3,0.5 (max 3)")
        self.sensorBandDefaultEdit.setEnabled(False)
        sensor_child_layout.addWidget(self.sensorBandDefaultEdit, 1)
        sensor_child_layout.addStretch(1)
        option_layout.addWidget(self.sensorChildFrame)

        self.bandAlphaFrame = QFrame()
        alpha_layout = QHBoxLayout(self.bandAlphaFrame)
        alpha_layout.setContentsMargins(22, 0, 0, 0)
        alpha_layout.setSpacing(12)

        alpha_layout.addWidget(QLabel("Band Alpha (%)"))
        self.bandAlphaSpin = QSpinBox()
        self.bandAlphaSpin.setRange(5, 60)
        self.bandAlphaSpin.setValue(20)
        self.bandAlphaSpin.setEnabled(False)
        alpha_layout.addWidget(self.bandAlphaSpin)

        alpha_layout.addStretch(1)
        option_layout.addWidget(self.bandAlphaFrame)

        self.manualColorCheck = QCheckBox("Custom Colors")
        self.manualColorCheck.setObjectName("switchToggle")
        self.manualColorCheck.setChecked(False)
        self.manualColorCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.manualColorCheck)

        layout.addWidget(option_card)

        table = QTableWidget(self.column_count, 7)
        table.setHorizontalHeaderLabels([
            "Export",
            "Y Column",
            "Y Axis Name",
            "Chart Title",
            "PNG Suffix",
            "Color",
            "Band Rules",
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setMinimumSectionSize(110)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)

        for row in range(self.column_count):

            col_number = row + 1

            export_check = QCheckBox()
            export_check.setChecked(True)
            check_host = QWidget()
            check_layout = QHBoxLayout(check_host)
            check_layout.setContentsMargins(8, 0, 8, 0)
            check_layout.addWidget(export_check)
            check_layout.addStretch(1)
            table.setCellWidget(row, 0, check_host)

            col_item = QTableWidgetItem(f"Column {col_number}")
            table.setItem(row, 1, col_item)

            default_y_name = self.header_names.get(col_number, f"Y{col_number}")
            y_name_edit = QLineEdit(default_y_name)
            table.setCellWidget(row, 2, y_name_edit)

            title_edit = QLineEdit(default_y_name)
            table.setCellWidget(row, 3, title_edit)

            default_suffix = self.header_names.get(col_number, f"col{col_number}")
            suffix_edit = QLineEdit(default_suffix)
            table.setCellWidget(row, 4, suffix_edit)

            color_btn = QPushButton("Pick")
            color_btn.setMinimumWidth(72)
            default_color = self._palette[(col_number - 1) % len(self._palette)]
            self._set_color_button_style(color_btn, default_color)
            color_btn.clicked.connect(
                lambda _checked=False, c=col_number: self._pick_export_color(c)
            )
            table.setCellWidget(row, 5, color_btn)

            band_rule_edit = QLineEdit("")
            band_rule_edit.setPlaceholderText("e.g. 15%,3,0.5 (max 3)")
            table.setCellWidget(row, 6, band_rule_edit)

            self._row_controls.append(
                {
                    "col": col_number,
                    "export_check": export_check,
                    "y_name": y_name_edit,
                    "chart_title": title_edit,
                    "suffix": suffix_edit,
                    "color": default_color,
                    "color_btn": color_btn,
                    "band_rules": band_rule_edit,
                }
            )

        self.table = table

        guide = QLabel(
            "Tip: when X Column is 0, X uses point index (1..N). Band Rules supports percent and absolute values."
        )
        guide.setObjectName("dialogSubtitle")
        layout.addWidget(guide)

        layout.addWidget(self.table, 1)

        self._refresh_toggle_state()
        self._update_x_time_unit_button_text()
        self._update_secondary_x_time_unit_button_text()
        self._update_x_source_unit_button_text()
        self._update_secondary_x_source_unit_button_text()
        self._on_x_column_changed(self.xColumnSpin.value())
        self._refresh_row_state()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_toggle_state(self):

        sensor_enabled = self.sensorModeCheck.isChecked()
        point_mode_enabled = self.pointModeRadio.isChecked()

        self.lineWidthFrame.setVisible(not point_mode_enabled)
        self.pointSizeFrame.setVisible(point_mode_enabled)

        self.sensorColumnSpin.setEnabled(sensor_enabled)
        self.sensorChildFrame.setVisible(sensor_enabled)
        self.sensorBandEnabledCheck.setEnabled(sensor_enabled)
        band_controls_enabled = sensor_enabled and self.sensorBandEnabledCheck.isChecked()
        self.sensorBandDefaultEdit.setEnabled(band_controls_enabled)
        self.bandAlphaFrame.setVisible(sensor_enabled)
        self.bandAlphaSpin.setEnabled(band_controls_enabled)

        manual_color_enabled = self.manualColorCheck.isChecked()
        for row_data in self._row_controls:
            row_data["color_btn"].setEnabled(manual_color_enabled)

        if not sensor_enabled:
            self.sensorBandEnabledCheck.setChecked(False)

        for row_data in self._row_controls:
            row_data["band_rules"].setEnabled(band_controls_enabled)

        secondary_enabled = self.secondaryPlotCheck.isChecked()
        self.secondaryFileBrowseButton.setEnabled(secondary_enabled)
        self.secondaryXColumnSpin.setEnabled(secondary_enabled)
        self.secondaryXAxisNameEdit.setEnabled(secondary_enabled)
        self.secondaryYColumnsEdit.setEnabled(secondary_enabled)
        self.secondaryYAxisNamesEdit.setEnabled(secondary_enabled)
        self.secondaryColorsEdit.setEnabled(secondary_enabled)
        self.secondaryPickColorsButton.setEnabled(secondary_enabled)
        self.secondaryMultiYAxisCheck.setEnabled(secondary_enabled)
        self.secondaryLineModeRadio.setEnabled(secondary_enabled)
        self.secondaryPointModeRadio.setEnabled(secondary_enabled)
        secondary_point_mode_enabled = self.secondaryPointModeRadio.isChecked()
        self.secondaryLineWidthFrame.setVisible(not secondary_point_mode_enabled)
        self.secondaryPointSizeFrame.setVisible(secondary_point_mode_enabled)
        self.secondaryLineWidthSpin.setEnabled(secondary_enabled and (not secondary_point_mode_enabled))
        self.secondaryPointSizeSpin.setEnabled(secondary_enabled and secondary_point_mode_enabled)

        self._sync_secondary_control_state()

        self._refresh_row_state()

    def _refresh_row_state(self):

        x_col = self.xColumnSpin.value()
        sensor_enabled = self.sensorModeCheck.isChecked()
        sensor_col = self.sensorColumnSpin.value()

        for row_data in self._row_controls:

            col = row_data["col"]

            disabled_for_x = (x_col != 0 and col == x_col)
            disabled_for_sensor = (sensor_enabled and col == sensor_col)

            blocked = disabled_for_x or disabled_for_sensor

            row_data["export_check"].setEnabled(not blocked)

            if blocked:
                row_data["export_check"].setChecked(False)

        self._sync_secondary_control_state()

    def _set_color_button_style(self, button, color_hex):

        button.setText(color_hex.upper())
        button.setStyleSheet(
            "QPushButton{"
            f"background:{color_hex};"
            "color:#0f172a;"
            "border:1px solid #94a3b8;"
            "border-radius:8px;"
            "padding:6px 8px;"
            "font-weight:600;"
            "}"
        )

    def _pick_export_color(self, col_number):

        row_data = next(
            (row for row in self._row_controls if row["col"] == col_number),
            None
        )

        if row_data is None:
            return

        selected = QColorDialog.getColor(
            QColor(row_data["color"]),
            self,
            f"Line Color - Column {col_number}"
        )

        if not selected.isValid():
            return

        color_hex = selected.name()
        row_data["color"] = color_hex
        self._set_color_button_style(row_data["color_btn"], color_hex)

    def _pick_secondary_colors(self):

        max_col = getattr(self, "_secondary_column_count", self.column_count)
        blocked_col = self.secondaryXColumnSpin.value() if self.secondaryXColumnSpin.value() != 0 else 0
        y_cols = self._parse_secondary_column_list(
            self.secondaryYColumnsEdit.text(),
            max_col,
            blocked_col,
        )

        if not y_cols:
            return

        existing_colors = self._parse_secondary_csv_text(
            self.secondaryColorsEdit.text(),
            len(y_cols),
        )

        selected_colors = []

        for idx, y_col in enumerate(y_cols):
            default_color = ""
            if idx < len(existing_colors):
                default_color = existing_colors[idx]

            if not default_color or not QColor(default_color).isValid():
                default_color = self._palette[idx % len(self._palette)]

            picked = QColorDialog.getColor(
                QColor(default_color),
                self,
                f"Chart 2 Color - Y{y_col}"
            )

            if picked.isValid():
                selected_colors.append(picked.name())
            else:
                selected_colors.append(default_color)

        if selected_colors:
            self.secondaryColorsEdit.setText(",".join(selected_colors))

    def _update_x_time_unit_button_text(self):

        label = self._time_unit_labels.get(self._x_time_unit, "Auto")
        self.xTimeUnitButton.setText(f"X Tick Unit: {label}")

    def _update_secondary_x_time_unit_button_text(self):

        label = self._time_unit_labels.get(self._secondary_x_time_unit, "Auto")
        self.secondaryXTimeUnitButton.setText(f"Chart 2 X Tick Unit: {label}")

    def _update_x_source_unit_button_text(self):

        label = self._source_unit_labels.get(self._x_source_unit, "s")
        self.xSourceUnitButton.setText(f"X Source Unit: {label}")

    def _update_secondary_x_source_unit_button_text(self):

        label = self._source_unit_labels.get(self._secondary_x_source_unit, "s")
        self.secondaryXSourceUnitButton.setText(f"Chart 2 X Source Unit: {label}")

    def _cycle_x_time_unit(self):

        idx = self._time_unit_choices.index(self._x_time_unit)
        self._x_time_unit = self._time_unit_choices[(idx + 1) % len(self._time_unit_choices)]
        self._update_x_time_unit_button_text()

    def _cycle_secondary_x_time_unit(self):

        idx = self._time_unit_choices.index(self._secondary_x_time_unit)
        self._secondary_x_time_unit = self._time_unit_choices[(idx + 1) % len(self._time_unit_choices)]
        self._update_secondary_x_time_unit_button_text()

    def _cycle_x_source_unit(self):

        idx = self._source_unit_choices.index(self._x_source_unit)
        self._x_source_unit = self._source_unit_choices[(idx + 1) % len(self._source_unit_choices)]
        self._update_x_source_unit_button_text()

    def _cycle_secondary_x_source_unit(self):

        idx = self._source_unit_choices.index(self._secondary_x_source_unit)
        self._secondary_x_source_unit = self._source_unit_choices[(idx + 1) % len(self._source_unit_choices)]
        self._update_secondary_x_source_unit_button_text()

    def _on_x_column_changed(self, value):

        if value == 0:
            self.xAxisNameEdit.setText("Index")
        elif value in self.header_names:
            self.xAxisNameEdit.setText(self.header_names[value])

        self._refresh_row_state()

    def _choose_secondary_file(self):

        start_dir = os.path.dirname(self.file_path) or ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select File for Chart 2",
            start_dir,
            "All Files (*);;Text Files (*.txt *.csv)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )

        if not file_path:
            return

        self._set_secondary_file(file_path)

    def _detect_file_column_count(self, file_path):

        rows, _, _, _ = _get_cached_file_data(file_path)
        max_cols = 0

        for row_index, row in enumerate(rows, start=1):
            max_cols = max(max_cols, len(row))

            if row_index >= 10000:
                break

        return max_cols

    def _detect_file_header_names(self, file_path, column_count):

        header_names = {}

        try:
            _, _, first_row, has_header = _get_cached_file_data(file_path)

            if not first_row or not has_header:
                return header_names

            for idx in range(min(column_count, len(first_row))):
                raw_name = (first_row[idx] or "").strip()

                if not raw_name:
                    continue

                header_names[idx + 1] = raw_name

        except Exception:
            return {}

        return header_names

    def _set_secondary_file(self, file_path):

        self.secondaryFileEdit.setText(file_path)

        column_count = max(1, self._detect_file_column_count(file_path))
        self._secondary_column_count = column_count
        self._secondary_header_names = self._detect_file_header_names(file_path, column_count)

        self.secondaryXColumnSpin.setRange(0, column_count)

        self.secondaryXColumnSpin.setValue(1 if column_count >= 1 else 0)
        self._on_secondary_x_column_changed(self.secondaryXColumnSpin.value())

        default_y = 2 if column_count >= 2 else 1
        self.secondaryYColumnsEdit.setText(str(default_y))

        if default_y in self._secondary_header_names:
            self.secondaryYAxisNamesEdit.setText(self._secondary_header_names[default_y])
        else:
            self.secondaryYAxisNamesEdit.setText(f"Y{default_y}")

        self._sync_secondary_control_state()

    def _on_secondary_x_column_changed(self, value):

        if value == 0:
            self.secondaryXAxisNameEdit.setText("Index")
        elif hasattr(self, "_secondary_header_names") and value in self._secondary_header_names:
            self.secondaryXAxisNameEdit.setText(self._secondary_header_names[value])
        else:
            self.secondaryXAxisNameEdit.setText(f"X{value}")

        self._sync_secondary_control_state()

    def _parse_secondary_column_list(self, text, max_col, blocked_col=0):

        columns = []

        for token in (text or "").split(","):
            value = token.strip()

            if not value or not value.isdigit():
                continue

            col_index = int(value)
            if col_index < 1 or col_index > max_col:
                continue

            if blocked_col and col_index == blocked_col:
                continue

            if col_index not in columns:
                columns.append(col_index)

            if len(columns) >= 6:
                break

        return columns

    def _parse_secondary_csv_text(self, text, count):

        tokens = [
            token.strip()
            for token in (text or "").split(",")
            if token.strip()
        ]

        if not tokens:
            return []

        values = []
        for idx in range(count):
            if idx < len(tokens):
                values.append(tokens[idx])
            else:
                values.append(tokens[-1])

        return values

    def _sync_secondary_control_state(self):

        if not hasattr(self, "secondaryYColumnsEdit"):
            return

        if not self.secondaryPlotCheck.isChecked():
            return

        max_col = getattr(self, "_secondary_column_count", self.column_count)
        blocked_col = self.secondaryXColumnSpin.value() if self.secondaryXColumnSpin.value() != 0 else 0
        parsed_cols = self._parse_secondary_column_list(
            self.secondaryYColumnsEdit.text(),
            max_col,
            blocked_col,
        )

        if not parsed_cols:
            fallback = 1
            for candidate in range(1, max_col + 1):
                if blocked_col and candidate == blocked_col:
                    continue
                fallback = candidate
                break

            parsed_cols = [fallback]

        cleaned_cols = ",".join(str(col) for col in parsed_cols)
        if self.secondaryYColumnsEdit.text().strip() != cleaned_cols:
            self.secondaryYColumnsEdit.setText(cleaned_cols)

        default_axis_names = []
        for col in parsed_cols:
            if hasattr(self, "_secondary_header_names") and col in self._secondary_header_names:
                default_axis_names.append(self._secondary_header_names[col])
            else:
                default_axis_names.append(f"Y{col}")

        raw_axis_names = [token.strip() for token in self.secondaryYAxisNamesEdit.text().split(",") if token.strip()]
        if not raw_axis_names:
            self.secondaryYAxisNamesEdit.setText(",".join(default_axis_names))

        raw_colors = [token.strip() for token in self.secondaryColorsEdit.text().split(",") if token.strip()]
        if not raw_colors:
            self.secondaryColorsEdit.setText("#ef4444")

    def get_config(self):

        x_col = self.xColumnSpin.value()
        x_axis_name = self.xAxisNameEdit.text().strip() or "X values"
        export_prefix = self.exportPrefixEdit.text().strip()

        if not export_prefix:
            export_prefix = os.path.splitext(os.path.basename(self.file_path))[0]

        exports = []

        for row_data in self._row_controls:

            if not row_data["export_check"].isChecked():
                continue

            y_col = row_data["col"]
            y_name = row_data["y_name"].text().strip() or f"Y{y_col}"
            suffix = row_data["suffix"].text().strip() or f"col{y_col}"

            exports.append(
                {
                    "y_col": y_col,
                    "y_axis_name": y_name,
                    "chart_title": row_data["chart_title"].text().strip(),
                    "png_suffix": suffix,
                    "color": row_data["color"],
                    "band_rules": row_data["band_rules"].text().strip(),
                }
            )

        return {
            "file_path": self.file_path,
            "x_col": x_col,
            "x_axis_name": x_axis_name,
            "x_time_unit": self._x_time_unit,
            "x_source_unit": self._x_source_unit,
            "export_prefix": export_prefix,
            "figure_title": self.figureTitleEdit.text().strip(),
            "sensor_mode": self.sensorModeCheck.isChecked(),
            "sensor_col": self.sensorColumnSpin.value(),
            "manual_color_mode": self.manualColorCheck.isChecked(),
            "plot_mode": "point" if self.pointModeRadio.isChecked() else "line",
            "line_width": self.lineWidthSpin.value(),
            "point_size": self.pointSizeSpin.value(),
            "sensor_band_enabled": self.sensorBandEnabledCheck.isChecked(),
            "sensor_band_default_rules": self.sensorBandDefaultEdit.text().strip(),
            "sensor_band_alpha": self.bandAlphaSpin.value() / 100.0,
            "secondary_plot": {
                "enabled": self.secondaryPlotCheck.isChecked(),
                "file_path": self.secondaryFileEdit.text().strip(),
                "x_col": self.secondaryXColumnSpin.value(),
                "y_cols": self._parse_secondary_column_list(
                    self.secondaryYColumnsEdit.text(),
                    getattr(self, "_secondary_column_count", self.column_count),
                    self.secondaryXColumnSpin.value() if self.secondaryXColumnSpin.value() != 0 else 0,
                ),
                "x_axis_name": self.secondaryXAxisNameEdit.text().strip() or "X values",
                "x_time_unit": self._secondary_x_time_unit,
                "x_source_unit": self._secondary_x_source_unit,
                "title": self.secondaryTitleEdit.text().strip() or "Chart 2",
                "y_axis_names": [
                    token.strip()
                    for token in self.secondaryYAxisNamesEdit.text().split(",")
                    if token.strip()
                ],
                "colors": [
                    token.strip()
                    for token in self.secondaryColorsEdit.text().split(",")
                    if token.strip()
                ],
                "multi_y": self.secondaryMultiYAxisCheck.isChecked(),
                "plot_mode": "point" if self.secondaryPointModeRadio.isChecked() else "line",
                "line_width": self.secondaryLineWidthSpin.value(),
                "point_size": self.secondaryPointSizeSpin.value(),
            },
            "exports": exports,
        }

class SensorDeviationBatchExportConfigDialog(SingleFileBatchExportConfigDialog):

    def __init__(
        self,
        file_path,
        column_count,
        default_sensor_col,
        header_names=None,
        parent=None,
    ):

        super().__init__(
            file_path,
            column_count,
            default_sensor_col,
            header_names,
            parent,
        )
        self.setWindowTitle("Sensor Deviation PNG Export")

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel(
            "Group sequential sensor rows, calculate average plus max/min deviation %, then export one PNG per chosen Y column."
        )
        tip.setObjectName("dialogSubtitle")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        file_info = QLabel(
            f"File: {os.path.basename(self.file_path)} | Detected Columns: {self.column_count}"
        )
        file_info.setObjectName("dialogSubtitle")
        file_info.setWordWrap(True)
        layout.addWidget(file_info)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(10)

        meta_layout.addWidget(QLabel("X Column (0=Group Index)"))
        self.xColumnSpin = QSpinBox()
        self.xColumnSpin.setRange(0, self.column_count)
        self.xColumnSpin.setValue(1 if self.column_count >= 1 else 0)
        self.xColumnSpin.valueChanged.connect(self._on_x_column_changed)
        meta_layout.addWidget(self.xColumnSpin)

        meta_layout.addWidget(QLabel("X Axis Name"))
        default_x_name = self.header_names.get(1, "X values")
        self.xAxisNameEdit = QLineEdit(default_x_name)
        meta_layout.addWidget(self.xAxisNameEdit, 2)

        self.xTimeUnitButton = QPushButton()
        self.xTimeUnitButton.clicked.connect(self._cycle_x_time_unit)
        meta_layout.addWidget(self.xTimeUnitButton)

        self.xSourceUnitButton = QPushButton()
        self.xSourceUnitButton.clicked.connect(self._cycle_x_source_unit)
        meta_layout.addWidget(self.xSourceUnitButton)

        meta_layout.addWidget(QLabel("Export Prefix"))
        base_name = os.path.splitext(os.path.basename(self.file_path))[0]
        self.exportPrefixEdit = QLineEdit(f"{base_name}_deviation")
        meta_layout.addWidget(self.exportPrefixEdit, 2)

        meta_layout.addWidget(QLabel("Overall Title"))
        self.figureTitleEdit = QLineEdit(base_name)
        meta_layout.addWidget(self.figureTitleEdit, 3)

        layout.addLayout(meta_layout)

        sensor_card = QFrame()
        sensor_card.setObjectName("card")
        sensor_layout = QVBoxLayout(sensor_card)
        sensor_layout.setContentsMargins(10, 10, 10, 10)
        sensor_layout.setSpacing(5)

        sensor_title = QLabel("Deviation Analysis")
        sensor_title.setObjectName("sectionTitle")
        sensor_layout.addWidget(sensor_title)

        sensor_row = QHBoxLayout()
        sensor_row.setContentsMargins(22, 0, 0, 0)
        sensor_row.setSpacing(10)

        sensor_row.addWidget(QLabel("Sensor Column"))
        self.sensorColumnSpin = QSpinBox()
        self.sensorColumnSpin.setRange(1, self.column_count)
        self.sensorColumnSpin.setValue(self.default_sensor_col)
        self.sensorColumnSpin.valueChanged.connect(self._refresh_row_state)
        self.sensorColumnSpin.valueChanged.connect(self._update_detected_sensor_count)
        sensor_row.addWidget(self.sensorColumnSpin)

        self.detectedSensorCountLabel = QLabel("Detected Sensors: -")
        self.detectedSensorCountLabel.setObjectName("dialogSubtitle")
        sensor_row.addWidget(self.detectedSensorCountLabel)

        sensor_row.addWidget(QLabel("Deviation Axis Name"))
        self.deviationAxisNameEdit = QLineEdit("Deviation (%)")
        sensor_row.addWidget(self.deviationAxisNameEdit, 2)
        sensor_row.addStretch(1)
        sensor_layout.addLayout(sensor_row)

        sensor_hint = QLabel(
            "Sensor count is auto-detected. Each group uses the first valid sensor row as the timestamp anchor, then computes max/min deviation percentage against the group average."
        )
        sensor_hint.setObjectName("dialogSubtitle")
        sensor_hint.setWordWrap(True)
        sensor_layout.addWidget(sensor_hint)

        layout.addWidget(sensor_card)

        secondary_card = QFrame()
        secondary_card.setObjectName("card")
        secondary_layout = QVBoxLayout(secondary_card)
        secondary_layout.setContentsMargins(12, 12, 12, 12)
        secondary_layout.setSpacing(8)

        secondary_title = QLabel("Chart 2 (Bottom Subplot)")
        secondary_title.setObjectName("sectionTitle")
        secondary_layout.addWidget(secondary_title)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(22, 0, 0, 0)
        secondary_row.setSpacing(10)

        self.secondaryPlotCheck = QCheckBox("Include Chart 2 below each exported image")
        self.secondaryPlotCheck.setObjectName("switchToggle")
        self.secondaryPlotCheck.setChecked(False)
        self.secondaryPlotCheck.stateChanged.connect(self._refresh_toggle_state)
        secondary_row.addWidget(self.secondaryPlotCheck)
        secondary_row.addStretch(1)
        secondary_layout.addLayout(secondary_row)

        secondary_data_hint = QLabel("Chart 2 Data Setup")
        secondary_data_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_data_hint)

        secondary_file_row = QHBoxLayout()
        secondary_file_row.setContentsMargins(22, 0, 0, 0)
        secondary_file_row.setSpacing(10)

        secondary_file_row.addWidget(QLabel("Chart 2 File"))
        self.secondaryFileEdit = QLineEdit("")
        self.secondaryFileEdit.setReadOnly(True)
        self.secondaryFileEdit.setPlaceholderText("Select a second file")
        secondary_file_row.addWidget(self.secondaryFileEdit, 3)

        self.secondaryFileBrowseButton = QPushButton("Browse")
        self.secondaryFileBrowseButton.clicked.connect(self._choose_secondary_file)
        secondary_file_row.addWidget(self.secondaryFileBrowseButton)

        secondary_layout.addLayout(secondary_file_row)

        secondary_meta = QHBoxLayout()
        secondary_meta.setContentsMargins(22, 0, 0, 0)
        secondary_meta.setSpacing(10)

        secondary_meta.addWidget(QLabel("Chart 2 X Column (0=Index)"))
        self.secondaryXColumnSpin = QSpinBox()
        self.secondaryXColumnSpin.setRange(0, self.column_count)
        self.secondaryXColumnSpin.setValue(1 if self.column_count >= 1 else 0)
        self.secondaryXColumnSpin.valueChanged.connect(self._on_secondary_x_column_changed)
        secondary_meta.addWidget(self.secondaryXColumnSpin)

        secondary_meta.addWidget(QLabel("Chart 2 X Axis Name"))
        self.secondaryXAxisNameEdit = QLineEdit("X values")
        secondary_meta.addWidget(self.secondaryXAxisNameEdit, 2)

        self.secondaryXTimeUnitButton = QPushButton()
        self.secondaryXTimeUnitButton.clicked.connect(self._cycle_secondary_x_time_unit)
        secondary_meta.addWidget(self.secondaryXTimeUnitButton)

        self.secondaryXSourceUnitButton = QPushButton()
        self.secondaryXSourceUnitButton.clicked.connect(self._cycle_secondary_x_source_unit)
        secondary_meta.addWidget(self.secondaryXSourceUnitButton)

        secondary_layout.addLayout(secondary_meta)

        secondary_y_row = QHBoxLayout()
        secondary_y_row.setContentsMargins(22, 0, 0, 0)
        secondary_y_row.setSpacing(10)

        secondary_y_row.addWidget(QLabel("Chart 2 Y Columns"))
        self.secondaryYColumnsEdit = QLineEdit("2")
        self.secondaryYColumnsEdit.setPlaceholderText("e.g. 2,3,5")
        self.secondaryYColumnsEdit.editingFinished.connect(self._sync_secondary_control_state)
        secondary_y_row.addWidget(self.secondaryYColumnsEdit, 2)

        secondary_y_row.addWidget(QLabel("Y Axis Names"))
        self.secondaryYAxisNamesEdit = QLineEdit("Y2")
        self.secondaryYAxisNamesEdit.setPlaceholderText("e.g. Y2,Temp,Pressure")
        secondary_y_row.addWidget(self.secondaryYAxisNamesEdit, 2)

        secondary_y_row.addWidget(QLabel("Line Colors"))
        self.secondaryColorsEdit = QLineEdit("#ef4444")
        self.secondaryColorsEdit.setPlaceholderText("e.g. #ef4444,#0ea5e9,#22c55e")
        secondary_y_row.addWidget(self.secondaryColorsEdit, 2)

        self.secondaryPickColorsButton = QPushButton("Pick Colors")
        self.secondaryPickColorsButton.clicked.connect(self._pick_secondary_colors)
        secondary_y_row.addWidget(self.secondaryPickColorsButton)

        secondary_layout.addLayout(secondary_y_row)

        secondary_title_row = QHBoxLayout()
        secondary_title_row.setContentsMargins(22, 0, 0, 0)
        secondary_title_row.setSpacing(10)
        secondary_title_row.addWidget(QLabel("Chart 2 Title"))
        self.secondaryTitleEdit = QLineEdit("Chart 2")
        secondary_title_row.addWidget(self.secondaryTitleEdit, 2)
        secondary_title_row.addStretch(1)
        secondary_layout.addLayout(secondary_title_row)

        secondary_color_hint = QLabel("Colors map to Y columns in order (first color -> first Y column).")
        secondary_color_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_color_hint)

        secondary_style_hint = QLabel("Chart 2 Style (applies to all Chart 2 Y columns)")
        secondary_style_hint.setObjectName("dialogSubtitle")
        secondary_layout.addWidget(secondary_style_hint)

        secondary_axis_row = QHBoxLayout()
        secondary_axis_row.setContentsMargins(22, 0, 0, 0)
        secondary_axis_row.setSpacing(10)

        self.secondaryMultiYAxisCheck = QCheckBox("Chart 2 Multi-Y Axis")
        self.secondaryMultiYAxisCheck.setObjectName("switchToggle")
        self.secondaryMultiYAxisCheck.setChecked(True)
        secondary_axis_row.addWidget(self.secondaryMultiYAxisCheck)
        secondary_axis_row.addStretch(1)
        secondary_layout.addLayout(secondary_axis_row)

        secondary_style_row = QHBoxLayout()
        secondary_style_row.setContentsMargins(22, 0, 0, 0)
        secondary_style_row.setSpacing(10)

        secondary_style_row.addWidget(QLabel("Render Mode"))
        self.secondaryPlotModeGroup = QButtonGroup(self)
        self.secondaryLineModeRadio = QRadioButton("Line")
        self.secondaryPointModeRadio = QRadioButton("Point")
        self.secondaryLineModeRadio.setChecked(True)
        self.secondaryLineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.secondaryPointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.secondaryPlotModeGroup.addButton(self.secondaryLineModeRadio, 1)
        self.secondaryPlotModeGroup.addButton(self.secondaryPointModeRadio, 2)
        secondary_style_row.addWidget(self.secondaryLineModeRadio)
        secondary_style_row.addWidget(self.secondaryPointModeRadio)

        self.secondaryLineWidthFrame = QFrame()
        secondary_line_width_layout = QHBoxLayout(self.secondaryLineWidthFrame)
        secondary_line_width_layout.setContentsMargins(0, 0, 0, 0)
        secondary_line_width_layout.setSpacing(8)
        secondary_line_width_layout.addWidget(QLabel("Line Width"))
        self.secondaryLineWidthSpin = QDoubleSpinBox()
        self.secondaryLineWidthSpin.setRange(0.5, 8.0)
        self.secondaryLineWidthSpin.setSingleStep(0.1)
        self.secondaryLineWidthSpin.setDecimals(1)
        self.secondaryLineWidthSpin.setValue(1.8)
        secondary_line_width_layout.addWidget(self.secondaryLineWidthSpin)
        secondary_style_row.addWidget(self.secondaryLineWidthFrame)

        self.secondaryPointSizeFrame = QFrame()
        secondary_point_size_layout = QHBoxLayout(self.secondaryPointSizeFrame)
        secondary_point_size_layout.setContentsMargins(0, 0, 0, 0)
        secondary_point_size_layout.setSpacing(8)
        secondary_point_size_layout.addWidget(QLabel("Point Size"))
        self.secondaryPointSizeSpin = QSpinBox()
        self.secondaryPointSizeSpin.setRange(1, 220)
        self.secondaryPointSizeSpin.setSingleStep(2)
        self.secondaryPointSizeSpin.setValue(24)
        secondary_point_size_layout.addWidget(self.secondaryPointSizeSpin)
        secondary_style_row.addWidget(self.secondaryPointSizeFrame)
        secondary_style_row.addStretch(1)

        secondary_layout.addLayout(secondary_style_row)
        layout.addWidget(secondary_card)

        option_card = QFrame()
        option_card.setObjectName("card")
        option_layout = QVBoxLayout(option_card)
        option_layout.setContentsMargins(12, 12, 12, 12)
        option_layout.setSpacing(8)

        option_title = QLabel("Chart 1 Style")
        option_title.setObjectName("sectionTitle")
        option_layout.addWidget(option_title)

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(22, 0, 0, 0)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(QLabel("Render Mode"))

        self.plotModeGroup = QButtonGroup(self)
        self.lineModeRadio = QRadioButton("Line")
        self.pointModeRadio = QRadioButton("Point")
        self.lineModeRadio.setChecked(True)
        self.lineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.pointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.plotModeGroup.addButton(self.lineModeRadio, 1)
        self.plotModeGroup.addButton(self.pointModeRadio, 2)
        mode_layout.addWidget(self.lineModeRadio)
        mode_layout.addWidget(self.pointModeRadio)
        mode_layout.addStretch(1)
        option_layout.addLayout(mode_layout)

        self.lineWidthFrame = QFrame()
        line_width_layout = QHBoxLayout(self.lineWidthFrame)
        line_width_layout.setContentsMargins(22, 0, 0, 0)
        line_width_layout.setSpacing(10)
        line_width_layout.addWidget(QLabel("Line Width"))
        self.lineWidthSpin = QDoubleSpinBox()
        self.lineWidthSpin.setRange(0.5, 8.0)
        self.lineWidthSpin.setSingleStep(0.1)
        self.lineWidthSpin.setDecimals(1)
        self.lineWidthSpin.setValue(1.8)
        line_width_layout.addWidget(self.lineWidthSpin)
        line_width_layout.addStretch(1)
        option_layout.addWidget(self.lineWidthFrame)

        self.pointSizeFrame = QFrame()
        point_size_layout = QHBoxLayout(self.pointSizeFrame)
        point_size_layout.setContentsMargins(22, 0, 0, 0)
        point_size_layout.setSpacing(10)
        point_size_layout.addWidget(QLabel("Point Size"))
        self.pointSizeSpin = QSpinBox()
        self.pointSizeSpin.setRange(1, 220)
        self.pointSizeSpin.setSingleStep(2)
        self.pointSizeSpin.setValue(24)
        point_size_layout.addWidget(self.pointSizeSpin)
        point_size_layout.addStretch(1)
        option_layout.addWidget(self.pointSizeFrame)

        layout.addWidget(option_card)

        table = QTableWidget(self.column_count, 4)
        table.setHorizontalHeaderLabels([
            "Export",
            "Y Column",
            "Chart Title",
            "PNG Suffix",
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setMinimumSectionSize(110)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        self._row_controls = []

        for row in range(self.column_count):

            col_number = row + 1

            export_check = QCheckBox()
            export_check.setChecked(True)
            check_host = QWidget()
            check_layout = QHBoxLayout(check_host)
            check_layout.setContentsMargins(8, 0, 8, 0)
            check_layout.addWidget(export_check)
            check_layout.addStretch(1)
            table.setCellWidget(row, 0, check_host)

            col_item = QTableWidgetItem(f"Column {col_number}")
            table.setItem(row, 1, col_item)

            default_y_name = self.header_names.get(col_number, f"Y{col_number}")

            title_edit = QLineEdit(default_y_name)
            table.setCellWidget(row, 2, title_edit)

            default_suffix = self.header_names.get(col_number, f"col{col_number}")
            suffix_edit = QLineEdit(f"{default_suffix}_deviation")
            table.setCellWidget(row, 3, suffix_edit)

            self._row_controls.append(
                {
                    "col": col_number,
                    "export_check": export_check,
                    "chart_title": title_edit,
                    "suffix": suffix_edit,
                }
            )

        self.table = table

        guide = QLabel(
            "Tip: X uses the first valid row in each sensor group. The chart only includes two lines: Max Deviation % (red) and Min Deviation % (blue)."
        )
        guide.setObjectName("dialogSubtitle")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        layout.addWidget(self.table, 1)

        self._refresh_toggle_state()
        self._update_x_time_unit_button_text()
        self._update_secondary_x_time_unit_button_text()
        self._update_x_source_unit_button_text()
        self._update_secondary_x_source_unit_button_text()
        self._on_x_column_changed(self.xColumnSpin.value())
        self._refresh_row_state()
        self._update_detected_sensor_count()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_toggle_state(self):

        point_mode_enabled = self.pointModeRadio.isChecked()

        self.lineWidthFrame.setVisible(not point_mode_enabled)
        self.pointSizeFrame.setVisible(point_mode_enabled)

        secondary_enabled = self.secondaryPlotCheck.isChecked()
        self.secondaryFileBrowseButton.setEnabled(secondary_enabled)
        self.secondaryXColumnSpin.setEnabled(secondary_enabled)
        self.secondaryXAxisNameEdit.setEnabled(secondary_enabled)
        self.secondaryYColumnsEdit.setEnabled(secondary_enabled)
        self.secondaryYAxisNamesEdit.setEnabled(secondary_enabled)
        self.secondaryColorsEdit.setEnabled(secondary_enabled)
        self.secondaryPickColorsButton.setEnabled(secondary_enabled)
        self.secondaryTitleEdit.setEnabled(secondary_enabled)
        self.secondaryMultiYAxisCheck.setEnabled(secondary_enabled)
        self.secondaryLineModeRadio.setEnabled(secondary_enabled)
        self.secondaryPointModeRadio.setEnabled(secondary_enabled)
        self.secondaryXTimeUnitButton.setEnabled(secondary_enabled)
        self.secondaryXSourceUnitButton.setEnabled(secondary_enabled)

        secondary_point_mode_enabled = self.secondaryPointModeRadio.isChecked()
        self.secondaryLineWidthFrame.setVisible(not secondary_point_mode_enabled)
        self.secondaryPointSizeFrame.setVisible(secondary_point_mode_enabled)
        self.secondaryLineWidthSpin.setEnabled(secondary_enabled and (not secondary_point_mode_enabled))
        self.secondaryPointSizeSpin.setEnabled(secondary_enabled and secondary_point_mode_enabled)

        self._sync_secondary_control_state()
        self._refresh_row_state()

    def _refresh_row_state(self):

        x_col = self.xColumnSpin.value()
        sensor_col = self.sensorColumnSpin.value()

        for row_data in self._row_controls:

            col = row_data["col"]
            blocked = (x_col != 0 and col == x_col) or (col == sensor_col)

            row_data["export_check"].setEnabled(not blocked)
            if blocked:
                row_data["export_check"].setChecked(False)

        self._sync_secondary_control_state()

    def get_config(self):

        x_col = self.xColumnSpin.value()
        x_axis_name = self.xAxisNameEdit.text().strip() or "X values"
        export_prefix = self.exportPrefixEdit.text().strip()

        if not export_prefix:
            export_prefix = os.path.splitext(os.path.basename(self.file_path))[0]

        exports = []

        for row_data in self._row_controls:

            if not row_data["export_check"].isChecked():
                continue

            y_col = row_data["col"]
            suffix = row_data["suffix"].text().strip() or f"col{y_col}_deviation"

            exports.append(
                {
                    "y_col": y_col,
                    "chart_title": row_data["chart_title"].text().strip(),
                    "png_suffix": suffix,
                }
            )

        sensor_count = self._estimate_sensor_count(self.sensorColumnSpin.value())

        return {
            "file_path": self.file_path,
            "x_col": x_col,
            "x_axis_name": x_axis_name,
            "x_time_unit": self._x_time_unit,
            "x_source_unit": self._x_source_unit,
            "export_prefix": export_prefix,
            "figure_title": self.figureTitleEdit.text().strip(),
            "sensor_col": self.sensorColumnSpin.value(),
            "sensor_group_size": sensor_count,
            "deviation_axis_name": self.deviationAxisNameEdit.text().strip() or "Deviation (%)",
            "plot_mode": "point" if self.pointModeRadio.isChecked() else "line",
            "line_width": self.lineWidthSpin.value(),
            "point_size": self.pointSizeSpin.value(),
            "secondary_plot": {
                "enabled": self.secondaryPlotCheck.isChecked(),
                "file_path": self.secondaryFileEdit.text().strip(),
                "x_col": self.secondaryXColumnSpin.value(),
                "y_cols": self._parse_secondary_column_list(
                    self.secondaryYColumnsEdit.text(),
                    getattr(self, "_secondary_column_count", self.column_count),
                    self.secondaryXColumnSpin.value() if self.secondaryXColumnSpin.value() != 0 else 0,
                ),
                "x_axis_name": self.secondaryXAxisNameEdit.text().strip() or "X values",
                "x_time_unit": self._secondary_x_time_unit,
                "x_source_unit": self._secondary_x_source_unit,
                "title": self.secondaryTitleEdit.text().strip() or "Chart 2",
                "y_axis_names": [
                    token.strip()
                    for token in self.secondaryYAxisNamesEdit.text().split(",")
                    if token.strip()
                ],
                "colors": [
                    token.strip()
                    for token in self.secondaryColorsEdit.text().split(",")
                    if token.strip()
                ],
                "multi_y": self.secondaryMultiYAxisCheck.isChecked(),
                "plot_mode": "point" if self.secondaryPointModeRadio.isChecked() else "line",
                "line_width": self.secondaryLineWidthSpin.value(),
                "point_size": self.secondaryPointSizeSpin.value(),
            },
            "exports": exports,
        }

    def _estimate_sensor_count(self, sensor_col):

        if sensor_col <= 0:
            return 0

        sensor_index = sensor_col - 1
        seen = set()

        _, data_rows, _, _ = _get_cached_file_data(self.file_path)

        for row in data_rows:
            if len(row) <= sensor_index:
                continue

            sensor_value = row[sensor_index].strip()
            if not sensor_value:
                continue

            seen.add(sensor_value)

            if len(seen) >= 256:
                break

        return len(seen)

    def _update_detected_sensor_count(self):

        count = self._estimate_sensor_count(self.sensorColumnSpin.value())
        if count <= 0:
            self.detectedSensorCountLabel.setText("Detected Sensors: 0")
            return

        self.detectedSensorCountLabel.setText(f"Detected Sensors: {count}")


class ChartDialog(QDialog):

    def __init__(self, chart_configs, parent=None):

        super().__init__(parent)
        _enable_dialog_resize(self)

        self.chart_configs = chart_configs
        self._numeric_column_cache = {}
        self._plot_x_column_cache = {}
        self._sensor_series_cache = {}

        self.setWindowTitle("FusionPlot Studio - Chart Builder")
        self.resize(2160, 1720)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinMaxButtonsHint
        )

        self.figure = Figure(facecolor="#ffffff")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(
            self.canvas,
            self
        )

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.98,
                height_ratio=0.95,
                min_width=1280,
                min_height=820,
            ),
        )
        self._rainbow_phase = 0
        self._rainbow_timer = QTimer(self)
        self._rainbow_timer.timeout.connect(self._update_rainbow_progress)
        self._rainbow_timer.start(110)
        self._update_rainbow_progress()
        self._resize_sync_timer = QTimer(self)
        self._resize_sync_timer.setSingleShot(True)
        self._resize_sync_timer.timeout.connect(self._sync_canvas_to_viewport)
        QTimer.singleShot(0, self.render_charts)

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel(
            "Configure one XYZ chart: choose X/Y/Z columns, render as line or point, and optionally split by Sensor No."
        )
        tip.setObjectName("dialogSubtitle")
        layout.addWidget(tip)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(10)

        meta_layout.addWidget(QLabel("Chart Name"))
        self.chartTitleEdit = QLineEdit(f"XYZ Chart {self.chart_index}")
        meta_layout.addWidget(self.chartTitleEdit, 2)

        meta_layout.addWidget(QLabel("X Axis Name"))
        self.xAxisNameEdit = QLineEdit("X values")
        meta_layout.addWidget(self.xAxisNameEdit, 2)

        meta_layout.addWidget(QLabel("Y Axis Name"))
        self.yAxisNameEdit = QLineEdit("Y values")
        meta_layout.addWidget(self.yAxisNameEdit, 2)

        meta_layout.addWidget(QLabel("Z Axis Name"))
        self.zAxisNameEdit = QLineEdit("Z values")
        meta_layout.addWidget(self.zAxisNameEdit, 2)

        layout.addLayout(meta_layout)

        option_card = QFrame()
        option_card.setObjectName("card")
        option_layout = QVBoxLayout(option_card)
        option_layout.setContentsMargins(12, 12, 12, 12)
        option_layout.setSpacing(8)

        option_title = QLabel("Analysis Options")
        option_title.setObjectName("sectionTitle")
        option_layout.addWidget(option_title)

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(22, 0, 0, 0)
        mode_layout.setSpacing(12)
        mode_layout.addWidget(QLabel("Render Mode"))

        self.plotModeGroup = QButtonGroup(self)
        self.lineModeRadio = QRadioButton("Line")
        self.pointModeRadio = QRadioButton("Point")
        self.lineModeRadio.setChecked(True)
        self.lineModeRadio.toggled.connect(self._refresh_toggle_state)
        self.pointModeRadio.toggled.connect(self._refresh_toggle_state)
        self.plotModeGroup.addButton(self.lineModeRadio, 1)
        self.plotModeGroup.addButton(self.pointModeRadio, 2)
        mode_layout.addWidget(self.lineModeRadio)
        mode_layout.addWidget(self.pointModeRadio)
        mode_layout.addStretch(1)
        option_layout.addLayout(mode_layout)

        self.lineWidthFrame = QFrame()
        line_width_layout = QHBoxLayout(self.lineWidthFrame)
        line_width_layout.setContentsMargins(22, 0, 0, 0)
        line_width_layout.setSpacing(10)
        line_width_layout.addWidget(QLabel("Line Width"))
        self.lineWidthSpin = QDoubleSpinBox()
        self.lineWidthSpin.setRange(0.5, 8.0)
        self.lineWidthSpin.setSingleStep(0.1)
        self.lineWidthSpin.setDecimals(1)
        self.lineWidthSpin.setValue(1.8)
        line_width_layout.addWidget(self.lineWidthSpin)
        line_width_layout.addStretch(1)
        option_layout.addWidget(self.lineWidthFrame)

        self.pointSizeFrame = QFrame()
        point_size_layout = QHBoxLayout(self.pointSizeFrame)
        point_size_layout.setContentsMargins(22, 0, 0, 0)
        point_size_layout.setSpacing(10)
        point_size_layout.addWidget(QLabel("Point Size"))
        self.pointSizeSpin = QSpinBox()
        self.pointSizeSpin.setRange(1, 220)
        self.pointSizeSpin.setSingleStep(2)
        self.pointSizeSpin.setValue(24)
        point_size_layout.addWidget(self.pointSizeSpin)
        point_size_layout.addStretch(1)
        option_layout.addWidget(self.pointSizeFrame)

        self.sensorModeCheck = QCheckBox("Sensor No (Split Lines)")
        self.sensorModeCheck.setObjectName("switchToggle")
        self.sensorModeCheck.setChecked(False)
        self.sensorModeCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.sensorModeCheck)

        sensor_width_layout = QHBoxLayout()
        sensor_width_layout.setContentsMargins(22, 0, 0, 0)
        sensor_width_layout.setSpacing(10)
        sensor_width_layout.addWidget(QLabel("Line Width"))
        self.sensorLineWidthSpin = QDoubleSpinBox()
        self.sensorLineWidthSpin.setRange(0.5, 6.0)
        self.sensorLineWidthSpin.setSingleStep(0.1)
        self.sensorLineWidthSpin.setDecimals(1)
        self.sensorLineWidthSpin.setValue(1.8)
        sensor_width_layout.addWidget(self.sensorLineWidthSpin)
        sensor_width_layout.addStretch(1)
        option_layout.addLayout(sensor_width_layout)

        self.sensorChildFrame = QFrame()
        sensor_child_layout = QHBoxLayout(self.sensorChildFrame)
        sensor_child_layout.setContentsMargins(22, 0, 0, 0)
        sensor_child_layout.setSpacing(18)

        self.avg15Check = QCheckBox("Show ±15% Pink Band")
        self.avg15Check.setObjectName("switchToggle")
        self.avg15Check.setChecked(False)
        self.avg15Check.setEnabled(False)
        sensor_child_layout.addWidget(self.avg15Check)

        self.avg30Check = QCheckBox("Show ±30% Lavender Band")
        self.avg30Check.setObjectName("switchToggle")
        self.avg30Check.setChecked(False)
        self.avg30Check.setEnabled(False)
        sensor_child_layout.addWidget(self.avg30Check)
        sensor_child_layout.addStretch(1)
        option_layout.addWidget(self.sensorChildFrame)

        self.bandAlphaFrame = QFrame()
        alpha_layout = QHBoxLayout(self.bandAlphaFrame)
        alpha_layout.setContentsMargins(22, 0, 0, 0)
        alpha_layout.setSpacing(12)

        alpha_layout.addWidget(QLabel("Pink Band Alpha (%)"))
        self.bandAlpha15Spin = QSpinBox()
        self.bandAlpha15Spin.setRange(5, 60)
        self.bandAlpha15Spin.setValue(22)
        alpha_layout.addWidget(self.bandAlpha15Spin)

        alpha_layout.addWidget(QLabel("Lavender Band Alpha (%)"))
        self.bandAlpha30Spin = QSpinBox()
        self.bandAlpha30Spin.setRange(5, 60)
        self.bandAlpha30Spin.setValue(18)
        alpha_layout.addWidget(self.bandAlpha30Spin)

        alpha_layout.addStretch(1)
        option_layout.addWidget(self.bandAlphaFrame)

        self.manualColorCheck = QCheckBox("Custom Colors")
        self.manualColorCheck.setObjectName("switchToggle")
        self.manualColorCheck.setChecked(False)
        self.manualColorCheck.stateChanged.connect(self._refresh_toggle_state)
        option_layout.addWidget(self.manualColorCheck)

        self.colorChildFrame = QFrame()
        color_child_layout = QHBoxLayout(self.colorChildFrame)
        color_child_layout.setContentsMargins(22, 0, 0, 0)
        color_child_layout.setSpacing(18)

        self.sameFamilyColorCheck = QCheckBox("Same Family by TXT")
        self.sameFamilyColorCheck.setObjectName("switchToggle")
        self.sameFamilyColorCheck.setChecked(True)
        color_child_layout.addWidget(self.sameFamilyColorCheck)
        color_child_layout.addStretch(1)
        option_layout.addWidget(self.colorChildFrame)

        layout.addWidget(option_card)

        table = QTableWidget(len(self.file_paths), 5)
        table.setHorizontalHeaderLabels([
            "Data File",
            "X Column (0=Index)",
            "Y Column",
            "Z Column",
            "Sensor Column (0=off)",
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(46)
        table.horizontalHeader().setMinimumSectionSize(120)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

        for row, file_path in enumerate(self.file_paths):

            file_item = QTableWidgetItem(os.path.basename(file_path))
            file_item.setToolTip(file_path)
            table.setItem(row, 0, file_item)

            x_spin = QSpinBox()
            x_spin.setRange(0, 999)
            x_spin.setValue(0)
            table.setCellWidget(row, 1, x_spin)

            y_spin = QSpinBox()
            y_spin.setRange(1, 999)
            y_spin.setValue(2)
            table.setCellWidget(row, 2, y_spin)

            z_spin = QSpinBox()
            z_spin.setRange(1, 999)
            z_spin.setValue(3)
            table.setCellWidget(row, 3, z_spin)

            sensor_spin = QSpinBox()
            sensor_spin.setRange(0, 999)
            sensor_spin.setValue(self.default_sensor_cols[file_path])
            table.setCellWidget(row, 4, sensor_spin)

            self._rows.append(
                {
                    "file_path": file_path,
                    "x_col": x_spin,
                    "y_col": y_spin,
                    "z_col": z_spin,
                    "sensor_col": sensor_spin,
                }
            )

        self.table = table

        table_guide = QLabel(
            "Guide: set X Column to 0 to use the point index as X values."
        )
        table_guide.setObjectName("dialogSubtitle")
        layout.addWidget(table_guide)

        layout.addWidget(self.table, 1)

        self._refresh_toggle_state()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_toggle_state(self):

        sensor_enabled = self.sensorModeCheck.isChecked()
        point_mode_enabled = self.pointModeRadio.isChecked()

        self.table.setColumnHidden(4, not sensor_enabled)

        self.lineWidthFrame.setVisible(not point_mode_enabled)
        self.pointSizeFrame.setVisible(point_mode_enabled)

        for row_data in self._rows:
            row_data["sensor_col"].setEnabled(sensor_enabled)

    def get_configs(self):

        return [
            {
                "file_path": row_data["file_path"],
                "x_col": row_data["x_col"].value(),
                "y_col": row_data["y_col"].value(),
                "z_col": row_data["z_col"].value(),
                "sensor_col": row_data["sensor_col"].value(),
            }
            for row_data in self._rows
        ]

    def get_meta(self):

        return {
            "chart_title": self.chartTitleEdit.text().strip() or f"XYZ Chart {self.chart_index}",
            "x_axis_name": self.xAxisNameEdit.text().strip() or "X values",
            "y_axis_name": self.yAxisNameEdit.text().strip() or "Y values",
            "z_axis_name": self.zAxisNameEdit.text().strip() or "Z values",
            "sensor_mode": self.sensorModeCheck.isChecked(),
            "plot_mode": "point" if self.pointModeRadio.isChecked() else "line",
            "line_width": self.lineWidthSpin.value(),
            "point_size": self.pointSizeSpin.value(),
        }

    def changeEvent(self, event):

        super().changeEvent(event)

        if event.type() == event.Type.WindowStateChange:
            self._resize_sync_timer.start(30)

    def resizeEvent(self, event):

        super().resizeEvent(event)
        self._resize_sync_timer.start(90)

    def _sync_canvas_to_viewport(self):

        if not hasattr(self, "scrollArea"):
            return

        chart_count = max(1, len(self.chart_configs))
        viewport = self.scrollArea.viewport().size()

        if self.isMaximized():
            # Maximized mode: fit the screen viewport.
            target_width_px = max(760, viewport.width() - 18)
        else:
            # Normal mode: slightly wider than viewport so x-spacing is visibly larger.
            target_width_px = max(1020, int(viewport.width() * 1.25))

        per_chart_height = max(380, int(viewport.height() * 0.62))
        target_height_px = max(viewport.height() - 12, chart_count * per_chart_height)

        dpi = max(72.0, float(self.figure.get_dpi()))
        self.figure.set_size_inches(
            target_width_px / dpi,
            target_height_px / dpi,
            forward=True
        )
        self.canvas.setMinimumHeight(target_height_px)
        self.canvas.setMinimumWidth(target_width_px)

    def _update_rainbow_progress(self):

        self._rainbow_phase = (self._rainbow_phase + 11) % 360

        c1 = QColor.fromHsv(self._rainbow_phase % 360, 205, 245).name()
        c2 = QColor.fromHsv((self._rainbow_phase + 72) % 360, 205, 245).name()
        c3 = QColor.fromHsv((self._rainbow_phase + 150) % 360, 205, 245).name()
        c4 = QColor.fromHsv((self._rainbow_phase + 230) % 360, 205, 245).name()
        c5 = QColor.fromHsv((self._rainbow_phase + 305) % 360, 205, 245).name()

        self.renderProgress.setStyleSheet(
            "QProgressBar{"
            "border:1px solid #d5deea;"
            "border-radius:9px;"
            "background:#f8fafc;"
            "text-align:center;"
            "color:#334155;"
            "min-height:20px;"
            "}"
            "QProgressBar::chunk{"
            "border-radius:8px;"
            f"background:qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {c1}, stop:0.25 {c2}, stop:0.5 {c3}, stop:0.75 {c4}, stop:1 {c5});"
            "}"
        )

    def _legend_column_count(self, item_count):

        if item_count <= 8:
            return 1

        if item_count <= 16:
            return 2

        if item_count <= 24:
            return 3

        return 4

    def _build_grouped_legend_items(self, chart):

        grouped = {}

        for line in chart.get("lines", []):
            if line.get("mode") == "band":
                continue

            file_path = line.get("file_path")

            if not file_path:
                continue

            entry = grouped.setdefault(
                file_path,
                {
                    "color": line.get("color", "#334155"),
                    "sensor_ids": [],
                    "line_count": 0,
                }
            )

            entry["line_count"] += 1

            sensor_id = line.get("sensor_id")
            if sensor_id is not None and sensor_id not in entry["sensor_ids"]:
                entry["sensor_ids"].append(sensor_id)

        handles = []
        labels = []

        for file_path, entry in grouped.items():
            file_label = os.path.splitext(os.path.basename(file_path))[0]

            if entry["sensor_ids"]:
                label = f"{file_label} ({len(entry['sensor_ids'])} sensors)"
            elif entry["line_count"] > 1:
                label = f"{file_label} ({entry['line_count']} lines)"
            else:
                label = file_label

            handles.append(
                Line2D(
                    [0],
                    [0],
                    color=entry["color"],
                    linewidth=2.2
                )
            )
            labels.append(label)

        return handles, labels

    def _apply_adaptive_legend(self, axis, chart, handles=None, labels=None):

        if handles is None or labels is None:
            handles, labels = axis.get_legend_handles_labels()

        if not handles:
            return

        line_count = len(chart.get("lines", []))
        sensor_line_count = sum(
            1
            for line in chart.get("lines", [])
            if line.get("mode") == "sensor"
        )

        legend_title = None

        if line_count >= 24 or sensor_line_count >= 16:
            grouped_handles, grouped_labels = self._build_grouped_legend_items(chart)

            if grouped_handles:
                handles = grouped_handles
                labels = grouped_labels
                legend_title = f"Grouped by TXT ({line_count} lines)"

        band_legend_entries = []
        seen_band_keys = set()

        for line in chart.get("lines", []):
            if line.get("mode") != "band":
                continue

            band_key = (
                line.get("band_legend", "Avg range band"),
                line.get("color", "#c4b5fd"),
                line.get("edge_color", "#7c3aed"),
                line.get("alpha", 0.2),
            )

            if band_key in seen_band_keys:
                continue

            seen_band_keys.add(band_key)
            band_legend_entries.append(
                {
                    "label": band_key[0],
                    "color": band_key[1],
                    "edge_color": band_key[2],
                    "alpha": band_key[3],
                }
            )

        for entry in band_legend_entries:
            handles.append(
                Patch(
                    facecolor=entry["color"],
                    edgecolor=entry["edge_color"],
                    linewidth=1.4,
                    linestyle="--",
                    alpha=entry["alpha"],
                )
            )
            labels.append(entry["label"])

        label_count = max(1, len(labels))
        legend_ncol = min(
            label_count,
            max(1, self._legend_column_count(label_count) * 2)
        )

        legend = axis.legend(
            handles,
            labels,
            facecolor="#ffffff",
            edgecolor="#d1d5db",
            labelcolor="#111827",
            loc="upper center",
            bbox_to_anchor=(0.5, -0.19),
            borderaxespad=0.0,
            ncol=legend_ncol,
            fontsize=8,
            title=legend_title,
            title_fontsize=8,
        )

        for text in legend.get_texts():
            text.set_color("#111827")

        title = legend.get_title()
        if title is not None:
            title.set_color("#475569")

    def _chart_x_margin(self):

        return 0.0

    def _chart_layout_rect(self):

        if self.isMaximized():
            return (0.02, 0.10, 0.985, 0.985)

        return (0.03, 0.14, 0.94, 0.98)

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("FusionPlot Charts")
        title.setObjectName("dialogTitle")

        subtitle = QLabel(
            f"Rendering {len(self.chart_configs)} chart(s). "
            "Each chart can contain multiple file-based lines."
        )
        subtitle.setObjectName("dialogSubtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.renderStatus = QLabel("Preparing chart rendering...")
        self.renderStatus.setObjectName("dialogSubtitle")
        layout.addWidget(self.renderStatus)

        self.renderProgress = QProgressBar()
        self.renderProgress.setRange(0, 100)
        self.renderProgress.setValue(0)
        layout.addWidget(self.renderProgress)

        button_bar = QHBoxLayout()
        button_bar.addStretch(1)

        save_button = QPushButton("Save PNG")
        save_button.clicked.connect(self.save_png)
        button_bar.addWidget(save_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_bar.addWidget(close_button)

        layout.addLayout(button_bar)
        layout.addWidget(self.toolbar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("chartScroll")
        scroll_area.setWidgetResizable(True)
        self.scrollArea = scroll_area

        canvas_host = QWidget()
        host_layout = QVBoxLayout(canvas_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addWidget(self.canvas)

        self.canvas.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )

        scroll_area.setWidget(canvas_host)
        layout.addWidget(scroll_area, 1)

    def _read_numeric_row(self, file_path, row_index):

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        if row_index < 1 or row_index > len(data_rows):
            return []

        row = data_rows[row_index - 1]
        numeric_values = []

        for cell in row:

            value = cell.strip()

            if not value:
                continue

            numeric_value = _coerce_numeric_value(value)
            if numeric_value is None:
                continue

            numeric_values.append(numeric_value)

        return numeric_values

    def _read_numeric_column(self, file_path, col_index_1_based):

        cache_key = (file_path, col_index_1_based)
        if cache_key in self._numeric_column_cache:
            return self._numeric_column_cache[cache_key]

        values = []
        col_index = col_index_1_based - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= col_index:
                continue

            value = row[col_index].strip()
            if not value:
                continue

            numeric_value = _coerce_numeric_value(value)
            if numeric_value is None:
                continue

            values.append(numeric_value)

        self._numeric_column_cache[cache_key] = values
        return values

    def _read_plot_x_column(self, file_path, col_index_1_based):

        cache_key = (file_path, col_index_1_based)
        if cache_key in self._plot_x_column_cache:
            return self._plot_x_column_cache[cache_key]

        values = []
        col_index = col_index_1_based - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= col_index:
                continue

            value = row[col_index].strip()
            if not value:
                continue

            parsed_value = _coerce_plot_x_value(value)
            if parsed_value is None:
                continue

            values.append(parsed_value)

        self._plot_x_column_cache[cache_key] = values
        return values

    def _read_sensor_series(
        self,
        file_path,
        x_col,
        y_col,
        sensor_col,
        sensor_id
    ):

        cache_key = (file_path, x_col, y_col, sensor_col, sensor_id)
        if cache_key in self._sensor_series_cache:
            return self._sensor_series_cache[cache_key]

        if sensor_col <= 0:
            return [], []

        x_values = []
        y_values = []

        if sensor_col <= 0:
            return x_values, y_values

        x_index = x_col - 1
        y_index = y_col - 1
        sensor_index = sensor_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= max(x_index, y_index, sensor_index):
                continue

            if row[sensor_index].strip() != sensor_id:
                continue

            x_value = _coerce_plot_x_value(row[x_index].strip())
            y_value = _coerce_numeric_value(row[y_index].strip())

            if x_value is None or y_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)

            self._sensor_series_cache[cache_key] = (x_values, y_values)
        return x_values, y_values

    def _normalize_chart_x_values(self, chart, x_values):

        if not x_values:
            return x_values

        return _normalize_x_values_to_seconds(
            x_values,
            chart.get("x_source_unit", "s"),
        )

    def render_charts(self):

        self.figure.clear()

        chart_count = len(self.chart_configs)
        self._sync_canvas_to_viewport()
        total_lines = sum(
            len(chart.get("lines", []))
            for chart in self.chart_configs
        )
        processed_lines = 0
        skipped_lines = []

        if total_lines == 0:
            self.renderProgress.setValue(100)
            self.renderStatus.setText("No lines to render.")
            self.canvas.draw_idle()
            return

        for axis_index, chart in enumerate(self.chart_configs, start=1):

            if chart.get("chart_mode") == "multi_y":
                axis = self.figure.add_subplot(chart_count, 1, axis_index)
                self._render_multi_y_chart(
                    axis,
                    axis_index,
                    chart_count,
                    chart,
                    total_lines,
                    skipped_lines,
                    processed_lines
                )

                processed_lines += len(chart.get("lines", []))
                continue

            axis = self.figure.add_subplot(chart_count, 1, axis_index)
            axis.set_facecolor("#ffffff")

            valid_line_count = 0
            has_datetime_x = False

            for line in chart["lines"]:

                if line.get("mode") == "sensor":
                    x_values, y_values = self._read_sensor_series(
                        line["file_path"],
                        line["x_col"],
                        line["y_col"],
                        line["sensor_col"],
                        line["sensor_id"]
                    )
                elif line.get("mode") == "column":
                    x_values = self._read_plot_x_column(
                        line["file_path"],
                        line["x_col"]
                    )
                    y_values = self._read_numeric_column(
                        line["file_path"],
                        line["y_col"]
                    )
                elif line.get("mode") == "band":
                    x_values = line.get("x_values", [])
                    y_values = line.get("y_low", [])
                else:
                    x_values = self._read_numeric_row(
                        line["file_path"],
                        line["x_row"]
                    )
                    y_values = self._read_numeric_row(
                        line["file_path"],
                        line["y_row"]
                    )

                point_count = min(len(x_values), len(y_values))

                processed_lines += 1
                progress_value = int(processed_lines / total_lines * 100)
                self.renderProgress.setValue(progress_value)
                self.renderStatus.setText(
                    f"Rendering chart {axis_index}/{chart_count}..."
                )
                QApplication.processEvents()

                if point_count == 0:
                    if line.get("mode") == "sensor":
                        skipped_lines.append(
                            (
                                chart["title"],
                                os.path.basename(line["file_path"]),
                                f"X col {line['x_col']}",
                                f"Y col {line['y_col']}, Sensor {line['sensor_id']}"
                            )
                        )
                    elif line.get("mode") == "column":
                        skipped_lines.append(
                            (
                                chart["title"],
                                os.path.basename(line["file_path"]),
                                f"X col {line['x_col']}",
                                f"Y col {line['y_col']}"
                            )
                        )
                    elif line.get("mode") == "band":
                        skipped_lines.append(
                            (
                                chart["title"],
                                os.path.basename(line.get("file_path", "band")),
                                "Band X",
                                line.get("band_label", "Band")
                            )
                        )
                    else:
                        skipped_lines.append(
                            (
                                chart["title"],
                                os.path.basename(line["file_path"]),
                                f"X row {line['x_row']}",
                                f"Y row {line['y_row']}"
                            )
                        )
                    continue

                x_plot = x_values[:point_count]
                y_plot = y_values[:point_count]

                x_plot = self._normalize_chart_x_values(chart, x_plot)

                if _series_has_datetime(x_plot):
                    has_datetime_x = True

                if line.get("mode") == "band":
                    band_len = min(
                        len(x_values),
                        len(line.get("y_low", [])),
                        len(line.get("y_high", []))
                    )

                    if band_len == 0:
                        continue

                    x_band = self._normalize_chart_x_values(chart, x_values[:band_len])
                    y_low = line.get("y_low", [])[:band_len]
                    y_high = line.get("y_high", [])[:band_len]

                    axis.fill_between(
                        x_band,
                        y_low,
                        y_high,
                        facecolor=line.get("color", "#c4b5fd"),
                        edgecolor=line.get("edge_color", "#7c3aed"),
                        alpha=line.get("alpha", 0.2),
                        linewidth=line.get("edge_width", 1.4),
                        linestyle=line.get("edge_style", "--"),
                        label=line.get("label", "_nolegend_")
                    )
                else:
                    if line.get("plot_mode", "line") == "point":
                        axis.scatter(
                            x_plot,
                            y_plot,
                            color=line.get("color", "#334155"),
                            s=max(1, line.get("point_size", 24)),
                            label=line.get("label", os.path.basename(line["file_path"]))
                        )
                    else:
                        axis.plot(
                            x_plot,
                            y_plot,
                            color=line.get("color", "#334155"),
                            linewidth=line.get("line_width", 1.8),
                            linestyle=line.get("linestyle", "-"),
                            label=line.get("label", os.path.basename(line["file_path"]))
                        )

                valid_line_count += 1

            if valid_line_count == 0:

                axis.text(
                    0.5,
                    0.5,
                    "No numeric points found for this chart config.",
                    color="#4b5563",
                    ha="center",
                    va="center",
                    transform=axis.transAxes
                )

            axis.set_title(
                chart.get("title", f"Chart {axis_index}"),
                color="#111827",
                fontsize=11,
                loc="left",
                pad=10
            )
            axis.set_xlabel(
                chart.get("x_label", "X values"),
                color="#374151"
            )
            y_names = chart.get("y_axis_names", {1: "Y values"})
            axis.set_ylabel(y_names.get(1, "Y values"), color="#374151")
            _apply_time_unit_x_axis(
                axis,
                chart.get("x_time_unit", "auto"),
                has_datetime_x,
            )
            if not has_datetime_x and chart.get("x_time_unit", "auto") == "auto":
                axis.xaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
            axis.margins(x=self._chart_x_margin())
            axis.grid(
                True,
                color="#d1d5db",
                alpha=0.6,
                linestyle="--",
                linewidth=0.8
            )
            axis.tick_params(colors="#374151")

            for spine in axis.spines.values():
                spine.set_color("#d1d5db")

            if valid_line_count > 0:
                self._apply_adaptive_legend(axis, chart)

        self.figure.tight_layout(rect=self._chart_layout_rect(), h_pad=2.0)
        self._sync_canvas_to_viewport()
        self.canvas.draw_idle()
        self.renderProgress.setValue(100)
        self.renderStatus.setText("Rendering complete.")

        if skipped_lines:
            details = "\n".join(
                [
                    f"{title} | {name} | {x_info}, {y_info}"
                    for title, name, x_info, y_info in skipped_lines
                ]
            )
            QMessageBox.information(
                self,
                "Skipped Lines",
                "Some lines were skipped because no numeric points were found.\n\n"
                + details
            )

    def _render_multi_y_chart(
        self,
        base_axis,
        axis_index,
        chart_count,
        chart,
        total_lines,
        skipped_lines,
        processed_lines_start
    ):

        base_axis.set_facecolor("#ffffff")

        axes = {1: base_axis}
        axis_primary_colors = {}
        valid_line_count = 0
        has_datetime_x = False

        for line_index, line in enumerate(chart.get("lines", []), start=1):

            if line.get("mode") == "sensor":
                x_values, y_values = self._read_sensor_series(
                    line["file_path"],
                    line["x_col"],
                    line["y_col"],
                    line["sensor_col"],
                    line["sensor_id"]
                )
            elif line.get("mode") == "column":
                x_values = self._read_plot_x_column(
                    line["file_path"],
                    line["x_col"]
                )
                y_values = self._read_numeric_column(
                    line["file_path"],
                    line["y_col"]
                )
            elif line.get("mode") == "band":
                x_values = line.get("x_values", [])
                y_values = line.get("y_low", [])
            else:
                x_values = self._read_numeric_row(
                    line["file_path"],
                    line["x_row"]
                )
                y_values = self._read_numeric_row(
                    line["file_path"],
                    line["y_row"]
                )

            processed = processed_lines_start + line_index
            progress_value = int(processed / total_lines * 100)
            self.renderProgress.setValue(progress_value)
            self.renderStatus.setText(
                f"Rendering chart {axis_index}/{chart_count}..."
            )
            QApplication.processEvents()

            point_count = min(len(x_values), len(y_values))
            if point_count == 0:
                if line.get("mode") == "sensor":
                    skipped_lines.append(
                        (
                            chart["title"],
                            os.path.basename(line["file_path"]),
                            f"X col {line['x_col']}",
                            f"Y col {line['y_col']}, Sensor {line['sensor_id']}"
                        )
                    )
                elif line.get("mode") == "column":
                    skipped_lines.append(
                        (
                            chart["title"],
                            os.path.basename(line["file_path"]),
                            f"X col {line['x_col']}",
                            f"Y col {line['y_col']}"
                        )
                    )
                elif line.get("mode") == "band":
                    skipped_lines.append(
                        (
                            chart["title"],
                            os.path.basename(line.get("file_path", "band")),
                            "Band X",
                            line.get("band_label", "Band")
                        )
                    )
                else:
                    skipped_lines.append(
                        (
                            chart["title"],
                            os.path.basename(line["file_path"]),
                            f"X row {line['x_row']}",
                            f"Y row {line['y_row']}"
                        )
                    )
                continue

            axis_id = max(1, int(line.get("axis_id", 1)))

            if axis_id not in axes:
                new_axis = base_axis.twinx()
                if axis_id > 2:
                    offset = 60 * (axis_id - 2)
                    new_axis.spines["right"].set_position(("outward", offset))
                axes[axis_id] = new_axis

            current_axis = axes[axis_id]
            current_axis.set_facecolor("#ffffff")

            x_plot = x_values[:point_count]
            y_plot = y_values[:point_count]

            x_plot = self._normalize_chart_x_values(chart, x_plot)

            if _series_has_datetime(x_plot):
                has_datetime_x = True

            if line.get("mode") == "band":
                band_len = min(
                    len(x_values),
                    len(line.get("y_low", [])),
                    len(line.get("y_high", []))
                )

                if band_len == 0:
                    continue

                x_band = self._normalize_chart_x_values(chart, x_values[:band_len])
                y_low = line.get("y_low", [])[:band_len]
                y_high = line.get("y_high", [])[:band_len]

                current_axis.fill_between(
                    x_band,
                    y_low,
                    y_high,
                    facecolor=line.get("color", "#c4b5fd"),
                    edgecolor=line.get("edge_color", "#7c3aed"),
                    alpha=line.get("alpha", 0.2),
                    linewidth=line.get("edge_width", 1.4),
                    linestyle=line.get("edge_style", "--"),
                    label=line.get("label", "_nolegend_")
                )
            else:
                if line.get("plot_mode", "line") == "point":
                    current_axis.scatter(
                        x_plot,
                        y_plot,
                        color=line.get("color", "#334155"),
                        s=max(1, line.get("point_size", 24)),
                        label=line.get("label", os.path.basename(line["file_path"]))
                    )
                else:
                    current_axis.plot(
                        x_plot,
                        y_plot,
                        color=line.get("color", "#334155"),
                        linewidth=line.get("line_width", 1.8),
                        linestyle=line.get("linestyle", "-"),
                        label=line.get("label", os.path.basename(line["file_path"]))
                    )

                if axis_id not in axis_primary_colors:
                    axis_primary_colors[axis_id] = line.get("color", "#334155")

            current_axis.tick_params(axis="x", colors="#374151")
            for spine in current_axis.spines.values():
                spine.set_color("#d1d5db")

            valid_line_count += 1

        if valid_line_count == 0:
            base_axis.text(
                0.5,
                0.5,
                "No numeric points found for this chart config.",
                color="#4b5563",
                ha="center",
                va="center",
                transform=base_axis.transAxes
            )

        base_axis.set_title(
            chart.get("title", "Chart"),
            color="#111827",
            fontsize=11,
            loc="left",
            pad=10
        )
        base_axis.set_xlabel(
            chart.get("x_label", "X values"),
            color="#374151"
        )
        y_names = chart.get("y_axis_names", {})
        base_axis.set_ylabel(
            y_names.get(1, "Y Axis 1"),
            color="#374151"
        )
        _apply_time_unit_x_axis(
            base_axis,
            chart.get("x_time_unit", "auto"),
            has_datetime_x,
        )
        if not has_datetime_x and chart.get("x_time_unit", "auto") == "auto":
            base_axis.xaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
        base_axis.margins(x=self._chart_x_margin())
        base_axis.grid(
            True,
            color="#d1d5db",
            alpha=0.6,
            linestyle="--",
            linewidth=0.8
        )
        base_axis.tick_params(colors="#374151")
        for spine in base_axis.spines.values():
            spine.set_color("#d1d5db")

        for axis_id, axis in axes.items():
            axis_color = axis_primary_colors.get(axis_id, "#374151")
            axis.set_ylabel(
                y_names.get(axis_id, f"Y Axis {axis_id}"),
                color=axis_color
            )
            axis.tick_params(axis="y", colors=axis_color)

            if axis_id == 1:
                axis.spines["left"].set_color(axis_color)
            else:
                axis.spines["right"].set_color(axis_color)

        handles = []
        labels = []
        for axis in axes.values():
            h, l = axis.get_legend_handles_labels()
            handles.extend(h)
            labels.extend(l)

        if handles:
            self._apply_adaptive_legend(
                base_axis,
                chart,
                handles,
                labels
            )

    def save_png(self):

        file_path = _safe_save_file_name(
            self,
            "Save Chart as PNG",
            "chart.png",
            "PNG Image (*.png)",
        )

        if not file_path:
            return

        self.figure.savefig(
            file_path,
            dpi=240,
            facecolor="white",
            bbox_inches="tight"
        )

        QMessageBox.information(
            self,
            "Saved",
            f"Chart saved to:\n{file_path}"
        )


class XYZChartDialog(QDialog):

    def __init__(self, chart_configs, parent=None):

        super().__init__(parent)
        _enable_dialog_resize(self)

        self.chart_configs = chart_configs
        self._xyz_series_cache = {}
        self._xyz_sensor_series_cache = {}

        self.setWindowTitle("FusionPlot Studio - XYZ Chart Builder")
        self.resize(2160, 1720)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinMaxButtonsHint
        )

        self.figure = Figure(facecolor="#ffffff")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(
            self.canvas,
            self
        )

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.98,
                height_ratio=0.95,
                min_width=1280,
                min_height=820,
            ),
        )
        self._rainbow_phase = 0
        self._rainbow_timer = QTimer(self)
        self._rainbow_timer.timeout.connect(self._update_rainbow_progress)
        self._rainbow_timer.start(110)
        self._update_rainbow_progress()
        self._resize_sync_timer = QTimer(self)
        self._resize_sync_timer.setSingleShot(True)
        self._resize_sync_timer.timeout.connect(self._sync_canvas_to_viewport)
        QTimer.singleShot(0, self.render_charts)

    def changeEvent(self, event):

        super().changeEvent(event)

        if event.type() == event.Type.WindowStateChange:
            self._resize_sync_timer.start(30)

    def resizeEvent(self, event):

        super().resizeEvent(event)
        self._resize_sync_timer.start(90)

    def _sync_canvas_to_viewport(self):

        if not hasattr(self, "scrollArea"):
            return

        chart_count = max(1, len(self.chart_configs))
        viewport = self.scrollArea.viewport().size()

        if self.isMaximized():
            target_width_px = max(760, viewport.width() - 18)
        else:
            target_width_px = max(1020, int(viewport.width() * 1.15))

        per_chart_height = max(420, int(viewport.height() * 0.68))
        target_height_px = max(viewport.height() - 12, chart_count * per_chart_height)

        dpi = max(72.0, float(self.figure.get_dpi()))
        self.figure.set_size_inches(
            target_width_px / dpi,
            target_height_px / dpi,
            forward=True
        )
        self.canvas.setMinimumHeight(target_height_px)
        self.canvas.setMinimumWidth(target_width_px)

    def _update_rainbow_progress(self):

        self._rainbow_phase = (self._rainbow_phase + 11) % 360

        c1 = QColor.fromHsv(self._rainbow_phase % 360, 205, 245).name()
        c2 = QColor.fromHsv((self._rainbow_phase + 72) % 360, 205, 245).name()
        c3 = QColor.fromHsv((self._rainbow_phase + 150) % 360, 205, 245).name()
        c4 = QColor.fromHsv((self._rainbow_phase + 230) % 360, 205, 245).name()
        c5 = QColor.fromHsv((self._rainbow_phase + 305) % 360, 205, 245).name()

        self.renderProgress.setStyleSheet(
            "QProgressBar{"
            "border:1px solid #d5deea;"
            "border-radius:9px;"
            "background:#f8fafc;"
            "text-align:center;"
            "color:#334155;"
            "min-height:20px;"
            "}"
            "QProgressBar::chunk{"
            "border-radius:8px;"
            f"background:qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {c1}, stop:0.25 {c2}, stop:0.5 {c3}, stop:0.75 {c4}, stop:1 {c5});"
            "}"
        )

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("FusionPlot XYZ Charts")
        title.setObjectName("dialogTitle")

        subtitle = QLabel(
            f"Rendering {len(self.chart_configs)} XYZ chart(s). "
            "Each chart can contain multiple file-based or sensor-based lines."
        )
        subtitle.setObjectName("dialogSubtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.renderStatus = QLabel("Preparing XYZ chart rendering...")
        self.renderStatus.setObjectName("dialogSubtitle")
        layout.addWidget(self.renderStatus)

        self.renderProgress = QProgressBar()
        self.renderProgress.setRange(0, 100)
        self.renderProgress.setValue(0)
        layout.addWidget(self.renderProgress)

        button_bar = QHBoxLayout()
        button_bar.addStretch(1)

        save_button = QPushButton("Save PNG")
        save_button.clicked.connect(self.save_png)
        button_bar.addWidget(save_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_bar.addWidget(close_button)

        layout.addLayout(button_bar)
        layout.addWidget(self.toolbar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("chartScroll")
        scroll_area.setWidgetResizable(True)
        self.scrollArea = scroll_area

        canvas_host = QWidget()
        host_layout = QVBoxLayout(canvas_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addWidget(self.canvas)

        self.canvas.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )

        scroll_area.setWidget(canvas_host)
        layout.addWidget(scroll_area, 1)

    def _read_xyz_series(self, file_path, x_col, y_col, z_col):

        cache_key = (file_path, x_col, y_col, z_col)
        if cache_key in self._xyz_series_cache:
            return self._xyz_series_cache[cache_key]

        x_values = []
        y_values = []
        z_values = []

        x_index = x_col - 1
        y_index = y_col - 1
        z_index = z_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row_number, row in enumerate(data_rows, start=1):

            if len(row) <= max(y_index, z_index):
                continue

            if x_col == 0:
                x_value = float(row_number)
            else:
                if len(row) <= x_index:
                    continue
                x_value = _coerce_plot_x_value(row[x_index].strip())

            y_value = _coerce_numeric_value(row[y_index].strip())
            z_value = _coerce_numeric_value(row[z_index].strip())

            if x_value is None or y_value is None or z_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)
            z_values.append(z_value)

        self._xyz_series_cache[cache_key] = (x_values, y_values, z_values)
        return x_values, y_values, z_values

    def _read_xyz_sensor_series(
        self,
        file_path,
        x_col,
        y_col,
        z_col,
        sensor_col,
        sensor_id
    ):

        cache_key = (file_path, x_col, y_col, z_col, sensor_col, sensor_id)
        if cache_key in self._xyz_sensor_series_cache:
            return self._xyz_sensor_series_cache[cache_key]

        if sensor_col <= 0:
            return [], [], []

        x_values = []
        y_values = []
        z_values = []

        x_index = x_col - 1
        y_index = y_col - 1
        z_index = z_col - 1
        sensor_index = sensor_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row_number, row in enumerate(data_rows, start=1):

            if len(row) <= max(y_index, z_index, sensor_index):
                continue

            if row[sensor_index].strip() != sensor_id:
                continue

            if x_col == 0:
                x_value = float(row_number)
            else:
                if len(row) <= x_index:
                    continue
                x_value = _coerce_plot_x_value(row[x_index].strip())

            y_value = _coerce_numeric_value(row[y_index].strip())
            z_value = _coerce_numeric_value(row[z_index].strip())

            if x_value is None or y_value is None or z_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)
            z_values.append(z_value)

        self._xyz_sensor_series_cache[cache_key] = (x_values, y_values, z_values)
        return x_values, y_values, z_values

    def render_charts(self):

        self.figure.clear()

        chart_count = len(self.chart_configs)
        self._sync_canvas_to_viewport()
        total_lines = sum(
            len(chart.get("lines", []))
            for chart in self.chart_configs
        )
        processed_lines = 0
        skipped_lines = []

        if total_lines == 0:
            self.renderProgress.setValue(100)
            self.renderStatus.setText("No lines to render.")
            self.canvas.draw_idle()
            return

        for axis_index, chart in enumerate(self.chart_configs, start=1):

            axis = self.figure.add_subplot(chart_count, 1, axis_index, projection="3d")
            axis.set_facecolor("#ffffff")
            axis.view_init(elev=24, azim=-58)

            valid_line_count = 0
            has_datetime_x = False

            for line in chart.get("lines", []):

                if line.get("mode") == "sensor":
                    x_values, y_values, z_values = self._read_xyz_sensor_series(
                        line["file_path"],
                        line["x_col"],
                        line["y_col"],
                        line["z_col"],
                        line["sensor_col"],
                        line["sensor_id"]
                    )
                else:
                    x_values, y_values, z_values = self._read_xyz_series(
                        line["file_path"],
                        line["x_col"],
                        line["y_col"],
                        line["z_col"]
                    )

                point_count = min(len(x_values), len(y_values), len(z_values))

                processed_lines += 1
                progress_value = int(processed_lines / total_lines * 100)
                self.renderProgress.setValue(progress_value)
                self.renderStatus.setText(
                    f"Rendering XYZ chart {axis_index}/{chart_count}..."
                )
                QApplication.processEvents()

                if point_count == 0:
                    if line.get("mode") == "sensor":
                        skipped_lines.append(
                            (
                                chart.get("title", f"XYZ Chart {axis_index}"),
                                os.path.basename(line["file_path"]),
                                f"X col {line['x_col']}",
                                f"Y col {line['y_col']}, Z col {line['z_col']}, Sensor {line['sensor_id']}"
                            )
                        )
                    else:
                        skipped_lines.append(
                            (
                                chart.get("title", f"XYZ Chart {axis_index}"),
                                os.path.basename(line["file_path"]),
                                f"X col {line['x_col']}",
                                f"Y col {line['y_col']}, Z col {line['z_col']}"
                            )
                        )
                    continue

                if line.get("plot_mode", "line") == "point":
                    axis.scatter(
                        x_values[:point_count],
                        y_values[:point_count],
                        z_values[:point_count],
                        color=line.get("color", "#334155"),
                        s=max(1, line.get("point_size", 24)),
                        label=line.get("label", os.path.basename(line["file_path"]))
                    )
                else:
                    axis.plot(
                        x_values[:point_count],
                        y_values[:point_count],
                        z_values[:point_count],
                        color=line.get("color", "#334155"),
                        linewidth=line.get("line_width", 1.8),
                        label=line.get("label", os.path.basename(line["file_path"]))
                    )

                if _series_has_datetime(x_values[:point_count]):
                    has_datetime_x = True

                valid_line_count += 1

            if valid_line_count == 0:
                axis.text2D(
                    0.5,
                    0.5,
                    "No numeric XYZ points found for this chart config.",
                    color="#4b5563",
                    ha="center",
                    va="center",
                    transform=axis.transAxes
                )

            axis.set_title(
                chart.get("title", f"XYZ Chart {axis_index}"),
                color="#111827",
                fontsize=11,
                loc="left",
                pad=10
            )
            axis.set_xlabel(chart.get("x_label", "X values"), color="#374151")
            axis.set_ylabel(chart.get("y_label", "Y values"), color="#374151")
            axis.set_zlabel(chart.get("z_label", "Z values"), color="#374151")
            if has_datetime_x:
                _apply_datetime_x_axis(axis)
            else:
                axis.xaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
            axis.yaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
            axis.zaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=4))
            axis.tick_params(colors="#374151")

            if valid_line_count > 0:
                legend = axis.legend(
                    facecolor="#ffffff",
                    edgecolor="#d1d5db",
                    labelcolor="#111827",
                    loc="upper center",
                    bbox_to_anchor=(0.5, -0.08),
                    ncol=1,
                    fontsize=8,
                )

                for text in legend.get_texts():
                    text.set_color("#111827")

        self.figure.tight_layout(rect=(0.03, 0.06, 0.97, 0.98), h_pad=2.0)
        self._sync_canvas_to_viewport()
        self.canvas.draw_idle()
        self.renderProgress.setValue(100)
        self.renderStatus.setText("XYZ rendering complete.")

        if skipped_lines:
            details = "\n".join(
                [
                    f"{title} | {name} | {x_info}, {y_info}"
                    for title, name, x_info, y_info in skipped_lines
                ]
            )
            QMessageBox.information(
                self,
                "Skipped Lines",
                "Some XYZ lines were skipped because no numeric points were found.\n\n"
                + details
            )

    def save_png(self):

        file_path = _safe_save_file_name(
            self,
            "Save XYZ Chart as PNG",
            "xyz-chart.png",
            "PNG Image (*.png)",
        )

        if not file_path:
            return

        self.figure.savefig(
            file_path,
            dpi=240,
            facecolor="white",
            bbox_inches="tight"
        )

        QMessageBox.information(
            self,
            "Saved",
            f"Chart saved to:\n{file_path}"
        )


class MainWindow(QWidget):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("FusionPlot Studio")

        self.resize(980, 720)

        self.file_set = set()
        self._sensor_deviation_series_cache = {}

        self.build_ui()
        QTimer.singleShot(
            0,
            lambda: _fit_window_to_screen(
                self,
                width_ratio=0.98,
                height_ratio=0.94,
                min_width=980,
                min_height=720,
            ),
        )
        self._rainbow_phase = 0
        self._rainbow_timer = QTimer(self)
        self._rainbow_timer.timeout.connect(self._update_rainbow_progress)
        self._rainbow_timer.start(110)
        self._update_rainbow_progress()

    def build_ui(self):

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        app_title = QLabel("FusionPlot Studio")
        app_title.setObjectName("appTitle")
        app_title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel(
            "File merge + TXT charting in one desktop tool.\n"
            "Manage files once, then run Plot or Merge as the two core workflows."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)

        layout.addWidget(app_title)
        layout.addWidget(subtitle)

        info_row = QHBoxLayout()
        info_row.addStretch(1)

        self.btnVersionInfo = QPushButton("v")
        self.btnVersionInfo.setObjectName("miniInfoButton")
        self.btnVersionInfo.setFixedSize(24, 24)
        self.btnVersionInfo.setToolTip("Version and maintenance info")
        info_row.addWidget(self.btnVersionInfo)

        layout.addLayout(info_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setSpacing(14)

        right_col = QVBoxLayout()
        right_col.setSpacing(14)

        list_card = QFrame()
        list_card.setObjectName("card")

        card_layout = QVBoxLayout(list_card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(10)

        drop_tip = QLabel("Drop files or folders below")
        drop_tip.setObjectName("sectionTitle")
        card_layout.addWidget(drop_tip)

        self.listWidget = DropListWidget()
        self.listWidget.setObjectName("dropList")
        self.listWidget.setAlternatingRowColors(True)

        self.listWidget.filesDropped.connect(
            self.add_files
        )

        card_layout.addWidget(self.listWidget)

        left_col.addWidget(list_card, 1)

        buttonLayout = QGridLayout()
        buttonLayout.setHorizontalSpacing(10)
        buttonLayout.setVerticalSpacing(10)

        self.btnAdd = QPushButton("Add Files")
        self.btnAdd.setIcon(self._load_icon("fa6s.folder-plus"))
        self.btnAdd.setObjectName("actionAddButton")
        self.btnAdd.setMinimumHeight(56)

        self.btnClear = QPushButton("Clear All")
        self.btnClear.setIcon(self._load_icon("fa6s.broom"))
        self.btnClear.setObjectName("actionClearButton")
        self.btnClear.setMinimumHeight(56)

        self.btnRemove = QPushButton("Remove Selected")
        self.btnRemove.setIcon(self._load_icon("fa6s.trash"))
        self.btnRemove.setObjectName("actionRemoveButton")
        self.btnRemove.setMinimumHeight(56)

        buttonLayout.addWidget(self.btnAdd, 0, 0)
        buttonLayout.addWidget(self.btnRemove, 0, 1)
        buttonLayout.addWidget(self.btnClear, 1, 0, 1, 2)

        left_col.addLayout(buttonLayout)

        merge_card = QFrame()
        merge_card.setObjectName("card")
        merge_layout = QVBoxLayout(merge_card)
        merge_layout.setContentsMargins(14, 14, 14, 14)
        merge_layout.setSpacing(10)

        merge_title = QLabel("Merge")
        merge_title.setObjectName("sectionTitle")
        merge_layout.addWidget(merge_title)

        merge_desc = QLabel(
            "Merge selected chunks into complete files and monitor progress in real time."
        )
        merge_desc.setObjectName("dialogSubtitle")
        merge_desc.setWordWrap(True)
        merge_layout.addWidget(merge_desc)

        outputLayout = QHBoxLayout()
        outputLayout.setSpacing(8)

        output_text = QLabel("Output Folder")
        output_text.setObjectName("sectionTitle")

        self.outputLabel = QLabel(
            os.path.abspath("output")
        )
        self.outputLabel.setObjectName("outputPath")

        self.btnBrowse = QPushButton(
            "Browse"
        )
        self.btnBrowse.setIcon(self._load_icon("fa6s.folder-open"))

        outputLayout.addWidget(output_text)
        outputLayout.addWidget(self.outputLabel, 1)
        outputLayout.addWidget(self.btnBrowse)
        merge_layout.addLayout(outputLayout)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        merge_layout.addWidget(self.progress)

        self.btnMerge = QPushButton(
            "Start Merge"
        )
        self.btnMerge.setObjectName("mergePrimaryButton")
        self.btnMerge.setIcon(self._load_icon("fa6s.link"))
        self.btnMerge.setMinimumHeight(72)
        merge_layout.addWidget(self.btnMerge)

        left_col.addWidget(merge_card)

        clean_card = QFrame()
        clean_card.setObjectName("card")
        clean_layout = QVBoxLayout(clean_card)
        clean_layout.setContentsMargins(14, 14, 14, 14)
        clean_layout.setSpacing(10)

        clean_title = QLabel("XY Clean")
        clean_title.setObjectName("sectionTitle")
        clean_layout.addWidget(clean_title)

        clean_desc = QLabel(
            "Strip labels and symbols from TXT/CSV text and export comma-separated numeric XY output."
        )
        clean_desc.setObjectName("dialogSubtitle")
        clean_desc.setWordWrap(True)
        clean_layout.addWidget(clean_desc)

        self.btnCleanData = QPushButton("Clean XY Numeric Text")
        self.btnCleanData.setIcon(self._load_icon("fa6s.filter"))
        self.btnCleanData.setMinimumHeight(72)
        self.btnCleanData.setObjectName("cleanPrimaryButton")
        clean_layout.addWidget(self.btnCleanData)

        left_col.addWidget(clean_card)

        plot_card = QFrame()
        plot_card.setObjectName("card")
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(14, 14, 14, 14)
        plot_layout.setSpacing(10)

        plot_title = QLabel("Plot")
        plot_title.setObjectName("sectionTitle")
        plot_layout.addWidget(plot_title)

        plot_desc = QLabel(
            "Build chart groups from selected data files with per-chart custom config."
        )
        plot_desc.setObjectName("dialogSubtitle")
        plot_desc.setWordWrap(True)
        plot_layout.addWidget(plot_desc)

        self.btnPlot = QPushButton("Open Plot Builder")
        self.btnPlot.setObjectName("plotPrimaryButton")
        self.btnPlot.setIcon(self._load_icon("fa6s.chart-line"))
        self.btnPlot.setMinimumHeight(72)
        plot_layout.addWidget(self.btnPlot)

        right_col.addWidget(plot_card)

        xyz_card = QFrame()
        xyz_card.setObjectName("card")
        xyz_layout = QVBoxLayout(xyz_card)
        xyz_layout.setContentsMargins(14, 14, 14, 14)
        xyz_layout.setSpacing(10)

        xyz_title = QLabel("XYZ Plot")
        xyz_title.setObjectName("sectionTitle")
        xyz_layout.addWidget(xyz_title)

        xyz_desc = QLabel(
            "Build simplified XYZ charts with custom X/Y/Z columns, optional Sensor No split, and X=0 index mode."
        )
        xyz_desc.setObjectName("dialogSubtitle")
        xyz_desc.setWordWrap(True)
        xyz_layout.addWidget(xyz_desc)

        self.btnXYZPlot = QPushButton("Open XYZ Plot Builder")
        self.btnXYZPlot.setIcon(self._load_icon("fa6s.cube"))
        self.btnXYZPlot.setMinimumHeight(72)
        self.btnXYZPlot.setObjectName("xyzPrimaryButton")
        xyz_layout.addWidget(self.btnXYZPlot)

        right_col.addWidget(xyz_card)

        single_export_card = QFrame()
        single_export_card.setObjectName("card")
        single_export_layout = QVBoxLayout(single_export_card)
        single_export_layout.setContentsMargins(14, 14, 14, 14)
        single_export_layout.setSpacing(10)

        single_export_title = QLabel("Single File PNG")
        single_export_title.setObjectName("sectionTitle")
        single_export_layout.addWidget(single_export_title)

        single_export_desc = QLabel(
            "Open one data file, auto-detect columns, configure X and Y naming, then export one PNG per chosen Y column."
        )
        single_export_desc.setObjectName("dialogSubtitle")
        single_export_desc.setWordWrap(True)
        single_export_layout.addWidget(single_export_desc)

        self.btnSingleBatchExport = QPushButton("Single File Batch PNG Export")
        self.btnSingleBatchExport.setIcon(self._load_icon("fa6s.file-export"))
        self.btnSingleBatchExport.setMinimumHeight(72)
        self.btnSingleBatchExport.setObjectName("singleExportPrimaryButton")
        single_export_layout.addWidget(self.btnSingleBatchExport)

        right_col.addWidget(single_export_card)

        deviation_export_card = QFrame()
        deviation_export_card.setObjectName("card")
        deviation_export_layout = QVBoxLayout(deviation_export_card)
        deviation_export_layout.setContentsMargins(14, 14, 14, 14)
        deviation_export_layout.setSpacing(10)

        deviation_export_title = QLabel("Sensor Deviation")
        deviation_export_title.setObjectName("sectionTitle")
        deviation_export_layout.addWidget(deviation_export_title)

        deviation_export_desc = QLabel(
            "Build one PNG per Y column using grouped sensor averages plus max/min deviation percentage, with optional Chart 2 below."
        )
        deviation_export_desc.setObjectName("dialogSubtitle")
        deviation_export_desc.setWordWrap(True)
        deviation_export_layout.addWidget(deviation_export_desc)

        self.btnSensorDeviationExport = QPushButton("Sensor Deviation PNG Export")
        self.btnSensorDeviationExport.setIcon(self._load_icon("fa6s.file-export"))
        self.btnSensorDeviationExport.setMinimumHeight(72)
        self.btnSensorDeviationExport.setObjectName("deviationPrimaryButton")
        deviation_export_layout.addWidget(self.btnSensorDeviationExport)

        right_col.addWidget(deviation_export_card)
        right_col.addStretch(1)

        content_row.addLayout(left_col, 6)
        content_row.addLayout(right_col, 5)

        layout.addLayout(content_row, 1)

        log_label = QLabel("Activity Log")
        log_label.setObjectName("sectionTitle")
        layout.addWidget(log_label)

        self.logEdit = QTextEdit()
        self.logEdit.setObjectName("logBox")

        self.logEdit.setReadOnly(True)

        layout.addWidget(
            self.logEdit,
            1
        )

        self.btnAdd.clicked.connect(
            self.choose_files
        )

        self.btnClear.clicked.connect(
            self.clear_files
        )

        self.btnRemove.clicked.connect(
            self.remove_selected
        )

        self.btnPlot.clicked.connect(
            self.open_chart_dialog
        )

        self.btnCleanData.clicked.connect(
            self.clean_numeric_text_files
        )

        self.btnXYZPlot.clicked.connect(
            self.open_xyz_chart_dialog
        )

        self.btnSingleBatchExport.clicked.connect(
            self.open_single_file_batch_export
        )

        self.btnSensorDeviationExport.clicked.connect(
            self.open_sensor_deviation_batch_export
        )

        self.btnBrowse.clicked.connect(
            self.choose_output
        )

        self.btnMerge.clicked.connect(
            self.merge_files
        )

        self.btnVersionInfo.clicked.connect(
            self.show_version_info
        )

        self.listWidget.itemDoubleClicked.connect(
            self.delete_item
        )

    def log(self, text):

        self.logEdit.append(text)

    def show_version_info(self):

        QMessageBox.information(
            self,
            "Version Information",
            "Version: v1.0.2\n"
            "Release Date: 2026/8/11\n"
            "Maintainer: LiNing(BST/ESA4)"
        )

    def _update_rainbow_progress(self):

        self._rainbow_phase = (self._rainbow_phase + 11) % 360

        c1 = QColor.fromHsv(self._rainbow_phase % 360, 205, 245).name()
        c2 = QColor.fromHsv((self._rainbow_phase + 72) % 360, 205, 245).name()
        c3 = QColor.fromHsv((self._rainbow_phase + 150) % 360, 205, 245).name()
        c4 = QColor.fromHsv((self._rainbow_phase + 230) % 360, 205, 245).name()
        c5 = QColor.fromHsv((self._rainbow_phase + 305) % 360, 205, 245).name()

        self.progress.setStyleSheet(
            "QProgressBar{"
            "border:1px solid #d5deea;"
            "border-radius:9px;"
            "background:#f8fafc;"
            "text-align:center;"
            "color:#334155;"
            "min-height:20px;"
            "}"
            "QProgressBar::chunk{"
            "border-radius:8px;"
            f"background:qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {c1}, stop:0.25 {c2}, stop:0.5 {c3}, stop:0.75 {c4}, stop:1 {c5});"
            "}"
        )

    def choose_files(self):

        files = self._safe_open_file_names(
            title="Select Files",
            file_filter="All Files (*);;Text Files (*.txt)"
        )

        if files:

            self.add_files(files)

    def _dialog_options(self):

        return QFileDialog.Option.DontUseNativeDialog

    def _safe_open_file_names(self, title, start_dir="", file_filter=""):

        try:
            files, _ = QFileDialog.getOpenFileNames(
                self,
                title,
                start_dir,
                file_filter,
                options=self._dialog_options(),
            )
            return files
        except Exception as error:
            self.log(f"Open file dialog failed: {error}")
            QMessageBox.critical(
                self,
                "Open File Error",
                "Failed to open file selector dialog."
            )
            return []

    def _safe_open_single_file(self, title, start_dir="", file_filter=""):

        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                title,
                start_dir,
                file_filter,
                options=self._dialog_options(),
            )
            return file_path
        except Exception as error:
            self.log(f"Open file dialog failed: {error}")
            QMessageBox.critical(
                self,
                "Open File Error",
                "Failed to open file selector dialog."
            )
            return ""

    def _safe_open_directory(self, title, start_dir=""):

        try:
            return QFileDialog.getExistingDirectory(
                self,
                title,
                start_dir,
                options=self._dialog_options(),
            )
        except Exception as error:
            self.log(f"Open folder dialog failed: {error}")
            QMessageBox.critical(
                self,
                "Open Folder Error",
                "Failed to open folder selector dialog."
            )
            return ""

    def add_files(self, files):

        count = 0

        for file in files:

            if not os.path.isfile(file):
                continue

            if file in self.file_set:
                continue

            self.file_set.add(file)

            item = QListWidgetItem(file)

            self.listWidget.addItem(item)

            count += 1

        self.log(f"Added {count} file(s).")

    def clear_files(self):

        self.file_set.clear()

        self.listWidget.clear()

        self.progress.setValue(0)

        self.log("File list cleared.")

    def remove_selected(self):

        items = self.listWidget.selectedItems()

        for item in items:

            self.file_set.discard(item.text())

            self.listWidget.takeItem(
                self.listWidget.row(item)
            )

        self.log(f"Removed {len(items)} selected file(s).")

    def delete_item(self, item):

        if item is None:
            return

        path = item.text()

        self.file_set.discard(path)

        self.listWidget.takeItem(
            self.listWidget.row(item)
        )

        self.log(f"Removed: {os.path.basename(path)}")

    def choose_output(self):

        folder = self._safe_open_directory(
            title="Select Output Folder",
            start_dir=self.outputLabel.text(),
        )

        if folder:

            self.outputLabel.setText(folder)

            self.log(
                f"Output folder: {folder}"
            )

    def get_selected_plot_files(self):

        return self.get_selected_or_all_files()

    def get_selected_or_all_files(self):

        selected_paths = [
            item.text()
            for item in self.listWidget.selectedItems()
        ]

        if selected_paths:
            return selected_paths

        return [
            path
            for path in sorted(self.file_set)
        ]

    def _discover_sensors(self, file_path, sensor_col):

        if sensor_col <= 0:
            return []

        sensor_values = []
        seen = set()

        sensor_index = sensor_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= sensor_index:
                continue

            sensor_value = row[sensor_index].strip()

            if not sensor_value:
                continue

            if sensor_value in seen:
                continue

            seen.add(sensor_value)
            sensor_values.append(sensor_value)

        return sensor_values

    def _read_sensor_series_values(
        self,
        file_path,
        x_col,
        y_col,
        sensor_col,
        sensor_id
    ):

        x_values = []
        y_values = []

        x_index = x_col - 1
        y_index = y_col - 1
        sensor_index = sensor_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= max(x_index, y_index, sensor_index):
                continue

            if row[sensor_index].strip() != sensor_id:
                continue

            x_value = _coerce_plot_x_value(row[x_index].strip())
            y_value = _coerce_numeric_value(row[y_index].strip())

            if x_value is None or y_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)

        return x_values, y_values

    def _parse_sensor_band_specs(self, text):

        specs = []
        seen = set()

        if not text:
            return specs

        normalized_text = (
            str(text)
            .replace("±", " ")
            .replace("+/-", " ")
            .replace("+-", " ")
        )

        token_matches = re.findall(
            r"[-+]?(?:\d*\.\d+|\d+)\s*%?",
            normalized_text,
        )

        for token in token_matches:
            raw = (token or "").strip()

            if not raw:
                continue

            is_percent = raw.endswith("%")
            number_text = raw[:-1].strip() if is_percent else raw

            try:
                value = abs(float(number_text))
            except ValueError:
                continue

            if value <= 0:
                continue

            kind = "percent" if is_percent else "absolute"
            key = (kind, value)

            if key in seen:
                continue

            seen.add(key)
            specs.append(
                {
                    "kind": kind,
                    "value": value,
                }
            )

            if len(specs) >= 3:
                break

        return specs

    def _resolve_sensor_band_specs_for_y(
        self,
        y_col,
        default_rules_text,
        override_rules_text
    ):

        default_specs = self._parse_sensor_band_specs(default_rules_text)
        if not default_specs:
            default_specs = self._parse_sensor_band_specs("15%,30%")

        if not override_rules_text:
            return default_specs

        override_text = override_rules_text.strip()

        if not override_text:
            return default_specs

        disable_marker = (
            override_text
            .replace(" ", "")
            .replace("±", "")
            .replace("+/-", "")
            .replace("+-", "")
            .replace("%", "")
            .replace(",", "")
            .replace(";", "")
        )

        if disable_marker and set(disable_marker) <= {"0", "."}:
            return []

        direct_specs = self._parse_sensor_band_specs(override_text)

        if direct_specs:
            return direct_specs

        return default_specs

    def _build_sensor_avg_band_lines(
        self,
        file_path,
        x_col,
        y_col,
        sensor_col,
        sensors,
        axis_id,
        band_specs,
        band_alpha
    ):

        if not sensors:
            return []

        if sensor_col <= 0:
            return []

        x_index = x_col - 1
        y_index = y_col - 1
        sensor_index = sensor_col - 1

        sensor_set = set(sensors)
        sensor_series = {
            sensor_id: {"x": [], "y": []}
            for sensor_id in sensors
        }

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= max(x_index, y_index, sensor_index):
                continue

            sensor_id = row[sensor_index].strip()
            if sensor_id not in sensor_set:
                continue

            x_value = _coerce_plot_x_value(row[x_index].strip())
            y_value = _coerce_numeric_value(row[y_index].strip())

            if x_value is None or y_value is None:
                continue

            sensor_series[sensor_id]["x"].append(x_value)
            sensor_series[sensor_id]["y"].append(y_value)

        base_sensor = next(
            (sensor_id for sensor_id in sensors if sensor_series[sensor_id]["x"]),
            None
        )

        if base_sensor is None:
            return []

        base_x = sensor_series[base_sensor]["x"]

        y_series_list = [
            sensor_series[sensor_id]["y"]
            for sensor_id in sensors
            if sensor_series[sensor_id]["y"]
        ]

        if not y_series_list:
            return []

        min_len = min(len(base_x), *(len(series) for series in y_series_list))

        if min_len <= 0:
            return []

        x_values = base_x[:min_len]
        trimmed_series = [series[:min_len] for series in y_series_list]

        avg_values = []
        for idx in range(min_len):
            avg_values.append(
                sum(series[idx] for series in trimmed_series) / len(trimmed_series)
            )

        base_label = os.path.basename(file_path)

        if not band_specs:
            return []

        ref_lines = []

        band_styles = [
            ("#c4b5fd", "#7c3aed"),
            ("#f472b6", "#9d174d"),
            ("#93c5fd", "#1d4ed8"),
            ("#86efac", "#15803d"),
            ("#fdba74", "#c2410c"),
            ("#f9a8d4", "#be185d"),
        ]

        sorted_specs = sorted(
            band_specs,
            key=lambda item: item.get("value", 0.0),
            reverse=True,
        )

        for idx, spec in enumerate(sorted_specs):
            kind = spec.get("kind")
            value = float(spec.get("value", 0.0))

            if value <= 0:
                continue

            if kind == "percent":
                ratio = value / 100.0
                y_high = [avg * (1.0 + ratio) for avg in avg_values]
                y_low = [avg * (1.0 - ratio) for avg in avg_values]
                display_value = f"{value:g}%"
            else:
                y_high = [avg + value for avg in avg_values]
                y_low = [avg - value for avg in avg_values]
                display_value = f"{value:g}"

            fill_color, edge_color = band_styles[idx % len(band_styles)]
            band_legend = f"Avg ±{display_value}"

            ref_lines.append(
                {
                    "mode": "band",
                    "file_path": file_path,
                    "x_values": x_values,
                    "y_low": y_low,
                    "y_high": y_high,
                    "axis_id": axis_id,
                    "color": fill_color,
                    "edge_color": edge_color,
                    "edge_width": 1.4,
                    "edge_style": "--",
                    "alpha": band_alpha,
                    "label": "_nolegend_",
                    "band_label": f"{base_label} | Y{y_col} {band_legend}",
                    "band_legend": band_legend,
                }
            )

        return ref_lines

    def _guess_sensor_column(self, file_path):

        try:
            _, _, header, has_header = _get_cached_file_data(file_path)

            if not header:
                return 1

            if not has_header:
                return 1

            for index, name in enumerate(header, start=1):

                normalized = name.strip().lower().replace("_", " ")

                if "sensor" in normalized and "no" in normalized:
                    return index

                if normalized in {"sensor", "sensor id", "sensorid"}:
                    return index

        except Exception:
            pass

        return 2

    def _build_tone_colors(self, base_hex, count):

        if count <= 1:
            return [base_hex]

        base = QColor(base_hex)

        if not base.isValid():
            return [base_hex] * count

        r = base.red()
        g = base.green()
        b = base.blue()

        colors = []

        for index in range(count):
            ratio = index / max(1, count - 1)

            if ratio <= 0.5:
                # Lighter variants for the first half.
                strength = ratio * 0.36
                rr = r + int((255 - r) * strength)
                gg = g + int((255 - g) * strength)
                bb = b + int((255 - b) * strength)
            else:
                # Darker variants for the second half.
                strength = (ratio - 0.5) * 0.56
                rr = int(r * (1.0 - strength))
                gg = int(g * (1.0 - strength))
                bb = int(b * (1.0 - strength))

            colors.append(QColor(rr, gg, bb).name())

        return colors

    def _detect_column_count(self, file_path):

        rows, _, _, _ = _get_cached_file_data(file_path)
        max_cols = 0

        for row_index, row in enumerate(rows, start=1):
            max_cols = max(max_cols, len(row))

            if row_index >= 10000:
                break

        return max_cols

    def _detect_header_names(self, file_path, column_count):

        header_names = {}

        try:
            _, _, first_row, has_header = _get_cached_file_data(file_path)

            if not first_row or not has_header:
                return header_names

            for idx in range(min(column_count, len(first_row))):
                raw_name = (first_row[idx] or "").strip()

                if not raw_name:
                    continue

                header_names[idx + 1] = raw_name

        except Exception:
            return {}

        return header_names

    def _read_xy_series_for_export(self, file_path, x_col, y_col):

        x_values = []
        y_values = []

        x_index = x_col - 1
        y_index = y_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= y_index:
                continue

            y_raw = row[y_index].strip()

            if not y_raw:
                continue

            y_value = _coerce_numeric_value(y_raw)
            if y_value is None:
                continue

            if x_col == 0:
                x_values.append(float(len(y_values) + 1))
                y_values.append(y_value)
                continue

            if len(row) <= x_index:
                continue

            x_raw = row[x_index].strip()

            if not x_raw:
                continue

            x_value = _coerce_plot_x_value(x_raw)
            if x_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)

        return x_values, y_values

    def _read_sensor_xy_series_for_export(
        self,
        file_path,
        x_col,
        y_col,
        sensor_col,
        sensor_id
    ):

        x_values = []
        y_values = []

        x_index = x_col - 1
        y_index = y_col - 1
        sensor_index = sensor_col - 1

        _, data_rows, _, _ = _get_cached_file_data(file_path)

        for row in data_rows:

            if len(row) <= max(y_index, sensor_index):
                continue

            if row[sensor_index].strip() != sensor_id:
                continue

            y_raw = row[y_index].strip()

            if not y_raw:
                continue

            y_value = _coerce_numeric_value(y_raw)
            if y_value is None:
                continue

            if x_col == 0:
                x_values.append(float(len(y_values) + 1))
                y_values.append(y_value)
                continue

            if len(row) <= x_index:
                continue

            x_raw = row[x_index].strip()

            if not x_raw:
                continue

            x_value = _coerce_plot_x_value(x_raw)
            if x_value is None:
                continue

            x_values.append(x_value)
            y_values.append(y_value)

        return x_values, y_values

    def _read_sensor_deviation_series_for_export(
        self,
        file_path,
        x_col,
        y_col,
        sensor_col,
        sensor_group_size,
    ):

        cache_key = (
            file_path,
            x_col,
            y_col,
            sensor_col,
            sensor_group_size,
        )
        if cache_key in self._sensor_deviation_series_cache:
            return self._sensor_deviation_series_cache[cache_key]

        if sensor_col <= 0:
            return [], [], []

        x_index = x_col - 1
        y_index = y_col - 1
        sensor_index = sensor_col - 1

        x_values = []
        red_dev_values = []
        blue_dev_values = []

        _, data_rows, _, _ = _get_cached_file_data(file_path)
        current_group = []
        seen_sensors = set()

        def _sensor_sort_key(sensor_text):
            token = (sensor_text or "").strip()
            number = _coerce_numeric_value(token)
            if number is None:
                return (1, token)
            return (0, number)

        def flush_group(group_rows):
            # Adaptive grouping: use all available sensors in the current cycle.
            if len(group_rows) < 2:
                return

            group_y_values = [item[2] for item in group_rows]
            average_value = sum(group_y_values) / len(group_y_values)
            delta_values = [value - average_value for value in group_y_values]

            if abs(average_value) < 1e-12:
                if any(abs(value) >= 1e-12 for value in group_y_values):
                    return
                red_dev = 0.0
                blue_dev = 0.0
            else:
                denominator = abs(average_value)
                max_delta = max(delta_values)
                min_delta = min(delta_values)
                upper_dev = abs(max_delta) / denominator * 100.0
                lower_dev = abs(min_delta) / denominator * 100.0
                red_dev = max(upper_dev, lower_dev)
                blue_dev = min(upper_dev, lower_dev)

            if x_col == 0:
                anchor_x = float(len(x_values) + 1)
            else:
                anchor_row = min(group_rows, key=lambda row: _sensor_sort_key(row[0]))
                anchor_x = anchor_row[1]
                if anchor_x is None:
                    return

            x_values.append(anchor_x)
            red_dev_values.append(red_dev)
            blue_dev_values.append(blue_dev)

        for row in data_rows:

            if len(row) <= max(y_index, sensor_index):
                continue

            sensor_value = row[sensor_index].strip()
            if not sensor_value:
                continue

            y_value = _coerce_numeric_value(row[y_index].strip())
            if y_value is None:
                continue

            if x_col == 0:
                x_value = None
            else:
                if len(row) <= x_index:
                    continue

                x_raw = row[x_index].strip()
                if not x_raw:
                    continue

                x_value = _coerce_plot_x_value(x_raw)
                if x_value is None:
                    continue

            if sensor_value in seen_sensors:
                flush_group(current_group)
                current_group = []
                seen_sensors = set()

            current_group.append((sensor_value, x_value, y_value))
            seen_sensors.add(sensor_value)

            # Group boundary is detected by sensor id repetition, so missing sensors
            # naturally form a smaller valid group (e.g. 1,2,4).

        flush_group(current_group)

        result = (x_values, red_dev_values, blue_dev_values)
        self._sensor_deviation_series_cache[cache_key] = result
        return result

    def _build_unique_png_path(self, output_dir, file_name):

        base_name, ext = os.path.splitext(file_name)

        output_path = os.path.join(output_dir, file_name)
        counter = 2

        while os.path.exists(output_path):
            output_path = os.path.join(
                output_dir,
                f"{base_name}_{counter}{ext}"
            )
            counter += 1

        return output_path

    def _fit_single_file_batch_figure(self, figure, axes, figure_title=None):

        try:
            axes = [axis for axis in axes if axis is not None]
            if not axes:
                return

            for _ in range(2):
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                fig_inverse = figure.transFigure.inverted()

                bboxes = []

                if figure_title and getattr(figure, "_suptitle", None) is not None:
                    try:
                        bboxes.append(figure._suptitle.get_window_extent(renderer))
                    except Exception:
                        pass

                for axis in axes:
                    try:
                        tight_bbox = axis.get_tightbbox(renderer)
                        if tight_bbox is not None:
                            bboxes.append(tight_bbox)
                    except Exception:
                        pass

                    legend = axis.get_legend()
                    if legend is not None:
                        try:
                            legend_bbox = legend.get_window_extent(renderer)
                            if legend_bbox is not None:
                                bboxes.append(legend_bbox)
                        except Exception:
                            pass

                if not bboxes:
                    return

                union_bbox = Bbox.union(bboxes).transformed(fig_inverse)
                current = figure.subplotpars

                left_overflow = max(0.0, 0.02 - union_bbox.x0)
                right_overflow = max(0.0, union_bbox.x1 - 0.98)
                bottom_overflow = max(0.0, 0.02 - union_bbox.y0)
                top_overflow = max(0.0, union_bbox.y1 - 0.98)

                left = min(max(current.left + left_overflow + 0.015, 0.06), 0.20)
                right = max(min(current.right - right_overflow - 0.015, 0.96), left + 0.45)
                bottom = min(max(current.bottom + bottom_overflow + 0.015, 0.16), 0.44)
                top = max(min(current.top - top_overflow - 0.015, 0.985), bottom + 0.32)

                if abs(left - current.left) < 0.002 and abs(right - current.right) < 0.002 and abs(bottom - current.bottom) < 0.002 and abs(top - current.top) < 0.002:
                    break

                figure.subplots_adjust(
                    left=left,
                    right=right,
                    top=top,
                    bottom=bottom,
                    hspace=current.hspace,
                )
        except Exception:
            return

    def _sanitize_name_for_file(self, text):

        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
        cleaned = cleaned.strip("._")

        return cleaned or "plot"

    def _render_single_batch_axis(
        self,
        axis,
        file_path,
        config,
        palette,
        y_col,
        y_axis_name,
        selected_color,
        band_rules,
        axis_title,
        x_axis_name,
        x_source_unit,
        x_time_unit,
        show_x_label,
        x_col_override=None,
        sensor_mode_override=None,
    ):

        line_drawn = False
        has_datetime_x = False
        x_bounds = (None, None)

        x_col_value = config["x_col"] if x_col_override is None else x_col_override
        sensor_mode = config.get("sensor_mode", False)
        if sensor_mode_override is not None:
            sensor_mode = bool(sensor_mode_override)

        if sensor_mode and config.get("sensor_col", 0) > 0:

            sensors = self._discover_sensors(
                file_path,
                config["sensor_col"]
            )

            sensor_series = []
            use_manual_color = config.get("manual_color_mode", False)

            if use_manual_color:
                sensor_colors = self._build_tone_colors(
                    selected_color,
                    max(1, len(sensors))
                )
            else:
                sensor_colors = [
                    palette[sensor_idx % len(palette)]
                    for sensor_idx in range(max(1, len(sensors)))
                ]

            for sensor_idx, sensor_id in enumerate(sensors):

                x_values, y_values = self._read_sensor_xy_series_for_export(
                    file_path,
                    x_col_value,
                    y_col,
                    config["sensor_col"],
                    sensor_id
                )

                point_count = min(len(x_values), len(y_values))

                if point_count == 0:
                    continue

                x_plot = x_values[:point_count]
                y_plot = y_values[:point_count]

                if x_col_value != 0:
                    x_plot = _normalize_x_values_to_seconds(
                        x_plot,
                        x_source_unit,
                    )

                x_bounds = _merge_axis_bounds(x_bounds, x_plot)

                color = sensor_colors[sensor_idx % len(sensor_colors)]

                if _series_has_datetime(x_plot):
                    has_datetime_x = True

                if config.get("plot_mode") == "point":
                    axis.scatter(
                        x_plot,
                        y_plot,
                        color=color,
                        s=max(1, config.get("point_size", 24)),
                        label=f"Sensor {sensor_id}"
                    )
                else:
                    axis.plot(
                        x_plot,
                        y_plot,
                        color=color,
                        linewidth=config.get("line_width", 1.8),
                        label=f"Sensor {sensor_id}"
                    )

                sensor_series.append((sensor_id, x_plot, y_plot))
                line_drawn = True

            y_band_specs = []

            if config.get("sensor_band_enabled", False):
                y_band_specs = self._resolve_sensor_band_specs_for_y(
                    y_col,
                    config.get("sensor_band_default_rules", ""),
                    band_rules,
                )

            if sensor_series and y_band_specs:
                min_len = min(len(series[2]) for series in sensor_series)

                if min_len > 0:
                    x_band = sensor_series[0][1][:min_len]
                    y_stack = [series[2][:min_len] for series in sensor_series]
                    avg_values = [
                        sum(values[idx] for values in y_stack) / len(y_stack)
                        for idx in range(min_len)
                    ]

                    band_styles = [
                        ("#c4b5fd", "#7c3aed"),
                        ("#f472b6", "#9d174d"),
                        ("#93c5fd", "#1d4ed8"),
                        ("#86efac", "#15803d"),
                        ("#fdba74", "#c2410c"),
                        ("#f9a8d4", "#be185d"),
                    ]

                    sorted_specs = sorted(
                        y_band_specs,
                        key=lambda item: item.get("value", 0.0),
                        reverse=True,
                    )

                    for band_idx, spec in enumerate(sorted_specs):
                        kind = spec.get("kind")
                        value = float(spec.get("value", 0.0))

                        if value <= 0:
                            continue

                        if kind == "percent":
                            ratio = value / 100.0
                            y_high = [avg * (1.0 + ratio) for avg in avg_values]
                            y_low = [avg * (1.0 - ratio) for avg in avg_values]
                            display_value = f"{value:g}%"
                        else:
                            y_high = [avg + value for avg in avg_values]
                            y_low = [avg - value for avg in avg_values]
                            display_value = f"{value:g}"

                        fill_color, edge_color = band_styles[band_idx % len(band_styles)]

                        axis.fill_between(
                            x_band,
                            y_low,
                            y_high,
                            facecolor=fill_color,
                            edgecolor=edge_color,
                            alpha=config.get("sensor_band_alpha", 0.2),
                            linewidth=1.4,
                            linestyle="--",
                            label=f"Avg ±{display_value}"
                        )
        else:
            x_values, y_values = self._read_xy_series_for_export(
                file_path,
                x_col_value,
                y_col
            )

            point_count = min(len(x_values), len(y_values))

            if point_count > 0:
                x_plot = x_values[:point_count]
                y_plot = y_values[:point_count]

                if x_col_value != 0:
                    x_plot = _normalize_x_values_to_seconds(
                        x_plot,
                        x_source_unit,
                    )

                x_bounds = _merge_axis_bounds(x_bounds, x_plot)

                if _series_has_datetime(x_plot):
                    has_datetime_x = True

                series_color = (
                    selected_color
                    if config.get("manual_color_mode", False)
                    else "#1982c4"
                )

                if config.get("plot_mode") == "point":
                    axis.scatter(
                        x_plot,
                        y_plot,
                        color=series_color,
                        s=max(1, config.get("point_size", 24)),
                        label=f"Y{y_col}"
                    )
                else:
                    axis.plot(
                        x_plot,
                        y_plot,
                        color=series_color,
                        linewidth=config.get("line_width", 1.8),
                        label=f"Y{y_col}"
                    )

                line_drawn = True

        axis.set_title(
            axis_title,
            color="#111827",
            fontsize=11,
            loc="left",
            pad=10
        )

        if show_x_label:
            axis.set_xlabel(x_axis_name, color="#374151", fontsize=8)
            axis.xaxis.labelpad = 2

        axis.set_ylabel(y_axis_name, color="#374151", fontsize=8)

        _apply_time_unit_x_axis(
            axis,
            x_time_unit,
            has_datetime_x,
        )

        axis.grid(
            True,
            color="#d1d5db",
            alpha=0.6,
            linestyle="--",
            linewidth=0.8
        )
        axis.tick_params(colors="#374151")
        axis.margins(x=0.0)

        for spine in axis.spines.values():
            spine.set_color("#d1d5db")

        if line_drawn:
            legend_handles, legend_labels = axis.get_legend_handles_labels()
            compact_labels = [
                _compact_legend_label_text(label)
                for label in legend_labels
            ]
            legend_ncol = max(1, len(compact_labels))
            axis.legend(
                legend_handles,
                compact_labels,
                facecolor="#ffffff",
                edgecolor="#d1d5db",
                fontsize=7,
                loc="upper left",
                bbox_to_anchor=(0.0, -0.20, 1.0, 0.1),
                ncol=legend_ncol,
                mode="expand",
                borderpad=0.24,
                labelspacing=0.22,
                handlelength=1.2,
                handletextpad=0.35,
                columnspacing=0.9,
                borderaxespad=0.0,
            )
        else:
            axis.text(
                0.5,
                0.5,
                "No numeric points found for this Y column.",
                color="#4b5563",
                ha="center",
                va="center",
                transform=axis.transAxes
            )

        return line_drawn, x_bounds, has_datetime_x

    def _render_sensor_deviation_axis(
        self,
        axis,
        file_path,
        config,
        y_col,
        axis_title,
        x_axis_name,
        x_source_unit,
        x_time_unit,
        deviation_axis_name,
        show_x_label,
    ):

        axis.set_facecolor("#ffffff")
        has_datetime_x = False
        x_bounds = (None, None)

        x_values, red_dev_values, blue_dev_values = self._read_sensor_deviation_series_for_export(
            file_path,
            int(config.get("x_col", 1)),
            y_col,
            int(config.get("sensor_col", 0)),
            int(config.get("sensor_group_size", 8)),
        )

        if not x_values:
            axis.text(
                0.5,
                0.5,
                "No complete sensor groups were found for deviation analysis.",
                color="#4b5563",
                ha="center",
                va="center",
                transform=axis.transAxes
            )
            axis.set_title(
                axis_title,
                color="#111827",
                fontsize=11,
                loc="left",
                pad=10,
            )
            axis.set_xlabel(x_axis_name if show_x_label else "", color="#374151")
            axis.set_ylabel(deviation_axis_name, color="#374151")
            return False, x_bounds, has_datetime_x

        x_plot = list(x_values)
        if int(config.get("x_col", 1)) != 0:
            x_plot = _normalize_x_values_to_seconds(
                x_plot,
                x_source_unit,
            )

        x_bounds = _merge_axis_bounds(x_bounds, x_plot)
        if _series_has_datetime(x_plot):
            has_datetime_x = True

        plot_mode = config.get("plot_mode", "line")
        line_width = float(config.get("line_width", 1.8))
        point_size = int(config.get("point_size", 24))

        max_dev_color = "#dc2626"
        min_dev_color = "#2563eb"

        max_label = "Larger Deviation (%)"
        min_label = "Smaller Deviation (%)"

        if plot_mode == "point":
            axis.scatter(
                x_plot,
                red_dev_values,
                color=max_dev_color,
                s=max(1, point_size),
                label=max_label,
            )
            axis.scatter(
                x_plot,
                blue_dev_values,
                color=min_dev_color,
                s=max(1, point_size),
                label=min_label,
            )
        else:
            axis.plot(
                x_plot,
                red_dev_values,
                color=max_dev_color,
                linewidth=line_width,
                label=max_label,
            )
            axis.plot(
                x_plot,
                blue_dev_values,
                color=min_dev_color,
                linewidth=line_width,
                label=min_label,
            )

        axis.axhline(
            0.0,
            color="#94a3b8",
            linewidth=1.0,
            linestyle="--",
            alpha=0.9,
        )

        axis.set_title(
            axis_title,
            color="#111827",
            fontsize=11,
            loc="left",
            pad=10,
        )
        axis.set_xlabel(x_axis_name if show_x_label else "", color="#374151")
        axis.xaxis.labelpad = 2
        axis.set_ylabel(deviation_axis_name, color="#374151")
        axis.tick_params(axis="y", colors="#374151")
        axis.spines["left"].set_color("#94a3b8")

        # Fixed deviation scale for easier comparison across exports.
        axis.set_ylim(0.0, 100.0)
        axis.set_yticks([0, 5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100])

        # Emphasize key thresholds requested by analysis workflow.
        for focus_level in (5, 10, 15, 20, 30, 40):
            axis.axhline(
                focus_level,
                color="#111111",
                linewidth=1.1,
                linestyle="--",
                alpha=0.72,
                zorder=0,
            )

        _apply_time_unit_x_axis(
            axis,
            x_time_unit,
            has_datetime_x,
        )

        axis.grid(
            True,
            color="#d1d5db",
            alpha=0.6,
            linestyle="--",
            linewidth=0.8
        )
        axis.tick_params(axis="x", colors="#374151")
        axis.margins(x=0.0)
        axis.spines["top"].set_color("#d1d5db")
        axis.spines["bottom"].set_color("#d1d5db")

        handles, labels = axis.get_legend_handles_labels()

        if handles:
            compact_labels = [
                _compact_legend_label_text(label)
                for label in labels
            ]
            axis.legend(
                handles,
                compact_labels,
                facecolor="#ffffff",
                edgecolor="#d1d5db",
                fontsize=7,
                loc="upper left",
                bbox_to_anchor=(0.0, -0.28, 1.0, 0.1),
                ncol=max(1, len(compact_labels)),
                mode="expand",
                borderpad=0.24,
                labelspacing=0.22,
                handlelength=1.2,
                handletextpad=0.35,
                columnspacing=0.9,
                borderaxespad=0.0,
            )

        return True, x_bounds, has_datetime_x

    def _render_single_batch_secondary_axis(
        self,
        base_axis,
        file_path,
        x_col,
        y_cols,
        y_axis_names,
        colors,
        plot_mode,
        line_width,
        point_size,
        x_axis_name,
        x_source_unit,
        x_time_unit,
        axis_title,
        enable_multi_y,
    ):

        base_axis.set_facecolor("#ffffff")
        has_datetime_x = False
        x_bounds = (None, None)

        if not y_cols:
            base_axis.text(
                0.5,
                0.5,
                "Chart 2 has no valid Y columns.",
                color="#4b5563",
                ha="center",
                va="center",
                transform=base_axis.transAxes
            )
            base_axis.set_xlabel(x_axis_name, color="#374151", fontsize=8)
            return False, x_bounds, has_datetime_x

        parsed_axis_names = list(y_axis_names or [])
        parsed_colors = list(colors or [])

        while len(parsed_axis_names) < len(y_cols):
            idx = len(parsed_axis_names)
            parsed_axis_names.append(f"Y{y_cols[idx]}")

        while len(parsed_colors) < len(y_cols):
            idx = len(parsed_colors)
            parsed_colors.append(self._palette[idx % len(self._palette)])

        validated_colors = []
        for idx, color_text in enumerate(parsed_colors):
            candidate = (color_text or "").strip()
            if candidate and QColor(candidate).isValid():
                validated_colors.append(candidate)
            else:
                validated_colors.append(self._palette[idx % len(self._palette)])
        parsed_colors = validated_colors

        axes = [base_axis]
        if enable_multi_y:
            for axis_offset in range(1, len(y_cols)):
                extra_axis = base_axis.twinx()
                # Keep the first right axis on the default spine so it remains visible;
                # only additional right axes are shifted outward.
                if axis_offset >= 2:
                    extra_axis.spines["right"].set_position(("outward", 22 * (axis_offset - 1)))
                axes.append(extra_axis)
        else:
            axes = [base_axis for _ in y_cols]

        any_line = False
        axis_color_map = {}

        for idx, y_col in enumerate(y_cols):

            axis = axes[idx]
            axis.set_facecolor("#ffffff")

            x_values, y_values = self._read_xy_series_for_export(
                file_path,
                x_col,
                y_col,
            )

            point_count = min(len(x_values), len(y_values))
            if point_count == 0:
                continue

            x_plot = x_values[:point_count]
            y_plot = y_values[:point_count]

            if x_col != 0:
                x_plot = _normalize_x_values_to_seconds(
                    x_plot,
                    x_source_unit,
                )

            x_bounds = _merge_axis_bounds(x_bounds, x_plot)

            if _series_has_datetime(x_plot):
                has_datetime_x = True

            color = parsed_colors[idx]
            label = f"Y{y_col}"

            if plot_mode == "point":
                axis.scatter(
                    x_plot,
                    y_plot,
                    color=color,
                    s=max(1, point_size),
                    label=label,
                )
            else:
                axis.plot(
                    x_plot,
                    y_plot,
                    color=color,
                    linewidth=line_width,
                    label=label,
                )

            axis_name = parsed_axis_names[idx]
            axis.set_ylabel(axis_name, color=color, fontsize=8)
            axis.tick_params(axis="y", colors=color)

            if axis is base_axis:
                axis.spines["left"].set_color(color)
                axis.yaxis.set_label_position("left")
                axis.yaxis.set_label_coords(-0.03, 0.5)
            else:
                axis.spines["right"].set_color(color)
                axis.yaxis.set_label_position("right")
                axis.yaxis.tick_right()
                axis.yaxis.set_label_coords(1.04, 0.5)
                axis.tick_params(axis="y", pad=2)

            axis_color_map[axis] = color

            any_line = True

        base_axis.set_title(
            axis_title,
            color="#111827",
            fontsize=11,
            loc="left",
            pad=10
        )
        base_axis.set_xlabel(x_axis_name, color="#374151", fontsize=8)
        base_axis.xaxis.labelpad = 2

        _apply_time_unit_x_axis(
            base_axis,
            x_time_unit,
            has_datetime_x,
        )

        base_axis.grid(
            True,
            color="#d1d5db",
            alpha=0.6,
            linestyle="--",
            linewidth=0.8
        )
        base_axis.tick_params(axis="x", colors="#374151")
        base_axis.margins(x=0.0)
        base_axis.spines["top"].set_color("#d1d5db")
        base_axis.spines["bottom"].set_color("#d1d5db")

        if base_axis not in axis_color_map:
            base_axis.spines["left"].set_color("#d1d5db")

        handles = []
        labels = []
        for axis in axes:
            h, l = axis.get_legend_handles_labels()
            handles.extend(h)
            labels.extend(l)

        if handles:
            compact_labels = [
                _compact_legend_label_text(label)
                for label in labels
            ]
            legend_ncol = max(1, len(compact_labels))
            base_axis.legend(
                handles,
                compact_labels,
                facecolor="#ffffff",
                edgecolor="#d1d5db",
                fontsize=7,
                loc="upper left",
                bbox_to_anchor=(0.0, -0.28, 1.0, 0.1),
                ncol=legend_ncol,
                mode="expand",
                borderpad=0.24,
                labelspacing=0.22,
                handlelength=1.2,
                handletextpad=0.35,
                columnspacing=0.9,
                borderaxespad=0.0,
            )
        elif not any_line:
            base_axis.text(
                0.5,
                0.5,
                "No numeric points found for Chart 2.",
                color="#4b5563",
                ha="center",
                va="center",
                transform=base_axis.transAxes
            )

        return any_line, x_bounds, has_datetime_x

    def open_single_file_batch_export(self):

        file_path = self._safe_open_single_file(
            title="Select One Data File for Batch PNG Export",
            file_filter="All Files (*);;Text Files (*.txt *.csv)",
        )

        if not file_path:
            return

        try:
            column_count = self._detect_column_count(file_path)

            if column_count <= 0:
                QMessageBox.warning(
                    self,
                    "Notice",
                    "No usable columns were detected in this file."
                )
                return

            default_sensor_col = self._guess_sensor_column(file_path)
            default_sensor_col = min(max(1, default_sensor_col), column_count)
            header_names = self._detect_header_names(file_path, column_count)

            config_dialog = SingleFileBatchExportConfigDialog(
                file_path,
                column_count,
                default_sensor_col,
                header_names,
                self
            )

            if config_dialog.exec() != QDialog.Accepted:
                return

            config = config_dialog.get_config()
            export_items = config.get("exports", [])
            secondary_plot_config = config.get("secondary_plot", {})
            secondary_enabled = bool(secondary_plot_config.get("enabled", False))
            figure_title = (config.get("figure_title", "") or "").strip()

            if secondary_enabled:
                secondary_file_path = secondary_plot_config.get("file_path", "").strip()

                if not secondary_file_path or not os.path.isfile(secondary_file_path):
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "Chart 2 is enabled, but no valid Chart 2 file is selected."
                    )
                    return

            if not export_items:
                QMessageBox.warning(
                    self,
                    "Notice",
                    "Please enable at least one Y column to export."
                )
                return

            output_dir = self.outputLabel.text()

            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            palette = [
                "#36d2ff",
                "#54f4a5",
                "#ffbc42",
                "#ff6b6b",
                "#b388ff",
                "#5be7ff",
                "#8ac926",
                "#1982c4",
                "#f72585",
                "#ffd166",
                "#06d6a0",
                "#ef476f",
            ]

            progress = QProgressDialog(
                "Exporting PNG files...",
                None,
                0,
                len(export_items),
                self
            )
            progress.setWindowTitle("Working")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()

            written_files = []
            secondary_file_path = secondary_plot_config.get("file_path", "")
            secondary_x_col = int(secondary_plot_config.get("x_col", 1))
            secondary_x_axis_name = secondary_plot_config.get("x_axis_name", "X values")
            secondary_x_source_unit = secondary_plot_config.get("x_source_unit", "s")
            secondary_x_time_unit = secondary_plot_config.get("x_time_unit", "auto")
            secondary_title = secondary_plot_config.get("title", "Chart 2")
            secondary_y_cols = [
                int(value)
                for value in secondary_plot_config.get("y_cols", [])
                if isinstance(value, int) or (isinstance(value, str) and str(value).isdigit())
            ]
            secondary_y_axis_names = secondary_plot_config.get("y_axis_names", [])
            secondary_colors = secondary_plot_config.get("colors", [])
            secondary_multi_y = bool(secondary_plot_config.get("multi_y", True))
            secondary_plot_mode = secondary_plot_config.get("plot_mode", "line")
            secondary_line_width = float(secondary_plot_config.get("line_width", 1.8))
            secondary_point_size = int(secondary_plot_config.get("point_size", 24))

            for idx, export_item in enumerate(export_items, start=1):

                y_col = export_item["y_col"]
                y_axis_name = export_item["y_axis_name"]
                custom_title = export_item.get("chart_title", "").strip()
                png_suffix = self._sanitize_name_for_file(export_item["png_suffix"])
                selected_color = export_item.get("color", "#1982c4")

                progress.setLabelText(
                    f"Exporting {idx}/{len(export_items)}: Y Column {y_col}"
                )
                QApplication.processEvents()

                figure = Figure(
                    facecolor="#ffffff",
                    figsize=(14.8, 11.0 if secondary_enabled else 7.4)
                )

                if secondary_enabled:
                    top_axis = figure.add_subplot(2, 1, 1)
                    bottom_axis = figure.add_subplot(2, 1, 2, sharex=top_axis)
                else:
                    top_axis = figure.add_subplot(1, 1, 1)
                    bottom_axis = None

                top_axis.set_facecolor("#ffffff")
                chart1_drawn, chart1_bounds, chart1_has_datetime = self._render_single_batch_axis(
                    top_axis,
                    file_path,
                    config,
                    palette,
                    y_col,
                    y_axis_name,
                    selected_color,
                    export_item.get("band_rules", ""),
                    custom_title or f"{os.path.basename(file_path)} | Chart 1 | Y Column {y_col}",
                    config.get("x_axis_name", "X values"),
                    config.get("x_source_unit", "s"),
                    config.get("x_time_unit", "auto"),
                    True,
                )

                combined_bounds = chart1_bounds
                combined_has_datetime = chart1_has_datetime

                if secondary_enabled and bottom_axis is not None:
                    bottom_axis.set_facecolor("#ffffff")
                    chart2_drawn, chart2_bounds, chart2_has_datetime = self._render_single_batch_secondary_axis(
                        bottom_axis,
                        secondary_file_path,
                        secondary_x_col,
                        secondary_y_cols,
                        secondary_y_axis_names,
                        secondary_colors,
                        secondary_plot_mode,
                        secondary_line_width,
                        secondary_point_size,
                        secondary_x_axis_name,
                        secondary_x_source_unit,
                        secondary_x_time_unit,
                        secondary_title or f"{os.path.basename(secondary_file_path)} | Chart 2",
                        secondary_multi_y,
                    )

                    combined_bounds = _merge_axis_bounds(combined_bounds, [chart2_bounds[0], chart2_bounds[1]])
                    combined_has_datetime = combined_has_datetime or chart2_has_datetime

                if combined_bounds[0] is not None and combined_bounds[1] is not None:
                    x_min, x_max = combined_bounds
                    top_axis.set_xlim(x_min, x_max)
                    if bottom_axis is not None:
                        bottom_axis.set_xlim(x_min, x_max)
                    if combined_has_datetime:
                        top_axis.figure.autofmt_xdate(rotation=0, ha="center")

                if figure_title:
                    figure.suptitle(
                        figure_title,
                        x=0.5,
                        y=0.985,
                        fontsize=14,
                        color="#111827",
                        ha="center",
                        va="top",
                    )

                self._fit_single_file_batch_figure(
                    figure,
                    [top_axis, bottom_axis],
                    figure_title if figure_title else None,
                )

                output_name = f"{self._sanitize_name_for_file(config['export_prefix'])}_{png_suffix}.png"
                output_path = self._build_unique_png_path(output_dir, output_name)

                figure.savefig(
                    output_path,
                    dpi=240,
                    facecolor="white",
                    bbox_inches="tight",
                    pad_inches=0.12,
                )

                figure.clear()

                written_files.append(output_path)
                progress.setValue(idx)
                QApplication.processEvents()

            progress.close()

            self.log("")
            self.log("====================")
            self.log("Single-file batch PNG export completed.")
            self.log(f"Source: {os.path.basename(file_path)}")

            for path in written_files:
                self.log(f"Exported: {os.path.basename(path)}")

            QMessageBox.information(
                self,
                "Done",
                f"Exported {len(written_files)} PNG file(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def open_sensor_deviation_batch_export(self):

        file_path = self._safe_open_single_file(
            title="Select One Data File for Sensor Deviation Export",
            file_filter="All Files (*);;Text Files (*.txt *.csv)",
        )

        if not file_path:
            return

        try:
            column_count = self._detect_column_count(file_path)

            if column_count <= 0:
                QMessageBox.warning(
                    self,
                    "Notice",
                    "No usable columns were detected in this file."
                )
                return

            default_sensor_col = self._guess_sensor_column(file_path)
            default_sensor_col = min(max(1, default_sensor_col), column_count)
            header_names = self._detect_header_names(file_path, column_count)

            config_dialog = SensorDeviationBatchExportConfigDialog(
                file_path,
                column_count,
                default_sensor_col,
                header_names,
                self,
            )

            if config_dialog.exec() != QDialog.Accepted:
                return

            config = config_dialog.get_config()
            export_items = config.get("exports", [])
            secondary_plot_config = config.get("secondary_plot", {})
            secondary_enabled = bool(secondary_plot_config.get("enabled", False))
            figure_title = (config.get("figure_title", "") or "").strip()
            sensor_group_size = int(config.get("sensor_group_size", 0))

            if sensor_group_size < 2:
                QMessageBox.warning(
                    self,
                    "Notice",
                    "Detected sensor count is less than 2, so deviation analysis cannot be computed."
                )
                return

            if secondary_enabled:
                secondary_file_path = secondary_plot_config.get("file_path", "").strip()

                if not secondary_file_path or not os.path.isfile(secondary_file_path):
                    QMessageBox.warning(
                        self,
                        "Notice",
                        "Chart 2 is enabled, but no valid Chart 2 file is selected."
                    )
                    return

            if not export_items:
                QMessageBox.warning(
                    self,
                    "Notice",
                    "Please enable at least one Y column to export."
                )
                return

            output_dir = self.outputLabel.text()

            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            progress = QProgressDialog(
                "Exporting sensor deviation PNG files...",
                None,
                0,
                len(export_items),
                self
            )
            progress.setWindowTitle("Working")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()

            written_files = []
            secondary_file_path = secondary_plot_config.get("file_path", "")
            secondary_x_col = int(secondary_plot_config.get("x_col", 1))
            secondary_x_axis_name = secondary_plot_config.get("x_axis_name", "X values")
            secondary_x_source_unit = secondary_plot_config.get("x_source_unit", "s")
            secondary_x_time_unit = secondary_plot_config.get("x_time_unit", "auto")
            secondary_title = secondary_plot_config.get("title", "Chart 2")
            secondary_y_cols = [
                int(value)
                for value in secondary_plot_config.get("y_cols", [])
                if isinstance(value, int) or (isinstance(value, str) and str(value).isdigit())
            ]
            secondary_y_axis_names = secondary_plot_config.get("y_axis_names", [])
            secondary_colors = secondary_plot_config.get("colors", [])
            secondary_multi_y = bool(secondary_plot_config.get("multi_y", True))
            secondary_plot_mode = secondary_plot_config.get("plot_mode", "line")
            secondary_line_width = float(secondary_plot_config.get("line_width", 1.8))
            secondary_point_size = int(secondary_plot_config.get("point_size", 24))

            for idx, export_item in enumerate(export_items, start=1):

                y_col = export_item["y_col"]
                custom_title = export_item.get("chart_title", "").strip()
                png_suffix = self._sanitize_name_for_file(export_item["png_suffix"])

                progress.setLabelText(
                    f"Exporting {idx}/{len(export_items)}: Y Column {y_col}"
                )
                QApplication.processEvents()

                figure = Figure(
                    facecolor="#ffffff",
                    figsize=(12, 9 if secondary_enabled else 6)
                )

                if secondary_enabled:
                    top_axis = figure.add_subplot(2, 1, 1)
                    bottom_axis = figure.add_subplot(2, 1, 2, sharex=top_axis)
                else:
                    top_axis = figure.add_subplot(1, 1, 1)
                    bottom_axis = None

                top_axis.set_facecolor("#ffffff")
                chart1_drawn, chart1_bounds, chart1_has_datetime = self._render_sensor_deviation_axis(
                    top_axis,
                    file_path,
                    config,
                    y_col,
                    custom_title or f"{os.path.basename(file_path)} | Deviation | Y Column {y_col}",
                    config.get("x_axis_name", "X values"),
                    config.get("x_source_unit", "s"),
                    config.get("x_time_unit", "auto"),
                    config.get("deviation_axis_name", "Deviation (%)"),
                    True,
                )

                combined_bounds = chart1_bounds
                combined_has_datetime = chart1_has_datetime

                if secondary_enabled and bottom_axis is not None:
                    bottom_axis.set_facecolor("#ffffff")
                    chart2_drawn, chart2_bounds, chart2_has_datetime = self._render_single_batch_secondary_axis(
                        bottom_axis,
                        secondary_file_path,
                        secondary_x_col,
                        secondary_y_cols,
                        secondary_y_axis_names,
                        secondary_colors,
                        secondary_plot_mode,
                        secondary_line_width,
                        secondary_point_size,
                        secondary_x_axis_name,
                        secondary_x_source_unit,
                        secondary_x_time_unit,
                        secondary_title or f"{os.path.basename(secondary_file_path)} | Chart 2",
                        secondary_multi_y,
                    )

                    combined_bounds = _merge_axis_bounds(combined_bounds, [chart2_bounds[0], chart2_bounds[1]])
                    combined_has_datetime = combined_has_datetime or chart2_has_datetime

                if combined_bounds[0] is not None and combined_bounds[1] is not None:
                    x_min, x_max = combined_bounds
                    top_axis.set_xlim(x_min, x_max)
                    if bottom_axis is not None:
                        bottom_axis.set_xlim(x_min, x_max)
                    if combined_has_datetime:
                        top_axis.figure.autofmt_xdate(rotation=0, ha="center")

                if figure_title:
                    figure.suptitle(
                        figure_title,
                        x=0.5,
                        y=0.985,
                        fontsize=14,
                        color="#111827",
                        ha="center",
                        va="top",
                    )

                top_margin = 0.955 if figure_title else 0.975
                bottom_margin = 0.28
                hspace = 0.58 if secondary_enabled else 0.28
                figure.subplots_adjust(
                    left=0.085,
                    right=0.94,
                    top=top_margin,
                    bottom=bottom_margin,
                    hspace=hspace,
                )

                output_name = f"{self._sanitize_name_for_file(config['export_prefix'])}_{png_suffix}.png"
                output_path = self._build_unique_png_path(output_dir, output_name)

                figure.savefig(
                    output_path,
                    dpi=240,
                    facecolor="white"
                )

                figure.clear()

                written_files.append(output_path)
                progress.setValue(idx)
                QApplication.processEvents()

            progress.close()

            self.log("")
            self.log("====================")
            self.log("Sensor deviation PNG export completed.")
            self.log(f"Source: {os.path.basename(file_path)}")

            for path in written_files:
                self.log(f"Exported: {os.path.basename(path)}")

            QMessageBox.information(
                self,
                "Done",
                f"Exported {len(written_files)} PNG file(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def open_chart_dialog(self):

        chart_configs = self._build_chart_configs()

        if not chart_configs:
            return

        try:

            dialog = ChartDialog(
                chart_configs,
                self
            )
            dialog.exec()

            self.log(
                f"Rendered {len(chart_configs)} chart(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def _build_output_path(self, source_path, suffix):

        output_dir = self.outputLabel.text()

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        base_name = os.path.splitext(os.path.basename(source_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}{suffix}.txt")
        counter = 2

        while os.path.exists(output_path):
            output_path = os.path.join(
                output_dir,
                f"{base_name}{suffix}_{counter}.txt"
            )
            counter += 1

        return output_path

    def clean_numeric_text_files(self):

        source_files = self.get_selected_or_all_files()

        if not source_files:
            QMessageBox.warning(
                self,
                "Notice",
                "Please add files first."
            )
            return

        written_files = []

        self.log("")
        self.log("====================")
        self.log("Numeric text cleaning started...")

        try:
            for file_path in source_files:
                output_path = self._build_output_path(file_path, "_cleaned")

                with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as source:
                    with open(output_path, "w", encoding="utf-8", newline="") as target:
                        for raw_line in source:
                            numbers = _extract_clean_row_tokens(raw_line)

                            if numbers:
                                target.write(",".join(numbers))
                                target.write("\n")

                written_files.append(output_path)
                self.log(
                    f"Cleaned {os.path.basename(file_path)} -> {os.path.basename(output_path)}"
                )

            self.log("====================")
            self.log("Numeric text cleaning completed.")

            QMessageBox.information(
                self,
                "Done",
                f"Generated {len(written_files)} cleaned TXT file(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def open_xyz_chart_dialog(self):

        chart_configs = self._build_xyz_chart_configs()

        if not chart_configs:
            return

        try:

            dialog = XYZChartDialog(
                chart_configs,
                self
            )
            dialog.exec()

            self.log(
                f"Rendered {len(chart_configs)} XYZ chart(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def _build_xyz_chart_configs(self):

        chart_count, ok = QInputDialog.getInt(
            self,
            "XYZ Chart Count",
            "How many XYZ charts do you want to generate?",
            1,
            1,
            12,
            1
        )

        if not ok:
            return None

        chart_configs = []

        palette = [
            "#36d2ff",
            "#54f4a5",
            "#ffbc42",
            "#ff6b6b",
            "#b388ff",
            "#5be7ff",
            "#8ac926",
            "#1982c4",
            "#f72585",
            "#ffd166",
            "#06d6a0",
            "#ef476f",
        ]

        default_files = self.get_selected_or_all_files()

        build_progress = QProgressDialog(
            "Preparing XYZ chart configuration...",
            None,
            0,
            chart_count,
            self
        )
        build_progress.setWindowTitle("Working")
        build_progress.setCancelButton(None)
        build_progress.setWindowModality(Qt.WindowModal)
        build_progress.show()

        for chart_index in range(1, chart_count + 1):

            build_progress.setLabelText(
                f"Preparing XYZ chart {chart_index}/{chart_count}..."
            )
            QApplication.processEvents()

            if default_files:
                selected_files = list(default_files)
                use_default = QMessageBox.question(
                    self,
                    "Use Selected Files",
                    (
                        f"Use currently selected file(s) for XYZ Chart {chart_index}?\n"
                        f"Count: {len(selected_files)}"
                    ),
                    QMessageBox.Yes | QMessageBox.No
                )

                if use_default == QMessageBox.No:
                    selected_files = []
            else:
                selected_files = []

            if not selected_files:
                selected_files = self._safe_open_file_names(
                    title=f"Select Files for XYZ Chart {chart_index}",
                    file_filter="All Files (*)"
                )

            if not selected_files:
                QMessageBox.information(
                    self,
                    "Notice",
                    f"No files selected for XYZ Chart {chart_index}."
                )
                build_progress.close()
                return None

            default_sensor_cols = {
                file_path: self._guess_sensor_column(file_path)
                for file_path in selected_files
            }

            config_dialog = XYZFileColumnConfigDialog(
                selected_files,
                default_sensor_cols,
                chart_index,
                self
            )

            if config_dialog.exec() != QDialog.Accepted:
                build_progress.close()
                return None

            file_column_configs = config_dialog.get_configs()
            chart_meta = config_dialog.get_meta()
            sensor_mode = chart_meta.get("sensor_mode", False)
            plot_mode = chart_meta.get("plot_mode", "line")
            line_width = chart_meta.get("line_width", 1.8)
            point_size = chart_meta.get("point_size", 24)
            lines = []

            for file_index, file_path in enumerate(selected_files):

                build_progress.setLabelText(
                    (
                        f"Preparing XYZ chart {chart_index}/{chart_count}... "
                        f"{file_index + 1}/{len(selected_files)}"
                    )
                )
                QApplication.processEvents()

                file_config = next(
                    cfg
                    for cfg in file_column_configs
                    if cfg["file_path"] == file_path
                )

                x_col = file_config["x_col"]
                y_col = file_config["y_col"]
                z_col = file_config["z_col"]
                sensor_col = file_config["sensor_col"]

                if sensor_mode and sensor_col > 0:

                    sensors = self._discover_sensors(
                        file_path,
                        sensor_col
                    )

                    if not sensors:
                        self.log(
                            f"No sensors found in {os.path.basename(file_path)} using column {sensor_col}."
                        )
                        continue

                    for sensor_index, sensor_id in enumerate(sensors):
                        lines.append(
                            {
                                "mode": "sensor",
                                "file_path": file_path,
                                "x_col": x_col,
                                "y_col": y_col,
                                "z_col": z_col,
                                "sensor_col": sensor_col,
                                "sensor_id": sensor_id,
                                "color": palette[(file_index + sensor_index) % len(palette)],
                                "plot_mode": plot_mode,
                                "line_width": line_width,
                                "point_size": point_size,
                                "label": (
                                    f"{os.path.basename(file_path)} | Sensor {sensor_id}"
                                ),
                            }
                        )

                    continue

                lines.append(
                    {
                        "mode": "column",
                        "file_path": file_path,
                        "x_col": x_col,
                        "y_col": y_col,
                        "z_col": z_col,
                        "color": palette[file_index % len(palette)],
                        "plot_mode": plot_mode,
                        "line_width": line_width,
                        "point_size": point_size,
                        "label": os.path.basename(file_path),
                    }
                )

            chart_configs.append(
                {
                    "title": chart_meta["chart_title"],
                    "x_label": chart_meta["x_axis_name"],
                    "y_label": chart_meta["y_axis_name"],
                    "z_label": chart_meta["z_axis_name"],
                    "lines": lines,
                }
            )

            build_progress.setValue(chart_index)
            QApplication.processEvents()

        build_progress.close()

        if not chart_configs:
            return None

        return chart_configs

    def _build_chart_configs(self):

        chart_count, ok = QInputDialog.getInt(
            self,
            "Chart Count",
            "How many charts do you want to generate?",
            1,
            1,
            12,
            1
        )

        if not ok:
            return None

        chart_configs = []

        palette = [
            "#36d2ff",
            "#54f4a5",
            "#ffbc42",
            "#ff6b6b",
            "#b388ff",
            "#5be7ff",
            "#8ac926",
            "#1982c4",
            "#f72585",
            "#ffd166",
            "#06d6a0",
            "#ef476f",
        ]

        default_files = self.get_selected_plot_files()

        build_progress = QProgressDialog(
            "Preparing chart configuration...",
            None,
            0,
            chart_count,
            self
        )
        build_progress.setWindowTitle("Working")
        build_progress.setCancelButton(None)
        build_progress.setWindowModality(Qt.WindowModal)
        build_progress.show()

        for chart_index in range(1, chart_count + 1):

            build_progress.setLabelText(
                f"Preparing chart {chart_index}/{chart_count}..."
            )
            QApplication.processEvents()

            if default_files:
                selected_files = list(default_files)
                use_default = QMessageBox.question(
                    self,
                    "Use Selected Files",
                    (
                        f"Use currently selected file(s) for Chart {chart_index}?\n"
                        f"Count: {len(selected_files)}"
                    ),
                    QMessageBox.Yes | QMessageBox.No
                )

                if use_default == QMessageBox.No:
                    selected_files = []
            else:
                selected_files = []

            if not selected_files:
                selected_files = self._safe_open_file_names(
                    title=f"Select Files for Chart {chart_index}",
                    file_filter="All Files (*)"
                )

            if not selected_files:
                QMessageBox.information(
                    self,
                    "Notice",
                    f"No files selected for Chart {chart_index}."
                )
                build_progress.close()
                return None

            lines = []

            default_sensor_cols = {
                file_path: self._guess_sensor_column(file_path)
                for file_path in selected_files
            }

            config_dialog = FileColumnConfigDialog(
                selected_files,
                default_sensor_cols,
                chart_index,
                self
            )

            if config_dialog.exec() != QDialog.Accepted:
                build_progress.close()
                return None

            file_column_configs = config_dialog.get_configs()
            chart_meta = config_dialog.get_meta()
            multi_y_mode = chart_meta.get("multi_y", False)
            sensor_mode = chart_meta.get("sensor_mode", False)
            plot_mode = chart_meta.get("plot_mode", "line")
            line_width = chart_meta.get("line_width", 1.8)
            point_size = chart_meta.get("point_size", 24)
            sensor_band_enabled = chart_meta.get("sensor_band_enabled", False)
            sensor_band_default_rules = chart_meta.get("sensor_band_default_rules", "")
            sensor_band_alpha = chart_meta.get("sensor_band_alpha", 0.2)
            manual_color_mode = chart_meta.get("manual_color_mode", False)
            same_family_colors = chart_meta.get("same_family_colors", False)

            def _chart_color_for_index(color_index):

                if color_index < len(palette):
                    return palette[color_index]

                hue = (color_index * 47) % 360
                return QColor.fromHsv(hue, 180, 240).name()

            chart_color_index = 0

            for file_index, file_path in enumerate(selected_files):

                build_progress.setLabelText(
                    (
                        f"Preparing chart {chart_index}/{chart_count}... "
                        f"{file_index + 1}/{len(selected_files)}"
                    )
                )
                QApplication.processEvents()

                file_config = next(
                    cfg
                    for cfg in file_column_configs
                    if cfg["file_path"] == file_path
                )
                x_col = file_config["x_col"]
                y_cols = file_config.get("y_cols") or [file_config["y_col"]]
                sensor_col = file_config["sensor_col"]
                axis_id = file_config.get("axis_id", 1)
                band_override_rules = file_config.get("sensor_band_rules", "")

                if sensor_mode and sensor_col > 0:

                    sensors = self._discover_sensors(
                        file_path,
                        sensor_col
                    )

                    if not sensors:
                        self.log(
                            f"No sensors found in {os.path.basename(file_path)} using column {sensor_col}."
                        )
                        continue

                    total_sensor_lines = len(sensors) * len(y_cols)

                    if manual_color_mode and same_family_colors:
                        default_base = palette[file_index % len(palette)]
                        selected_base = QColorDialog.getColor(
                            QColor(default_base),
                            self,
                            f"Base Color - {os.path.basename(file_path)}"
                        )

                        if selected_base.isValid():
                            base_color = selected_base.name()
                        else:
                            base_color = default_base

                        sensor_colors = self._build_tone_colors(
                            base_color,
                            total_sensor_lines
                        )
                    else:
                        sensor_colors = [
                            _chart_color_for_index(chart_color_index + line_idx)
                            for line_idx in range(total_sensor_lines)
                        ]

                    chart_color_index += total_sensor_lines

                    line_cursor = 0

                    for y_idx, y_col in enumerate(y_cols):

                        if multi_y_mode and len(y_cols) > 1:
                            target_axis_id = axis_id + y_idx
                        else:
                            target_axis_id = axis_id

                        for sensor_id in sensors:

                            color_hex = sensor_colors[line_cursor]

                            if manual_color_mode and not same_family_colors:

                                selected_color = QColorDialog.getColor(
                                    QColor(color_hex),
                                    self,
                                    (
                                        f"Line Color - {os.path.basename(file_path)} "
                                        f"Y Col {y_col}, Sensor {sensor_id}"
                                    )
                                )

                                if selected_color.isValid():
                                    color_hex = selected_color.name()

                            lines.append(
                                {
                                    "mode": "sensor",
                                    "file_path": file_path,
                                    "x_col": x_col,
                                    "y_col": y_col,
                                    "sensor_col": sensor_col,
                                    "sensor_id": sensor_id,
                                    "axis_id": target_axis_id,
                                    "color": color_hex,
                                    "plot_mode": plot_mode,
                                    "line_width": line_width,
                                    "point_size": point_size,
                                    "label": (
                                        f"{os.path.basename(file_path)} | "
                                        f"Y{y_col} | Sensor {sensor_id}"
                                    ),
                                }
                            )

                            line_cursor += 1

                        if sensor_band_enabled:
                            y_band_specs = self._resolve_sensor_band_specs_for_y(
                                y_col,
                                sensor_band_default_rules,
                                band_override_rules
                            )

                            if not y_band_specs:
                                continue

                            lines.extend(
                                self._build_sensor_avg_band_lines(
                                    file_path,
                                    x_col,
                                    y_col,
                                    sensor_col,
                                    sensors,
                                    target_axis_id,
                                    y_band_specs,
                                    sensor_band_alpha
                                )
                            )

                    continue

                if manual_color_mode and same_family_colors:
                    default_base = palette[file_index % len(palette)]
                    selected_base = QColorDialog.getColor(
                        QColor(default_base),
                        self,
                        f"Base Color - {os.path.basename(file_path)}"
                    )

                    if selected_base.isValid():
                        base_color = selected_base.name()
                    else:
                        base_color = default_base

                    column_colors = self._build_tone_colors(
                        base_color,
                        len(y_cols)
                    )
                else:
                    column_colors = [
                        palette[(file_index + idx) % len(palette)]
                        for idx in range(len(y_cols))
                    ]

                for y_idx, y_col in enumerate(y_cols):

                    if multi_y_mode and len(y_cols) > 1:
                        target_axis_id = axis_id + y_idx
                    else:
                        target_axis_id = axis_id

                    color_hex = column_colors[y_idx]

                    if manual_color_mode and not same_family_colors:
                        selected_color = QColorDialog.getColor(
                            QColor(color_hex),
                            self,
                            (
                                f"Line Color - {os.path.basename(file_path)} "
                                f"Y Col {y_col}"
                            )
                        )

                        if selected_color.isValid():
                            color_hex = selected_color.name()
                    elif not manual_color_mode:
                        color_hex = _chart_color_for_index(chart_color_index)

                    if not manual_color_mode:
                        chart_color_index += 1

                    lines.append(
                        {
                            "mode": "column",
                            "file_path": file_path,
                            "x_col": x_col,
                            "y_col": y_col,
                            "axis_id": target_axis_id,
                            "color": color_hex,
                            "plot_mode": plot_mode,
                            "line_width": line_width,
                            "point_size": point_size,
                            "label": (
                                f"{os.path.basename(file_path)} | Y{y_col}"
                            ),
                        }
                    )

            chart_configs.append(
                {
                    "title": chart_meta["chart_title"],
                    "x_label": chart_meta["x_axis_name"],
                    "x_time_unit": chart_meta.get("x_time_unit", "auto"),
                    "x_source_unit": chart_meta.get("x_source_unit", "s"),
                    "y_axis_names": chart_meta["y_axis_names"],
                    "chart_mode": (
                        "multi_y"
                        if chart_meta["multi_y"]
                        else "single"
                    ),
                    "lines": lines,
                }
            )

            build_progress.setValue(chart_index)
            QApplication.processEvents()

        build_progress.close()

        if not chart_configs:
            return None

        return chart_configs

    def merge_files(self):

        if len(self.file_set) == 0:

            QMessageBox.warning(
                self,
                "Notice",
                "Please add files first."
            )

            return

        output_dir = self.outputLabel.text()

        if not os.path.exists(output_dir):

            os.makedirs(output_dir)

        self.progress.setValue(0)

        QApplication.processEvents()

        self.log("")
        self.log("====================")
        self.log("Merge started...")

        try:

            logs = FileMerger.merge(
                list(self.file_set),
                output_dir
            )

            total = len(logs)

            if total == 0:

                QMessageBox.information(
                    self,
                    "Notice",
                    "No valid chunks were found.\n\n"
                    "Filename pattern must be:\n"
                    "name_number.ext"
                )

                return

            for i, log in enumerate(logs):

                self.progress.setValue(
                    int((i + 1) / total * 100)
                )

                QApplication.processEvents()

                self.log(log)

            self.progress.setValue(100)

            self.log("====================")
            self.log("Merge completed.")

            QMessageBox.information(
                self,
                "Done",
                f"Successfully generated {total} merged file(s)."
            )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Error",
                str(error)
            )

            self.log(str(error))

    def closeEvent(self, event):

        reply = QMessageBox.question(
            self,
            "Exit",
            "Are you sure you want to exit?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:

            event.accept()

        else:

            event.ignore()

    def _load_icon(self, icon_name):

        if qtawesome is not None:
            return qtawesome.icon(icon_name, color="#334155")

        return self.style().standardIcon(
            QStyle.SP_FileIcon
        )


def set_dark_theme(app):

    app.setFont(QFont("Segoe UI", 10))

    app.setStyleSheet("""

QWidget{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #f8fafc,
        stop:0.55 #f2f6fb,
        stop:1 #eaf0f8);

    color:#1f2937;

    font-size:10pt;

}

QPushButton{

    background:qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff,
        stop:1 #e7edf5);

    color:#1f2937;

    border:1px solid #c8d3e1;

    border-radius:11px;

    padding:10px 14px;

    font-weight:600;

}

QPushButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff,
        stop:1 #dde7f3);

}

QPushButton:pressed{

    background:#d4ddeb;

    padding-top:11px;

    padding-bottom:9px;

}

QPushButton#mergePrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #2a77ff,
        stop:1 #57c2ff);

    color:white;

    border:1px solid #1d63da;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#plotPrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0aa6c2,
        stop:1 #1fd08d);

    color:white;

    border:1px solid #0d8f9c;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#plotPrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #17b8d1,
        stop:1 #34e49f);

}

QPushButton#cleanPrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff7b57,
        stop:1 #ffc153);

    color:white;

    border:1px solid #df6f2d;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#cleanPrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff8f66,
        stop:1 #ffd071);

}

QPushButton#xyzPrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6f63ff,
        stop:1 #14b9d8);

    color:white;

    border:1px solid #5b53d4;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#xyzPrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #8176ff,
        stop:1 #2ad0ea);

}

QPushButton#singleExportPrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #00a58f,
        stop:1 #4cc96e);

    color:white;

    border:1px solid #0d8b6f;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#singleExportPrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0ec0a9,
        stop:1 #61d98b);

}

QPushButton#deviationPrimaryButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #e25555,
        stop:1 #f08a4b);

    color:white;

    border:1px solid #c94949;

    border-radius:18px;

    padding:14px 16px;

    font-size:11pt;

    font-weight:700;

}

QPushButton#deviationPrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ef6767,
        stop:1 #f7a061);

}

QPushButton#mergePrimaryButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #3b8cff,
        stop:1 #70cfff);

}

QPushButton#actionAddButton,
QPushButton#actionRemoveButton,
QPushButton#actionClearButton{

    border-radius:16px;

    font-size:10.5pt;

    font-weight:700;

    color:white;

    padding:12px 12px;

}

QPushButton#actionAddButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #2bb673,
        stop:1 #4fd89f);

    border:1px solid #219a60;

}

QPushButton#actionAddButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #3dc282,
        stop:1 #66e2af);

}

QPushButton#actionRemoveButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #f25f6b,
        stop:1 #f5845e);

    border:1px solid #de4f59;

}

QPushButton#actionRemoveButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #f67882,
        stop:1 #f89c70);

}

QPushButton#actionClearButton{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6f6ee8,
        stop:1 #8d8af6);

    border:1px solid #5f5ed5;

}

QPushButton#actionClearButton:hover{

    background:qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #807ff0,
        stop:1 #a3a0ff);

}

QPushButton#miniInfoButton{

    background:#ffffff;

    color:#334155;

    border:1px solid #c8d3e1;

    border-radius:12px;

    min-width:24px;

    max-width:24px;

    min-height:24px;

    max-height:24px;

    padding:0;

    font-size:9pt;

    font-weight:700;

}

QPushButton#miniInfoButton:hover{

    background:#eef3f9;

}

QListWidget{

    background:#ffffff;

    border:1px solid #d7e0ec;

    border-radius:12px;

    padding:6px;

    outline:none;

}

QListWidget::item{

    padding:8px;

    border-radius:8px;

}

QListWidget::item:alternate{

    background:#f7f9fc;

}

QListWidget::item:selected{

    background:#dbeafe;

    color:#0f172a;

}

QTextEdit{

    background:#ffffff;

    border:1px solid #d7e0ec;

    border-radius:12px;

    padding:8px;

}

QLineEdit, QSpinBox{

    background:#ffffff;

    border:1px solid #cfd8e6;

    border-radius:9px;

    padding:8px 10px;

    min-height:26px;

    selection-background-color:#dbeafe;

    color:#0f172a;

}

QSpinBox::up-button, QSpinBox::down-button{

    width:0px;

    height:0px;

    border:none;

    background:transparent;

}

QLineEdit:focus, QSpinBox:focus{

    border:1px solid #5aa9ff;

}

QTableWidget{

    background:#ffffff;

    border:1px solid #d7e0ec;

    border-radius:12px;

    gridline-color:#ecf1f7;

    color:#0f172a;

}

QTableWidget::item{

    padding:6px;

}

QHeaderView::section{

    background:#e8eef7;

    color:#0f172a;

    border:none;

    border-bottom:1px solid #d8e1ed;

    padding:7px;

    font-weight:700;

    border-right:1px solid #d8e1ed;

}

QDialogButtonBox QPushButton{

    min-width:96px;

}

QComboBox{

    background:#ffffff;

    border:1px solid #cbd5e1;

    border-radius:10px;

    padding:8px 10px;

    min-height:22px;

}

QComboBox QAbstractItemView{

    background:#ffffff;

    border:1px solid #cbd5e1;

    selection-background-color:#dbeafe;

}

QCheckBox#switchToggle{

    spacing:8px;

    font-weight:600;

    color:#334155;

}

QCheckBox#switchToggle::indicator{

    width:18px;

    height:18px;

    border-radius:9px;

    border:2px solid #94a3b8;

    background:#ffffff;

}

QCheckBox#switchToggle::indicator:checked{

    border:2px solid #0ea5e9;

    background:qradialgradient(cx:0.4, cy:0.4, radius:0.9,
        fx:0.35, fy:0.35,
        stop:0 #8be8ff,
        stop:1 #0ea5e9);

}

QCheckBox#switchToggle::indicator:hover{

    border-color:#38bdf8;

}

QLabel{

    color:#111827;

}

QLabel#appTitle{

    font-size:32px;

    font-weight:800;

    letter-spacing:0.5px;

}

QLabel#subtitle{

    color:#4b5563;

    font-size:10.5pt;

}

QLabel#sectionTitle{

    color:#475569;

    font-size:10.5pt;

    font-weight:700;

}

QLabel#dialogTitle{

    font-size:22px;

    font-weight:800;

}

QLabel#dialogSubtitle{

    color:#64748b;

    line-height:1.25;

}

QLabel#outputPath{

    background:#ffffff;

    border:1px solid #cbd5e1;

    border-radius:10px;

    padding:8px;

    color:#374151;

}

QFrame#card{

    background:rgba(255, 255, 255, 0.96);

    border:1px solid #d6e0ec;

    border-radius:18px;

}

QScrollArea#chartScroll{

    border:1px solid #d8e1ed;

    border-radius:16px;

    background:#ffffff;

}

QProgressBar{

    border:1px solid #d8e1ed;

    border-radius:9px;

    background:#f8fafc;

    text-align:center;

    color:#334155;

    min-height:20px;

}

QProgressBar::chunk{

    border-radius:8px;

    background:qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 #22a6ff,
    stop:1 #3dd6ff);

}

QDialog QToolBar{

    background:#e9eef7;

    border:1px solid #c8d3e1;

    border-radius:10px;

    spacing:6px;

    padding:4px;

}

QDialog QToolButton{

    background:#e2e8f4;

    color:#0f172a;

    border:1px solid #c2cfe0;

    border-radius:8px;

    padding:6px 9px;

    font-weight:600;

}

QDialog QToolButton:hover{

    background:#d6dfef;

    border:1px solid #adbdd5;

}

QDialog QToolButton:pressed,
QDialog QToolButton:checked{

    background:#c7d4e8;

    border:1px solid #93a9c8;

}

QDialog QToolBar QLabel{

    color:#0f172a;

    font-weight:600;

}

""")


class MergeApplication:

    def __init__(self):

        self.app = QApplication([])

        set_dark_theme(
            self.app
        )

        self.window = MainWindow()

    def run(self):

        self.window.show()

        return self.app.exec()