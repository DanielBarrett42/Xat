# Test dial
import math
from PyQt6.QtWidgets import QDial
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PyQt6.QtCore import Qt, QPointF


class CustomColorDial(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Optional: Hide default notches if you are painting everything custom
        self.setNotchesVisible(False)
        self.params = {
            'clearance'         : 1,
            'line_width'        : 2,
            'line_color'        : '#2C3E50',
            'fill_color'        : '#EEEE55',
            'indicator_radius'  : 3,
            'indicator_inset'   : 3,
            'indicator_color'   : '#E74C3C',
            'wheel_step_size'   : 1,                # Steps per mouse wheel step
            'start_angle'       : 240,              # Degrees
            'span_angle'        : -300,             # Degrees of rotation (neg for CW)
        }

    def wheelEvent(self, event):
        # Get the scroll direction (positive = up/away, negative = down/toward)
        degrees = event.angleDelta().y()
        
        if degrees == 0:
            event.ignore()
            return

        # Determine direction: +1 for scrolling up, -1 for scrolling down
        direction = 1 if degrees > 0 else -1
        
        # Calculate the new value
        new_value = self.value() + (direction * self.params['wheel_step_size'])
        
        # Ensure the value stays within the dial's min and max bounds
        #new_value = max(self.minimum(), min(self.maximum(), new_value))

        self.setValue(new_value)
        event.accept()

    def paintEvent(self, event):
        # 1. Initialize the painter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 2. Calculate the geometry and center point
        # Keep it square based on the smallest dimension
        side = min(self.width(), self.height())
        center_x = self.width() / 2
        center_y = self.height() / 2
        #print(f'KNOB width={self.width()} height={self.height()}')
        inner_radius = side/2 - self.params['clearance']
        
        outer_ring_color = 'black'
        inner_fill_color = 'white'

        # Knob outer ring
        painter.setPen(QPen(QColor(self.params['line_color']), self.params['line_width']))

        # Knob inner fill color
        painter.setBrush(QBrush(QColor(self.params['fill_color'])))
        painter.drawEllipse(QPointF(center_x, center_y), inner_radius, inner_radius)


        # self.params['indicator_radius'] = 3
        # self.params['indicator_inset'] = 3
        # self.params['indicator_color'] = ,#E74C3C'

        # 4. Calculate the angle for the indicator knob
        # Map current value to degrees. Standard QDial spans ~270 degrees.
        # It starts roughly at 135 degrees (bottom left) and goes to -135 degrees (bottom right)
        val_range = self.maximum() - self.minimum()
        if val_range == 0:
            percentage = 0
        else:
            percentage = (self.value() - self.minimum()) / val_range

        # Convert to standard geometric radians (Qt 0 degrees is 3 o'clock)
        # A standard dial spans from roughly 240° down to -60°
        #self.params['start_angle'] = 240
        #span_angle = -300  # Rotates clockwise
        current_angle_deg = self.params['start_angle'] + (percentage * self.params['span_angle'])
        angle_rad = math.radians(current_angle_deg)

        # 5. Calculate position and draw the custom-colored knob/indicator dot
        # Distance of the indicator dot from the center
        indicator_dist = inner_radius - self.params['indicator_inset'] 
        
        knob_x = center_x + indicator_dist * math.cos(angle_rad)
        knob_y = center_y - indicator_dist * math.sin(angle_rad)  # Subtract because Qt Y-axis goes down

        # Set the custom color for your indicator knob
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(self.params['indicator_color'])))
        painter.drawEllipse(QPointF(knob_x, knob_y), self.params['indicator_radius'], self.params['indicator_radius'])
        
        painter.end()

    #--- Dictionary method handlers

    def cget(self, key):
        if key in self.params.keys():
            return self.params[key]
        raise ValueError
        
    def configure(self, **kwargs):
        # Extract and handle our args
        for key, value in kwargs.items():
            match key:
                case '_':
                    pass
            self.params[key] = value
                    
    config = configure

    def __getitem__(self, key):
        return self.cget(key)
        
    def __setitem__(self, key, value):
        self.configure(**{key: value})

