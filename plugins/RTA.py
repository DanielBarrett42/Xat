import os
import re
import json
import numpy as np
from time import time

from PyQt6.QtGui import QPainter, QImage, QColor, QFont
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QFrame, 
    QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy,
    QDial, QLabel, QComboBox, QPushButton, QInputDialog, QMessageBox
)

# # For FFT window functions and band spacing calcs:
# from scipy.fft import rfft
# from scipy.signal import windows

# For Bargraph display
import pyqtgraph as pg

# For 3D display
from vispy import app, gloo
from vispy.util.transforms import perspective, translate, rotate

# Local imports
from src.config import DBMIN, DBMAX
from src.plugin_interface import BasePlugin
from src.common import RingBuffer, ftos, bpf_filter_bank
from src.dials import CustomColorDial
from src.ch_ena_block import ChEnaBlockIO
from src.colormap import ColorPickerApp

# ------------------------------------------------------------------------------
# 3D Waterfall
# ------------------------------------------------------------------------------

class Waterfall3D(app.Canvas):
    DEFAULTS = {
        'settings'      : None,
        'bands'         : np.zeros(1),
        'floor'         : DBMIN,
        'ceiling'          : DBMAX,
        'cursor'        : 'Global peak',    # 'Current peak', 'Global peak', 'Mouse'
        'cursor_gate'   : -50,
        'line_period'   : 0.01,
        'width'         : 256,              # Number of frequency bands
        'depth'         : 128,               # Number of time slices

        # Specific for app.Canvas
        'size'          : (1024, 768),      # Default size if not specified
        
    }
    
    def cget(self, key):
        if key in self.params.keys():
            return self.params[key]
        else:
            raise ValueError
            
    def configure(self, cnf=None, **kwargs):
        for key, value in kwargs.items():
            self.params[key] = value
            match key:
                case 'width' | 'depth':
                    self.matrix_data = np.zeros((self['depth'], self['width']), dtype=np.float32)
                    self.texture.set_data(self.matrix_data)
                    self._setup_geometry()
                    self.update()

                case 'cmap':
                    # Binary colormap data
                    self.cmap_texture.set_data(value)
                    self.update()

                case 'cmap_array':
                    # Put this in vispy colormap format
                    x_points = np.array(value[0])
                    r_y = np.array(value[1])
                    g_y = np.array(value[2])
                    b_y = np.array(value[3])
                    x_all = np.arange(256)
                    r_channel = np.interp(x_all, x_points, r_y)
                    g_channel = np.interp(x_all, x_points, g_y)
                    b_channel = np.interp(x_all, x_points, b_y)
                    alpha_channel = np.ones_like(x_all) * 255.0
                    gradient_2d = np.stack([r_channel, g_channel, b_channel, alpha_channel], axis=-1).astype(np.uint8)
                    gradient_float_2d = (gradient_2d / 255.0).astype(np.float32)
                    self.gradient_float_3d = gradient_float_2d[np.newaxis, :, :]    # (Shape: 1, 256, 4)
                    self['cmap'] = self.gradient_float_3d
            
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __init__(self, *args, **kwargs):
        self.params = self.DEFAULTS.copy()
        self.params.update(kwargs)

        # Create settings instance
        new_settings = QSettings()
        if self['settings']:
            new_settings.beginGroup(self['settings'].group())
        new_settings.beginGroup(self.__class__.__name__)
        self['settings'] = new_settings

        self.allow_close = True

        # Pass only specific args to init
        a = {
            'keys'          : 'interactive',
            'title'         : "3D Waterfall Spectrogram",
            'show'          : False,            # Don't override this
            'size'          : self['size'],
        }
        super().__init__(**a)

        # Hook mouse enter/leave events
        self.native.enterEvent = self.on_mouse_enter
        self.native.leaveEvent = self.on_mouse_leave

        # hook 'X' button
        self.native.closeEvent = self.native_close_event
           
        # Misc vars
        self.cursor_text_readout = ""
        self.mouse_pressed = False
        self.mouse_x = 0
        self.mouse_y = 0
        self.ctrl_pressed = False
        self.mouse_in_window = False
        self.show_cursor = False
        self.height_scale = -0.4
        self.draw_axis = False

        # Shader programs
        self.vertex_shader = """
          uniform mat4 u_model;
          uniform mat4 u_view;
          uniform mat4 u_projection;

          attribute vec2 a_grid_pos;    // (X, Y) normalized grid coordinates [0.0, 1.0]
          uniform sampler2D u_amplitude_tex; // Your 2D matrix map
          uniform float u_height_scale; // Scales visual height of the decibel peaks

          varying float v_amplitude;
          varying vec2 v_uv;

          void main() {
            v_uv = a_grid_pos;
            
            // 1. Look up amplitude from your rolling 2D matrix texture
            v_amplitude = texture2D(u_amplitude_tex, v_uv).r;
            
            // 2. DISPLACE UPWARD: Keep the Z coordinate positive or negative 
            vec3 pos = vec3(a_grid_pos.x, a_grid_pos.y, v_amplitude * u_height_scale);
            
            // Center the mesh layout around (0,0)
            pos.xy -= 0.5; 

            gl_Position = u_projection * u_view * u_model * vec4(pos, 1.0);
          }
        """

        self.fragment_shader = """
          varying float v_amplitude;
          varying vec2 v_uv;

          uniform sampler2D u_cmap_tex; // The 1D Look-Up Table loaded as a GPU texture

          void main() {
            // 1. Look up the RGBA color from our custom colormap using the amplitude [0.0, 1.0]
            vec3 color = texture2D(u_cmap_tex, vec2(v_amplitude, 0.5)).rgb;
            
            // 2. Add subtle structural matrix grid lines based on UV coordinate mapping
            float grid_line = step(0.98, fract(v_uv.x * 64.0)) * 0.15 + step(0.98, fract(v_uv.y * 64.0)) * 0.15;
            color += vec3(grid_line);

            gl_FragColor = vec4(color, 1.0);
          }
        """

        # === NEW CODE: Simplified Axis Line Shaders ===
        self.axis_vertex_shader = """
          uniform mat4 u_model;
          uniform mat4 u_view;
          uniform mat4 u_projection;
          attribute vec3 a_position;
          void main() {
            gl_Position = u_projection * u_view * u_model * vec4(a_position, 1.0);
          }
        """

        self.axis_fragment_shader = """
          uniform vec4 u_line_color;
          void main() {
            gl_FragColor = u_line_color;
          }
        """
        
        # This program draws both your boundary framework and the red crosshairs!
        self.axis_program = gloo.Program(self.axis_vertex_shader, self.axis_fragment_shader)
        self.axis_program['u_line_color'] = (0.6, 0.6, 0.6, 0.8) 

        # Dedicated setup for our cursor draw loop
        self.cursor_program = gloo.Program(self.axis_vertex_shader, self.axis_fragment_shader)
        self.cursor_program['u_line_color'] = (1.0, 0.2, 0.2, 1.0) # Vibrant Red Crosshair

        self.cursor_vertices = np.zeros((4, 3), dtype=np.float32)
        self.cursor_vertex_buffer = gloo.VertexBuffer(self.cursor_vertices)

        # Initial dummy 2D rolling matrix data (Values normalized 0.0 to 1.0)
        self.matrix_data = np.zeros((self['depth'], self['width']), dtype=np.float32)
        
        # Setup GLSL Program
        self.program = gloo.Program(self.vertex_shader, self.fragment_shader)
        
        # Build standard mesh topologies & geometry indexes once
        self._setup_geometry()
        
        # INITIALIZE THE GPU COLORED LOOK-UP TEXTURE
        self.cmap_texture = gloo.Texture2D(shape=(256, 1, 4), format='rgba', internalformat='rgba8')
        self.program['u_cmap_tex'] = self.cmap_texture
        
        # Bind Uniforms & GPU Texture Layouts
        self.texture = gloo.Texture2D(self.matrix_data, format='luminance', internalformat='r32f')
        self.program['u_amplitude_tex'] = self.texture

        self.program['u_height_scale'] = self.height_scale 
        
        # Load saved view settings
        self.view_angle_x   = float(self['settings'].value("view_angle_x", 120.0) )  # Pitch
        self.view_angle_y   = float(self['settings'].value("view_angle_y", 345.0) )  # Yaw
        self.view_dist      = float(self['settings'].value("view_dist", -1.5)     )
        
        self._update_matrices()
                
        gloo.set_state(clear_color='black', depth_test=True)

        # Use saved geometry if available
        saved_geometry = self['settings'].value("geometry", None)
        if saved_geometry is not None:
            self.native.restoreGeometry(saved_geometry)

        self.fullscreen = bool(self['settings'].value("fullscreen", 0))

        self.configure(**self.params)    # Trigger dictionary handlers

    def native_close_event(self, q_event):
        # Exit hook to trap 'X' button
        # Block the event and just hide the window unless force_close()
        if self.allow_close == False:
            q_event.ignore()
            self.hide()
            
    def on_close(self, event):
        geometry_data = self.native.saveGeometry()
        self['settings'].setValue("geometry", geometry_data)
        self['settings'].setValue("fullscreen", int(self.fullscreen))
        self['settings'].setValue("view_angle_x", self.view_angle_x)
        self['settings'].setValue("view_angle_y", self.view_angle_y)
        self['settings'].setValue("view_dist", self.view_dist)
        
    def _setup_geometry(self):
        
        """Creates the mesh grid points and index buffers instantly using vectorized NumPy operations."""
        x = np.linspace(0.0, 1.0, self['width'], dtype=np.float32)
        y = np.linspace(0.0, 1.0, self['depth'], dtype=np.float32)
        xv, yv = np.meshgrid(x, y)
        grid_points = np.stack([xv, yv], axis=-1).reshape(-1, 2)
        self.program['a_grid_pos'] = gloo.VertexBuffer(grid_points)
        
        r = np.arange(self['depth'] - 1, dtype=np.uint32)[:, np.newaxis]
        c = np.arange(self['width'] - 1, dtype=np.uint32)[np.newaxis, :]
        top_left = r * self['width'] + c

        top_right = top_left + 1
        bottom_left = top_left + self['width']
        bottom_right = bottom_left + 1

        indices = np.empty((self['depth'] - 1, self['width'] - 1, 6), dtype=np.uint32)
        indices[..., 0] = top_left
        indices[..., 1] = top_right
        indices[..., 2] = bottom_left
        indices[..., 3] = top_right
        indices[..., 4] = bottom_right
        indices[..., 5] = bottom_left

        self.index_buffer = gloo.IndexBuffer(indices.ravel())

        # Setup the axis lines
        h_scale = self.height_scale
        base_elements = [
            # Corner Pillars
            [-0.5, -0.5, 0.0], [-0.5, -0.5, h_scale],
            [ 0.5, -0.5, 0.0], [ 0.5, -0.5, h_scale],
            [-0.5,  0.5, 0.0], [-0.5,  0.5, h_scale],
            [ 0.5,  0.5, 0.0], [ 0.5,  0.5, h_scale],
            # Base Floor Ring
            [-0.5, -0.5, 0.0], [ 0.5, -0.5, 0.0],
            [ 0.5, -0.5, 0.0], [ 0.5,  0.5, 0.0],
            [ 0.5,  0.5, 0.0], [-0.5,  0.5, 0.0],
            [-0.5,  0.5, 0.0], [-0.5, -0.5, 0.0],
            # Ceiling Height Ring
            [-0.5, -0.5, h_scale], [ 0.5, -0.5, h_scale],
            [ 0.5, -0.5, h_scale], [ 0.5,  0.5, h_scale],
            [ 0.5,  0.5, h_scale], [-0.5,  0.5, h_scale],
            [-0.5,  0.5, h_scale], [-0.5, -0.5, h_scale],
        ]

        tick_len = 0.01  # 8% of full frame layout
        ticks = []
        
        for pct in [0.20, 0.40, 0.60, 0.80]:
            z_pos = h_scale * pct
            ticks.extend([[-0.5, -0.5, z_pos], [-0.5 + tick_len, -0.5, z_pos]])
            ticks.extend([[-0.5, -0.5, z_pos], [-0.5, -0.5 + tick_len, z_pos]])
            ticks.extend([[ 0.5, -0.5, z_pos], [ 0.5 - tick_len, -0.5, z_pos]])
            ticks.extend([[ 0.5, -0.5, z_pos], [ 0.5, -0.5 + tick_len, z_pos]])
            ticks.extend([[-0.5,  0.5, z_pos], [-0.5 + tick_len,  0.5, z_pos]])
            ticks.extend([[-0.5,  0.5, z_pos], [-0.5,  0.5 - tick_len, z_pos]])
            ticks.extend([[ 0.5,  0.5, z_pos], [ 0.5 - tick_len,  0.5, z_pos]])
            ticks.extend([[ 0.5,  0.5, z_pos], [ 0.5,  0.5 - tick_len, z_pos]])

        all_lines = base_elements + ticks

        # Combine lines together into a single master Vertex Buffer Array
        all_lines = base_elements + ticks
        self.axis_vertex_buffer = gloo.VertexBuffer(np.array(all_lines, dtype=np.float32))
        
    def _update_matrices(self):
        """Standard 3D Projection configuration helper safely optimized for multi-monitors."""
        w, h = self.physical_size
        if w == 0 or h == 0:
            return np.eye(4, dtype=np.float32)
            
        model = np.eye(4, dtype=np.float32)
        
        view = np.eye(4, dtype=np.float32)
        view = translate((0, 0, self.view_dist)) @ view
        
        view = rotate(self.view_angle_x, (1, 0, 0)) @ view
        view = rotate(self.view_angle_y, (0, 0, 1)) @ view
        
        aspect = float(w) / float(h)
        proj = perspective(45.0, aspect, 0.1, 10.0)
        
        self.program['u_model'] = model
        self.program['u_view'] = view
        self.program['u_projection'] = proj

        self.axis_program['u_model'] = model
        self.axis_program['u_view'] = view
        self.axis_program['u_projection'] = proj

        self.cursor_program['u_model'] = model
        self.cursor_program['u_view'] = view
        self.cursor_program['u_projection'] = proj
        
        # Explicitly cache stack and return to caller
        self._full_mvp = proj @ view @ model
        return self._full_mvp
        
    def on_resize(self, event):
        w, h = self.physical_size
        if w > 0 and h > 0:
            gloo.set_viewport(0, 0, w, h)
            self._update_matrices()

    def on_draw(self, event):
        gloo.clear(color=True, depth=True)
        self.program.draw('triangles', self.index_buffer)

        if self.draw_axis:
            # Draw structural reference boundaries
            gloo.set_line_width(1.0) 
            self.axis_program['a_position'] = self.axis_vertex_buffer
            self.axis_program.draw('lines')        

        # Render Cursor Overlay Over Mesh
        if self.show_cursor:
            gloo.set_line_width(1.0)  
            self.cursor_program['a_position'] = self.cursor_vertex_buffer
            self.cursor_program.draw('lines')
            
        # === NEW CODE: PyQt Floating HUD Text Overlay ===
        if self.show_cursor and self.cursor_text_readout:
            
            painter = QPainter(self.native)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # 1. Configuration Constants
            box_width = 320
            box_height = 35
            margin = 15
            
            # 2. Draw Semi-Transparent Dark Background Panel Block
            # Positions it in the Top-Left Corner gracefully
            rect_color = QColor(0, 0, 0, 160) # Dark Charcoal with 60% opacity
            painter.setBrush(rect_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(margin, margin, box_width, box_height)
            
            # 3. Print the Coordinate Labels 
            text_font = QFont("Consolas", 11) # Standard monospaced font
            text_font.setBold(True)
            painter.setFont(text_font)
            painter.setPen(QColor(255, 255, 255)) # Sharp Solid White Text
            
            # Offset text drawing alignment baseline inside container frame rectangle boundaries
            text_x_pos = margin + 12
            text_y_pos = margin + 22
            painter.drawText(text_x_pos, text_y_pos, self.cursor_text_readout)

            # Add view info
            txt = f"x={self.view_angle_x:.0f}  y={self.view_angle_y:.0f} d={self.view_dist:.2f}"
            painter.setPen(QColor(100,100,100))
            painter.drawText(text_x_pos, text_y_pos+16, txt)
            
            painter.end()

    # --- Key and mouse handlers
    
    def on_key_press(self, event):
        if event.key == 'Control':
            self.ctrl_pressed = True
        if event.key == 'f':
            self.fullscreen = not self.fullscreen
        if event.key == 'a':
            self.draw_axis = not self.draw_axis
            
    def on_key_release(self, event):
        # Unused
        if event.key == 'Control':
            self.ctrl_pressed = False

    def on_mouse_enter(self, event):
        self.mouse_in_window = True
    
    def on_mouse_leave(self, event):
        self.mouse_in_window = False
        self.show_cursor = False
        self.update()
        
    def on_mouse_press(self, event):
        if event.button == 1:  # Left click
            self.mouse_pressed = True

    def on_mouse_release(self, event):
        if event.button == 1:
            self.mouse_pressed = False

    def on_mouse_move(self, event):
        self.mouse_x = event.pos[0]
        self.mouse_y = event.pos[1]
        
        if self.mouse_pressed and event.last_event:
            # Camera view changes when dragging in window
            dx = event.pos[0] - event.last_event.pos[0]
            dy = event.pos[1] - event.last_event.pos[1]
            
            self.view_angle_x -= dy * 0.5
            self.view_angle_y += dx * 0.5
            
            self.view_angle_x = self.view_angle_x % 360.0
            self.view_angle_y = self.view_angle_y % 360.0
            
            self._update_matrices()
            self.show_cursor = False

        self.update()

    def on_mouse_wheel(self, event):
        self.view_dist += event.delta[1] * 0.1
        self.view_dist = min(-0.2, max(-5.0, self.view_dist))
        self._update_matrices()
        self.update()

    def draw_cursor(self, x, y):
        # x,y are coordinates in self.matrix_data
        w, h = self.size
        if w == 0 or h == 0:
            return
        if x < 0 or x >= self['width']:
            return
        if y < 0 or y >= self['depth']:
            return

        # Update display
        cfreq = self['bands'][x]
        ctime = self['line_period'] * y
        cdb = self.matrix_data[y, x] * (self['ceiling'] - self['floor']) + self['floor']
        self.cursor_text_readout = f"{ftos(cfreq)}Hz  |  Time: {ftos(ctime)}s  |  {cdb:.0f}dB"
        
        # 2. Convert raw pixels to simple horizontal and vertical percentage steps [0.0, 1.0]
        u_coord = x / (self['width'] - 1)
        v_coord = y / (self['depth'] - 1)
        amplitude = self.matrix_data[y, x]

        # Reconstruct surface coordinate positions centered around (0,0)
        x_mesh_pos = u_coord - 0.5
        y_mesh_pos = v_coord - 0.5
        z_height = (amplitude * self.height_scale) #+ (0.015 * np.sign(self.height_scale))

        # 3. Explicitly construct crosshair geometry vertex positions
        # Line 1: Frequency line spanning full mesh column history depth (Y axis stroke)
        zh = z_height   # Cross height
        line1_start = [x_mesh_pos, y_mesh_pos - 0.05, zh]
        line1_end   = [x_mesh_pos,  y_mesh_pos + 0.05, zh]
        
        # Line 2: Timeline line spanning full mesh row spectrum width (X axis stroke)
        line2_start = [x_mesh_pos - 0.05, y_mesh_pos, zh]
        line2_end   = [x_mesh_pos + 0.05, y_mesh_pos, zh]

        line3_start = [x_mesh_pos, y_mesh_pos, 0]
        line3_end   = [x_mesh_pos, y_mesh_pos, self.height_scale]

        # Combine line segments together into a unified array
        new_vertices = np.array([line1_start, line1_end, line2_start, line2_end, line3_start, line3_end], dtype=np.float32)

        # 4. FORCE RE-UPLOAD UP TO GPU CARDS MEMORY
        self.cursor_vertex_buffer.set_data(new_vertices)
        self.show_cursor = True
        self.update()

    # --- Public methods
    
    def force_close(self):
        # Call this instead of close() 
        self.allow_close = True
        self.close()

    def push_fft_frame(self, new_fft_slice):
        new_fft_slice = np.asarray(new_fft_slice, dtype=np.float32)

        if new_fft_slice.shape[0] != self['width']:
          self['width'] = new_fft_slice.shape[0]
          return

        self.matrix_data[1:, :] = self.matrix_data[:-1, :]
        self.matrix_data[0, :] = new_fft_slice
        
        self.texture.set_data(self.matrix_data)

        if self.mouse_in_window == False:
            self.show_cursor = False
            peak_all = None
            if self['cursor'] == 'Global peak':
                peak_all = np.unravel_index(np.argmax(self.matrix_data), self.matrix_data.shape)  #y,x
            elif self['cursor'] == 'Current peak':
                peak_all = (0, np.argmax(new_fft_slice))
            
            if peak_all != None:
                db = self.matrix_data[peak_all] * (self['ceiling'] - self['floor']) + self['floor']
                if True or db >= self['cursor_gate']:
                    self.draw_cursor(peak_all[1], peak_all[0])
        else:
            # Cursor tracks mouse position while in window
            w, h = self.size
            if w > 0 and h > 0:
                x = round(float(self.mouse_x) / float(w) * (self['width'] - 1))
                y = round(float(self.mouse_y) / float(h) * (self['depth'] - 1))
                self.draw_cursor(x, y)

        

        self.update()

    def isVisible(self):
        # Compatibility function to make this look like a QWidget
        return self.native.isVisible()

    def hide(self):
        # Compatibility function to make this look like a QWidget
        self.show(visible=False)

    def deleteLater(self):
        #self.native.deleteLater()
        pass

# ------------------------------------------------------------------------------
# 2D Waterfall
# ------------------------------------------------------------------------------

class Waterfall2D(QWidget):
    DEFAULTS = {
        'settings'      : None,
        'bands'         : np.zeros(1),
        'floor'         : DBMIN,
        'ceiling'          : DBMAX,
        'cursor'        : 'Global peak',    # 'Current peak', 'Global peak', 'Mouse'
        'cursor_gate'   : -50,
        'line_period'   : 0.01,
        'width'         : 256,              # Number of frequency bands
        'depth'         : 128,              # Number of time slices
        'size'          : (1024, 768),      # Default size if not specified
    }

    def cget(self, key):
        if key in self.params.keys():
            return self.params[key]
        else:
            raise ValueError
            
    def configure(self, cnf=None, **kwargs):
        for key, value in kwargs.items():
            self.params[key] = value
            match key:
                case 'width' | 'depth':
                    self.data_matrix = np.full((self['depth'], self['width']), DBMIN, dtype=np.float32)
                    self.rgb_matrix = np.zeros((self['depth'], self['width'], 3), dtype=np.uint8)
                    self.matrix_ptr = 0
                    self.update()
                        
                case 'cmap_array':
                    # Put this in RGBx256 cmap format
                    x_points = np.array(value[0])
                    x_all = np.arange(256)
                    r_channel = np.interp(x_all, x_points, np.array(value[1]))
                    g_channel = np.interp(x_all, x_points, np.array(value[2]))
                    b_channel = np.interp(x_all, x_points, np.array(value[3]))
                    self.lut = np.stack([r_channel, g_channel, b_channel], axis=-1).astype(np.uint8)

    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})
        
    def __init__(self, *args, **kwargs):
        self.params = self.DEFAULTS.copy()
        self.params.update(kwargs)

        # Initialize base class
        super().__init__()
        
        # Create settings instance
        new_settings = QSettings()
        if self['settings']:
            new_settings.beginGroup(self['settings'].group())
        new_settings.beginGroup(self.__class__.__name__)
        self['settings'] = new_settings

        self.allow_close = False

        self.setWindowTitle("2D Waterfall Spectrogram")
        
        saved_geometry = self['settings'].value("geometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        
        # Restore your exact original data allocation matrix structures
        self.data_matrix = np.full((self['depth'], self['width']), DBMIN, dtype=np.float32)
        self.rgb_matrix = np.zeros((self['depth'], self['width'], 3), dtype=np.uint8)
        self.matrix_ptr = 0

        self.configure(**self.params)   # Trigger all the dictionary handlers

    def moveEvent(self, event: QMoveEvent):
        """ Updates the tracking coordinates whenever the window is dragged """
        # We use frameGeometry to capture the true OS window edges correctly
        rect = self.frameGeometry()
        self.last_known_geo = (rect.x(), rect.y(), rect.width(), rect.height())
        super().moveEvent(event)

    def resizeEvent(self, event: QResizeEvent):
        """ Updates the tracking coordinates whenever the window is resized """
        rect = self.frameGeometry()
        self.last_known_geo = (rect.x(), rect.y(), rect.width(), rect.height())
        super().resizeEvent(event)        
        
    def closeEvent(self, event: QCloseEvent):
        # Block the event unless it's from calling force_close()

        self['settings'].setValue("geometry", self.saveGeometry())
        
        if callable(getattr(self, 'close_callback', None)):
            self.close_callback()

        if self.allow_close == False:
            event.ignore()
        else:
            event.accept()
            
    def force_close(self):
        self.allow_close = True
        self.close()
                    
    def push_fft_frame(self, new_fft_slice):
        if not hasattr(self, 'lut'):
            print('---- NO LUT')
            return
        """RESTORED TRACKING: Uses your exact circular row indexing logic."""
        try:
            new_fft_slice = np.asarray(new_fft_slice, dtype=np.float32)

            if new_fft_slice.shape[0] != self['width']:
                self['width'] = new_fft_slice.shape[0]
                return


            # Store raw float data row
            self.data_matrix[self.matrix_ptr, :] = new_fft_slice

            
            # Map incoming floats using parent's color lookup palette table
            # f = self.ctl.floor_db
            # c = self.ctl.ceiling_db
            #norm = np.clip((new_fft_slice - f) / (c - f) * 255, 0, 255).astype(np.uint8)
            norm = np.clip(new_fft_slice * 255, 0, 255).astype(np.uint8)
            
            # Populate the RGB color tracking slice matrix row
            self.rgb_matrix[self.matrix_ptr, :, :] = self.lut[norm]
            
            # Advance your circular timeline pointer loop index
            self.matrix_ptr = (self.matrix_ptr + 1) % self['depth']
            
            # Re-fire native paintEvent asynchronously
            self.update()
        except Exception as e:
            print(f'Exc: {e}')
            
    def paintEvent(self, event):
        """NATIVE PIXMAP BLIT: Performs direct hardware raster copies with 0% shader lag."""
        painter = QPainter(self)
        
        height = self.rgb_matrix.shape[0]
        width = self.rgb_matrix.shape[1]
        if height == 0 or width == 0:
            return

        # RESTORED SLICING MATH: Rebuilds continuous timeline rows directly on the CPU
        photo_matrix = np.vstack((
            self.rgb_matrix[self.matrix_ptr - 1 : : -1, :, :],
            self.rgb_matrix[ : self.matrix_ptr - 1 : -1, :, :]
        ))

        # Pack raw interleaved RGB bytes directly into a fast, native hardware QImage
        q_img = QImage(
            photo_matrix.tobytes(),
            width,
            height,
            width * 3,
            QImage.Format.Format_RGB888
        )

        # Blit the image directly onto the window canvas viewport, scaling smoothly to fit
        painter.drawImage(self.rect(), q_img)

    def keyPressEvent(self, event):
        # Toggle full screen with 'F' key
        if event.key() == Qt.Key.Key_F:
            self.showFullScreen()
        
        # Exit full screen with 'Escape' key
        elif event.key() == Qt.Key.Key_Escape:
            self.showNormal()
            
        else:
            super().keyPressEvent(event)
        
# ------------------------------------------------------------------------------
# Bargraph
# ------------------------------------------------------------------------------

class BargraphWindow(QMainWindow):
    DEFAULTS = {
        'settings'      : None,
        'bands'         : np.zeros(1),
        'floor'         : DBMIN,
        'ceiling'          : DBMAX,
        'width'         : 1,
        'dkrate'        : 40,   # dB/sec decay rate
        'cursor'        : 'Current peak',
    }

    def cget(self, key):
        if key in self.params.keys():
            return self.params[key]
        else:
            raise ValueError
            
    def configure(self, cnf=None, **kwargs):
        for key, value in kwargs.items():
            #print(f'********** configure {key} = {value}')
            self.params[key] = value
            match key:
                case 'width':
                
                    if len(self.params['bands']) != self.params['width']:
                        self.params['bands'] = np.zeros(self.params['width'])
                        
                    self.update_axis_geometry()
                    self.setWindowTitle(f"RTA - {self['width']} bands")
                    
                    # 1. Select a healthy maximum number of visual target ticks
                    max_ticks = 16
                    
                    # 2. Handle small band arrays safely
                    if self['width'] < max_ticks:
                        max_ticks = self['width']

                    # 3. Proportionally layout your text locations
                    indices = np.linspace(0, self['width'] - 1, num=max_ticks)
                    
                    bottom_ticks = []
                    for idx in indices:
                        band_idx = int(np.round(idx))
                        
                        # Match the bar geometric layout center
                        pos = band_idx + 0.5
                        
                        # Guard against potential initialization sequence delays
                        if band_idx < len(self['bands']):
                            label = f"{ftos(self['bands'][band_idx], sig_digits=4)}Hz"
                            bottom_ticks.append((pos, label))
                    
                    # 4. Apply clean styles to let PyQtGraph auto-manage layout crowding
                    bottom_axis = self.plot_widget.getAxis('bottom')
                    bottom_axis.setStyle(
                        hideOverlappingLabels=True,  # Bypasses rotation by cleanly culling text overlap
                        autoExpandTextSpace=True
                    )
                    bottom_axis.setTicks([bottom_ticks])



                case 'floor' | 'ceiling':
                    self.plot_widget.setYRange(0.0, 1.0, padding=0.025)
                    
                    raw_positions = np.linspace(0.0, 1.0, num=11)
                    db_values = np.linspace(self['floor'], self['ceiling'], num=11)
                    
                    custom_ticks = [(pos, f"{db:.1f} dB") for pos, db in zip(raw_positions, db_values)]
                    
                    left_axis = self.plot_widget.getAxis('left')
                    left_axis.setStyle(
                        hideOverlappingLabels=False,
                        autoExpandTextSpace=True
                    )
                    left_axis.setTicks([custom_ticks])
                
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __init__(self, *args, **kwargs):
        self.params = self.DEFAULTS.copy()
        self.params.update(kwargs)

        # Initialize base class
        super().__init__()
        
        # Create settings instance
        new_settings = QSettings()
        if self['settings']:
            new_settings.beginGroup(self['settings'].group())
        new_settings.beginGroup(self.__class__.__name__)
        self['settings'] = new_settings

        self.allow_close = True  # Added to ensure closeEvent doesn't break
        
        self.setWindowTitle(f"RTA - {self['width']} bands")

        saved_geometry = self['settings'].value("geometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        
        # 1. Main Widget and Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 3. Configure PyQtGraph Plot Widget
        self.plot_widget = pg.PlotWidget()
        main_layout.addWidget(self.plot_widget)
        
        # Optimize performance settings
        self.plot_widget.setAntialiasing(False)
        self.plot_widget.setYRange(0.0, 1.0)
        self.plot_widget.showGrid(x=False, y=True, alpha=0.3)
        self.plot_widget.getAxis('bottom').setLabel("Frequency Bands")
        self.plot_widget.getAxis('left').setLabel("Amplitude (dB)")

        # 4. Initialize Data Structure & Curve Item
        self.update_axis_geometry()
        
        self.bar_graph = pg.PlotCurveItem(
            self.x_edges, 
            self.y_data, 
            stepMode="center", 
            fillLevel=0, 
            brush=(46, 204, 113, 200),
            pen=pg.mkPen((46, 204, 113), width=1)
        )
        self.plot_widget.addItem(self.bar_graph)

        # 5. Initialize Crosshairs & Tooltip Overlay Elements
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('y', width=1, style=Qt.PenStyle.DashLine))
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('y', width=1, style=Qt.PenStyle.DashLine))
        self.plot_widget.addItem(self.v_line, ignoreBounds=True)
        self.plot_widget.addItem(self.h_line, ignoreBounds=True)
        
        self.v_line.hide()
        self.h_line.hide()

        # Tooltip item box styled with a semi-transparent gray background and white borders
        self.tooltip = pg.TextItem(anchor=(0, 1), fill=(40, 40, 40, 220), border='w')
        self.plot_widget.addItem(self.tooltip)
        self.tooltip.hide()

        # Connect mouse moves from the PlotWidget's underlying graphic scene
        self.plot_widget.scene().sigMouseMoved.connect(self.mouse_moved)

        # Override the leave event specifically for the plotting window frame 
        self.plot_widget.leaveEvent = self.on_mouse_leave
        self.mouse_in_window = False
        self.cursor_active = False
        self.timestamp = time()
        
        self.configure(**self.params)   # Trigger all the dictionary handlers

    def update_axis_geometry(self):
        """Recalculates sizes and updates the plot view bounds."""
        self.x_edges = np.arange(self['width'] + 1)
        self.y_data = np.zeros(self['width'])
        self.plot_widget.setXRange(0, self['width'])
        
    def closeEvent(self, event: QCloseEvent):
        self['settings'].setValue("geometry", self.saveGeometry())

        self['settings'].endGroup()
        if callable(getattr(self, 'close_callback', None)):
            self.close_callback()
        if self.allow_close == False:
            event.ignore()
        else:
            event.accept()
            
    def force_close(self):
        self.allow_close = True
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        elif event.key() == Qt.Key.Key_Escape:
            self.showNormal()
        else:
            super().keyPressEvent(event)

    def on_mouse_leave(self, evt):
        self.mouse_in_window = False
        self.clear_cursor_elements()

    def mouse_moved(self, evt):
        """Tracks cursor movement, snaps lines to bar column centers, and calculates dynamic dB values."""
        pos = evt
        # Make sure mouse is actively within the plot layout box bounds
        if self.plot_widget.plotItem.sceneBoundingRect().contains(pos):
            self.mouse_in_window = True

            mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            x_val = mouse_point.x()
            
            # Ground out index because column blocks span from integer index to index + 1
            bin_idx = int(np.floor(x_val))
            self.draw_cursor(bin_idx)

        else:
            self.mouse_in_window = False
            self.clear_cursor_elements()

    def clear_cursor_elements(self):
        if self.cursor_active:
            """Hides tracking indicators if they are currently painting on screen."""
            if hasattr(self, 'v_line') and self.v_line.isVisible():
                self.v_line.hide()
                self.h_line.hide()
            if hasattr(self, 'tooltip') and self.tooltip.isVisible():
                self.tooltip.hide()
            self.cursor_active = False
            
    def draw_cursor(self, bin_idx):
        # Bound check strictly against the parameter system's active band count
        if 0 <= bin_idx < self['width']:
            bin_center_x = bin_idx + 0.5
            actual_y_coord = self.y_data[bin_idx]
            
            # Un-normalize data coordinate (0.0 - 1.0) back into target dB domain labels
            actual_db = self['floor'] + (actual_y_coord * (self['ceiling'] - self['floor']))
            
            # Snaps crosshair indicators perfectly to the amplitude peak intersection point
            self.v_line.setPos(bin_center_x)
            self.h_line.setPos(actual_y_coord)
            
            # Optimization: Only trigger show() if lines are currently hidden
            if not self.v_line.isVisible():
                self.v_line.show()
                self.h_line.show()
            
            # Update visual labels and float tooltip above target bar column
            self.tooltip.setText(f" {ftos(self['bands'][bin_idx])}Hz\n {actual_db:.1f} dB ")
            self.tooltip.setPos(bin_center_x, actual_y_coord)
            
            if not self.tooltip.isVisible():
                self.tooltip.show()
                
            self.cursor_active = True
            
        else:
            self.clear_cursor_elements()
            
    def push_fft_frame(self, new_fft_slice):
        if new_fft_slice.shape[0] != self['width']:
            self['width'] = new_fft_slice.shape[0]
            return

        # Peak-hold filter
        elapsed = time() - self.timestamp
        self.timestamp = time()
        db_drop = self['dkrate'] * elapsed
        db_range = self['ceiling'] - self['floor']
        k = db_drop / db_range

        self.y_data -= k
        self.y_data = np.clip(np.maximum(self.y_data, new_fft_slice), 0.0, 1.0)
        self.bar_graph.setData(self.x_edges, self.y_data)
        
        if not self.mouse_in_window:
            if self['cursor'] != 'Mouse':
                peak_all = np.argmax(self.y_data)
                self.draw_cursor(peak_all)
            else:
                self.clear_cursor_elements()
                

