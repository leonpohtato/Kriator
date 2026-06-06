import base64
import json
import os
import tempfile
import time
import urllib.request

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, InfoObject, Krita
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QSpinBox,
    QLineEdit,
    QTextEdit,
    QMessageBox,
)


APP_URL = os.environ.get("KRITA_GUIDE_AGENT_URL", "http://localhost:8788")
OVERLAY_NAME = "KGA Live Overlay - do not draw here"
SESSION_ID = "krita-live"


class KritaGuideLiveDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Krita Guide Live Coach")
        self.project_id = ""
        self.last_step = None
        self.last_overlay_path = ""
        self.busy = False

        root = QWidget()
        self.setWidget(root)
        layout = QVBoxLayout(root)

        self.status = QLabel("Generate a guide in the web app, then press Start.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.project_input = QLineEdit()
        self.project_input.setPlaceholderText("Project id optional: latest ready project is used")
        layout.addWidget(self.project_input)

        row = QHBoxLayout()
        self.start_btn = QPushButton("Start live")
        self.stop_btn = QPushButton("Stop")
        self.once_btn = QPushButton("Analyze now")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.once_btn)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self.auto_overlay = QCheckBox("Auto overlay")
        self.auto_overlay.setChecked(True)
        self.auto_advance = QCheckBox("Auto follow step")
        self.auto_advance.setChecked(True)
        row2.addWidget(self.auto_overlay)
        row2.addWidget(self.auto_advance)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Every sec"))
        self.interval = QSpinBox()
        self.interval.setRange(2, 30)
        self.interval.setValue(4)
        row3.addWidget(self.interval)
        self.make_layers_btn = QPushButton("Make work layers")
        row3.addWidget(self.make_layers_btn)
        layout.addLayout(row3)

        self.step_label = QLabel("Step: -")
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label)

        self.feedback = QTextEdit()
        self.feedback.setReadOnly(True)
        self.feedback.setMinimumHeight(210)
        layout.addWidget(self.feedback)

        self.timer = QTimer()
        self.timer.timeout.connect(self.analyze_now)
        self.start_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_live)
        self.once_btn.clicked.connect(self.analyze_now)
        self.make_layers_btn.clicked.connect(self.make_work_layers)

    def canvasChanged(self, canvas):
        pass

    def start_live(self):
        self.timer.start(self.interval.value() * 1000)
        self.status.setText("Live monitoring is running. The coach hides its overlay before each capture.")
        self.analyze_now()

    def stop_live(self):
        self.timer.stop()
        self.status.setText("Live monitoring stopped.")

    def analyze_now(self):
        if self.busy:
            return
        doc = Krita.instance().activeDocument()
        if doc is None:
            self.status.setText("No active Krita document.")
            return
        self.busy = True
        try:
            snapshot = self.export_clean_snapshot(doc)
            result = self.post_snapshot(snapshot)
            self.apply_feedback(doc, result)
        except Exception as exc:
            self.status.setText("Live coach error: " + str(exc))
        finally:
            self.busy = False

    def export_clean_snapshot(self, doc):
        hidden_nodes = []
        for node in self.walk_nodes(doc.rootNode()):
            if node.name().startswith("KGA "):
                if node.visible():
                    node.setVisible(False)
                    hidden_nodes.append(node)
        doc.refreshProjection()
        path = os.path.join(tempfile.gettempdir(), "krita_guide_live_snapshot.png")
        info = InfoObject()
        doc.exportImage(path, info)
        for node in hidden_nodes:
            node.setVisible(True)
        doc.refreshProjection()
        return path

    def post_snapshot(self, snapshot_path):
        with open(snapshot_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        payload = {
            "sessionId": SESSION_ID,
            "projectId": self.project_input.text().strip(),
            "snapshotDataUrl": "data:image/png;base64," + encoded,
            "timestamp": time.time(),
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            APP_URL + "/api/live/feedback",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Feedback request failed"))
        return result["feedback"]

    def apply_feedback(self, doc, result):
        if result.get("status") != "ok":
            self.status.setText(result.get("message", "No feedback available."))
            return
        self.project_id = result.get("projectId", self.project_id)
        self.project_input.setText(self.project_id)
        step = result.get("recommendedStep") if self.auto_advance.isChecked() else result.get("step")
        self.last_step = step
        self.step_label.setText(
            "Step {0}: {1}\nLayer: {2} | Brush: {3} {4}px | {5}".format(
                result.get("step"),
                result.get("stepTitle"),
                result.get("layer"),
                result.get("brush"),
                result.get("brushSizePx"),
                result.get("color"),
            )
        )
        comments = result.get("comments") or [result.get("message", "")]
        details = [
            "Progress estimate: {0}%".format(result.get("progressPercent", 0)),
            "",
            result.get("instruction", ""),
            "",
            "Feedback:",
        ] + ["- " + str(item) for item in comments] + [
            "",
            "Checkpoint: " + str(result.get("checkpoint", "")),
            "Common mistake: " + str(result.get("commonMistake", "")),
        ]
        self.feedback.setPlainText("\n".join(details))
        self.status.setText("Reading Krita live. Focus: step {0}.".format(result.get("step")))
        if self.auto_overlay.isChecked() and result.get("overlayPath"):
            self.ensure_overlay_layer(doc, result.get("overlayPath"))

    def ensure_overlay_layer(self, doc, overlay_path):
        if overlay_path == self.last_overlay_path and self.find_node(doc, OVERLAY_NAME):
            return
        old = self.find_node(doc, OVERLAY_NAME)
        if old:
            try:
                old.parentNode().removeChildNode(old)
            except Exception:
                old.setVisible(False)
        layer = doc.createFileLayer(OVERLAY_NAME, overlay_path, "None")
        layer.setOpacity(165)
        doc.rootNode().addChildNode(layer, None)
        self.last_overlay_path = overlay_path
        doc.refreshProjection()

    def make_work_layers(self):
        doc = Krita.instance().activeDocument()
        if doc is None:
            QMessageBox.information(None, "Krita Guide Live Coach", "Open or create a document first.")
            return
        root = doc.rootNode()
        existing = {node.name() for node in self.walk_nodes(root)}
        for name in [
            "My Rough Sketch",
            "My Lineart",
            "My Flat Colors",
            "My Shadows",
            "My Highlights and Texture",
            "My Small Details",
        ]:
            if name not in existing:
                node = doc.createNode(name, "paintLayer")
                root.addChildNode(node, None)
        doc.refreshProjection()
        self.status.setText("Created beginner work layers. Draw on these, not the KGA overlay layer.")

    def find_node(self, doc, name):
        for node in self.walk_nodes(doc.rootNode()):
            if node.name() == name:
                return node
        return None

    def walk_nodes(self, node):
        yield node
        for child in node.childNodes():
            for item in self.walk_nodes(child):
                yield item


Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("krita_guide_live", DockWidgetFactoryBase.DockRight, KritaGuideLiveDocker)
)
