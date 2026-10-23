"""
    eXperimental Audio Tool
    dbarrett@tenxp.com
    2026-09-26
    Noise generator plugin
"""
import numpy as np
import math
from scipy.signal import lfilter, lfilter_zi

from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer, QPointF, QSettings
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, 
    QLabel, QComboBox, QPushButton
)

from src.config import DBMIN, DBMAX
from src.plugin_interface import BasePlugin
from src.output_control import OutputCtlKnob    # Output strip w/ volume and ch buttons

class NoisePlugin(BasePlugin):
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
            .NoisePlugin {
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
        title_label = QLabel('Noise Generator')
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

        self.lbl = {}
        self.cbx = {}
        self.lbl['noise_type'] = QLabel('Noise type')
        #self.lbl[].setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.cbx['noise_type'] = QComboBox()
        self.cbx['noise_type'].currentIndexChanged.connect(lambda *args, c='noise_type': self.on_combo(c, *args))

        # Output strip
        self.out_ctl = OutputCtlKnob(settings=self['settings'])

        # ----------------------------------------------------------------------
        # Layout
        # ----------------------------------------------------------------------

        # Control frame
        control_frame = QFrame()
        control_layout = QGridLayout(control_frame)
        control_layout.setSpacing(2)
        control_layout.setContentsMargins(2,2,2,2)
        control_layout.addWidget(self.lbl['noise_type'], 0, 0)
        control_layout.addWidget(self.cbx['noise_type'], 0, 1)
        control_layout.setColumnStretch(1, 1)
        
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
        self.main_body_layout.addWidget(control_frame, 1)
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

        # Runtime vars
        v = self['settings'].value(f'gain', -20.0, type=float)
        #self.out_ctl['gain_db'] = v
        self.next_gain = 0

        v = self['settings'].value(f'white_gain', -18.0, type=float)
        self.white_gain = 10 ** (v / 20) 

        v = self['settings'].value(f'pink_gain', 10.0, type=float)
        self.pink_gain = 10 ** (v / 20) 

        v = self['settings'].value(f'pink_poles', 5, type=int)
        self.pink_poles = v

        v = self['settings'].value(f'pink_hpf', 5.0, type=float)
        self.pink_hpf = v

        v = self['settings'].value(f'noise_type', 'White', type=str)
        self.noise_types = ['White', 'Pink']
        self.noise_type = self.noise_types.index(v)
        self.cbx['noise_type'].blockSignals(True)
        self.cbx['noise_type'].addItems(self.noise_types)
        self.cbx['noise_type'].setCurrentIndex(self.noise_type)
        self.cbx['noise_type'].blockSignals(False)

        self.blocksize = 0

        # Add callbacks
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
        
        self['sd'].remove_callback('config', self.sd_config_callback)
        self['sd'].remove_callback('stream', self.sd_stream_callback)

        # Save show/hide state
        self['settings'].setValue('visible', int(self.main_body_frame.isVisible()))

        self['settings'].setValue(f'white_gain', 20 * math.log10(self.white_gain))
        self['settings'].setValue(f'pink_gain', 20 * math.log10(self.pink_gain))
        self['settings'].setValue(f'pink_poles', self.pink_poles)
        self['settings'].setValue(f'pink_hpf', self.pink_hpf)
        self['settings'].setValue(f'noise_type', self.cbx['noise_type'].currentText())

    def on_combo(self, c, idx):
        # Handler for all ComboBox changes
        
        # idx is -1 when boxes are cleared
        if idx < 0:
            return

        txt = self.cbx[c].currentText()
        dat = self.cbx[c].currentData()

        match c:
            case 'noise_type':
                self['settings'].setValue(f'{c}', txt)
                self.noise_type = idx
                if txt == 'Pink':
                    self.init_pink_filter()

    def init_pink_filter(self):
        """
            self.pink_poles
            self.pink_hpf
            self.fs
        """
        
        # 1. Setup frequency limits in rad/s
        w_min = 2 * np.pi * self.pink_hpf
        w_max = 2 * np.pi * (self.fs / 2.1) # Safe approach to the Nyquist limit
        
        # 2. Number of pole-zero pairs (4 pairs covers roughly 4 decades perfectly)
        
        # Calculate geometric spacing factor 'k' to bridge the frequency band
        # w_max = w_min * k^(self.pink_poles - 0.5)
        k = (w_max / w_min) ** (1.0 / (self.pink_poles - 0.5))
        
        analog_poles = []
        analog_zeros = []
        
        # 3. Geometrically place poles and zeros along the analog s-plane real axis
        for i in range(self.pink_poles):
            p_analog = -w_min * (k ** i)
            z_analog = -w_min * (k ** (i + 0.5))
          
            analog_poles.append(p_analog)
            # Omit the final zero if it pushes past Nyquist boundaries
            if i < self.pink_poles - 1:
                analog_zeros.append(z_analog)

        #print(f'poles={analog_poles}  zeros={analog_zeros}')

        # 4. Map analog roots to digital Z-domain via Bilinear Transform
        digital_poles = [(2 * self.fs + p) / (2 * self.fs - p) for p in analog_poles]
        digital_zeros = [(2 * self.fs + z) / (2 * self.fs - z) for z in analog_zeros]
        
        # 5. Convert roots back into standard polynomial arrays
        self.a = np.poly(digital_poles)
        self.b = np.poly(digital_zeros)
        
        # 6. Normalize DC Gain so that the filter doesn't blow up or attenuate too heavily
        # At DC (z=1), Gain = sum(b) / sum(a)
        dc_gain = np.sum(self.b) / np.sum(self.a)
        self.b = self.b / dc_gain  

        # Initialize filter state to zeros (perfectly phase continuous)
        self.zi = lfilter_zi(self.b, self.a) * 0.0

    def noise(self):
        white = np.random.normal(0, 1, self.blocksize)
        match self.noise_type_shadow:
            case 0: # while
                #white = white / np.max(np.abs(white))
                return white * self.white_gain
            case 1: # pink
                pink, self.zi = lfilter(self.b, self.a, white, zi=self.zi)
                #pink = pink / np.max(np.abs(pink)) * 1
                return pink * self.pink_gain

    def sd_config_callback(self, params):
        # Setup before calling generator
        self.blocksize = params['blocksize']
        self.fs = params['samplerate']

        # Set output strip channel count
        self.out_ctl['channels'] = params['out_ch']

        self.init_pink_filter()
        
    def sd_stream_callback(self, outdata, indata):
        if not np.any(self.out_ctl['ch_enables']):
            return

        # Ramp volume from self.next_gain to gain slider setting
        gain_ramp = np.linspace(self.next_gain, self.out_ctl['gain'], self.blocksize)
        self.next_gain = self.out_ctl['gain']

        white = np.random.normal(0, 1, self.blocksize)
        match self.noise_type:
            case 0: # while
                sig = white * self.white_gain
            case 1: # pink
                pink, self.zi = lfilter(self.b, self.a, white, zi=self.zi)
                sig =  pink * self.pink_gain

        # Add to outdata (with volume and ch enables)
        outdata += np.outer(sig * gain_ramp, self.out_ctl['ch_enables'])