# ------------------------------------------------------------------------------
# Main plugin 
# ------------------------------------------------------------------------------

"""
    params for target windows:
        Name:            Applies to:         Notes:
        'settings'      Always passed to children
        
        'bands'         All
        'floor'         All
        'ceiling'       All
        'width'         All
        'decay'         Bars
        'cursor'        All                 'Current peak', 'Global peak', 'Mouse'
        'depth'         2D, 3D              Number of time slices
        'line_period'   3D
"""

class RTAPlugin(BasePlugin):
    DEFAULTS = {
        'settings'      : None,                 # Parent group if present
        'sd'            : None,                 # Required - SoundDecCtl window
        'floor'         : DBMIN,
        'ceiling'       : DBMAX,
    }

    def cget(self, key):
        match key:
            case 'cmap_array':
                if self.target_win and hasattr(self.target_win, 'cmap_array'):
                    self.params['cmap_array'] = self.target_win['cmap_array']
        if key in self.params.keys():
            return self.params[key]
        else:
            raise ValueError
            
    def configure(self, cnf=None, **kwargs):
        """
        'bands'
        'floor'
        'ceiling'
        'width'
        'decay'
        'cursor'
        'depth'
        'line_period'
        """
    
        for key, value in kwargs.items():
            self.params[key] = value
            # Update graphics 
            if hasattr(self, 'target_win') and self.target_win:
                self.target_win[key] = value
                
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
        self.setStyleSheet(f"""
            .{self.__class__.__name__} {{
                border: 1px solid #888888;
                border-radius: 1px;
                background-color: #E5F5E5;
            }}
            QFrame {{
                border: 1px solid #DDDDDD;
                border-radius: 1px;
                /*background-color: #f9f9f9;*/
            }}
            QLabel {{
                border: none;
                background-color: transparent;
            }}
            QLineEdit {{
                border: none;
            }}
            QPushButton {{
                padding: 2px 2px 2px 2px;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 1px;
                /*background-color: #0078d4;*/
                color: #000000;
            }}
            QPushButton:pressed {{
                background-color: #0078d4;
                color: #ffffff;
                border-color: #005a9e;
            }}
            PushButton:hover {{
                border-color: #0078d4;
            }}
        """)

        # ----------------------------------------------------------------------
        # Top label & show/hide button
        # ----------------------------------------------------------------------
        self.title_label = QLabel('3D Waterfall')
        self.title_label.setStyleSheet("""
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

        # All widgets with save-able values:
        self.controls = {}
        self.labels = {}
        

        # ----------------------------------------------------------------------
        # Combobox frame
        # ----------------------------------------------------------------------
        self.combo_frame = QFrame()
        self.combo_layout = QGridLayout(self.combo_frame)
        self.combo_layout.setSpacing(2)
        self.combo_layout.setContentsMargins(2,2,2,2)
        
        type_list = [ 'Bars', '2D', '3D' ]
        filter_list =   [ 'FFT 256' , 'FFT 512', 'FFT 1024', 'FFT 2048', 
                            '1/3 oct', '1/6 oct', '1/12 oct', '1/24 oct']
        fft_windows =   ['Boxcar', 'Hamming', 'Cosine', 'Hann', 'Blackman']
        depth_list =    [ '128', '256', '512', '1024', '2048' ]
        cursor_list =   [ 'Mouse', 'Current peak', 'Global peak' ]
        dkrate_list =   [ '40dB/sec', '20dB/sec', '10dB/sec', '5dB/sec', '2dB/sec', '1dB/sec', '0dB/sec' ]
        combos = [
            # id            list                        default         Label
            ('type',        type_list,                  type_list[0],   'Type',         0, 0),
            ('filter',      filter_list,                filter_list[0], 'Filter',       0, 2),
            ('fft_win',     fft_windows,                fft_windows[0], 'Window',       0, 4),
            ('cursor',      cursor_list,                cursor_list[0], 'Cursor',       1, 0),
            ('depth',       depth_list,                 depth_list[0],  'Depth',        1, 2),
            ('dkrate',      dkrate_list,                dkrate_list[0], 'Decay rate',   1, 4),
        ]
        """
        self.cbx = {
            'type' : {
                'label' : {
                    'text'      : 'Type',
                },
                'combo' : {
                    'opts'      : [ 'Bars', '2D', '3D' ],
                    'default'   : 'Bars',
                }
            }
        }
        """
        for (name, vlist, vdef, lab, row, col) in combos:
            self.labels[name] = QLabel(lab)
            self.labels[name].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            
            self.controls[name] = QComboBox()
            self.controls[name].addItems(vlist)
            self.controls[name].currentTextChanged.connect(lambda *args, n=name : self.on_control(n, *args))

            self.combo_layout.addWidget(self.labels[name], row, col)
            self.combo_layout.addWidget(self.controls[name], row, col + 1)

        # Colormap frame
        self.labels['cmap'] = QLabel('Colormap')
        self.labels['cmap'].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        class DynamicComboBox(QComboBox):
            about_to_show_popup = pyqtSignal()
            def __init__(self, callback):
                self.callback = callback
                super().__init__()
            def showPopup(self):
                self.callback()
                super().showPopup()        
        
        self.controls['cmap'] = DynamicComboBox(self.cmap_combobox_show_popup)
        self.cmap_combobox_show_popup() # Add items
        self.controls['cmap'].currentTextChanged.connect(lambda *args, n='cmap' : self.on_control(n, *args))

        self.cmap_btn = {}
        for key in [ 'Edit', 'Delete', 'Save', 'Save as' ]:
            self.cmap_btn[key]  = QPushButton(key)
            self.cmap_btn[key].setFixedWidth(50)
            self.cmap_btn[key].clicked.connect(lambda *args, btn=key : self.on_cmap_button(btn, *args))

        self.cmap_frame = QFrame()
        self.cmap_layout = QGridLayout(self.cmap_frame)
        self.cmap_layout.setSpacing(2)
        self.cmap_layout.setContentsMargins(2,2,2,2)

        self.cmap_layout.addWidget(self.controls['cmap'],       0, 1)
        self.cmap_layout.addWidget(self.cmap_btn['Edit'],       0, 2)
        self.cmap_layout.addWidget(self.cmap_btn['Delete'],     0, 3)
        self.cmap_layout.addWidget(self.cmap_btn['Save'],       0, 4)
        self.cmap_layout.addWidget(self.cmap_btn['Save as'],    0, 5)
        
        # ----------------------------------------------------------------------
        # Dial frame
        # ----------------------------------------------------------------------
        dials = [ 
            # Name,     Text,       Min,        Max,        Color
            ('floor',   'Floor',    DBMIN,      DBMAX-2,    '#C58'   ), 
            ('ceiling',    'Ceiling',  DBMIN+2,    DBMAX,      '#FD4'   ), 
        ]
        self.dial_name_label = {}
        self.dial_value_label = {}
        self.dial_last_value = {}
        for name, text, vmin, vmax, color in dials:
            # Dial title label
            self.dial_name_label[name] = QLabel(text)

            # Dial
            self.controls[name] = CustomColorDial()
            self.controls[name].setWrapping(False)
            self.controls[name].setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.controls[name].setFixedSize(32,32)
            self.controls[name].setRange(vmin, vmax)
            self.controls[name].config(fill_color = color, indicator_color = '000')
            self.controls[name].valueChanged.connect(lambda *args, ctl=name: self.on_control(ctl, *args))

            # Dial value label
            self.dial_value_label[name] = QLabel()
            self.dial_value_label[name].setFixedWidth(45)
            self.dial_value_label[name].setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.dial_value_label[name].setStyleSheet("QLabel { color: blue;}")

        self.dial_frame = QFrame()
        self.dial_layout = QGridLayout(self.dial_frame)
        for col, (name, _, _, _, _,)   in enumerate(dials):
            self.dial_layout.addWidget(self.dial_name_label[name],  0, col, Qt.AlignmentFlag.AlignCenter)
            self.dial_layout.addWidget(self.dial_value_label[name], 1, col)
            self.dial_layout.addWidget(self.controls[name],         2, col, Qt.AlignmentFlag.AlignCenter)

        # ----------------------------------------------------------------------
        # Frame layout
        # ----------------------------------------------------------------------
        self.mid_frame = QFrame()
        self.mid_layout = QVBoxLayout(self.mid_frame)
        self.mid_layout.setSpacing(2)
        self.mid_layout.setContentsMargins(2,2,2,2)
        self.mid_layout.addWidget(self.combo_frame)
        self.mid_layout.addWidget(self.cmap_frame)
        
        # Button frame layout
        self.button_frame = ChEnaBlockIO(button_callback=self.button_callback)

        # Top frame with title label and show/hide button
        self.title_bar_frame = QFrame()
        self.title_bar_layout = QHBoxLayout(self.title_bar_frame)
        self.title_bar_layout.setSpacing(2)
        self.title_bar_layout.setContentsMargins(2,2,2,2)
        self.title_bar_layout.addWidget(self.title_label, 1)
        self.title_bar_layout.addWidget(show_hide_button)

        # Bottom frame with everything else (this gets hidden/shown)
        self.main_body_frame = QFrame()
        self.main_body_layout = QHBoxLayout(self.main_body_frame)
        self.main_body_layout.setContentsMargins(0,0,0,0)
        self.main_body_layout.setSpacing(0)
        self.main_body_layout.addWidget(self.button_frame, 0)
        self.main_body_layout.addWidget(self.mid_frame, 1)
        self.main_body_layout.addWidget(self.dial_frame, 0)

        # Restore show/hide state
        if bool(self['settings'].value('visible', True)) == False:
            self.main_body_frame.hide()

        # Main layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(0)
        self.main_layout.setContentsMargins(0,0,0,0)
        self.main_layout.addWidget(self.title_bar_frame)
        self.main_layout.addWidget(self.main_body_frame)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setLayout(self.main_layout)

        # Misc vars
        self.ringbuf=RingBuffer(capacity=16384, dtype=np.float32) # Audio input buf
        self.fft_overlap = 0
        self.mtx = np.zeros(1)

        self.filt = bpf_filter_bank(dbmin = DBMIN)

        # Timer setup
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.on_timer)

        # Add stuff not visible to base class
        self.target_win = None
        
        self.cmap_win = ColorPickerApp(settings=self['settings'], sa=self)
        self.cmap_win.hide()

        self.title_label.setText('Real Time Analyzer')

        # Load saved settings & trigger all the widget handlers
        self.load_settings()

        # Add sd callbacks
        self['sd'].add_callback('config', self.sd_config_callback)
        self['sd'].add_callback('monitor', self.sd_monitor_callback)

    def cmap_combobox_show_popup(self):
        # Clear current items in combo
        self.controls['cmap'].blockSignals(True)
        self.controls['cmap'].clear()

        # Get list of saved cmaps
        cm_grp = QSettings()
        cm_grp.beginGroup('Colormaps')
        reg_items = cm_grp.childKeys()
        
        # Create a default if non exists
        if len(reg_items) == 0:
            sa = """[
                [0, 23, 94, 200, 219, 255], 
                [42, 0, 0, 255, 255, 255], 
                [0, 202, 255, 255, 0, 255], 
                [125, 202, 0, 0, 102, 255]
            ]"""
            cm_grp.setValue(f"Colormap 1", sa)
            reg_items = cm_grp.childKeys()
        cm_grp.endGroup()
        
        # Populate combobox
        for cm in reg_items:
            self.controls['cmap'].addItem(cm)
        self.controls['cmap'].blockSignals(False)
    
    def on_cmap_button(self, btn, state):
        if not hasattr(self, 'cmap_array'):
            return
            
        # Get currently selected item
        name = self.controls['cmap'].currentText()
        
        # Convert current cmap to string for save/save as
        cmap_json = json.dumps(self['cmap_array'])

        # Open colormaps reg area
        cm_grp = QSettings()
        cm_grp.beginGroup('Colormaps')

        match btn:
            case 'Edit':
                self.cmap_win.load_cmap(self['cmap_array'])
                self.cmap_win.show()
            case 'Save':
                cm_grp.setValue(f"{name}", cmap_json)
            case 'Save as':
                text, ok = QInputDialog.getText(self, 'Save as', 'Colormap name:')
                if ok:
                    cm_grp.setValue(f"{text}", cmap_json)
                    self.controls['cmap'].blockSignals(True)
                    self.controls['cmap'].addItem(text)
                    self.controls['cmap'].setCurrentText(text)
                    self.controls['cmap'].blockSignals(False)
            case 'Delete':
                #if name == "Colormap 1":
                #    return
                msg_box = QMessageBox(self)

                # 2. Configure window details and iconography
                msg_box.setWindowTitle("Confirm")
                msg_box.setText(f'Delete {name}?')
                msg_box.setIcon(QMessageBox.Icon.Question)
                
                # 3. Define action buttons using PyQt6 StandardButton enums
                msg_box.setStandardButtons(
                    QMessageBox.StandardButton.Yes | 
                    QMessageBox.StandardButton.Cancel
                )
                
                # 4. Set the default focus button
                msg_box.setDefaultButton(QMessageBox.StandardButton.No)

                # 5. Execute the dialog blockingly and capture the user's action
                return_value = msg_box.exec()
                if return_value == QMessageBox.StandardButton.Yes:
                    self.controls['cmap'].removeItem(self.controls['cmap'].findText(name))
                    cm_grp.remove(name)

        # Close reg group
        cm_grp.endGroup()
        
    def on_show_hide_button(self):
        # Handle the titlebar show/hide button
        if self.main_body_frame.isVisible():
            self.main_body_frame.hide()
        else:
            self.main_body_frame.show()
            
    def load_settings(self):

        # Fetch control settings
        for name in self.controls.keys():
            self.set_control(name, self['settings'].value(name, self.get_control(name)))

        # Fetch channel enable buttons
        s = self['settings'].value('ch_buttons', '', type=str)
        self.button_frame.set_save_state(s)

    def save_settings(self):
        # Save show/hide state
        self['settings'].setValue('visible', int(self.main_body_frame.isVisible()))
        
        # Save control settings
        for name in self.controls.keys():
            self['settings'].setValue(name, self.get_control(name))
            
        # Save channel enable buttons
        s = self.button_frame.get_save_state()
        self['settings'].setValue('ch_buttons', s)        
        
    def get_control(self, name):
        c = self.controls
        
        if isinstance(c[name], QComboBox):
            return c[name].currentText()
        elif isinstance(c[name], QDial):
            return c[name].value()

    def set_control(self, name, value, block_sig=False):
        #print(f'-------- {name}   {value}    {type(self.controls[name])}   {type(value)}   ')
        if name == None or value == None:
            return
        c = self.controls
        # Note that setting ComboBox, QDial and others to their current value
        # does not trigger the handler signal. So we force that below by
        # calling the handler directly in those cases. However, that will have no
        # effect if block_sig is True.
        
        if block_sig:
            c[name].blockSignals(True)
            
        if isinstance(c[name], QComboBox):
            if c[name].currentText() == value:
                self.on_control(name, value)
            else:
                c[name].setCurrentText(value)
        elif isinstance(c[name], QDial):
            if c[name].value() == value:
                self.on_control(name, value)
            else:
                c[name].setValue(value)
            
        if block_sig:
            c[name].blockSignals(False)

    def on_control(self, name, value):
        # signal handler for control widget value changes
        # Kill the timer while we do this
        tmr_active = self.timer.isActive()
        if tmr_active:
            self.timer.stop()

        c = self.controls
        if isinstance(c[name], QComboBox):
            cget = c[name].currentText
            cset = c[name].setCurrentText
        elif isinstance(c[name], QDial):
            cget = c[name].value
            cset = c[name].setValue
            
        match name:
            # ComboBox handlers:
            case 'type':
                if self.target_win:
                    self.target_win.force_close()
                    self.target_win.deleteLater()
                    self.target_win = None
                    
                match value:
                    case 'Bars':
                        self.labels['depth'].hide()
                        self.controls['depth'].hide()
                        
                        self.labels['dkrate'].show()
                        self.controls['dkrate'].show()
                        
                        self.cmap_frame.hide()
                        
                        self.target_win = BargraphWindow(**self.params)

                    case '2D':
                        self.labels['depth'].show()
                        self.controls['depth'].show()
                        
                        self.labels['dkrate'].hide()
                        self.controls['dkrate'].hide()
                        
                        self.cmap_frame.show()
                        
                        self.target_win = Waterfall2D(**self.params)

                    case '3D':
                        self.labels['depth'].show()
                        self.controls['depth'].show()
                        
                        self.labels['dkrate'].hide()
                        self.controls['dkrate'].hide()
                        
                        self.cmap_frame.show()
                        
                        self.target_win = Waterfall3D(**self.params)

                if np.any(self.mtx):
                    self.target_win.show()
                tmr_active = True
                    
            
            case 'filter' | 'fft_win':
                self.filter_init()

            case 'depth':
                self['depth'] = int(value)
                
            case 'cmap':
                cm_grp = QSettings()
                cm_grp.beginGroup('Colormaps')
                sa = cm_grp.value(f"{value}")
                cm_grp.endGroup()
                self['cmap_array'] = json.loads(sa)
                self.cmap_win.load_cmap(self['cmap_array'])

            case 'cursor':
                self['cursor'] = value
                
            case 'dkrate':
                if (m := re.match(r"^([0-9]+).*", value)):
                    self['dkrate'] = float(m.groups()[0])
                
            # Dial handlers
            case 'floor':
                self.dial_value_label['floor'].setText(f'{value}dB')
                self['floor'] = value
                if c['ceiling'].value() < (self['floor'] + 2):
                    # Make sure ceil > floor
                    c['floor'].blockSignals(True)
                    c['ceiling'].setValue(self['floor'] + 2)
                    c['floor'].blockSignals(False)
                    
            case 'ceiling':
                self.dial_value_label['ceiling'].setText(f'{value}dB')
                self['ceiling'] = value
                if c['floor'].value() > (self['ceiling'] - 2):
                    # Make sure ceil > floor
                    c['ceiling'].blockSignals(True)
                    c['floor'].setValue(self['ceiling'] - 2)
                    c['ceiling'].blockSignals(False)
                            
        # Restart timer if it was running
        if tmr_active:
            self.timer.start(20)

    def on_exit(self):
        # Called from main app when closing. Equivalent to closeEvent()
        self.timer.stop()
        self.save_settings()
        if self.target_win:
            self.target_win.force_close()
            self.target_win.deleteLater()
        self.cmap_win.force_close()
        super().on_exit()
        
    def button_callback(self, matrix):
        # Callback from button_frame when something is clicked
        # or the button count changes.
        
        self.mtx = matrix

        if self.target_win:
            # Open/close window as needed
            if np.any(self.mtx) and self.target_win.isVisible() == False:
                self.target_win.show()
                self.timer.start(20)
            elif not np.any(self.mtx) and self.target_win.isVisible() == True:
                self.timer.stop()
                self.target_win.hide()
            
    def sd_config_callback(self, params):
        #self.params = params
        self['samplerate'] = params['samplerate']
        self['blocksize'] = params['blocksize']
        self['in_ch'] = params['in_ch']
        self['out_ch'] = params['out_ch']
        
        
        # Update channel buttons
        self.button_frame.add_remove_ch_buttons(params['in_ch'], params['out_ch'])
        
        # Initialize filter module as needed
        self.filter_init()
        
    def sd_monitor_callback(self, data):
        # Sum the selected channels and add to ringbuf.
        self.ringbuf.write(np.dot(data, self.mtx))

    def filter_init(self):
        if 'samplerate' not in self.params.keys():
            return
            
        # Kill the timer while we do this
        tmr_active = self.timer.isActive()
        if tmr_active:
            self.timer.stop()

        if (m := re.match(r"^FFT ([0-9]+)$", self.get_control('filter'))):
            # 'FFT <size>'
            self.controls['fft_win'].setEnabled(True)
            self.filt.config(
                ftype='FFT', 
                fft_size = int(m.groups()[0]),
                fft_win = self.get_control('fft_win'),
            )

        elif (m := re.match(r"^1/([0-9]+) oct$", self.get_control('filter'))):
            # '1/x oct'
            self.controls['fft_win'].setEnabled(False)
            self.filt.config(
                ftype = 'CQT',
                fs = self['samplerate'], 
                bpo = int(m.groups()[0]), 
                f1 = self['samplerate'] / 2,
                fft_size = self['blocksize'],
            )
        self['line_period'] = self.filt['fft_size'] / self['samplerate']
        self['bands'] = self.filt['bands']
        
        # Restart timer if it was running
        if tmr_active:
            self.timer.start(20)

    def on_timer(self):
        if not np.any(self.mtx):
            return
            
        # Just loop until have stream params
        if self.params == None:
            if self['sd']['active'] == True:
                self.sd_config_callback(self['sd'].get_stream_params())
            else:
                self.timer.start(20)
                return

        while True:
            try:
                sig = self.ringbuf.read(self.filt['fft_size'], overlap=0)
                db = self.filt.filter_block_return_db(sig)
                db -= self['floor']
                db /= (self['ceiling'] - self['floor'])
                db = np.clip(db, 0.0, 1.0)
                if self.target_win:
                    self.target_win.push_fft_frame(db)
            except ValueError:
                break

        self.timer.start(20)

