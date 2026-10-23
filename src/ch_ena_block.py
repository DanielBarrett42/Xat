

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QPushButton
import math
import numpy as np

class ChEnaBlockIO(QFrame):
    def __init__(self, *args, **kwargs):
        # Grab our args
        self.button_callback = kwargs.pop('button_callback', None)

        # Initialize base class
        super().__init__(*args, **kwargs)

        self.setStyleSheet("""
            .ChEnaBlockIO {
                border: 1px solid #888888;
                border-radius: 1px;
                background-color: #E5F5E5;
            }
            QFrame {
                border: 1px solid #0078d4;
                border-radius: 2px;
                background-color: #f9f9f9;
            }            

            QPushButton {
                padding: 0px 2px 0px 2px;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 1px;
                background-color: #f0f0f0;
                color: #000000;
            }
            QPushButton:checked {
                border-color: #005a9e;
                background-color: #0078d4;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #0078d4;
            }
        """)
        
        # Button frame layout
        self.button_layout = QGridLayout(self)
        self.button_layout.setContentsMargins(2,2,2,2) #w,n,e,s
        self.button_layout.setSpacing(2)
        self.setLayout(self.button_layout)
        
        self.btns = {}
        self.saved_button_state = ''
        self.mtx = np.zeros(1)
        self.in_ch = 0

    def on_ch_button(self, ctl, state):
        # Handler for all channel in/out buttons
        ch = int(ctl[1:])
        ch += self.in_ch if ctl[0] == 'O' else 0
        self.mtx[ch] = 1 if self.btns[ctl].isChecked() else 0

        # Save the button settings
        self.saved_button_state = ", ".join(key for key, button in self.btns.items() if button.isChecked())

        # Tell parent something changed
        if callable(getattr(self, 'button_callback', None)):
            self.button_callback(self.mtx)
   
    # --- Public functions
   
    def add_remove_ch_buttons(self, in_ch, out_ch):
        """
            in_ch, out_ch               New channel counts
            self.btns                   Dictionary of all buttons
            self.button_layout          Layout for above
            self.saved_button_state     String of last-known ON buttons 
            self.mtx                    numpy.array of button states
            Notes:
                Button counts can change as SoundDevice interface / API changes.
                Buttons are only added to self.btns[], and reused when needed.
        """

        # Need this for the matrix output channel offset
        self.in_ch = in_ch

        # Get current button counts (visible or not)
        old_in_count  = len([key for key in self.btns if key[0] == 'I'])
        old_out_count = len([key for key in self.btns if key[0] == 'O'])

        # Done if nothing needs updating
        layout_changed = (old_in_count != in_ch or old_out_count != out_ch)
        if layout_changed == False:
            return
            
        # Add input buttons if needed
        new_in_count = max(old_in_count, in_ch)
        for ch in range(new_in_count):
            key = f'I{ch:02d}'
            if key not in self.btns:
                # Add a new button
                self.btns[key] = QPushButton(f'i{1 + ch}')
                self.btns[key].setCheckable(True)
                self.btns[key].setStyleSheet("""
                    QPushButton {
                        background-color: #DAF2D0;
                        color: #000000;
                    }
                    QPushButton:checked {
                        background-color: #8ED973;
                        color: #FFFFFF;
                    }
                """)
                # Enable button if it was on when we last saw it
                self.btns[key].setChecked(key in self.saved_button_state)
                # Add handler
                self.btns[key].toggled.connect(lambda *args, ctl=key : self.on_ch_button(ctl, *args))
        
        # Add output buttons if needed
        new_out_count = max(old_out_count, out_ch)
        for ch in range(new_out_count):
            key = f'O{ch:02d}'
            if key not in self.btns:
                # Add a new button
                self.btns[key] = QPushButton(f'o{1 + ch}')
                self.btns[key].setCheckable(True)
                self.btns[key].setStyleSheet("""
                    QPushButton {
                        background-color: #CAFDFB;
                        color: #000000;
                    }
                    QPushButton:checked {
                        background-color: #61CBF3; 
                        color: #FFFFFF;
                    }
                """)
                # Enable button if it was on when we last saw it
                self.btns[key].setChecked(key in self.saved_button_state)
                # Add handler
                self.btns[key].toggled.connect(lambda *args, ctl=key : self.on_ch_button(ctl, *args))
        
        # Clear button frame layout
        for btn in self.btns.values():
            if self.button_layout.indexOf(btn) != -1:
                self.button_layout.removeWidget(btn)
                btn.hide()

        # Create layout: ins populate down columns up to max_rows, then outs
        max_rows = 4
        n_in_cols = math.ceil(in_ch / max_rows)
        n_out_cols = math.ceil(out_ch / max_rows)
        for key in self.btns.keys():
            stream = key[0]
            ch = int(key[1:])
            if (stream == 'I' and ch < in_ch) or (stream == 'O' and ch < out_ch):
                row = ch % max_rows
                col = ch // max_rows
                col += n_in_cols if stream == 'O' else 0
                self.button_layout.addWidget(self.btns[key], row, col)
                self.btns[key].show()
                
        # Update the gain matrix size if needed, keeping button states.
        if self.mtx.shape != in_ch + out_ch:
            self.mtx = np.zeros(in_ch + out_ch)
            for ch in range(in_ch):
                self.mtx[ch] = 1 if self.btns[f'I{ch:02d}'].isChecked() else 0
            for ch in range(out_ch):
                self.mtx[ch+in_ch] = 1 if self.btns[f'O{ch:02d}'].isChecked() else 0

        # Tell parent something changed
        if callable(getattr(self, 'button_callback', None)):
            self.button_callback(self.mtx)

    def get_button_matrix(self):
        return self.mtx

    def get_save_state(self):
        return self.saved_button_state
        
    def set_save_state(self, state):
        self.saved_button_state = state

