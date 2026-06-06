import base64
import json
import os
import tempfile
import time
import urllib.request

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, InfoObject, Krita
from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
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
    QScrollArea,
)


APP_URL = os.environ.get("KRITA_GUIDE_AGENT_URL", "http://localhost:8788")
OVERLAY_NAME = "KGA Live Overlay - do not draw here"
SESSION_ID = "krita-live"
TABLET_EVENTS = {
    int(QEvent.TabletPress): "tablet_press",
    int(QEvent.TabletMove): "tablet_move",
    int(QEvent.TabletRelease): "tablet_release",
}
MOUSE_EVENTS = {
    int(QEvent.MouseButtonPress): "mouse_press",
    int(QEvent.MouseMove): "mouse_move",
    int(QEvent.MouseButtonRelease): "mouse_release",
}


class KritaGuideLiveDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Krita Guide Live Coach")
        self.project_id = ""
        self.last_step = None
        self.last_overlay_path = ""
        self.focus_step = None
        self.segment_results = []
        self.input_events = []
        self.input_event_limit = 1200
        self.last_input_summary = {}
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
        self.follow_btn = QPushButton("Follow detected")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.once_btn)
        row.addWidget(self.follow_btn)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self.auto_overlay = QCheckBox("Auto overlay")
        self.auto_overlay.setChecked(True)
        self.auto_advance = QCheckBox("Auto follow step")
        self.auto_advance.setChecked(True)
        self.visual_compare = QCheckBox("Visual compare")
        self.visual_compare.setChecked(True)
        self.record_input = QCheckBox("Record input metrics")
        self.record_input.setChecked(True)
        row2.addWidget(self.auto_overlay)
        row2.addWidget(self.auto_advance)
        row2.addWidget(self.visual_compare)
        row2.addWidget(self.record_input)
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

        self.telemetry_label = QLabel("Input telemetry: waiting for live session.")
        self.telemetry_label.setWordWrap(True)
        layout.addWidget(self.telemetry_label)

        self.visual_label = QLabel("Visual comparison appears after analysis.")
        self.visual_label.setWordWrap(True)
        self.visual_label.setAlignment(Qt.AlignCenter)
        self.visual_label.setMinimumHeight(170)
        self.visual_label.setStyleSheet("QLabel { background: #202428; color: #d9dee3; border: 1px solid #535a60; }")
        layout.addWidget(self.visual_label)

        self.segment_label = QLabel("Segments: analyze the canvas to populate clickable comments.")
        self.segment_label.setWordWrap(True)
        layout.addWidget(self.segment_label)

        self.segment_scroll = QScrollArea()
        self.segment_scroll.setWidgetResizable(True)
        self.segment_scroll.setMinimumHeight(160)
        self.segment_container = QWidget()
        self.segment_layout = QVBoxLayout(self.segment_container)
        self.segment_scroll.setWidget(self.segment_container)
        layout.addWidget(self.segment_scroll)

        self.feedback = QTextEdit()
        self.feedback.setReadOnly(True)
        self.feedback.setMinimumHeight(210)
        layout.addWidget(self.feedback)

        self.timer = QTimer()
        self.timer.timeout.connect(self.analyze_now)
        self.start_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_live)
        self.once_btn.clicked.connect(self.analyze_now)
        self.follow_btn.clicked.connect(self.follow_detected)
        self.make_layers_btn.clicked.connect(self.make_work_layers)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def canvasChanged(self, canvas):
        pass

    def eventFilter(self, obj, event):
        try:
            if self.timer.isActive() and self.record_input.isChecked():
                event_type = int(event.type())
                if event_type in TABLET_EVENTS:
                    self.capture_tablet_event(event, TABLET_EVENTS[event_type])
                elif event_type in MOUSE_EVENTS:
                    self.capture_mouse_event(event, MOUSE_EVENTS[event_type])
        except Exception:
            pass
        return False

    def start_live(self):
        self.input_events = []
        self.last_input_summary = {}
        self.timer.start(self.interval.value() * 1000)
        self.status.setText("Live monitoring is running. The coach hides its overlay before each capture.")
        self.analyze_now()

    def stop_live(self):
        self.timer.stop()
        self.status.setText("Live monitoring stopped.")

    def follow_detected(self):
        self.focus_step = None
        self.status.setText("Following the strongest detected segment again.")
        self.analyze_now()

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
            result = self.post_snapshot(snapshot, doc)
            self.apply_feedback(doc, result)
        except Exception as exc:
            self.status.setText("Live coach error: " + str(exc))
        finally:
            self.busy = False

    def export_clean_snapshot(self, doc):
        hidden_nodes = []
        for node in self.walk_nodes(doc.rootNode()):
            if self.hide_for_capture(node):
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

    def hide_for_capture(self, node):
        name = node.name().strip().lower()
        if name.startswith("kga "):
            return True
        if name.startswith("guide overlay"):
            return True
        if "reference" in name:
            return True
        return False

    def post_snapshot(self, snapshot_path, doc):
        with open(snapshot_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        telemetry = self.drain_input_telemetry(doc)
        payload = {
            "sessionId": SESSION_ID,
            "projectId": self.project_input.text().strip(),
            "focusStep": self.focus_step,
            "telemetry": telemetry,
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
        self.segment_results = result.get("segments") or [result]
        self.render_segment_buttons()
        segment = self.choose_segment(result)
        self.display_segment(doc, segment, locked=self.focus_step is not None)
        self.status.setText(
            "Reading whole drawing. {0} segment comments updated.".format(len(self.segment_results))
        )

    def choose_segment(self, result):
        if self.focus_step is not None:
            found = self.find_segment_result(self.focus_step)
            if found:
                return found
        if not self.auto_advance.isChecked() and self.last_step is not None:
            found = self.find_segment_result(self.last_step)
            if found:
                return found
        return result

    def display_segment(self, doc, result, locked=False):
        step = result.get("step")
        self.last_step = step
        self.step_label.setText(
            "{0}Step {1}: {2}\nLayer: {3} | Brush: {4} {5}px | {6}".format(
                "Locked focus: " if locked else "",
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
        self.update_telemetry_label(result)
        self.update_visual_preview(result)
        if self.auto_overlay.isChecked():
            overlay = result.get("liveOverlayPath") or result.get("overlayPath")
            if overlay:
                self.ensure_overlay_layer(doc, overlay)

    def update_visual_preview(self, result):
        if not self.visual_compare.isChecked():
            self.visual_label.setText("Visual compare is off.")
            self.visual_label.setPixmap(QPixmap())
            return
        visual_path = result.get("visualPath") or result.get("cardPath")
        if not visual_path or not os.path.exists(visual_path):
            self.visual_label.setText("No visual comparison available for this section yet.")
            self.visual_label.setPixmap(QPixmap())
            return
        pixmap = QPixmap(visual_path)
        if pixmap.isNull():
            self.visual_label.setText("Could not load visual comparison image.")
            return
        width = max(260, self.visual_label.width() - 12)
        scaled = pixmap.scaled(width, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.visual_label.setPixmap(scaled)

    def update_telemetry_label(self, result):
        summary = self.last_input_summary or {}
        pressure_count = summary.get("pressureSamples", 0)
        if not self.record_input.isChecked():
            self.telemetry_label.setText("Input telemetry: off.")
            return
        text = "Input telemetry: {0} events, {1}px movement".format(
            summary.get("eventCount", 0),
            round(summary.get("distancePx", 0), 1),
        )
        if pressure_count:
            text += ", pressure avg {0} max {1}".format(
                round(summary.get("avgPressure", 0), 3),
                round(summary.get("maxPressure", 0), 3),
            )
        else:
            text += ", pressure not exposed this interval"
        if result.get("telemetryStored"):
            text += ". Stored."
        self.telemetry_label.setText(text)

    def capture_tablet_event(self, event, name):
        pos = self.event_position(event)
        item = {
            "t": time.time(),
            "kind": name,
            "source": "tablet",
            "x": pos[0],
            "y": pos[1],
            "pressure": self.event_number(event, "pressure"),
            "xTilt": self.event_number(event, "xTilt"),
            "yTilt": self.event_number(event, "yTilt"),
            "rotation": self.event_number(event, "rotation"),
            "tangentialPressure": self.event_number(event, "tangentialPressure"),
            "button": self.enum_int(self.event_value(event, "button")),
            "buttons": self.enum_int(self.event_value(event, "buttons")),
            "pointerType": self.enum_int(self.event_value(event, "pointerType")),
            "device": self.enum_int(self.event_value(event, "device")),
        }
        self.append_input_event(item)

    def capture_mouse_event(self, event, name):
        button = self.enum_int(self.event_value(event, "button"))
        buttons = self.enum_int(self.event_value(event, "buttons"))
        if name == "mouse_move" and not buttons:
            return
        pos = self.event_position(event)
        self.append_input_event({
            "t": time.time(),
            "kind": name,
            "source": "mouse",
            "x": pos[0],
            "y": pos[1],
            "button": button,
            "buttons": buttons,
        })

    def append_input_event(self, item):
        self.input_events.append(item)
        if len(self.input_events) > self.input_event_limit:
            self.input_events = self.input_events[-self.input_event_limit:]

    def drain_input_telemetry(self, doc):
        events = self.input_events
        self.input_events = []
        strokes = self.build_strokes(events)
        context = self.collect_document_context(doc)
        summary = self.summarize_input_events(events, strokes)
        self.last_input_summary = summary
        return {
            "schema": "kriator-live-input-v2",
            "events": events,
            "strokes": strokes,
            "summary": summary,
            "context": context,
        }

    def build_strokes(self, events):
        strokes = []
        current = []
        for item in events:
            kind = item.get("kind", "")
            starts = kind.endswith("_press")
            ends = kind.endswith("_release")
            active = bool(item.get("buttons")) or (item.get("pressure") is not None and float(item.get("pressure") or 0) > 0)
            if starts or (active and not current):
                if current:
                    strokes.append(self.summarize_stroke(current))
                current = [item]
            elif current:
                current.append(item)
            elif active:
                current = [item]
            if ends and current:
                strokes.append(self.summarize_stroke(current))
                current = []
        if current:
            strokes.append(self.summarize_stroke(current))
        return strokes[-80:]

    def summarize_stroke(self, events):
        pressure_values = [
            float(item["pressure"]) for item in events
            if item.get("pressure") is not None and float(item.get("pressure", 0)) > 0
        ]
        xs = [float(item.get("x")) for item in events if item.get("x") is not None]
        ys = [float(item.get("y")) for item in events if item.get("y") is not None]
        distance = 0.0
        max_speed = 0.0
        previous = None
        for item in events:
            x = item.get("x")
            y = item.get("y")
            t = item.get("t")
            if x is None or y is None:
                continue
            point = (float(x), float(y), float(t or 0))
            if previous is not None:
                dx = point[0] - previous[0]
                dy = point[1] - previous[1]
                segment = (dx * dx + dy * dy) ** 0.5
                distance += segment
                dt = max(0.001, point[2] - previous[2])
                max_speed = max(max_speed, segment / dt)
            previous = point
        duration = int((events[-1].get("t", 0) - events[0].get("t", 0)) * 1000) if len(events) >= 2 else 0
        return {
            "source": events[0].get("source", ""),
            "startTime": events[0].get("t"),
            "endTime": events[-1].get("t"),
            "durationMs": max(0, duration),
            "eventCount": len(events),
            "distancePx": distance,
            "avgSpeedPxPerSec": distance / max(0.001, duration / 1000.0) if duration else 0,
            "maxSpeedPxPerSec": max_speed,
            "bounds": {
                "x": min(xs) if xs else None,
                "y": min(ys) if ys else None,
                "w": (max(xs) - min(xs)) if xs else None,
                "h": (max(ys) - min(ys)) if ys else None,
            },
            "pressureSamples": len(pressure_values),
            "avgPressure": sum(pressure_values) / len(pressure_values) if pressure_values else 0,
            "minPressure": min(pressure_values) if pressure_values else 0,
            "maxPressure": max(pressure_values) if pressure_values else 0,
        }

    def summarize_input_events(self, events, strokes):
        pressure_values = [
            float(item["pressure"]) for item in events
            if item.get("pressure") is not None and float(item.get("pressure", 0)) > 0
        ]
        distance = 0.0
        previous = None
        for item in events:
            x = item.get("x")
            y = item.get("y")
            if x is None or y is None:
                continue
            if previous is not None:
                dx = float(x) - float(previous[0])
                dy = float(y) - float(previous[1])
                distance += (dx * dx + dy * dy) ** 0.5
            previous = (x, y)
        tablet_count = len([item for item in events if item.get("source") == "tablet"])
        return {
            "eventCount": len(events),
            "tabletEvents": tablet_count,
            "mouseEvents": len(events) - tablet_count,
            "strokeCount": len(strokes),
            "pressureSamples": len(pressure_values),
            "avgPressure": sum(pressure_values) / len(pressure_values) if pressure_values else 0,
            "maxPressure": max(pressure_values) if pressure_values else 0,
            "distancePx": distance,
            "avgStrokeDistancePx": sum(stroke.get("distancePx", 0) for stroke in strokes) / len(strokes) if strokes else 0,
            "maxStrokeSpeedPxPerSec": max([stroke.get("maxSpeedPxPerSec", 0) for stroke in strokes] or [0]),
            "durationMs": int((events[-1]["t"] - events[0]["t"]) * 1000) if len(events) >= 2 else 0,
        }

    def collect_document_context(self, doc):
        active = self.safe_call(doc, "activeNode")
        active_info = self.node_info(active)
        visible = {}
        total_visible = 0
        for node in self.walk_nodes(doc.rootNode()):
            if node == doc.rootNode():
                continue
            try:
                is_visible = bool(node.visible())
            except Exception:
                is_visible = False
            if not is_visible:
                continue
            category = self.layer_category(node.name())
            visible[category] = visible.get(category, 0) + 1
            total_visible += 1
        return {
            "documentName": self.safe_call(doc, "name") or "",
            "documentFileName": self.safe_call(doc, "fileName") or "",
            "canvas": {
                "width": self.safe_call(doc, "width"),
                "height": self.safe_call(doc, "height"),
            },
            "activeLayer": active_info,
            "activeCategory": active_info.get("category", "Other"),
            "visibleCategoryCounts": visible,
            "visibleLayerCount": total_visible,
            "assessmentMode": "combined-visible-artwork",
            "categoryAssessment": "Visible layers with matching beginner categories are assessed as one combined result.",
        }

    def node_info(self, node):
        if node is None:
            return {"name": "", "category": "Unknown"}
        name = node.name()
        return {
            "name": name,
            "category": self.layer_category(name),
            "type": self.safe_call(node, "type") or "",
            "visible": self.safe_call(node, "visible"),
            "locked": self.safe_call(node, "locked"),
            "opacity": self.safe_call(node, "opacity"),
        }

    def layer_category(self, name):
        text = str(name or "").lower()
        if text.startswith("kga ") or "guide overlay" in text or "reference" in text:
            return "Guide/Reference"
        if "rough" in text or "sketch" in text:
            return "Rough Sketch"
        if "line" in text or "ink" in text:
            return "Lineart"
        if "flat" in text or "color" in text or "colour" in text:
            return "Flat Colors"
        if "shadow" in text or "shade" in text:
            return "Shadows"
        if "highlight" in text or "texture" in text:
            return "Highlights and Texture"
        if "detail" in text:
            return "Small Details"
        return "Other"

    def safe_call(self, obj, method):
        try:
            attr = getattr(obj, method, None)
            if attr is None:
                return None
            return attr() if callable(attr) else attr
        except Exception:
            return None

    def event_position(self, event):
        for name in ("posF", "pos", "globalPosF", "globalPos"):
            value = self.event_value(event, name)
            if value is not None:
                try:
                    return [float(value.x()), float(value.y())]
                except Exception:
                    pass
        x = self.event_number(event, "x")
        y = self.event_number(event, "y")
        return [x, y]

    def event_number(self, event, name):
        value = self.event_value(event, name)
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def event_value(self, event, name):
        attr = getattr(event, name, None)
        if attr is None:
            return None
        try:
            return attr() if callable(attr) else attr
        except Exception:
            return None

    def enum_int(self, value):
        try:
            return int(value)
        except Exception:
            return None

    def render_segment_buttons(self):
        while self.segment_layout.count():
            item = self.segment_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if self.focus_step is None:
            self.segment_label.setText("Segments found: {0}. Click one to lock focus and update its overlay.".format(len(self.segment_results)))
        else:
            self.segment_label.setText("Segments found: {0}. Locked to step {1}; Follow detected unlocks.".format(len(self.segment_results), self.focus_step))
        for segment in self.segment_results:
            step = segment.get("step")
            title = segment.get("stepTitle", "Untitled")
            progress = segment.get("progressPercent", 0)
            prefix = "* " if self.focus_step == step else ""
            button = QPushButton("{0}Step {1}: {2} ({3}%)".format(prefix, step, title, progress))
            button.setToolTip("\n".join(segment.get("comments") or []))
            button.clicked.connect(lambda checked=False, s=segment: self.focus_segment(s))
            self.segment_layout.addWidget(button)
        self.segment_layout.addStretch(1)

    def focus_segment(self, segment):
        self.focus_step = segment.get("step")
        doc = Krita.instance().activeDocument()
        if doc is not None:
            self.display_segment(doc, segment, locked=True)
        self.render_segment_buttons()
        self.status.setText("Locked focus to step {0}. Live updates will keep that section selected.".format(self.focus_step))

    def find_segment_result(self, step):
        for segment in self.segment_results:
            if segment.get("step") == step:
                return segment
        return None

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
