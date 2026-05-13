"""Backend / environment hints persisted outside ``registry.json``."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from omega_studio.gui_settings_store import (
    EnvVarRow,
    PortableGuiSettings,
    load_gui_settings,
    save_gui_settings,
)

_ORT_ITEMS: list[tuple[str, str]] = [
    ("CUDA", "CUDAExecutionProvider"),
    ("DML", "DmlExecutionProvider"),
    ("CPU", "CPUExecutionProvider"),
    ("Vitis AI", "VitisAIExecutionProvider"),
]


class BackendEnvTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Optional tweaks stored in gui_settings.json (ONNX provider order, Vulkan, bundle "
            "paths, API key, extra env). Applied when you start the API from the Studio window—"
            "skip this tab until something clearly needs it."
        )
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.TextFormat.PlainText)
        blurb.setProperty("class", "muted")
        outer.addWidget(blurb)

        ort = QGroupBox("ONNX Runtime provider order (drag to reorder)")
        ol = QVBoxLayout(ort)
        self.lw_ort = QListWidget()
        self.lw_ort.setDragDropMode(QAbstractItemView.InternalMove)
        self.lw_ort.setDefaultDropAction(Qt.DropAction.MoveAction)
        for label, ep in _ORT_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ep)
            self.lw_ort.addItem(item)
        ol.addWidget(self.lw_ort)
        outer.addWidget(ort)

        flags = QFormLayout()
        self.chk_vulkan = QCheckBox(
            "Prefer Vulkan llama-server (prepend vulkan bin to PATH when starting server)"
        )
        flags.addRow(self.chk_vulkan)
        outer.addLayout(flags)

        paths = QGroupBox(
            "Paths & gateway (gui_settings.json; merged into server env when launched from GUI)"
        )
        pf = QFormLayout(paths)
        self.le_bundle = QLineEdit()
        self.le_harvest = QLineEdit()
        self.le_api_key = QLineEdit()
        self.le_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.le_workers = QLineEdit()
        pf.addRow("OMEGA_BUNDLE_ROOT", self.le_bundle)
        pf.addRow("OMEGA_RUNTIME_HARVEST", self.le_harvest)
        pf.addRow("OMEGA_API_KEY (client + server subprocess)", self.le_api_key)
        pf.addRow("OMEGA_GATEWAY_GGUF_WORKERS", self.le_workers)
        outer.addWidget(paths)

        env = QGroupBox("Custom environment variables")
        ev = QVBoxLayout(env)
        self.tbl_env = QTableWidget(0, 2)
        self.tbl_env.setHorizontalHeaderLabels(["Key", "Value"])
        row_btns = QHBoxLayout()
        btn_add = QPushButton("Add row")
        btn_add.clicked.connect(lambda: self._env_add_row())
        btn_rm = QPushButton("Remove selected")
        btn_rm.clicked.connect(self._env_remove_row)
        ev.addWidget(self.tbl_env)
        ev.addLayout(row_btns)
        outer.addWidget(env)

        btn_save = QPushButton("Save backend settings")
        btn_save.clicked.connect(self._on_save)
        outer.addWidget(btn_save)

        self._load_ui()

    def _load_ui(self) -> None:
        gs = load_gui_settings()
        order = gs.ort_ep_order or [ep for _, ep in _ORT_ITEMS]
        label_by_ep = {ep: lab for lab, ep in _ORT_ITEMS}
        seen: set[str] = set()
        self.lw_ort.clear()
        for ep in order:
            if ep in label_by_ep:
                lab = label_by_ep[ep]
                item = QListWidgetItem(lab)
                item.setData(Qt.ItemDataRole.UserRole, ep)
                self.lw_ort.addItem(item)
                seen.add(ep)
        for lab, ep in _ORT_ITEMS:
            if ep not in seen:
                item = QListWidgetItem(lab)
                item.setData(Qt.ItemDataRole.UserRole, ep)
                self.lw_ort.addItem(item)
        self.chk_vulkan.setChecked(gs.prefer_vulkan_llama)
        self.le_bundle.setText(gs.omega_bundle_root)
        self.le_harvest.setText(gs.omega_runtime_harvest)
        self.le_api_key.setText(gs.omega_api_key)
        self.le_workers.setText(gs.omega_gateway_gguf_workers)
        self.tbl_env.setRowCount(0)
        for row in gs.custom_env:
            self._env_add_row(row.key, row.value)

    def _env_add_row(self, key: str = "", val: str = "") -> None:
        r = self.tbl_env.rowCount()
        self.tbl_env.insertRow(r)
        self.tbl_env.setItem(r, 0, QTableWidgetItem(key))
        self.tbl_env.setItem(r, 1, QTableWidgetItem(val))

    def _env_remove_row(self) -> None:
        r = self.tbl_env.currentRow()
        if r >= 0:
            self.tbl_env.removeRow(r)

    def _collect(self) -> PortableGuiSettings:
        eps: list[str] = []
        for i in range(self.lw_ort.count()):
            it = self.lw_ort.item(i)
            if it is None:
                continue
            ep = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(ep, str) and ep:
                eps.append(ep)
        rows: list[EnvVarRow] = []
        for r in range(self.tbl_env.rowCount()):
            k = self.tbl_env.item(r, 0)
            v = self.tbl_env.item(r, 1)
            rows.append(
                EnvVarRow(
                    key=k.text() if k else "",
                    value=v.text() if v else "",
                )
            )
        return PortableGuiSettings(
            ort_ep_order=eps,
            prefer_vulkan_llama=self.chk_vulkan.isChecked(),
            omega_bundle_root=self.le_bundle.text().strip(),
            omega_runtime_harvest=self.le_harvest.text().strip(),
            omega_api_key=self.le_api_key.text(),
            omega_gateway_gguf_workers=self.le_workers.text().strip(),
            custom_env=rows,
        )

    def _on_save(self) -> None:
        save_gui_settings(self._collect())
        QMessageBox.information(
            self,
            "Saved",
            "Backend settings saved.\nRestart the API server (or relaunch from a new shell) "
            "so environment variables and PATH changes take full effect.",
        )
