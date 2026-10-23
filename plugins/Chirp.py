"""
    Todo:
        Add Hz - snap 'rotary encoders'
        Add modulation

"""
import re
import numpy as np

from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer, QPointF, QSettings
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, 
    QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QGridLayout, 
    QPushButton, QLabel, QSlider, QComboBox, QLineEdit, QDial
)


from src.config import DBMIN, DBMAX
from src.plugin_interface import BasePlugin
from src.common import stof, ftos
from src.output_control import OutputCtlKnob    # Output strip w/ volume and ch buttons
#from dials import CustomColorDial

class ChirpPlugin(BasePlugin):
    DEFAULTS = {
        'settings'      : None,                 # Parent group if present
        'sd'            : None,                 # Required - SoundDecCtl window
    }
        
    def cget(self, key):
        if key in self.params.keys():
            return self.params[key]
        else:
            raise ValueError
            
    def configure(self, cnf=None, **kwargs):
        for key, value in kwargs.items():
            self.params[key] = value
                
    config = configure

    def __getitem__(self, key):
        return self.cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __init__(self, *args, **kwargs):
        self.params = self.DEFAULTS.copy()
        self.params.update(kwargs)
        #self.configure(**kwargs)   # Trigger all the dictionary handlers

        # Initialize base class
        super().__init__()
        
        # Create settings instance
        new_settings = QSettings()
        if self['settings']:
            new_settings.beginGroup(self['settings'].group())
        new_settings.beginGroup(self.__class__.__name__)
        self['settings'] = new_settings

        # Frame styling
        self.setStyleSheet("""
            .ChirpPlugin {
                border: 1px solid #888888;
                border-radius: 1px;
                background-color: #E5F5E5;
            }
            QFrame {
                border: 1px solid #DDDDDD;
                border-radius: 1px;
                /*background-color: #f9f9f9;*/
            }
            QLabel {
                border: none;
                background-color: transparent;
            }
            QLineEdit {
                border: none;
            }
            QPushButton {
                padding: 2px 2px 2px 2px;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 1px;
                /*background-color: #0078d4;*/
                color: #000000;
            }
            QPushButton:pressed {
                background-color: #0078d4;
                color: #ffffff;
                border-color: #005a9e;
            }
            PushButton:hover {
                border-color: #0078d4;
            }
        """)

        # ----------------------------------------------------------------------
        # Top label & show/hide button
        # ----------------------------------------------------------------------
        title_label = QLabel('Chirp Generator')
        title_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: 'blue';
                border: none;
                padding: 2px 4px 2px 4px;
            }
        """)

        show_hide_button = QPushButton()
        show_hide_button.setFixedWidth(15)
        show_hide_button.clicked.connect(self.on_show_hide_button)        

        # ----------------------------------------------------------------------

        self.knob = {}  # CustomColorDial
        self.lbl = {}   # QLabel
        self.edit = {}  # QLineEdit
        self.btn = {}   # QPushButton
        self.cbx = {}   # QComboBox
        
        # Labels
        labels = [
            # id            text
            ('fmin',        'Fmin'),
            ('fmax',        'Fmax'),
            ('time',        'Time'),
        ]
        for (name, text) in labels:
            self.lbl[name] = QLabel(text)
            self.lbl[name].setAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Edit boxes
        edits = [
            # id            placeholder     default_val
            ('fmin',        'Start Hz',     '10'    ),
            ('fmax',        'End Hz',       '20k'   ),
            ('time',        'Duration',     '5'     ),
        ]
        for (name, phold, vdef) in edits:
            self.edit[name] = QLineEdit()
            self.edit[name].setPlaceholderText(phold)
            self.edit[name].setText(self['settings'].value(f'{name}', vdef))
            self.edit[name].textChanged.connect(lambda *args, n=name : self.on_edit(n, *args))
        
        # Comboboxes
        combos = [
            # id            list                default
            ('chirp_type',  ['Lin', 'Log'],     'Lin'         ),
        ]
        for (name, vlist, vdef) in combos:
            self.cbx[name] = QComboBox()
            self.cbx[name].addItems(vlist)
            self.cbx[name].setCurrentText(self['settings'].value(f'{name}', vdef))
            self.cbx[name].currentIndexChanged.connect(lambda *args, n=name : self.on_combo(n, *args))

        # Pushbuttons
        buttons = [
            # id            text
            ('start',       'Start' ),
        ]
        for (name, text) in buttons:
            self.btn[name] = QPushButton(text)
            self.btn[name].clicked.connect(lambda *args, n=name : self.on_button(n, *args))

        # Output strip
        self.out_ctl = OutputCtlKnob(settings=self['settings'])

        # ----------------------------------------------------------------------
        # Layout
        # ----------------------------------------------------------------------
    
        # Label & Edit frame
        self.edit_frm = QFrame()
        self.edit_layout = QGridLayout(self.edit_frm)
        self.edit_layout.setContentsMargins(2,2,2,2) #w,n,e,s
        self.edit_layout.setSpacing(2)
        for i, name in enumerate(self.edit.keys()):
            self.edit_layout.addWidget(self.lbl[name], i, 0)
            self.edit_layout.addWidget(self.edit[name], i, 1)

        self.cb_frm = QFrame()
        self.cb_layout = QVBoxLayout(self.cb_frm)
        self.cb_layout.setContentsMargins(2,2,2,2) #w,n,e,s
        self.cb_layout.setSpacing(2)
        self.cb_layout.addWidget(self.cbx['chirp_type'])
        self.cb_layout.addWidget(self.btn['start'])


        # Top frame with title label and show/hide button
        self.title_bar_frame = QFrame()
        self.title_bar_layout = QHBoxLayout(self.title_bar_frame)
        self.title_bar_layout.setSpacing(2)
        self.title_bar_layout.setContentsMargins(2,2,2,2)
        self.title_bar_layout.addWidget(title_label, 1)
        self.title_bar_layout.addWidget(show_hide_button)

        # Bottom frame with everything else (this gets hidden/shown)
        self.main_body_frame = QFrame()
        self.main_body_layout = QHBoxLayout(self.main_body_frame)
        self.main_body_layout.setContentsMargins(0,0,0,0)
        self.main_body_layout.setSpacing(0)
        self.main_body_layout.addWidget(self.edit_frm, 1)
        self.main_body_layout.addWidget(self.cb_frm)
        self.main_body_layout.addWidget(self.out_ctl)

        # Main layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(0)
        self.main_layout.setContentsMargins(0,0,0,0)
        self.main_layout.addWidget(self.title_bar_frame)
        self.main_layout.addWidget(self.main_body_frame)
        self.setLayout(self.main_layout)

        # Restore show/hide state
        if bool(self['settings'].value('visible', True)) == False:
            self.main_body_frame.hide()

        # These are set by start_streams()
        self.fs = 48000             # Sample rate
        self.blocksize = 1024       # Block size
        
        # Chirp updates these when running. Initialize to zero
        self.next_gain = 0
        self.next_t = 0             # Next buffer timestamp
        self.next_p = 0             # Next buffer phase
        self.block = 0              # Current block while running
        self.running = False        # Set to start chirp, clears when finished
        
        # Chirp params
        self.fmin = stof(self.edit['fmin'].text())
        self.fmax = stof(self.edit['fmax'].text())
        self.time = stof(self.edit['time'].text())
        self.chirp_type = self.cbx['chirp_type'].currentIndex()            # 0=Lin, 1=Log

        # Set controls
        #a = self['settings'].value(f'gain', -20)
        #self.out_ctl['gain_db'] = a

        # Add sd callbacks
        self['sd'].add_callback('config', self.sd_config_callback)
        self['sd'].add_callback('stream', self.sd_stream_callback)

    def on_show_hide_button(self):
        # Handle the titlebar show/hide button
        if self.main_body_frame.isVisible():
            self.main_body_frame.hide()
        else:
            self.main_body_frame.show()

    def on_exit(self):
        # Called from main app when closing or plugin removal.

        # Save show/hide state
        self['settings'].setValue('visible', int(self.main_body_frame.isVisible()))
        
        self['sd'].remove_callback('config', self.sd_config_callback)
        self['sd'].remove_callback('stream', self.sd_stream_callback)
        
    # --- Widget handlers

    def on_combo(self, name, value):
        txt = self.cbx[name].currentText()
        self['settings'].setValue(f'{name}', txt)
        self.chirp_type = value
    
    def on_edit(self, name, value):
        self.edit[name].setStyleSheet(' QLineEdit {color: #000000; }')
        try:
            n = stof(value)
            self['settings'].setValue(f'{name}', value)
            match name:
                case 'fmin':    self.fmin = n
                case 'fmax':    self.fmax = n
                case 'time':    self.time = n
        except ValueError:
            self.edit[name].setStyleSheet(' QLineEdit {color: #FF0000; }')

    def on_button(self, name, state):
        if self.running == True:
            self.running = False
            self.btn[name].setStyleSheet("background-color: #0078d4")
        else:
            self.running = True
            self.btn[name].setStyleSheet("background-color: #DD0000")
            self.next_p = 0   # Reset phase
            self.next_t = 0   # Reset time
            self.block = 0    # Reset block counter
            
    # --- SoundDev callbacks 

    def sd_config_callback(self, params):
        self.blocksize = params['blocksize']
        self.fs = params['samplerate']

        # Set output strip channel count
        self.out_ctl['channels'] = params['out_ch']
        
    def sd_stream_callback(self, outdata, indata):
        if self.running == False:
            return

        # Generate continuous array of timestamps. 
        # Array size is blocksize + 1, and the last element is stripped
        # and saved and used as the starting time for the next call.
        t = np.linspace(
          self.next_t, 
          self.blocksize / self.fs + self.next_t, 
          self.blocksize + 1,
          endpoint=True,
        )
        self.next_t = t[self.blocksize]
        t = t[:self.blocksize]
          
        # Convert time to frequency:
        match self.chirp_type:
          case 0: # Lin
            freq = self.fmin + (self.fmax - self.fmin) * (t / self.time)
          case 1: # Exp
            freq = self.fmin * (self.fmax / self.fmin) ** (t / self.time)

        # Array of phase increments
        pinc = (2 * np.pi * freq) / self.fs
        
        # Array of running phase
        phi = self.next_p + np.cumsum(pinc) % (2 * np.pi)
        
        # Save last phase for next buffer
        self.next_p = phi[-1]

        # Ramp volume from self.next_gain to gain slider setting
        gain_ramp = np.linspace(self.next_gain, self.out_ctl['gain'], self.blocksize)
        self.next_gain = self.out_ctl['gain']
        
        # # Add to outdata (with volume and ch enables)
        outdata += np.outer(np.sin(phi) * gain_ramp, self.out_ctl['ch_enables'])

        if self.next_t >= self.time:
            self.running = False
            self.btn['start'].setStyleSheet("background-color: #0078d4")
            