"""
    Tone gernerator plugin
    2026-10-06
    Todo:
        Add modulation

"""

import re
import numpy as np

from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer, QPointF, QSettings
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QGridLayout, 
    QPushButton, QLabel, QSlider, QComboBox, QLineEdit, QDial
)

from src.config import DBMIN, DBMAX
from src.plugin_interface import BasePlugin
from src.common import stof, ftos
from src.output_control import OutputCtlKnob    # Output strip w/ volume and ch buttons
from src.dials import CustomColorDial


def square_wave(phase):
    """Takes an array of phases (in radians) and returns a square wave between -1 and 1."""
    # np.sign returns -1, 0, or 1. We replace 0 with 1 to keep it binary if needed,
    # but standard sign is usually sufficient.
    res = np.sign(np.sin(phase))
    # Optional: ensure exact 0 points don't flatten the wave unpredictably
    res[res == 0] = 1.0
    return res

def triangle_wave(phase):
    """Takes any raw phase array (-inf to +inf) and returns a clean triangle wave."""
    return (2 / np.pi) * np.arcsin(np.sin(phase))


class TonePlugin(BasePlugin):
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
            .TonePlugin {
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
        title_label = QLabel('Tone Generator')
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

        # Sliders
        self.sli = {}
        self.lbl = {}
        self.sliders = [ 
            # Name,     Min,    Max,    Color   AllowWrap
            ('lin',     0,      100,    '#C58', True    ), 
            ('course',  0,      100,    '#FD4', True    ), 
            ('fine',    0,      100,    '#EE5', True    ), 
            ('note',    0,      12,     '#8D4', True    ), 
            ('cents',   -50,    50,     '#8D0', True    ) 
        ]
        self.slider_last_value = {}
        for i, (s, min_val, max_val, color, wrap) in enumerate(self.sliders):
            self.lbl[s] = QLabel(s.capitalize())
            self.lbl[s].setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sli[s] = CustomColorDial()
            self.sli[s].setWrapping(wrap)
            self.sli[s].setFixedSize(32,32)
            self.sli[s].setRange(min_val, max_val)
            self.sli[s].config(
                fill_color = color, 
                indicator_color = '000',
                wheel_step_size = 1,
            )
            match s:
                case 'lin' | 'course' | 'fine' :
                    self.sli[s].config(
                        span_angle = -360,          # Rotary-encoder mode
                        indicator_radius = 0,
                    )
                    
            self.sli[s].valueChanged.connect(lambda *args, ctl=s: self.on_slider(ctl, *args))

            # Save value for wraparound checking
            self.slider_last_value[s] = 0

        # Per-knob styling
        #self.sli['lin']['indicator_radius'] = 0


        # Direct-entry edit box
        self.freq_entry = QLineEdit()
        self.freq_entry.setPlaceholderText('Enter freq')
        self.freq_entry.textChanged.connect(self.on_freq_entry)
        self.freq_entry.setToolTip(f"""
            <div style="white-space: nowrap; color: #000; padding: 6px;
            font-family: 'Courier New', monospace; font-size: 16px;">
                <b>Direct frequency entry:</b><br>
                freq | note_name[octave_num][+/-cents] | #midi_note<br>
                Examples:
                <table style="margin-top: 5px; border-collapse: collapse;">
                    <tr>
                        <td style="padding-left: 10px;color: #00F; ">100.4</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>100.4Hz</td>
                    </tr>
                    <tr>
                        <td style="padding-left: 10px; color: #00F">1.234k</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>1234Hz</td>
                    </tr>
                    <tr>
                        <td style="padding-left: 10px; color: #00F">#69</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>MIDI note #69, A4, 440Hz</td>
                    </tr>
                    <tr>
                        <td style="padding-left: 10px; color: #00F">A</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>Note A, octave 4(def), MIDI #69, 440Hz</td>
                    </tr>
                    <tr>
                        <td style="padding-left: 10px; color: #00F">C2</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>Note C, octave 2, MIDI #36, 65.406Hz</td>
                    </tr>
                    <tr>
                        <td style="padding-left: 10px; color: #00F">A#-1+36</td>
                        <td style="padding-left: 10px; padding-right: 10px;">:</td>
                        <td>Note A#, octave -1, MIDI #10 -36cents</td>
                    </tr>
                </table>
            </div>
        """)    

        # Display labels
        self.dis = {}
        ll = [ 
            # Name, color, width
            ('hz',      '#F55', 90), 
            ('midi',    '#000', 40), 
            ('note',    '#000', 40), 
            ('cents',   '#000', 40), 
        ]
        for i, (s, color, width) in enumerate(ll):
            self.dis[s] = QLabel(s)
            self.dis[s].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.dis[s].setFixedWidth(width)
            self.dis[s].setStyleSheet(f"""
                QLabel {{
                    font-family: 'Courier New';
                    font-size: 14px;
                    font-weight: bold;
                    color: {color};
                }}
            """)
            
        # Wafeform button
        self.btn ={}

        
        self.btn['wave'] = QPushButton('wave')
        self.btn['wave'].clicked.connect(lambda *args, ctl='wave' : self.on_button(ctl, *args))
        self.btn['wave'].setStyleSheet("""
            QPushButton {
                padding: 2px 2px 2px 2px;
                font-size: 14px;
                border: 1px solid #555555;
                border-radius: 2px;
                background-color: #f0f0f0;
                color: #000000;
          }
            QPushButton:pressed {
                background-color: #0078d4;
                color: #ffffff;
            border-color: #005a9e;
          }
            QPushButton:hover {
                border-color: #0078d4;
          }
        """)



        # ----------------------------------------------------------------------

        # Output strip
        self.out_ctl = OutputCtlKnob(settings=self['settings'])


        # ----------------------------------------------------------------------
        # Layout
        # ----------------------------------------------------------------------
    
        # Display layout
        self.display_frm = QFrame()
        self.display_frm.setStyleSheet('background-color: #DFE;')
        self.display_layout = QHBoxLayout(self.display_frm)
        self.display_layout.setContentsMargins(2,2,2,2) #w,n,e,s
        self.display_layout.setSpacing(2)

        for s in self.dis.keys():
            self.display_layout.addWidget(self.dis[s])

        # Display + Edit frame layout
        self.de_frm = QFrame()
        self.de_lo = QVBoxLayout(self.de_frm)
        self.de_lo.setContentsMargins(2,2,2,2) #w,n,e,s
        self.de_lo.setSpacing(2)
        self.de_lo.addWidget(self.display_frm)
        self.de_lo.addWidget(self.btn['wave'])
        self.de_lo.addWidget(self.freq_entry)

        # Slider frame
        self.slider_frm = QFrame()
        self.slider_lo = QGridLayout(self.slider_frm)
        self.slider_lo.setContentsMargins(2,2,2,2) #w,n,e,s
        self.slider_lo.setSpacing(2)
        for i, s in enumerate(self.sli.keys()):
            self.slider_lo.addWidget(self.lbl[s], 0, i)
            self.slider_lo.addWidget(self.sli[s], 1, i)

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
        self.main_body_layout.addWidget(self.de_frm, 1)
        self.main_body_layout.addWidget(self.slider_frm)
        self.main_body_layout.addWidget(self.out_ctl)

        # Main layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(0)
        self.main_layout.setContentsMargins(0,0,0,0)
        self.main_layout.addWidget(self.title_bar_frame)
        self.main_layout.addWidget(self.main_body_frame)
        self.setLayout(self.main_layout)

        self.waves = [
            # Button text, function
            ('Sine',        np.sin),
            ('Triangle',    triangle_wave),
            ('Square',      square_wave)
        ]
        self.wave_idx = 0
        self.btn['wave'].setText(self.waves[self.wave_idx][0])
        
        # Misc constants
        self.note_list = [ 
            'C', 'C#' , 'D', 'D#', 'E', 'F', 
            'F#', 'G', 'G#' ,'A', 'A#', 'B' 
        ]
        self.fmin = 1               # Constant for min frequency
        self.scale = 0.01           # fine knob +/- percentage
        # Misc vars set by sd_config callback
        self.fs = 20000
        self.blocksize = 0
        # Tone generator state vars:
        self.freq = 1000            # Target frequency
        self.next_freq = self.freq
        self.next_gain = 0
        self.next_p = 0

        # Load settings
        f = stof(self['settings'].value('frequency', '220'))
        self.set_note_cents_sliders_from_freq(f)
        self.set_tone_freq(f)

        # Restore show/hide state
        if bool(self['settings'].value('visible', True)) == False:
            self.main_body_frame.hide()

        # Add sd callbacks
        self['sd'].add_callback('config', self.sd_config_callback)
        self['sd'].add_callback('stream', self.sd_stream_callback)
        
    def on_exit(self):
        # Called from main app when closing or plugin removal.

        # Save show/hide state
        self['settings'].setValue('visible', int(self.main_body_frame.isVisible()))
        
        self['sd'].remove_callback('config', self.sd_config_callback)
        self['sd'].remove_callback('stream', self.sd_stream_callback)

        self['settings'].setValue('frequency', ftos(self.freq))

        super().on_exit()

    def on_show_hide_button(self):
        # Handle the titlebar show/hide button
        if self.main_body_frame.isVisible():
            self.main_body_frame.hide()
        else:
            self.main_body_frame.show()

            
    #--- Helper functions

    def hz_to_note(self, f):
        # Return (midi_note, note_name, cents) from frequency f

        # Find nearest semitone by midi note number
        midi_note = round(np.log(f/440) / np.log(2) * 12 + 69)
        
        # Find octave number and note name
        midi_oct = int(midi_note / 12) - 1
        note_name = f'{self.note_list[midi_note % 12]}{midi_oct}'

        # Find delta between nearest note and f 
        exact_note_freq = 440 * 2 ** ((midi_note-69)/12)
        cents = round(np.log(f/exact_note_freq) / np.log(2) * 1200)

        return midi_note, note_name, cents  
    
    def set_note_cents_sliders_from_freq(self, f):
        self.sli['note'].blockSignals(True)
        self.sli['cents'].blockSignals(True)

        midi_note, note_name, cents = self.hz_to_note(f)

        self.sli['note'].setValue(midi_note)
        self.sli['cents'].setValue(cents)

        self.sli['note'].blockSignals(False)
        self.sli['cents'].blockSignals(False)

    def set_course_fine_sliders_from_freq(self, f):
        return
    
        self.sli['course'].blockSignals(True)
        self.sli['fine'].blockSignals(True)

        # Get v (0.0 - 1.0) as log of freq
        v = np.log(f / self.fmin) / np.log(self.fs / 2 / self.fmin)

        # Some constants for clearer math
        cmin = self.sli['course'].minimum()   # 0
        cmax = self.sli['course'].maximum()   # 200
        fmin = self.sli['fine'].minimum()     # -100
        fmax = self.sli['fine'].maximum()     # 100
        s = self.scale

        if v <= s:
            cf = 0
        elif v >= (1 - s):
            cf = 1
        else:
            cf = v / (1 - 2*s)
            cf = round((cmax-cmin) * cf) / (cmax-cmin)

        ff = (v - cf * (1 - 2*s)) / (2*s)
        self.sli['course'].setValue(int(cmin + cf * (cmax-cmin)))
        self.sli['fine'].setValue(int(fmin + ff * (fmax-fmin)))
            
        self.sli['course'].blockSignals(False)
        self.sli['fine'].blockSignals(False)

    def course_fine_sliders_to_freq(self):
        cmin = self.sli['course'].minimum()   # 0
        cmax = self.sli['course'].maximum()   # 200
        fmin = self.sli['fine'].minimum()     # -100
        fmax = self.sli['fine'].maximum()     # 100
        
        # Get course as float (0.0 - 1.0)
        cval = self.sli['course'].value()
        cf = (cval - cmin) / (cmax - cmin)

        # Get fine as float (-1 .. 1)
        fval = self.sli['fine'].value()
        ff = (fval - fmin) / (fmax - fmin)

        # Combine course and fine to v (0.0 - 1.0)
        v = cf * (1 - self.scale * 2) + ff * self.scale * 2
        return self.fmin * (self.fs / 2 / self.fmin) ** v

    def midi_note_to_freq(self, midi_note, cents=0):
        # Return frquency in hz given a midi note number and optional cents.
        # Return None if out of range.
        if (0 <= midi_note <= 127) and (-1200 <= cents <= 1200):
            return 440 * 2 ** (((midi_note - 69) / 12) + (cents / 1200))
        return None

    def set_tone_freq(self, f):
        # Update display and osc frequency from f (hz).
        if f == None or f < 0 or f > 1E06: 
            return False
        
        # Set tone freq
        self.freq = f
        
        # Update display
        self.dis['hz'].setText(f'{ftos(f, between=' ')}Hz ')
        midi_note, note_name, cents = self.hz_to_note(f)
        self.dis['midi'].setText(f'#{midi_note} ')
        self.dis['note'].setText(f'{note_name} ')
        self.dis['cents'].setText(f'{cents:+d}c ')
        
        return True
        
    # --- Widget handlers
    
    def on_slider(self, ctl, value):

        # Block all slider signals in group
        for s in self.sli.keys():
            self.sli['fine'].blockSignals(True)

        # Reset fine sliders when course sliders change
        match ctl:
            # case 'course':
                # self.sli['fine'].setValue(0)
            case 'note':
                self.sli['cents'].setValue(0)

        # Get control direction, inc or dec
        cmin = self.sli[ctl].minimum()   # 0
        cmax = self.sli[ctl].maximum()   # 100
        d = value - self.slider_last_value[ctl]
        wth = (cmax-cmin) / 2
        if   d > wth:   delta = -1  # Negative wrap
        elif d < -wth:  delta = 1   # Positive wrap
        elif d < 0:     delta = -1  # dec
        elif d > 0:     delta = 1   # inc
        else:           delta = 0


        match ctl:
            case 'course':
                # 10/ decade step
                f = self.freq * 10 ** (delta / 10)
            case 'fine':
                # 1/100 decade step
                f = self.freq * 10 ** (delta / 100)
            case 'note':
                # Note step
                f = self.freq * 2 ** (delta / 12)
            case 'cents':
                # 1/100 note step
                f = self.freq * 2 ** (delta / 1200)
                
            case 'lin':
                # Linear step with rounding
                f = self.freq
                if   f >= 1000:     step = 100
                elif f >= 100:      step = 10
                else:               step = 1
                
                f = round(f/step + delta) * step
        f = np.clip(f, self.fmin, self.fs / 2)        
        #self.set_course_fine_sliders_from_freq(f)
        self.set_note_cents_sliders_from_freq(f)
        self.set_tone_freq(f)                
                
        self.freq_entry.clear()
        
        # Save state to compare next time
        self.slider_last_value[ctl] = value
        
        # Restore all slider signals in group
        for s in self.sli.keys():
            self.sli['fine'].blockSignals(False)

    def on_freq_entry(self, text):
        """
            Direct freq entry. Valid formats:
            10        10.0Hz
            23.45k    23.45kHz
            C#        C#4 (octave defaults to 4)
            A5 -47    A4 minus 50 cents 
            #69       MIDI note 69 (=A4 = 440Hz)
            #69 +2    MIDI note 69 plus 2 cents
        """

        self.freq_entry.setStyleSheet(' QLineEdit {color: #000000; }')
        text = text.strip()
        f = None
        
        # Try decimal number with optional k suffix
        if (m := re.match(r"^([0-9.k]+)$", text)):
            f = stof(m.groups()[0])
            
        # Try NoteName [[+/-]OctaveNumber] [[+-]cents]
        elif (m := re.match(r"^([a-gA-G]#?)\s*([-+]?[0-9]+)?\s*([-+]?[0-9]+)?$", text)):
            # Group[0] = note_name
            # Group[1] is octave number if present, default 4 if not
            # Group[2] is cents if present, default 0 if not
            note_name = m.groups()[0].upper()
            note_name = 'C' if note_name == 'B#' else note_name
            note_name = 'F' if note_name == 'E#' else note_name
            octave = int(m.groups()[1]) if m.groups()[1] else 4
            midi_note = (1 + octave) * 12 + self.note_list.index(note_name)
            cents = int(m.groups()[2]) if m.groups()[2] else 0
            f = self.midi_note_to_freq(midi_note, cents)
            
        # Try #MidiNoteNum [[+-]cents]
        elif (m := re.match(r'^#(\d+)\s*([-+]?[0-9]+)?$', text)):
            # Group[0] is midi_note
            # Group[1] is cents if present, default 0 if not.
            midi_note = int(m.groups()[0])
            cents = int(m.groups()[1]) if m.groups()[1] else 0
            f = self.midi_note_to_freq(midi_note, cents)
        
        # Set frequency if something above worked
        if f and f > self.fmin and f < self.fs/2:
            self.set_tone_freq(f)
            #self.set_course_fine_sliders_from_freq(f)
            self.set_note_cents_sliders_from_freq(f)

        else:
            # Bad entry
            if text != '':
                self.freq_entry.setStyleSheet(' QLineEdit {color: #FF0000; }')
            return 

    def on_button(self, ctl, state):
        match ctl:
            case 'wave':
                self.wave_idx = (self.wave_idx + 1) % len(self.waves)
                self.btn['wave'].setText(self.waves[self.wave_idx][0])

                    
    # --- SoundDev callbacks 

    def sd_config_callback(self, params):
        self.blocksize = params['blocksize']
        self.fs = params['samplerate']

        # Set output strip channel count
        self.out_ctl['channels'] = params['out_ch']
        
    def sd_stream_callback(self, outdata, indata):
        if not np.any(self.out_ctl['ch_enables']):
            return

        """
        Simple mono tone generator
                Input variables
                    self.blocksize              How many sample to generate
                    self.fs                     Sample rate
                    self.freq                   Target frequency for this buf
                    self.out_ctl['gain']        Output level
                    self.out_ctl['ch_enables']  Which channels to broadcase to
                Saved state between calls:
                    self.next_p
                    self.next_gain
                    self.next_freq
        """
        # Ramp frequency from self.next_freq to self.freq
        freq = np.linspace(self.next_freq, self.freq, self.blocksize)
        # Save ending freq as start for next call
        self.next_freq = self.freq

        # Ramp volume from self.next_gain to gain slider setting
        gain_ramp = np.linspace(self.next_gain, self.out_ctl['gain'], self.blocksize)
        self.next_gain = self.out_ctl['gain']
        
        # Array of phase increments from freq array
        pinc = (2 * np.pi * freq) / self.fs
        
        # Array of running phase
        phi = self.next_p + np.cumsum(pinc) % (2 * np.pi)
        
        # Save last phase for next buffer
        self.next_p = phi[-1]
        
        # Add to outdata (with volume and ch enables)
        func = self.waves[self.wave_idx][1]
        outdata += np.outer(func(phi) * gain_ramp, self.out_ctl['ch_enables'])


