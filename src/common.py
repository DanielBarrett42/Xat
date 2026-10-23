"""
    Misc common utility functions
    dbarrett@tenxp.com
    2026-09-06
    Module requirements:
"""
import re
import math
import threading  # lock
#from datetime import datetime
import numpy as np



"""
    ftos
    Return a floating point number as a string, with suffix.
    12345 -> '12.345k'
    Optional args:
        sig_digits
        before
        after
        no_blank_suffix
"""
def ftos(f, **kwargs):
    sig_digits = 5
    before = ''
    between = ''
    after = ''
    no_blank_suffix = True
    
    for key, value in kwargs.items():
        if key == 'sig_digits':
            sig_digits = value
        if key == 'before':
            before = value
        if key == 'between':
            between = value
        if key == 'after':
            after = value
        if key == 'no_blank_suffix':
            no_blank_suffix = value
            
    # Find suffix from log10 of number
    def log_to_sfx(l):
        if   l < -15: return 'a'
        if   l < -12: return 'f'
        elif l < -9:  return 'p'
        elif l < -6:  return 'n'
        elif l < -3:  return 'u'
        elif l < 0:   return 'm'
        elif l < 3:   return ''
        elif l < 6:   return 'k'
        elif l < 9:   return 'M'
        elif l < 12:  return 'G'
        elif l < 15:  return 'T'
        elif l < 18:  return 'P'
        elif l < 21:  return 'X'
        return suf

    if f == 0:
        l = 0
        m = 0
    else:
        l = math.floor(np.log10(f))
        m = f / 10**int(3 * np.floor(l / 3))
    
    
    while True:
        if m > 100:   p = sig_digits-3
        elif m > 10:  p = sig_digits-2
        else:         p = sig_digits-1
        # Make sure number didn't round up to 1000
        s = f'{m:.{p}f}'
        if float(s) < 1000:
            break
        m /= 1000
        l += 3

    sfx = log_to_sfx(l)
    if sfx == '' and no_blank_suffix == True:
        sfx = ' '

    res = f'{before}{m:>{sig_digits+1}.{p}f}{between}{sfx}{after}'
    #print(f'res=[{res}]    {no_blank_suffix}')
    return res

"""
    stof
"""
def stof(s):
    # Parse a float from string. Can have mutiplier suffix char: k
    # Try decimal number with optional k suffix
    m = re.match(r"^([0-9.]+)([afpnumkMGTPX])?(.*)$", s)
    if m == None or m.groups()[0] == '':
        raise ValueError(f'Unable to parse string "{s}"')
    
    mant = float(m.groups()[0])
    x = 0
    if m.groups()[1]:
        match m.groups()[1]:
            case 'a':   x = -18
            case 'f':   x = -15
            case 'p':   x = -12
            case 'n':   x = -9
            case 'u':   x = -6
            case 'm':   x = -3
            case 'k':   x = 3
            case 'M':   x = 6
            case 'G':   x = 9
            case 'T':   x = 12
            case 'P':   x = 15
            case 'X':   x = 18

    return mant * 10 ** x


# ------------------------------------------------------------------------------

