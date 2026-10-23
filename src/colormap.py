import sys
import numpy as np
import json
from PyQt6.QtWidgets import (
    QWidget, QMainWindow, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QAbstractSpinBox, QColorDialog
)
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtCore import Qt, QTimer

MAX_POINTS=8

class ColorStrip(QFrame):
    def __init__(self, **kwargs):
        self.idx                = kwargs.pop('idx', 0)
        self.color              = kwargs.pop('color', QColor(255, 255, 255))
        point                   = kwargs.pop('point', 0)
        self.callback           = kwargs.pop('callback', None)
        self.ignore_callback    = kwargs.pop('callback', False)
        slider_enabled          = kwargs.pop('slider_enabled', True)
        
        super().__init__()

        # Slider
        self.sli = QSlider(Qt.Orientation.Horizontal, self)
        self.sli.setMinimum(0)
        self.sli.setMaximum(255)
        self.sli.setValue(point)
        self.sli.setEnabled(slider_enabled)
        self.sli.valueChanged.connect(self.on_slider)

        # Label
        self.lab = QLabel()
        self.lab.setFixedWidth(150)
        self.lab.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        # R/G/B/H/S/V SpinBoxes
        self.slabs = {}
        self.spins = {}
        for s in ['R', 'G', 'B', 'H', 'S', 'V']:
            self.slabs[s] = QLabel(s)
            self.slabs[s].setFixedWidth(10)
            self.slabs[s].setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        
            self.spins[s] = QSpinBox()
            self.spins[s].setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.spins[s].setRange(0, 255)
            self.spins[s].valueChanged.connect(lambda *args, c=s : self.on_spinbox(c, *args))
            self.spins[s].blockSignals(False)

        # Hue is a little different
        self.spins['H'].setRange(0, 359)
        self.spins['H'].setWrapping(True)

        # Button
        self.btn = QPushButton('Pick')
        self.btn.clicked.connect(self.on_button)

        # Layout
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(2)
        self.layout.addWidget(self.lab)
        self.layout.addWidget(self.sli, 1)
        for s in ['R', 'G', 'B']:
            self.layout.addWidget(self.slabs[s])
            self.layout.addWidget(self.spins[s])

        self.layout.addWidget(self.btn)

        for s in ['H', 'S', 'V']:
            self.layout.addWidget(self.slabs[s])
            self.layout.addWidget(self.spins[s])


        self.no_callback = False

    def on_spinbox(self, c, value):
        h, s, v, a = self.color.getHsv()
        match c:
            case 'R':
                self.color.setRed(value)
            case 'G':
                self.color.setGreen(value)
            case 'B':
                self.color.setBlue(value)
            case 'H':
                self.color.setHsv(value, s, v, a)
            case 'S':
                self.color.setHsv(h, value, v, a)
            case 'V':
                self.color.setHsv(h, s, value, a)
        self.update_all()
        
    def on_button(self, *args):
        color = QColorDialog.getColor(self.color, None, f'color for point {self.idx}')
        if color.isValid():
            self.color = color
            self.update_all()
                
    def on_slider(self, value):
        self.update_all()
                
    def update_all(self):
        # Update color label
        fg = QColor(
            255 - self.color.red(),
            255 - self.color.green(),
            255 - self.color.blue(),
            self.color.alpha()
        )         
        self.lab.setStyleSheet(f""" 
            QLabel {{
                background-color : {self.color.name()};
                color : {fg.name()}; 
            }}
        """)
        self.lab.setText(f'{self.sli.value()} = {self.color.name()}')

        # Spinboxes
        for s in self.spins.values():
            s.blockSignals(True)

        self.spins['R'].setValue(self.color.red())
        self.spins['G'].setValue(self.color.green())
        self.spins['B'].setValue(self.color.blue())
        self.spins['H'].setValue(self.color.hue())
        self.spins['S'].setValue(self.color.saturation())
        self.spins['V'].setValue(self.color.value())
        
        for s in self.spins.values():
            s.blockSignals(False)
        
        # Update parent callback
        if hasattr(self, 'callback') and self.callback:
            if  self.ignore_callback == False and self.no_callback == False:
                self.callback(self.idx, self.sli.value(), self.color)

    def configure(self, cnf=None, **kwargs):
        for key, value in kwargs.items():
            match key:
                case 'idx':
                    # Set the id for the callback
                    self.idx = value
                case 'color':
                    # Update everything from a QColor
                    self.color = value
                    self.no_callback = True
                    self.update_all()
                    self.no_callback = False
                case 'point':
                    # Set slider position
                    self.no_callback = True
                    self.sli.setValue(value)
                    self.no_callback = False
                case 'callback':
                    # Set the callback for when slider or color is changed
                    self.callback = value
                case 'ignore_callback':
                    # Temporarily enable/disable calling the above callback
                    self.ignore_callback = value
                case 'slider_enabled':
                    # Enable/disable the slider (for fixed min and max)
                    self.sli.setEnabled(value)
                    
    config = configure
        
    def cget(self, key):
        match key:
            case 'idx':
                return self.idx
            case 'color':
                return self.color
            case 'point':
                return self.sli.value()
            case 'callback':
                return self.callback
            case 'ignore_callback':
                return self.ignore_callback
            case 'slider_enabled':
                return self.sli.isEnabled()
                
        raise ValueError

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})

    
class ColorPickerApp(QMainWindow):

    def __init__(self, **kwargs):
        super().__init__()
        # Parent waterfall app to send colormap to
        self.sa = kwargs.pop('sa', None)
        
        self.setWindowTitle("Colormap Builder")

        # Color swatch
        self.colormap_label = QLabel()

        # Number of points label & slider
        self.num_pt_label = QLabel('Points')
        self.num_pt_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.num_points_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.num_points_slider.setMinimum(2)
        self.num_points_slider.setMaximum(MAX_POINTS)
        self.num_points_slider.setValue(2)
        self.num_points_slider.valueChanged.connect(self.on_num_points_slider)

        # Point sliders / labels/ buttons
        self.b_frame = QFrame()
        self.b_layout = QGridLayout(self.b_frame)
        
        self.strips = {}
        for i in range(MAX_POINTS):
            self.strips[i] = ColorStrip(idx=i, callback=self.strip_callback)
            self.b_layout.addWidget(self.strips[i], i, 0)
            if i >= self.num_points_slider.value():
                self.strips[i].hide()
        
        # Main layout
        self.setStatusBar(None)
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(5, 5, 5, 5)
        self.layout.setSpacing(0)
        self.layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self.layout.addWidget(self.num_pt_label)
        self.layout.addWidget(self.num_points_slider)
        self.layout.addWidget(self.colormap_label)
        self.layout.addWidget(self.b_frame)
        #self.layout.addStretch()
        
        self.allow_close = False
        
    def closeEvent(self, event: QtGui.QCloseEvent):
        # Block the event unless it's from calling force_close()
        if self.allow_close == False:
            self.hide()
            event.ignore()
        else:
            event.accept()
            
    def force_close(self):
        self.allow_close = True
        self.close()
            
    def on_num_points_slider(self, value):
        self.num_pt_label.setText(f"Points ({value})")
        
        for i in range(MAX_POINTS):
            # Show/hide this point's widgets
            if i < value:
                self.strips[i].show()
            else:
                self.strips[i].hide()
                
        # Space initial slider points equally
        for i in range(value):
            pos = int(255 * i / (value-1))
            if pos == 0 or pos == 255:
                self.strips[i]['slider_enabled'] = False
            else:
                self.strips[i]['slider_enabled'] = True
            self.strips[i]['point'] = pos

        QTimer.singleShot(0, self.force_window_shrink)
         
    def force_window_shrink(self):
        # Explicitly update the central layout geometry cache
        self.centralWidget().layout().activate()
        
        # Pull the QMainWindow down to the layout's new minimum size hint
        self.resize(self.minimumSizeHint())
        self.adjustSize()
       
    def strip_callback(self, idx, point, color):
        self.make_cmap_array()
                
    def make_cmap_array(self):
        x = [ self.strips[i]['point']           for i in range(self.num_points_slider.value()) ]
        r = [ self.strips[i]['color'].red()     for i in range(self.num_points_slider.value()) ]
        g = [ self.strips[i]['color'].green()   for i in range(self.num_points_slider.value()) ]
        b = [ self.strips[i]['color'].blue()    for i in range(self.num_points_slider.value()) ]
        
        cmap_array = [x, r, g, b]

        # Update color swatch
        self.show_strip(cmap_array)

        # Update the plot window with the new colomap
        if hasattr(self, 'sa') and self.sa:
            self.sa['cmap_array'] = cmap_array

    def show_strip(self, cmap_array):
        #print(cmap_array)
        
        # Draw the color swatch
        x_points = np.array(cmap_array[0])
        r_y = np.array(cmap_array[1])
        g_y = np.array(cmap_array[2])
        b_y = np.array(cmap_array[3])
        
        x_all = np.arange(256)
        
        r_x = g_x = b_x = x_points 

        # 2. Interpolate each channel independently across the full 256 array
        r_channel = np.interp(x_all, r_x, r_y)
        g_channel = np.interp(x_all, g_x, g_y)
        b_channel = np.interp(x_all, b_x, b_y)

        # 3. Stack channels along the last axis to get a (256, 3) color gradient
        self.cmap_2d_rgb = np.stack([r_channel, g_channel, b_channel], axis=-1).astype(np.uint8)
        
        # 4. Use a 1-pixel high image instead of tiling it vertically.
        # This keeps the image lightweight so Qt can rescale it instantly.
        image_data = self.cmap_2d_rgb.reshape(1, 256, 3)

        # 5. Convert NumPy array to QImage and make a deep copy
        q_img = QImage(
            image_data.data, 
            256, 
            1, 
            3 * 256, 
            QImage.Format.Format_RGB888
        ).copy() 

        # 6. Configure the label for fluid stretching
        pixmap = QPixmap.fromImage(q_img)
        
        # Crucial: Allow the Pixmap to stretch to any size the label takes
        self.colormap_label.setScaledContents(True) 
        self.colormap_label.setPixmap(pixmap)
        
        # Lock down only the height, letting the layout expand the width infinitely
        self.colormap_label.setFixedHeight(64) 

    # --- Public
    
    def load_cmap(self, cmap_array):
        x_points = np.array(cmap_array[0])
        r_y = np.array(cmap_array[1])
        g_y = np.array(cmap_array[2])
        b_y = np.array(cmap_array[3])
        num_points = len(x_points)
        
        # Set number of points slider
        self.num_points_slider.blockSignals(True)
        self.on_num_points_slider(num_points)
        self.num_points_slider.setValue(num_points)
        self.num_points_slider.blockSignals(False)
        
        # Set each point slider position and label
        for i in range(num_points):
            self.strips[i]['point'] = x_points[i]
            self.strips[i]['color'] = QColor(r_y[i], g_y[i], b_y[i])
            
        # Draw the color swatch
        self.show_strip(cmap_array)



    
if __name__ == "__main__":
    from PyQt6.QtWidgets import (QApplication)
    app = QApplication(sys.argv)
    window = ColorPickerApp()
    window.load_cmap( [ [0, 127, 255], [0,0,255], [0,255,0], [255,0,0] ] )
    window.allow_close = True   # So we can kill it
    window.show()
    sys.exit(app.exec())
