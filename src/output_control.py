"""
  Output strip with volume slider and channel enable buttons.
"""
import numpy as np

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, 
    QPushButton, QLabel, QSlider, QSpacerItem, QSizePolicy
)
from src.config import APP_CO, APP_NAME, DBMIN, DBMAX
from src.common import stof, ftos
from src.dials import CustomColorDial


class OutputCtlKnob(QFrame):
    def __init__(self, parent=None, **kwargs):
        # Grab our args
        self.sd = kwargs.pop('sd', None)
        self.settings = kwargs.pop('settings', QSettings())

        # Initialize base class
        super().__init__(parent)

        # Gain pot
        self.gain_pot = CustomColorDial()
        self.gain_pot.setFixedSize(32,32)
        self.gain_pot.config(fill_color = '#5AC', indicator_color = '000')
        self.gain_pot.setRange(DBMIN, DBMAX)
        self.gain_pot.valueChanged.connect(self.on_gain_pot)

        # Gain label
        self.gain_label = QLabel()
        self.gain_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.gain_label.setStyleSheet(f"""
            QLabel {{
                border: none;
                color: blue;
                /*font-family: 'Courier New'; */
                /*font-size: 16px;    */
                font-weight: bold;
            }}
        """)
        
        
        # Layout
        # self  -> pot_label_frm    -> gain_pot, gain_label
        #       -> btn_frm          -> buttons
        self.pot_label_frm = QFrame()
        self.pot_label_frm.setStyleSheet('border: none;')
        self.pot_label_layout = QVBoxLayout(self.pot_label_frm)
        self.pot_label_layout.setContentsMargins(2,2,2,2)
        self.pot_label_layout.setSpacing(2)
        self.pot_label_layout.setAlignment(Qt.AlignmentFlag.AlignBottom) 
        self.pot_label_layout.addWidget(self.gain_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.pot_label_layout.addWidget(self.gain_pot, alignment=Qt.AlignmentFlag.AlignHCenter)
        
        self.button_frm = QFrame()
        self.button_frm.setStyleSheet('border: none;')
        self.button_layout = QGridLayout(self.button_frm)
        self.button_layout.setContentsMargins(2,2,2,2)
        self.button_layout.setSpacing(2)
        self.button_layout.setAlignment(Qt.AlignmentFlag.AlignBottom) 
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(2,2,2,2) #w,n,e,s
        self.layout.setSpacing(1)
        self.layout.addWidget(self.pot_label_frm)
        self.layout.addWidget(self.button_frm)
        self.layout.addStretch()
        self.setLayout(self.layout)
        
        # Misc vars (public read/write with dictionary methods
        self.params = {
            'channels'        : 0,            # Number of channels currently active
            'gain'            : 0,            # Current gain setting (A, not dB)
            'gain_db'         : 0,            # Current gain setting in dB
            'ch_enables'      : np.zeros(0),  # Numpy array of channel enables (1.0=on, 0.0=off)
        }

        # Channel enable buttons will be added by set_channels()
        self.ch_buttons = {}  
        
        # Set initial gain pot default
        self['gain_db'] = self.settings.value('out_gain', -60, type=int)
    
    def on_ch_button(self, ctl, state):
        # Handler for channel enable buttons
        st = 1 if self.ch_buttons[ctl].isChecked() else 0
        self.params['ch_enables'][int(ctl)] = st
        self.settings.setValue(f'out_ena{ctl}', int(state))

    def on_gain_pot(self, value):
        self.params['gain'] = 10 ** (value / 20)
        self.gain_label.setText(f'{value}dB')
        self.settings.setValue('out_gain', int(value))

    # --- Support functions
    
    def set_ch_count(self, channels):
        
        # Add/show/hide output buttons
        self.params['channels'] = channels
        ch_all = max(self.params['channels'], len(self.ch_buttons))
        
        for ch in range(ch_all):
            if ch not in self.ch_buttons:
                # Add channel button if needed
                self.ch_buttons[ch] = QPushButton(f'{1+ch}')
                self.ch_buttons[ch].setCheckable(True)
                self.ch_buttons[ch].setChecked(self.settings.value(f'out_ena{ch}', 0, type=int))
                self.ch_buttons[ch].toggled.connect(lambda *args, ctl=ch : self.on_ch_button(ctl, *args))
                self.ch_buttons[ch].setStyleSheet("""
                    QPushButton {
                        padding: 0px 2px 0px 2px;
                        font-size: 10px;
                        border: 1px solid #555555;
                        border-radius: 1px;
                        
                        /* Unchecked (Default) State Colors */
                        background-color: #f0f0f0;
                        color: #000000;
                    }
                    
                    /* Checked (Depressed) State Colors */
                    QPushButton:checked {
                        background-color: #0078d4;
                        color: #ffffff;
                        border-color: #005a9e;
                    }
                    
                    /* Optional: Hover effect for a smoother feel */
                    QPushButton:hover {
                        border-color: #0078d4;
                    }
                """)
                self.button_layout.addWidget(self.ch_buttons[ch], ch % 2, ch // 2)
            else:
                # Button exists; Show/hide
                if ch < self.params['channels']:
                    self.ch_buttons[ch].show()
                else:
                    self.ch_buttons[ch].hide()

        # Resize ch_enable matrix to match current channel count
        if self.params['ch_enables'].shape != self.params['channels']:
            self.params['ch_enables'] = np.zeros(self.params['channels'])

        # Sync the ch_enables with button state
        for ch in range(self.params['channels']):
            self.params['ch_enables'][ch] = 1 if self.ch_buttons[ch].isChecked() else 0

    def set_ch_states(self, ch_list):
        if isinstance(ch_list, list):
            mtx = np.array(ch_list)
        elif isinstance(ch_list, np.ndarray):
            mtx = ch_list
        else:
            raise ValueError('ch_list must be a np.array or list')
        
        # Set channel count if needed
        n = mtx.shape[0]
        if n > self.params['channels']:
            self.set_ch_count(n)
            
        for ch in range(n):
            self.ch_buttons[ch].setChecked(True if mtx[ch] == 1 else False)
            
    #--- Public dictionary method handlers

    def cget(self, key):
        if key not in self.params.keys():
            raise ValueError(f'Key {key} not in dictionary.')
        match key:
            case 'gain_db':
                return self.gain_pot.value()
            case _:
                return self.params[key]

    def configure(self, cnf=None, **kwargs):
        # Dictionary method write handler
        for key, value in kwargs.copy().items():
            if key in self.params.keys():
                kwargs.pop(key, None)
                # Things that need special handling:
                match key:
                    case 'channels':
                        self.set_ch_count(value)
                        
                    case 'gain_db':
                        cv = self.gain_pot.value()
                        if value == cv:
                            self.on_gain_pot(value)
                        else:
                            self.gain_pot.setValue(round(value))
                            
                    case 'gain':
                        self.params['gain_db'] = round(20 * np.log10(value))
                        
                    case 'ch_enables':
                        self.set_ch_states(value)
                        
                    case _:
                        self.params[key] = value
                    
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})
    

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow
    app = QApplication(sys.argv)
    window = QMainWindow()
    x = OutputCtlKnob()
    window.setCentralWidget(x)
    window.show()
    sys.exit(app.exec())