class RingBuffer:
    """
        Ring buffer implementaion:
            Write:
                - May be any size up to capacity.
                - Always succeed unless they exceed capacity
            get_nowait:
                - Return
        Requires: threading
            
    """
    def __init__(self, capacity, dtype=np.float64):
        """Initialize the ring buffer with a fixed capacity and thread lock."""
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=dtype)
        self.write_idx = 0
        self.read_idx = 0
        self.size = 0
        self.lock = threading.Lock()  # Protects buffer state across threads

    def write(self, block):
        """Thread-safe write operation for any block size."""
        length = len(block)
        if length > self.capacity:
            raise ValueError("Block size exceeds ring buffer capacity.")
                
        with self.lock:
            # # Check if writing this data would overwrite unread data
            # if self.size + length > self.capacity:
                # raise OverflowError("Buffer overflow: writing faster than reading.")

            # Handle wrap-around
            end_idx = self.write_idx + length
            if end_idx <= self.capacity:
                self.data[self.write_idx:end_idx] = block
            else:
                part1_len = self.capacity - self.write_idx
                self.data[self.write_idx:] = block[:part1_len]
                self.data[:length - part1_len] = block[part1_len:]
                    
            # Update internal tracking
            self.size += length
            self.write_idx = end_idx % self.capacity

    def read(self, block_size, overlap=0):
        """Thread-safe read operation with optional overlap or skip."""
        overlap = int(overlap)
        with self.lock:
            if block_size > self.size:
                raise ValueError("Not enough data in the buffer to satisfy read block size.")
                #raise queue.Empty("Not enough data in buffer to satisfy read block size.")

            # Handle negative overlap (skipping data) early if necessary
            # Determine start position considering overlap
            start_idx = self.read_idx - overlap
            if start_idx < 0:
                start_idx += self.capacity

            end_idx = start_idx + block_size

            # Extract data handling wrap-around
            if end_idx <= self.capacity:
                block = self.data[start_idx:end_idx].copy()
            else:
                part1_len = self.capacity - start_idx
                block = np.empty(block_size, dtype=self.data.dtype)
                block[:part1_len] = self.data[start_idx:]
                block[part1_len:] = self.data[:block_size - part1_len]

            # Step forward based only on unread data consumed
            step = block_size - overlap
            self.read_idx = (self.read_idx + step) % self.capacity
            self.size -= step

            return block

# ------------------------------------------------------------------------------
import numpy as np
from scipy.signal import butter, sosfilt_zi, sosfilt
from scipy.fft import rfft, rfftfreq
from scipy.signal import windows

