# Plugin base class. Classes derrived from this can be loaded into main window

from PyQt6.QtWidgets import QFrame
from PyQt6.QtCore import pyqtSignal, QSettings

"""
    kwargs:
        settings    An open QSettings object
                    Default: New QSettings object
        group       QSettings group for this plugin
                    Default: Class name
"""
class BasePlugin(QFrame):
    # Every external plugin must inherit from this class.
    """
    # SIGNAL: Allows the plugin to send messages/data back to the Main Window
    # We pass (plugin_name, event_type, payload_dict)
    """
    plugin_event = pyqtSignal(str, str, dict)

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent)
        #print(f'BasePlugin init:  {self.__class__.__name__}')

    def on_exit(self):
        # Called from main app when closing or plugin removal.
        pass
        
    def receive_global_broadcast(self, event_type: str, data: dict):
        """
        The Main Window calls this method to push data DOWN into the plugin.
        Override this in your plugin to react to main window events.
        """
        print(f'[base plugin]  received: event {event_type}  data {data}')
