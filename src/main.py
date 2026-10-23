import sys
import os
import re
from pathlib import Path
import pkgutil
import configparser as cp
import importlib.machinery
import importlib.util

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFrame, QWidget, QScrollArea,
    QVBoxLayout, QTextEdit,  QLabel
)
from PyQt6.QtCore import Qt, QTimer, QDateTime, QMimeData, QSettings, QCoreApplication
from PyQt6.QtGui import QDrag, QPixmap, QCloseEvent

# These 3 imports are not used in this module, but need to be included so that
# PyInstaller will bundle them correctly into the exe.
import vispy
import cmap
import scipy


if getattr(sys, 'frozen', False):
    # Inside the .exe environment, make sure sys._MEIPASS is treated as the primary lookup anchor
    if sys._MEIPASS not in sys.path:
        sys.path.insert(0, sys._MEIPASS)
        
    # Crucial Fix: Ensure the nested 'src' data path inside the extraction sandbox is mapped
    internal_src = os.path.join(sys._MEIPASS, 'src')
    if internal_src not in sys.path:
        sys.path.insert(0, internal_src)
else:
    # Local Development Path Routing Fix
    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        
        
from src.plugin_interface import BasePlugin  #  Absolute package import
from src.sd_control import SoundDevCtl
from src.config import APP_CO, APP_NAME, DBMIN, DBMAX

class MainDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)

        # Set tooltip style (app-wide!)
        app = QApplication.instance()
        app.setStyleSheet("""
            QToolTip { 
                background-color: #FFFF88; 
                /*border: none; */
            }
        """)
        
        # Keep track of all active runtime plugin instances
        self.loaded_plugins = []

        # Saved settings
        QCoreApplication.setOrganizationName(APP_CO)
        #QCoreApplication.setOrganizationDomain("mysoft.com")
        QCoreApplication.setApplicationName(APP_NAME)  
        self.settings = QSettings()
        self.settings.beginGroup('MainDashboard')
        
        # Try to load window geometry
        saved_geometry = self.settings.value("geometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
            
        # Initialize SoundDevice Control
        self.sd = SoundDevCtl(parent=self)

        # Logging console to show incoming plugin events
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setPlaceholderText("System logs from plugins will stream here...")
        self.log_console.setStyleSheet("""
            background: #111; 
            color: #00ff00; 
            font-family: monospace; 
            border-radius: 6px;
        """)

        # Container widget for drag & drop
        self.container = QWidget()
        self.container.setObjectName("PluginContainer")
        self.container.setStyleSheet("#PluginContainer { border-radius: 2px; background-color: #FFA; }")
        self.container.setAcceptDrops(True)
        self.container.dragEnterEvent = self._container_drag_enter
        self.container.dragMoveEvent = self._container_drag_move
        self.container.dropEvent = self._container_drop

        # Scroll area
        self.scroll = QScrollArea()      
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll.setWidget(self.container)

        # Plugin layout for container
        self.plugin_layout = QVBoxLayout(self.container)
        self.plugin_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.plugin_layout.setSpacing(5)
        ##self.plugin_layout.addWidget(self.placeholder)

        # Central Widget Layout
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        self.setCentralWidget(central_widget)
        main_layout.addWidget(self.sd)
        main_layout.addWidget(self.scroll, stretch=2)
        main_layout.addWidget(self.log_console)

        # Add drop/drag label for empty window behind plugins
        self.restore_placeholder()

        # Scan for plugins
        self.load_plugins()
        self.sd.enable_streams()


    # ─── DRAG & DROP HANDLERS FOR CONTAINER WIDGET ───────────────────────

    def _container_drag_enter(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-qt-widget-pointer"):
            event.acceptProposedAction()

    def _container_drag_move(self, event):
        event.acceptProposedAction()

    def _container_drop(self, event):
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                file_path = url.toLocalFile()
                if file_path.endswith('.py'):
                    self.load_plugin(file_path)
            event.acceptProposedAction()

    # ─── PLUGIN LOADER & LIFECYCLE ───────────────────────────────────────

    def unload_plugin(self, plugin_instance):
        """Cleanly unloads a plugin instance from the UI and tracking lists."""
        if plugin_instance in self.loaded_plugins:
            # 1. Stop upstream communication signals
            try:
                plugin_instance.plugin_event.disconnect(self.handle_plugin_event)
            except TypeError:
                pass # Already disconnected or no slot found

            # Call plugin's on_exit()
            if hasattr(plugin_instance, 'on_exit'):
                exit_func = getattr(plugin_instance, 'on_exit')
                if callable(exit_func):
                    try:
                        exit_func()
                    except Exception as e:
                        print(f"Error executing on_exit for {plugin_instance}: {e}")
            
            # 2. Remove it from the layout visually
            self.plugin_layout.removeWidget(plugin_instance)
            
            # 3. Clean up the tracking registry array
            self.loaded_plugins.remove(plugin_instance)
            
            # 4. Safely delete the C++ object from memory
            plugin_instance.deleteLater()
            
            # 5. Log the action
            plugin_name = plugin_instance.__class__.__name__
            self.log_console.append(f"[SYSTEM] Cleanly unloaded: {plugin_name}")
            
            # 6. Restore the placeholder if no plugins are left
            if not self.loaded_plugins:
                self.restore_placeholder()

    def show_plugin_context_menu(self, position, plugin_instance):
        """Displays a context menu at the mouse coordinates to unload the plugin."""
        from PyQt6.QtWidgets import QMenu
        
        menu = QMenu(self)
        unload_action = menu.addAction("Unload Plugin")
        
        # Display the menu at the global cursor position
        action = menu.exec(plugin_instance.mapToGlobal(position))
        if action == unload_action:
            self.unload_plugin(plugin_instance)

    def restore_placeholder(self):
        """Brings back the default visual prompt if all plugins are deleted."""
        self.placeholder = QLabel("Drag & Drop your plugin `.py` files here")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #a0aec0; font-size: 15px; padding: 50px;")
        self.plugin_layout.addWidget(self.placeholder)

    def enable_internal_drag_reordering(self, widget):
        """Injects custom mouse handlers to turn the dynamic plugin into a draggable item."""
        def dynamic_mouse_move(mouse_event):
            # Only trigger movement execution when holding left click down
            if mouse_event.buttons() != Qt.MouseButton.LeftButton:
                return
                
            drag_operation = QDrag(widget)
            mime_data = QMimeData()
            mime_data.setData("application/x-qt-widget-pointer", b"")
            drag_operation.setMimeData(mime_data)
            
            # Snap a clean visual ghost copy to slide around with the mouse
            pixmap = QPixmap(widget.size())
            widget.render(pixmap)
            drag_operation.setPixmap(pixmap)
            drag_operation.setHotSpot(mouse_event.position().toPoint())
            
            # Hide the physical card during dragging to give a clean layout gap feedback feel
            widget.hide()
            
            # Execute drop tracking loop
            if drag_operation.exec(Qt.DropAction.MoveAction) == Qt.DropAction.IgnoreAction:
                widget.show()

        # Monkeypatch the custom logic straight onto the active runtime widget instance
        widget.mouseMoveEvent = dynamic_mouse_move

    # ─── BI-DIRECTIONAL COMMUNICATION ────────────────────────────────────

    def handle_plugin_event(self, plugin_id: str, event_type: str, payload: dict):
        """UPSTREAM SIGNAL: Collects real-time events sent up by plugins."""
        log_msg = f"[{plugin_id} -> {event_type}]: {payload}"
        self.log_console.append(log_msg)

    def broadcast_to_plugins(self):
        """DOWNSTREAM TIMER: Pushes global clock sync down into plugins."""
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        broadcast_data = {"time": current_time}
        
        for plugin in self.loaded_plugins:
            try:
                # Dispatches the time sync payload safely down the interface contract
                plugin.receive_global_broadcast("SYSTEM_TICK", broadcast_data)
            except Exception as e:
                import traceback
                print(f"Failed broadcasting to {plugin.__class__.__name__}:")
                traceback.print_exc()

    def closeEvent(self, event: QCloseEvent):
        # Call on_exit for each plugin
        for p in self.loaded_plugins:

            #print(f'Unloading {p.plugin_id}    path={p.file_path}')
            
            if hasattr(p, 'on_exit'):
                exit_func = getattr(p, 'on_exit')
                if callable(exit_func):
                    try:
                        exit_func()
                    except Exception as e:
                        print(f"Error executing on_exit for {p}: {e}")
                        
        # Save main window geometry
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.endGroup()
        
        super().closeEvent(event)

    def load_plugin(self, file_path):
        #print(f'LOAD PLUGIN: {file_path}')
        module_name = os.path.splitext(os.path.basename(file_path))[0] # Ensure [0] grabs string name cleanly
        try:
            if module_name in sys.modules:
                del sys.modules[module_name]
                self.log_console.append(f"[SYSTEM] Cleared cache for {module_name}.")
                
            loader = importlib.machinery.SourceFileLoader(module_name, file_path)
            spec = importlib.util.spec_from_loader(module_name, loader)
            module = importlib.util.module_from_spec(spec)
            
            print
            print(f'  file_path = {file_path}  ')
            print(f'  module_name = {module_name}  ')
            #print(f'  loader = {loader}  ')
            #print(f'  spec = {loader}  ')
            #print(f'  module = {loader}  ')
            # Map the module globally BEFORE executing it
            sys.modules[module_name] = module

            # Ensure parent search paths handle local vs bundled runtime base directories
            if getattr(sys, 'frozen', False):
                # THE CRITICAL EXECUTION ANCHOR:
                # Force the plugin runner to prioritize looking at the temporary extraction root
                if sys._MEIPASS not in sys.path:
                    sys.path.insert(0, sys._MEIPASS)
            else:
                base_dir = Path(__file__).parent.parent
                if str(base_dir) not in sys.path:
                    sys.path.insert(0, str(base_dir))

            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BasePlugin) and attr is not BasePlugin:
                    if attr.__module__ == module.__name__:
                        if self.placeholder:
                            self.placeholder.deleteLater()
                            self.placeholder = None
                        
                        plugin_instance = attr(sd=self.sd)
                        plugin_instance.file_path = Path(file_path).as_posix()
                        
                        # Add tracking list and apply to core layout stack
                        self.loaded_plugins.append(plugin_instance)
                        self.plugin_layout.addWidget(plugin_instance)
                        
                        # Connect right-click context menu triggers 
                        plugin_instance.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                        plugin_instance.customContextMenuRequested.connect(
                            lambda pos, p=plugin_instance: self.show_plugin_context_menu(pos, p)
                        )
                        
                        # Inject layout drag and drop rules
                        self.enable_internal_drag_reordering(plugin_instance)
                        
                        # Connect PyQt signals upstream to dashboard log terminal
                        plugin_instance.plugin_event.connect(self.handle_plugin_event)
                        
                        self.log_console.append(f"[SYSTEM] Successfully registered: {attr.__name__}")
                        return
        except Exception as e:
            if module_name in sys.modules:
                del sys.modules[module_name]
            self.log_console.append(f"[ERROR] Failed to load {file_path}: {e}")
            print(f"[ERROR] Failed to load {file_path}: {e}")

    def load_plugins(self):
        # Scan and load external loose user plugins out of 'plugins/' directory
        base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent 
        external_plugins_dir = base_dir / "plugins"
        
        #print(f"[DEBUG] Scanning for user plugins at: {external_plugins_dir.resolve()}")

        if external_plugins_dir.exists():
            for file_path in external_plugins_dir.glob("*.py"):
                if file_path.name == "__init__.py":
                    continue
                self.log_console.append(f"[SYSTEM] Found external user plugin: {file_path.name}")
                self.load_plugin(str(file_path))

        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainDashboard()
    window.show()
    sys.exit(app.exec())
