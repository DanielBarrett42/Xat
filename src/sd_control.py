"""
    eXperimental Audio Tool
    dbarrett@tenxp.com
    2026-10-01
    Requirements:   pyqt6 sounddevice numpy
        python -m venv venv
        ./venv/Scripts/Activate.ps1
        pip install pyqt6 sounddevice numpy


        Chains:
            
            [in] -> [input strips] -> input chain -> [queue]
                                   |
                                   |----------------------->[monitor]
                                                        --->[        ]
                                                                       |
            [queue] -> stream chain -> output chain -> [output strips] -> [out]
            
            Name             Data       Intended use:
            config            params    Called with stream params before streaming starts
            input             in        Just for input strip
            stream            in,out    For general plugin use, effects, siggens, etc.
            output            out       Just for output strip
            monitor           in+out    For FFT, RTA, etc.

        Block of variables we make available:
        - params is passed to everything in config chain just before stream starts
        - These are also available as dictionary items, e.g., self['samplerate']
        self.params = {
            'api'                 : Name of selected API
            'samplerate'          : 48000,
            'blocksize'           : 1024,
            'in_idx'              : SoundDevice device index
            'in_name'             : Device name
            'in_ch'               : Number of channels in stream
            'in_lat_ms'           : Reported latency in ms
            'in_lat_samples'      : Latency in samples
            'in_lat_blocks'       : Latency in blocks
            'out_idx'             : <as above>
            'out_name'            : ...
            'out_ch'              : 
            'out_lat_ms'          : 
            'out_lat_samples'     : 
            'out_lat_blocks'      : 
            'active'              : True if streaming, False otherwise
        }
            
"""


import os
os.environ["SD_ENABLE_ASIO"] = "1"  # Set before importing sounddevice. Value not important.
import sounddevice as sd
import numpy as np
import queue
import threading  # for lock
import re
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QSizePolicy,
    QLabel, QComboBox, QPushButton, QSpacerItem, QProgressBar,
    QSlider, QFrame
)

from src.config import APP_CO, APP_NAME, DBMIN, DBMAX

def DBGPRINT(*args, **kwargs):
    if False:
        print(*args, *kwargs)