"""
class LabeledKnob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 1. Set a default initial size hint and a reasonable minimum footprint
        self.setMinimumSize(100, 100) 
        self.resize(120, 120) 

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 2. Dynamically calculate boundaries based on the current widget footprint
        width = self.width()
        height = self.height()
        center = QPointF(width / 2, height / 2)
        
        # Determine the maximum available space for our drawing circle
        max_diameter = min(width, height)
        
        # Scaling factors: Calculate widths relative to overall size
        # This keeps the design looking identical whether it's 100px or 500px wide.
        padding = max_diameter * 0.18        # Room for labels around the edge
        knob_radius = (max_diameter / 2) - padding
        
        tick_length = max_diameter * 0.06    # Dash line sizes scaled down
        text_distance = knob_radius + (tick_length * 2.2)
        font_size = max(8, int(max_diameter * 0.05)) # Scales text size dynamically

        # Angle configurations
        start_angle = 230
        sweep_angle = 280
        
        val_min = self.minimum()
        val_max = self.maximum()
        val_range = val_max - val_min if val_max > val_min else 1
        ratio = (self.value() - val_min) / val_range
        current_angle = start_angle - (ratio * sweep_angle)

        # =====================================================================
        # DRAW LABELED TICK MARKS
        # =====================================================================
        num_ticks = 5  
        painter.setFont(QFont("Arial", font_size, QFont.Weight.Bold))
        
        for i in range(num_ticks):
            tick_ratio = i / (num_ticks - 1)
            tick_angle = start_angle - (tick_ratio * sweep_angle)
            tick_rad = math.radians(tick_angle)
            tick_value = int(val_min + (tick_ratio * val_range))
            
            x1 = center.x() + (knob_radius + 4) * math.cos(tick_rad)
            y1 = center.y() - (knob_radius + 4) * math.sin(tick_rad)
            x2 = center.x() + (knob_radius + 4 + tick_length) * math.cos(tick_rad)
            y2 = center.y() - (knob_radius + 4 + tick_length) * math.sin(tick_rad)
            
            if self.value() >= tick_value:
                painter.setPen(QPen(QColor("#00adb5"), max(1, int(max_diameter * 0.015))))
            else:
                painter.setPen(QPen(QColor("#555555"), max(1, int(max_diameter * 0.01))))
                
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            
            tx = center.x() + text_distance * math.cos(tick_rad)
            ty = center.y() - text_distance * math.sin(tick_rad)
            
            text_str = str(tick_value)
            painter.setPen(QPen(QColor("#a0a0a0")))
            
            # Dynamically size text boundary boxes
            box_w = int(max_diameter * 0.25)
            box_h = int(max_diameter * 0.12)
            painter.drawText(
                int(tx - box_w/2), int(ty - box_h/2), box_w, box_h, 
                Qt.AlignmentFlag.AlignCenter, text_str
            )

        # =====================================================================
        # DRAW KNOB BASE & TRACK
        # =====================================================================
        painter.setPen(QPen(QColor("#444444"), max(1, int(max_diameter * 0.015))))
        painter.setBrush(QBrush(QColor("#2d2d2d")))
        painter.drawEllipse(center, knob_radius, knob_radius)

        # Active Track Arc
        track_thickness = max(2, int(max_diameter * 0.025))
        painter.setPen(QPen(QColor("#00adb5"), track_thickness))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        offset = track_thickness / 2
        painter.drawArc(
            int(center.x() - knob_radius + offset), 
            int(center.y() - knob_radius + offset), 
            int((knob_radius - offset) * 2), 
            int((knob_radius - offset) * 2), 
            int(start_angle * 16), 
            int(-ratio * sweep_angle * 16)
        )

        # Pointer Indicator
        rad = math.radians(current_angle)
        start_x = center.x() + (knob_radius * 0.3) * math.cos(rad)
        start_y = center.y() - (knob_radius * 0.3) * math.sin(rad)
        end_x = center.x() + (knob_radius * 0.8) * math.cos(rad)
        end_y = center.y() - (knob_radius * 0.8) * math.sin(rad)

        painter.setPen(QPen(QColor("#ffffff"), max(2, int(max_diameter * 0.02)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(start_x, start_y), QPointF(end_x, end_y))

class ModernKnob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(80, 80)
        self.setMaximumSize(120, 120)

    def paintEvent(self, event):
        # Initialize the painter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Get geometry dimensions
        width = self.width()
        height = self.height()
        radius = min(width, height) // 2 - 10
        center = QPointF(width / 2, height / 2)

        # 1. Draw the Background Base of the Knob
        painter.setPen(QPen(QColor("#444444"), 2))
        painter.setBrush(QBrush(QColor("#2d2d2d")))
        painter.drawEllipse(center, radius, radius)

        # 2. Calculate the rotation angle based on the dial value
        # QDial values span an arc. We map the current value to degrees.
        val_range = self.maximum() - self.minimum()
        if val_range == 0:
            val_range = 1
            
        # Map value to an angle between ~240 degrees (start) to ~ -60 degrees (end)
        # In Qt, 0 degrees is 3 o'clock, counting counter-clockwise.
        ratio = (self.value() - self.minimum()) / val_range
        
        # Audio knobs typically start around 230 degrees and sweep 280 degrees clockwise
        start_angle = 230
        sweep_angle = 280
        current_angle = start_angle - (ratio * sweep_angle)
        
        # Convert angle to radians for trigonometry
        rad = math.radians(current_angle)

        # 3. Draw the Active Value Arc (Track)
        arc_pen = QPen(QColor("#00adb5"), 4) # Neon teal active track
        painter.setPen(arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # Qt drawArc uses 1/16th of a degree
        # We draw the track from the start angle to the current position
        painter.drawArc(
            int(center.x() - radius + 5), 
            int(center.y() - radius + 5), 
            int((radius - 5) * 2), 
            int((radius - 5) * 2), 
            int(start_angle * 16), 
            int(-ratio * sweep_angle * 16)
        )

        # 4. Draw the Pointer Indicator (The Line/Dot on the Knob)
        # Calculate line start (near center) and end (near outer edge)
        pointer_start_dist = radius * 0.3
        pointer_end_dist = radius * 0.8
        
        start_x = center.x() + pointer_start_dist * math.cos(rad)
        start_y = center.y() - pointer_start_dist * math.sin(rad) # Y is inverted in screen space
        end_x = center.x() + pointer_end_dist * math.cos(rad)
        end_y = center.y() - pointer_end_dist * math.sin(rad)

        pointer_pen = QPen(QColor("#ffffff"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pointer_pen)
        painter.drawLine(QPointF(start_x, start_y), QPointF(end_x, end_y))


"""