class bpf_filter_bank():
    def __init__(self, *args, **kwargs):
        self.params = {
            # Common
            'dbmin'     : kwargs.pop('dbmin', -120),    # Set to -120 for pro-audio range
            'ftype'      : kwargs.pop('ftype', 'CQT'),    # CQT or FFT
            # FFT Params
            'fft_size'  : kwargs.pop('fft_size', 1024), # Note: this is 2x 'bands'
            'fft_win'   : kwargs.pop('fft_win', 'Boxcar'),
            # CQT params
            'fs'        : kwargs.pop('fs',    48000),   # Native Samplerate
            'bpo'       : kwargs.pop('bpo',   24),      # 24 Bands per octave
            'f0'        : kwargs.pop('f0',    5),       
            'fc'        : kwargs.pop('fc',    1000),    
            'f1'        : kwargs.pop('f1',    20000),   
            #'gain'      : kwargs.pop('gain',  1),           # UNUSED
            #'kavg'      : kwargs.pop('kavg',  1),          # UNUSED
        }
        # Read-only params (after initialization
        # 'q'           # Returns filter Q in CQT mode
        # 'bands'       # Returns list of bins/bands

        self.fft_windows = {
            # Name      : (function,          gain),
            'Boxcar'    : (windows.boxcar,    1.0),
            'Hamming'   : (windows.hamming,   1.8534),
            'Cosine'    : (windows.cosine,    1.5708),
            'Hann'      : (windows.hann,      2.0),
            'Blackman'  : (windows.blackman,  2.38),
        }

        self.lock = threading.Lock()  # Protects buffer state across threads
        
        self.filter_init()
        
    def cget(self, key):
        if key not in self.params.keys():
            raise ValueError

        return self.params[key]
            
    def configure(self, cnf=None, **kwargs):
        changed = False
        for key, value in kwargs.items():
            # Complain if we don't have that key
            if key not in self.params.keys():
                raise ValueError
            # Do nothing if value doesn't change
            if self.params[key] == value:
                continue
            self.params[key] = value
            # See if we need to initialize
            match key:
                case 'dbmin' | 'gain' | 'fft_win':
                    continue
            changed = True

        if changed:
            self.filter_init()
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})
        
    def filter_init(self):
        with self.lock:
        
            # FFT init:
            if self.params['ftype'] == 'FFT':
                self.params['bands'] = rfftfreq(self.params['fft_size'], 1 / self.params['fs'])[1:]  # Drop 0th bin
                return
                
            # CQT init:
            
            # 1. Generate clean logarithmic center frequencies
            octs_lo = np.log2(self.params['fc'] / self.params['f0'])
            bands_lo = int(np.ceil(octs_lo * self.params['bpo']))
            f_start = self.params['fc'] / 2 ** (bands_lo / self.params['bpo'])
            a_lo = np.logspace(np.log2(f_start), np.log2(self.params['fc']), bands_lo, base=2, endpoint=False)

            octs_hi = np.log2(self.params['f1'] / self.params['fc'])
            bands_hi = int(np.ceil(octs_hi * self.params['bpo'])) + 1
            f_end = self.params['fc'] * 2 ** (bands_hi / self.params['bpo'])
            a_hi = np.logspace(np.log2(f_start if bands_lo == 0 else self.params['fc']), np.log2(f_end), bands_hi+1, base=2)
            
            fa = np.concatenate((a_lo, a_hi))
            valid_bands = np.unique(fa[fa < (self.params['fs']/2)])

            # # Round band frequencies cleanly
            # with np.nditer(valid_bands, op_flags=['readwrite']) as it:
                # for f in it:
                    # expo = 10 ** np.floor(np.log10(f))
                    # mant = round(f / expo, 3)
                    # f[...] = mant * expo
            
            self.params['bands'] = np.unique(valid_bands)
            self.params['q'] = ((2 ** (1/self.params['bpo'])) ** 0.5) / (2 ** (1/self.params['bpo']) - 1)
            
            self.rta_sosx = []
            active_bands = []
            
            # 2. Build uniform 6th-order filters operating at the native sample rate
            for f in self.params['bands']:
                f_low = f * (2 ** (-1 / (2 * self.params['bpo'])))
                f_high = f * (2 ** (1 / (2 * self.params['bpo'])))
                
                if f_low <= 0.1 or f_high >= (self.params['fs'] / 2):
                    continue
                
                # N=3 designs a 6th-order Butterworth filter (3 cascaded SOS sections)
                sos = butter(N=3, Wn=[f_low, f_high], btype='bandpass', fs=self.params['fs'], output='sos')
                self.rta_sosx.append(sos)
                active_bands.append(f)
                
            self.params['bands'] = np.array(active_bands)
            self.params['nbands'] = len(self.params['bands'])
      
            # 3. Pristine standalone 2D array list state allocation
      
            self.rta_zix = np.zeros((self.params['nbands'], 3, 2))

            self.rta_db_out = np.empty(self.params['nbands'])

    def filter_block_return_db(self, buf):
        with self.lock:

            # Should check for buf width
            if self.params['ftype'] == 'FFT':
                blocksize = buf.size
                func, gain = self.fft_windows[self.params['fft_win']]
                win = gain * func(blocksize, sym=False)
                yf = rfft(buf * win)
                mag = np.abs(yf) * 2.0 / blocksize  # Normalize gain
                mag = mag[1:]   # Drop 0th (DC) bin
                mag = np.maximum(mag, 10**(self.params['dbmin']/20)) # Limit floor for log
                db = 20 * np.log10(mag) # Convert to db
                return db



            
            dbflr = 10 ** (self.params['dbmin'] / 20)
            
            # Apply the scalar adjustment uniformly outside the filter loop
            #gain_scalar = self.params['gain'] / self.params['q']
            #print(f'GS = {gain_scalar}   {self.params['gain']}   {self.params['q']}')
            gain_scalar = 1.414  #self.params['gain']

            for i in range(self.params['nbands']):
                # Overwrite the list pointer directly with SciPy's native new trailing state
                b, self.rta_zix[i] = sosfilt(self.rta_sosx[i], buf, zi=self.rta_zix[i])
                
                if True:
                    # Compute true Root-Mean-Square (RMS) power integration over the block
                    rms = np.sqrt(np.mean(b ** 2)) * gain_scalar
                    self.rta_db_out[i] = 20 * np.log10(np.maximum(rms, dbflr))
                else:
                    # Compute peak
                    pk = np.max(np.abs(b))
                    self.rta_db_out[i] = 20 * np.log10(np.maximum(pk, dbflr))
                    
                    
            return self.rta_db_out