class ChannelStrip(QFrame):
    def __init__(self, **kwargs):
        # Args that must be specified:
        self.ch = kwargs.pop('ch', 0)               # channel number (zero-based)
        self.io_type = kwargs.pop('io_type', 'in')  # 'in' or 'out'
        self.settings = kwargs.pop('settings', QSettings())
        
        super().__init__(**kwargs)

        # Configurable constants
        self.meter_peak_hold_time = 0.2         # seconds
        self.meter_peak_hold_decay_rate = 20    # dB/seconds
        self.tmr_ms = 100                       # Repeating timer rate
        self.gain_db = 0                        # Initial slider gain

        # Misc variables
        self.gain = self.gain_save = 10 ** (self.gain_db)
        self.peak_db = self.gain_db
        self.peak_hold_db = DBMIN
        self.peak_hold_ts = datetime.now().timestamp()
        self.level_queue = queue.Queue(maxsize=10)
        
        self.wgt = {} # All the widgets in the strip

        # Meter
        self.wgt['meter'] = QProgressBar(self)
        self.wgt['meter'].setMinimum(DBMIN)
        self.wgt['meter'].setMaximum(DBMAX)
        self.wgt['meter'].setValue(DBMIN)
        self.wgt['meter'].setTextVisible(False)    
        self.wgt['meter'].setFixedWidth(30)

        # Slider
        self.wgt['slider'] = QSlider(Qt.Orientation.Horizontal, self)
        self.wgt['slider'].setMinimum(-60)
        self.wgt['slider'].setMaximum(60)
        self.wgt['slider'].setValue(self.gain_db)
        self.wgt['slider'].valueChanged.connect(self.on_slider)

        # Channel label
        self.wgt['ch_label'] = QLabel(f'{self.ch+1}')
        self.wgt['ch_label'].setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: 'blue';
            }
        """)

        # Slider Gain label
        self.wgt['gain_label'] = QLabel(f'{self.gain_db:+5.0f}dB')
        self.wgt['gain_label'].setAlignment(Qt.AlignmentFlag.AlignRight)
        self.wgt['gain_label'].setFixedWidth(30) 
        self.wgt['gain_label'].setStyleSheet("""
            QLabel {
                color: "#0000FF";
            }
         """)    

        # Pre-gain Level label
        self.wgt['pre_level_label'] = QLabel()
        self.wgt['pre_level_label'].setAlignment(Qt.AlignmentFlag.AlignRight)
        self.wgt['pre_level_label'].setFixedWidth(30) 
        
        # Post-gain Level label
        self.wgt['post_level_label'] = QLabel()
        self.wgt['post_level_label'].setAlignment(Qt.AlignmentFlag.AlignRight)
        self.wgt['post_level_label'].setFixedWidth(30) 

        # Frame styling
        self.setStyleSheet("""
            .ChannelStrip {
                border: 1px solid #888888;
                border-radius: 1px;
                background-color: #F5F5E5;
            }
        """)

        # Frame layout
        if self.io_type == 'in':
            order = [ 'ch_label', 'pre_level_label', 'meter', 'gain_label', 'slider', 'post_level_label', ]
        else:
            order = [ 'ch_label', 'pre_level_label', 'slider', 'gain_label', 'meter', 'post_level_label', ]
        
        self.lo = QGridLayout()
        self.lo.setContentsMargins(4,0,4,0)
        self.setLayout(self.lo)
        for idx, c in enumerate(order):
            #self.wgt[c].setMaximumHeight(35)
            self.wgt[c].setFixedHeight(15)
            self.lo.addWidget(self.wgt[c], 0, idx)
            if c == 'gain_slider':
                self.lo.setColumnStretch(idx, 1)  # Slider absorbs stretch
        
        # Load saved setting
        self.set_name = f'sd_ch_strip_{self.io_type}_{self.ch}'
        db = self.settings.value(self.set_name, 0)
        self.wgt['slider'].setValue(db)
        self.on_slider(db)  # In case value is same and handler doesn't fire:

        # Start
        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer)
        self.timer.start(self.tmr_ms)

    def on_timer(self):
        # Reating timer function to update level labels and meter

        # Get the peak from the queue
        peak = 10 ** (DBMIN / 20)
        while True:
            try:
                peak = max(peak, self.level_queue.get_nowait())
            except queue.Empty:
                break

        db_in = 20 * np.log10(peak)
        db_peak = max(db_in, self.peak_hold_db)

        # Track and hold peak and set timer when exceeded
        if db_in > self.peak_hold_db:
            self.peak_hold_db = db_in
            self.peak_hold_ts = datetime.now().timestamp()

        # Hold for a bit, then begin decaying
        if (datetime.now().timestamp() - self.peak_hold_ts) > self.meter_peak_hold_time:
            self.peak_hold_db -= (self.meter_peak_hold_decay_rate * self.tmr_ms / 1000)

        # Update Pre-gain Level label
        self.wgt['pre_level_label'].setText(f'{db_peak:+5.0f}dB')

        # Update meter level
        if self.io_type == 'in':    
            self.wgt['meter'].setValue(int(db_peak))  # Meter is pre-gain
        else:
            self.wgt['meter'].setValue(int(db_peak + self.gain_db))  # Meter is post-gain

        # Update Post-gain Level label
        self.wgt['post_level_label'].setText(f'{db_peak + self.gain_db:+5.0f}dB')

        # Set colors
        def level_to_color(db):
            if    db > -3:  return '#FF8888' # Red
            elif  db > -10: return '#FFFF88' # Yel
            else:           return '#88FF88' # Grn
            
        def clip_color(db):
            if    db > -3:  return '#FF0000' # Red
            else:           return '#000000' # Black

        # Set meter color
        c = level_to_color(db_peak)
        self.wgt['meter'].setStyleSheet(f'QProgressBar::chunk {{background-color: {c};}}')

        # Set Pre-gain Level color
        c = clip_color(db_peak)
        self.wgt['pre_level_label'].setStyleSheet(f'QLabel {{color: {c};}}')

        # Post-gain Label - Change to red foreground at db > -3
        c = clip_color(db_peak + self.gain_db)
        self.wgt['post_level_label'].setStyleSheet(f'QLabel {{color: {c};}}')

    def on_slider(self, value):
        self.gain_db = value
        self.wgt['gain_label'].setText(f'{self.gain_db:+5.0f}dB')
        self.gain = 10 ** (self.gain_db / 20)
        self.settings.setValue(self.set_name, self.gain_db)
        
    def pa_callback(self, buf):
        # Adjust the gain for all samples in buf
        # This runs in PortAudio RT context.
        
        # Find the peak signal level in this buf and feed into queue.
        # This is pulled and processed in on_timer()
        peak = np.max(np.abs(buf))
        try:
            self.level_queue.put_nowait(peak)
        except queue.Full:
            pass
        
        # Apply gain ramp to buf (single-channel).
        # This allows a sample-by-sample smooth gain change
        # if slider changes quickly.
        a = np.linspace(self.gain_save, self.gain, buf.shape[0])
        self.gain_save = self.gain
        buf *= a
        
class SoundDevCtl(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        """
            API: [v] Hz: [v] Blocksize: [v] Latency: [v] [Scan] [Start] [Stop]
            In: [v]               Out: [v]
            Input channels:       Output channels:
            <channel strips>      <channel strips>
        """
        # Saved settings
        self.settings = QSettings()
        self.settings.beginGroup('SoundDevCtl')

        # Frame styling
        self.setStyleSheet("""
            .SoundDevCtl {
                border: 1px solid #888888;
                border-radius: 1px;
                background-color: #F5F5E5;
            }
        """)
            
        self.lo = {}    # Layouts
        self.lbl = {}   # QLabels
        self.cbx = {}   # QComboBoxs
        self.btn = {}   # QPushButtons
        self.in_strips = {}
        self.out_strips = {}


        # Setup the main window layout
        self.lo['main'] = QGridLayout()
        self.lo['main'].setSpacing(4)

        for r in ['row0', 'row1', 'row2', 'row3']:
            self.lo[r] = QHBoxLayout()
            
        for r in ['ins', 'outs']:
            self.lo[r] = QGridLayout()
            self.lo[r].setRowStretch(20,1)  # Empty last row absorbs stretch
            self.lo['row3'].addLayout(self.lo[r])
            
        for r_idx, key in enumerate(['row0', 'row1', 'row2', 'row3']):
            self.lo['main'].addLayout(self.lo[key], r_idx, 0)
        self.setLayout(self.lo['main'])


        # Row 0
        for s, t in [('api', 'API:'), ('fs', 'Hz:'), ('blocksize', 'Blocksize:'), ('latency', 'Latency:')]:
            self.lbl[s] = QLabel(t)
            self.lbl[s].setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.cbx[s] = QComboBox()
            self.cbx[s].currentIndexChanged.connect(lambda *args, c=s: self.on_combo(c, *args))
            self.lo['row0'].addWidget(self.lbl[s])
            self.lo['row0'].addWidget(self.cbx[s])
            
            
        for s in ['scan', 'start', 'stop']:
            self.btn[s] = QPushButton(s.capitalize())
            self.btn[s].clicked.connect(lambda *args, c=s: self.on_button(c, *args))
            self.btn[s].setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.lo['row2'].addWidget(self.btn[s])
        self.lbl['status'] = QLabel()
        self.lbl['status'].setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lo['row2'].addWidget(self.lbl['status'])
        
        # Row 1
        for s, t in [('indev', 'In:'), ('outdev', 'Out:')]:
            self.lbl[s] = QLabel(t)
            self.lbl[s].setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.cbx[s] = QComboBox()
            self.cbx[s].currentIndexChanged.connect(lambda *args, c=s: self.on_combo(c, *args))
            self.lo['row1'].addWidget(self.lbl[s])
            self.lo['row1'].addWidget(self.cbx[s])
        
        # Row 2
        for s, t in [('ins', 'Input channels:'), ('outs', 'Output channels:')]:
            # Add label to row 0
            self.lbl[s] = QLabel(t)
            self.lbl[s].setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lo[s].addWidget(self.lbl[s], 0, 0)
        
        # Block of variables we make available:
        self.params = {
            'api'                 : '',
            'samplerate'          : 48000,
            'blocksize'           : 1024,
            'in_idx'              : 0,
            'in_name'             : '',
            'in_ch'               : 1,
            'in_lat_ms'           : 1,
            'in_lat_samples'      : 1,
            'in_lat_blocks'       : 1,
            'out_idx'             : 0,
            'out_name'            : '',
            'out_ch'              : 1,
            'out_lat_ms'          : 1,
            'out_lat_samples'     : 1,
            'out_lat_blocks'      : 1,
            'active'              : False,
        }
        self.enabled = False  # Don't start streaming yet
        self.ostrm = None     # Output stream
        self.istrm = None     # Input stream
        self._last_latency = None   # Save latency state to detect change
        self.in_queue = queue.Queue(maxsize = 8)  # Queue for input processor
        self.in_queue_preload = 2   # How many buffers to preload at stream start
        # Chains
        self.lock = threading.Lock()
        self.chains = {
            'config' : [],
            'input' : [],
            'stream' : [],
            'output' : [],
            'monitor' : [],
        }
        
        # Kick things off
        self.sd_init()

        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer)
        self.timer.start(100)

    def on_exit(self):
        self.settings.endGroup()
            
    # --- Widget event handlers
        
    def on_combo(self, c, idx):
        # Handler for all ComboBox changes
        
        # idx is -1 when boxes are cleared
        if idx < 0:
            return

        #if self.initialzed == False:
        #  return
            
        txt = self.cbx[c].currentText()
        dat = self.cbx[c].currentData()
        
        # Save changed parameter to ini file
        api_name = self.cbx['api'].currentText()
        match c:
            case 'indev':
                # Prepend API name to device name
                self.settings.setValue(f'{api_name}/InputDevice', txt)
            case 'outdev':
                # Prepend API name to device name
                self.settings.setValue(f'{api_name}/OutputDevice', txt)
            case _:
                # Just save setting by name. api, fs, latency, blocksize
                self.settings.setValue(c, txt)
                
        # Call dependent handlers when things change
        match c:
            case 'api':
                self.get_input_options()
                self.get_output_options()
                self.get_srate_options()

            case 'indev' | 'outdev':
                self.get_srate_options()
        
        # (re)start the streams
        self.sd_start_streams()
            
    def on_button(self, c, x):
        match c:
            case 'start':
                self.sd_start_streams()
            case 'stop':
                self.sd_stop_streams()
            case 'scan':
                self.sd_init()

    def on_timer(self):
        # Update play/rec LEDs

        if (self.ostrm and self.istrm and
            self.ostrm.active and self.istrm.active    ):
            cpuo = 100 * self.ostrm.cpu_load
            cpui = 100 * self.istrm.cpu_load
            cpu_max = max(cpui, cpuo)
            
            cpuo_txt = f'Streaming, cpu load {cpui:.2f}, {cpuo:.2f}%'
            
            if   cpu_max > 70:  cpuo_color = '#FF8888'
            elif cpu_max > 30:  cpuo_color = '#FFFF88'
            else:               cpuo_color = '#DDFFDD'
        else:
            cpuo_color = '#FFFFFF'
            cpuo_txt = "Stopped"
        
        self.lbl['status'].setStyleSheet(f'background-color: {cpuo_color};')
        self.lbl['status'].setText(cpuo_txt)


    # --- Misc support functions

    def clean_name(self, name):
        # Clean up a device name.

        # Remove all control chars from name.
        name = re.sub(r'[\x00-\x1F\x7F-\x9F\u200B-\u200D\u2028-\u2029\uFEFF]', '', name)

        # Replace problematic punc chars with space.
        name = re.sub(r'[\r\n\t#@%;,\\]', ' ', name)

        # Collapse multiple spaces to one, trim leading/trailing space.
        name = re.sub(r'\s+', ' ', name).strip()
        
        # Truncate to max length.
        #name = name[:60]

        return name

    # --- Functions to populate comboboxes

    def get_api_options(self):
        # Populate API Combobox and choose an API.

        # Block on_combo handler
        self.cbx['api'].blockSignals(True)

        # Clear current contents
        self.cbx['api'].clear()

        # Get list of host API's
        self.apis = sd.query_hostapis()
        
        # Populate combobox with those that have available devices
        for i, api in enumerate(self.apis):
            if len(api['devices']) > 0:
                self.cbx['api'].addItem(api['name'], i)
            else:
                DBGPRINT(f'No available {api['name']} devices.')
                
        api_list = [self.cbx['api'].itemText(i) for i in range(self.cbx['api'].count())]

        # Try to get preferred api
        api = self.settings.value('api')
        if api and api not in api_list:
            api = None

        # Pick one from list if that failed
        if api == None:
            for a in [ 'MME', 'Windows WASAPI', 'ASIO', 'Windows WDM_KS', 'Windows DirectSound' ]:
                if a in api_list:
                    api = a
                    break

        # Use the default if that failed
        if api == None:
            api = self.apis[sd.default.hostapi]['name']     

        # Set combobox selection & save to ini
        if api != None:
            self.cbx['api'].setCurrentText(api)
            self.settings.setValue('api', api)

        # Restore on_combo handler
        self.cbx['api'].blockSignals(False)

    def get_input_options(self):
        # Populate Input Device combobox and choose a device.
        # Note: get_api_options() must run first.
        
        # Block on_combo handler
        self.cbx['indev'].blockSignals(True)
        
        # Clear current contents
        self.cbx['indev'].clear()
        
        # Get list of all devices
        self.devs = sd.query_devices()

        # Get current API
        api_idx = self.cbx['api'].currentData()
        api_name = self.cbx['api'].currentText()
        
        # Get list of input devices with channels for the current API
        for idx, dev in enumerate(self.devs):
            if dev['hostapi'] != api_idx:
                continue
            if dev['max_input_channels'] < 1:
                continue
            self.cbx['indev'].addItem(self.clean_name(dev['name']), idx)

        # Combobox contents
        dev_list = [self.cbx['indev'].itemText(i) for i in range(self.cbx['indev'].count())]

        # Try to get preferred device
        dev = self.settings.value(f'{api_name}/InputDevice')
        if dev and dev not in dev_list:
            dev = None
        else:
            DBGPRINT(f'Using saved input: {f'{api_name}/InputDevice'} = {dev}')

        # Use the default if that failed
        if dev == None:
            d_idx = self.apis[api_idx]['default_input_device']
            dev = self.clean_name(self.devs[d_idx]['name'])
            DBGPRINT(f'Using default input: {dev}')
        
        # Set combobox selection & save to ini
        if dev != None:
            self.cbx['indev'].setCurrentText(dev)
            self.settings.setValue(f'{api_name}/InputDevice', dev)
        # Restore on_combo handler
        self.cbx['indev'].blockSignals(False)

    def get_output_options(self):
        # Populate Output Device combobox and choose a device.
        # Note: get_api_options() must run first.

        # Block on_combo handler
        self.cbx['outdev'].blockSignals(True)

        # Clear current contents
        self.cbx['outdev'].clear()

        # Get list of all devices
        self.devs = sd.query_devices()

        # Get current API
        api_idx = self.cbx['api'].currentData()
        api_name = self.cbx['api'].currentText()
        
        # Get list of output devices with channels for the current API
        for idx, dev in enumerate(self.devs):
            if dev['hostapi'] != api_idx:
                continue
            if dev['max_output_channels'] < 1:
                continue
            self.cbx['outdev'].addItem(self.clean_name(dev['name']), idx)

        # Combobox contents
        dev_list = [self.cbx['outdev'].itemText(i) for i in range(self.cbx['outdev'].count())]

        # Try to get preferred device from ini, if it's available
        dev = self.settings.value(f'{api_name}/OutputDevice')
        if dev and dev not in dev_list:
         dev = None
        else:
            DBGPRINT(f'Using saved output: {f'{api_name}/OutputDevice'} = {dev}')

        # Use the default if that failed
        if dev == None:
            d_idx = self.apis[api_idx]['default_output_device']
            dev = self.clean_name(self.devs[d_idx]['name'])
            DBGPRINT(f'Using default output: {dev}')
        
        # Set combobox selection & save to ini
        if dev != None:
            self.cbx['outdev'].setCurrentText(dev)
            self.settings.setValue(f'{api_name}/OutputDevice', dev)
        # Restore on_combo handler
        self.cbx['outdev'].blockSignals(False)

    def get_srate_options(self):
        # Populate samplerate combobox with available samplerates

        # Block on_combo handler
        self.cbx['fs'].blockSignals(True)

        # Clear current contents
        self.cbx['fs'].clear()

        indev_idx = self.cbx['indev'].currentData()
        outdev_idx = self.cbx['outdev'].currentData()
        
        rates_to_test = [
                8000, 11025, 16000, 22050, 32000, 
                44100, 48000, 88200, 96000, 192000
        ]

        input_rates = []
        output_rates = []    
        for rate in rates_to_test:
            try:
                sd.check_input_settings(device=indev_idx, samplerate=rate)
                input_rates.append(str(rate))
            except sd.PortAudioError:
                pass

            try:
                sd.check_output_settings(device=outdev_idx, samplerate=rate)
                output_rates.append(str(rate))
            except sd.PortAudioError:
                pass
                
        common_rates = list(set(input_rates) & set(output_rates))
        common_rates.sort(key=int)
        if True:
            DBGPRINT('Supported sample rates:')
            DBGPRINT(f'  Input  : {input_rates}')
            DBGPRINT(f'  Output : {output_rates}')
            DBGPRINT(f'  Common : {common_rates}')
            
        if len(common_rates) == 0:
            DBGPRINT('No common samplerates available!')
        else:

            # Add supported rates to combobox
            self.cbx['fs'].addItems(common_rates)

            # Try to load samplerate from ini file
            fs = self.settings.value('fs')
            if fs not in common_rates:
                fs = None

            # Pick the highest available if that fails
            if fs == None:
                fs = str(common_rates[-1])
                DBGPRINT(f"-- Using highest supported sample rate: {fs}")
            else:
                DBGPRINT(f"-- Using saved samplerate: {fs}")
            
            # Set combobox selection and save to ini
            self.cbx['fs'].setCurrentText(fs)
            self.settings.setValue('fs', fs)

        # Unblock on_combo handler
        self.cbx['fs'].blockSignals(False)

    def get_misc_options(self):
        # Add some options and load defaults for blocksize and latency
        self.cbx['blocksize'].blockSignals(True)
        self.cbx['blocksize'].addItems(['128', '256', '512', '1024', '2048', '4096', '8192'])
        self.cbx['blocksize'].setCurrentText(self.settings.value('blocksize', '1024'))
        self.cbx['blocksize'].blockSignals(False)
        
        self.cbx['latency'].blockSignals(True)
        self.cbx['latency'].addItems(['low', 'high'])
        self.cbx['latency'].setCurrentText(self.settings.value('latency', 'high'))
        self.cbx['latency'].blockSignals(False)

    #--- Dictionary method handlers

    def cget(self, key):
        """
            Parameters: 
                'api'                 : '',
                'samplerate'          : 48000,
                'blocksize'           : 1024,
                'in_idx'              : 0,
                'in_name'             : '',
                'in_ch'               : 1,
                'in_lat_ms'           : 1,
                'in_lat_samples'      : 1,
                'in_lat_blocks'       : 1,
                'out_idx'             : 0,
                'out_name'            : '',
                'out_ch'              : 1,
                'out_lat_ms'          : 1,
                'out_lat_samples'     : 1,
                'out_lat_blocks'      : 1,
                'active'              : True if streaming
        """
        if key in self.params.keys():
            return self.params[key]

        return super().cget(key)

    def configure(self, cnf=None, **kwargs):
        # Extract and handle our args
        # for key, value in kwargs.copy().items():
            # match key:
                # case 'xxx':
                    # self.var.set(value)
                    # kwargs.pop(key, None)
                    
        # Pass what's left to Frame
        return super().configure(cnf, **kwargs)
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})

    # --- SoundDevice functions
    
    def sd_init(self):
        # (re)initialize SoundDevice / PortAudio and populate combobox options
        self.sd_stop_streams()
        sd._terminate()
        sd._initialize()
        self.get_api_options()
        self.get_input_options()
        self.get_output_options()
        self.get_srate_options()
        self.get_misc_options()

    def sd_stop_streams(self):
        # Note: Must close the output stream first, since it may
        # source data from the input stream
        
        self.params['active'] = False
        
        if self.ostrm != None:
            try:
                DBGPRINT('Output stream: abort()... ', end='')
                self.ostrm.abort()
                DBGPRINT('close()... ', end='')
                self.ostrm.close()
                DBGPRINT('Success.')
            except sd.PortAudioError as e:
                print(f'Exception: {e}.')
            self.ostrm = None

        if self.istrm != None:
            try:
                DBGPRINT('Input stream abort()... ', end='')
                self.istrm.abort()
                DBGPRINT('close()... ', end='')
                self.istrm.close()
                DBGPRINT('Success.')
            except sd.PortAudioError as e:
                print(f'Exception: {e}.')
            self.istrm = None

    def sd_start_streams(self):
        # Prepare SoundDevice streams and start streaming

        # Get the current config from comboboxes:
        api = self.cbx['api'].currentText()
        blocksize = self.cbx['blocksize'].currentText()
        latency = self.cbx['latency'].currentText()
        in_idx = self.cbx['indev'].currentData()
        out_idx = self.cbx['outdev'].currentData()
        fs = self.cbx['fs'].currentText()

        DBGPRINT(f'api={api}')
        DBGPRINT(f'indev={in_idx}')
        DBGPRINT(f'outdev={out_idx}')
        DBGPRINT(f'fs={fs}')
        DBGPRINT(f'blocksize={blocksize}')
        DBGPRINT(f'latency={latency}')

        # Sanity check values
        if fs == '' or in_idx == None or out_idx == None:
            DBGPRINT('Bad config, not starting')
            return
        
        blocksize = int(blocksize)
        fs = int(fs)

        # Compare to current config if active
        settings_match = (
            self.istrm != None and 
            self.ostrm != None and
            blocksize == self.istrm.blocksize == self.ostrm.blocksize and
            fs == self.istrm.samplerate == self.ostrm.samplerate and
            latency == self._last_latency and
            in_idx == self.istrm.device and
            out_idx == self.ostrm.device
        )

        # Have to save latency state, since it's only stored in stream as time
        self._last_latency = latency
        
        if settings_match:
            return
        
        # Stop both streams
        self.sd_stop_streams()

        # Configure new streams
        try:
            self.istrm = sd.InputStream(
                device = in_idx, 
                callback = self.sd_in_callback, 
                samplerate = fs,
                blocksize = blocksize,
                latency = latency,
            )
        except sd.PortAudioError as e:
            print(f'InputStream exception: {e}')
            self.istrm = None
            return

        try:
            self.ostrm = sd.OutputStream(
                device = out_idx,
                callback = self.sd_out_callback,
                samplerate = fs,
                blocksize = blocksize,
                latency = latency,
            )
        except sd.PortAudioError as e:
            print(f'OutputStream exception: {e}')
            self.ostrm = None
            return

        #DBGPRINT('STREAM CONFIG OK')

        self.params['api'] =              api
        self.params['samplerate'] =       fs
        self.params['blocksize'] =        blocksize
        self.params['in_idx'] =           self.istrm.device
        self.params['in_name'] =          sd.query_devices(self.istrm.device)['name']
        self.params['in_ch'] =            self.istrm.channels
        self.params['in_lat_ms'] =        self.istrm.latency * 1000
        self.params['in_lat_samples'] =   self.istrm.latency * fs
        self.params['in_lat_blocks'] =    self.istrm.latency * fs / blocksize
        self.params['out_idx'] =          self.ostrm.device
        self.params['out_name'] =         sd.query_devices(self.ostrm.device)['name']
        self.params['out_ch'] =           self.ostrm.channels
        self.params['out_lat_ms'] =       self.ostrm.latency * 1000
        self.params['out_lat_samples'] =  self.ostrm.latency * fs
        self.params['out_lat_blocks'] =   self.ostrm.latency * fs / blocksize
        
        if True:
            p = self.params
            #self.root.log(None, set_defaults=True, foreground='green')
            DBGPRINT(f"Stream params:")
            DBGPRINT(f"  api        : {p['api']}")
            DBGPRINT(f"  samplerate : {p['samplerate']}")
            DBGPRINT(f"  blocksize  : {p['blocksize']}")
            DBGPRINT(f"Input device:")
            DBGPRINT(f"  index      : {p['in_idx']}")
            DBGPRINT(f"  name       : {p['in_name']}")
            DBGPRINT(f"  channels   : {p['in_ch']}")
            DBGPRINT(f"  latency    : {p['in_lat_ms']:.3f}ms, {p['in_lat_blocks']} blocks, {p['in_lat_samples']} samples")
            DBGPRINT(f"Output device:")
            DBGPRINT(f"  index      : {p['out_idx']}")
            DBGPRINT(f"  name       : {p['out_name']}")
            DBGPRINT(f"  channels   : {p['out_ch']}")
            DBGPRINT(f"  latency    : {p['out_lat_ms']:.3f}ms, {p['out_lat_blocks']} blocks, {p['out_lat_samples']} samples")
            #self.root.log(None, set_defaults=True, foreground='black')

        # Tell everyone who cares about new stream parameters
        for func in self.chains['config']:
            try:
                func(self.params)
            except AttributeError as e:
                print(f'stream_start(): Exception: {func}  : {e}')
                pass

        # Manage channels strips
        self.config_strips()

        # Empty & and pre-load input queue
        while not self.in_queue.empty():
            try:
                self.in_queue.get_nowait()
            except queue.Empty:
                break
        while self.in_queue.qsize() < self.in_queue_preload:
            self.in_queue.put(np.zeros((self.params['blocksize'], self.params['in_ch'])))

        # Create an array of all input and output channels
        in_ch = self.params['in_ch']
        out_ch = self.params['out_ch']
        self.monbuf = np.zeros((self.params['blocksize'], in_ch + out_ch))
        self.monbuf_in_view  = self.monbuf[:,      :in_ch ] # In channels first,
        self.monbuf_out_view = self.monbuf[:, in_ch:      ] # then outputs

        # Start streams
        try:
            self.istrm.start()
            DBGPRINT(f"Input stream started")
        except sd.PortAudioError as e:
            self.istrm.close()
            self.istrm = None
            print(f'InputStream.start exception: {e}')
            return
            
        try:
            self.ostrm.start()
            DBGPRINT(f"Output stream started")
        except sd.PortAudioError as e:
            self.ostrm.close()
            self.ostrm = None
            print(f'OutputStream.start exception: {e}')
            self.stop_streams()
            return

        self.params['active'] = True

    def config_strips(self):

        # Add any input strips?
        for ch in range(self.params['in_ch']):
            if ch not in self.in_strips:
                self.in_strips[ch] = ChannelStrip(ch=ch, io_type='in', settings=self.settings)
                self.lo['ins'].addWidget(self.in_strips[ch], ch+1, 0)
                #self.in_strips[ch].setFixedHeight(60) 
                
        # Add any output strips?
        for ch in range(self.params['out_ch']):
            if ch not in self.out_strips:
                self.out_strips[ch] = ChannelStrip(ch=ch, io_type='out', settings=self.settings)
                self.lo['outs'].addWidget(self.out_strips[ch], ch+1, 0)

        # Set input strip visibility
        for ch in range(max(self.params['in_ch'], len(self.in_strips))):
            if ch < self.params['in_ch']:
                self.in_strips[ch].show()
            else:
                self.in_strips[ch].hide()

        # Set output strip visibility
        for ch in range(max(self.params['out_ch'], len(self.out_strips))):
            if ch < self.params['out_ch']:
                self.out_strips[ch].show()
            else:
                self.out_strips[ch].hide()    
        
    # --- RT-context SoundDevice callbacks
    
    def sd_in_callback(self, indata, frames, time, status):
        # Sounddevice callback for input data
        
        if status:
            print(f'Input callback status: {status}')

        # Pipe through input strips
        for i in range(indata.shape[1]):
            self.in_strips[i].pa_callback(indata[:, i])

        # Pipe through input chain
        with self.lock:
            chain = self.chains['input']
            for func in chain:
                func(indata)

        # Copy into monitor buf:
        self.monbuf_in_view[:] = indata   # Copy indata into the first columns

        # Enque data for the stream (i/o) callback chain
        try:
            self.in_queue.put_nowait(indata.copy())
        except queue.Full:
            pass
    
    def sd_out_callback(self, outdata, frames, time, status):
        # Sounddevice callback for output data
        if status:
            print(f'Output callback status: {status}')

        # Start with block of silence
        outdata.fill(0.0)

        # Get input block from queue
        try:
            indata = self.in_queue.get_nowait()
        except queue.Empty:
            indata = np.zeros((self.params['blocksize'], self.params['in_ch']))
            
        # Pipe through stream chain
        with self.lock:
            chain = self.chains['stream']
            for func in chain:
                func(outdata, indata)

        # Pipe through output chain
        with self.lock:
            chain = self.chains['output']
            for func in chain:
                func(outdata)

        # Pipe through output strips
        for i in range(outdata.shape[1]):
            self.out_strips[i].pa_callback(outdata[:, i])

        # Copy into monitor buf:
        self.monbuf_out_view[:] = outdata  # Copy outdata into the remaining 4 columns

        # Send all channels to monitor chain
        with self.lock:
            chain = self.chains['monitor']
            for func in chain:
                func(self.monbuf)

    #--- Public methods

    def enable_streams(self, enable=True):
        self.enable = enable
        if self.enable:
            self.sd_start_streams()
        else: 
            self.sd_stop_streams()
    
    def add_callback(self, chain, func, pos=0):
                if chain not in self.chains.keys():
                        raise ValueError
            
                with self.lock:
                        if pos == 'end':
                                self.chains[chain].append(func)
                        else:
                                self.chains[chain].insert(pos, func)
                                
                        # Call the function now if config chain and we are streaming
                        if chain == 'config' and self['active'] == True:
                                try:
                                    func(self.params)
                                except AttributeError as e:
                                    print(f'add_callback(): Exception: {func}  : {e}')
                                pass

    def remove_callback(self, chain, func):
        if chain not in self.chains.keys():
            raise ValueError
            
        with self.lock:
            try:
                self.chains[chain].remove(func)
                DBGPRINT(f'remove_callback: {func}')      
            except ValueError as e:
                print(f'remove_callback: Exception: {e}')      
                raise ValueError

    def get_stream_params(self):
        return self.params



if __name__ == '__main__':
    """
        Test function. Should just open the window and start streaming
    """
    from PyQt6.QtWidgets import QApplication, QMainWindow
    import re
    import sys
    
    class sdc_test_win(QMainWindow):
        def __init__(self):
            super().__init__()
            
            self.setWindowTitle("SDC Test")
            self.sdc = SoundDevCtl(parent=self)
            self.sdc.enable_streams() # Call this after everything initializes 
            self.setCentralWidget(self.sdc)
            
    app = QApplication([])
    app.setStyle('Fusion')   
    win = sdc_test_win()
    win.show()
    app.exec()
    