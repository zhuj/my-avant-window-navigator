#!/usr/bin/python
# -*- coding: utf-8 -*-

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import re
import stat
import subprocess
import cairo
import pangocairo

import pygtk
pygtk.require('2.0')
import gtk
from gtk import gdk

from awn.extras import _, awnlib, __version__
from awn import OverlayText


applet_name = _("Sensors Monitor")
applet_description = _("An applet to monitor the sensor's values")

# Themed logo of the applet, used as the applet's icon and shown in the GTK About dialog
applet_logo = os.path.join(os.path.dirname(__file__), "sensors.svg")
images_path = os.path.join(os.path.dirname(__file__), "images")

# Interval in seconds between two successive draws of the icon
draw_interval = 5


def sysfs_thermal_read(fn):
    try: return int(open(fn).readline().strip())/1000
    except: return 0

def sysfs_thermal_sensor(path, name, name_file, input_file, max_file, sensor_min):
    sensor_name = open(os.path.join(path, name, name_file)).readline().strip()
    sensor_max  = int(float(open(os.path.join(path, name, max_file)).readline().strip())*0.8 / 1000)
    return (
        name, 
        sensor_name, 
        sensor_min, 
        sensor_max, 
        lambda: sysfs_thermal_read(os.path.join(path, name, input_file)),
        "°C"
    )

sensors = []

# hwmon layer
try:
    sysfs_virtual_hwmon = "/sys/devices/virtual/hwmon"
    sensors += [
        sysfs_thermal_sensor(sysfs_virtual_hwmon, i, "name", "temp1_input", "temp1_crit", 40)
        for i in os.listdir(sysfs_virtual_hwmon) if i.startswith("hwmon")
    ]
except:
    pass

# thermal
try:
    sysfs_virtual_thermal = "/sys/devices/virtual/thermal"
    sensors += [
        sysfs_thermal_sensor(sysfs_virtual_thermal, i, "type", "temp", "trip_point_0_temp", 40)
        for i in os.listdir(sysfs_virtual_thermal) if i.startswith("thermal_zone")
    ]
except:
    pass


# core2 sensors
try:
    sysfs_platform = "/sys/devices/platform"
    for i in [ i for i in os.listdir(sysfs_platform) if i.startswith("coretemp.") ]:
        sensors += [
            sysfs_thermal_sensor(sysfs_platform, i, prefix+"_label", prefix+"_input", prefix+"_max", 40)
            for prefix in [ x[:-6] for x in os.listdir(os.path.join(sysfs_platform, i)) if x.startswith("temp") and x.endswith("_label") ]
        ]
except:
    pass

#aticonfig
try:
    f = lambda: int(float( 
        subprocess.Popen(["aticonfig","--pplib-cmd","get temperature 0" ], stdout=subprocess.PIPE)
        .stdout.readline().strip().split()[-1] 
    ))

    if f() is not None:
        sensors += [( "fglrx", "fglrx", 50, 90, f, "°C" )]
except:
    pass

#nvidia-temperature
try:
    f = lambda: int(float( 
        subprocess.Popen(["nvidia-temperature"], stdout=subprocess.PIPE)
        .stdout.readline().strip().split()[-1] 
    ))

    if f() is not None:
        sensors += [( "nvidia", "nvidia", 50, 90, f, "°C" )]
except:
    pass

#hdd-temp
try:
    f = lambda: int(float(
        subprocess.Popen(["hdd-temperature"], stdout=subprocess.PIPE)
        .stdout.readline().strip().split("|")[3]
    ))

    if f() is not None:
        sensors += [( "hdd", "hdd", 40, 60, f, "°C" )]
except:
    pass


# sort
sensors.sort()

def safe(c):
    try: return c()
    except: return 0.0

class SensorsApplet:
    """ """

    __button_press_event_id = None

    def __init__(self, applet):
        self.applet = applet

        self.size_changed_cb()
        applet.connect_size_changed(self.size_changed_cb)
        applet.timing.register(self.draw_cb, draw_interval)
        applet.tooltip.connect_becomes_visible(self.update_title)

    def size_changed_cb(self):
        """Update the applet's icon to reflect the new height. """

        self.setup_icon()
        self.draw_cb()

    def setup_icon(self):
        """Load the images that are going to be used as the applet's icon. """

        self.__temp_overlay = OverlayText()
        self.__temp_overlay.props.active = 1
        self.__temp_overlay.props.font_sizing = 16
        self.__temp_overlay.props.y_override = 6
        self.applet.add_overlay(self.__temp_overlay)

        self.icon_states = []
        sz = self.applet.get_size()
        for i in range(0,14):
            path = os.path.join(images_path, "cpufreq-%s.svg" % i)
            pixbuf = gdk.pixbuf_new_from_file(path)
            pixbuf = gdk.pixbuf_new_from_file_at_size(path, pixbuf.get_width() * sz / pixbuf.get_height(), sz)
            self.icon_states.append(pixbuf)

    def icon(self, value):
        if (value < 0.0): return self.icon_states[0]
        if (value > 0.9): return self.icon_states[-1]
        return self.icon_states[ int(round(len(self.icon_states) * value)) ]

    def values(self, ico=None):
        return [
             ( name, sensor_name, sensor_val, ico and self.icon(float(sensor_val - sensor_min) / (sensor_max - sensor_min)), sensor_unit )
             for name, sensor_name, sensor_min, sensor_max, sensor_val, sensor_unit in [
                ( name, sensor_name, sensor_min, sensor_max, callback(), sensor_unit )
                for name, sensor_name, sensor_min, sensor_max, callback, sensor_unit in sensors
             ]
        ]

    def draw_cb(self):
        """Draw the icon and updates the title. """
        T = 21

        values = self.values(1)
        width = sum([ max(T, v[3].get_width()) for v in values ])
        height = max([ v[3].get_height() for v in values ])
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        gdk_cr = gtk.gdk.CairoContext(context)

        overlay_text = ""
        left = 0
        for name, sensor_name, sensor_val, sensor_ico, sensor_unit in values:
            gdk_cr.set_source_pixbuf(sensor_ico, left, height-sensor_ico.get_height())
            gdk_cr.paint()
            left += max(T, sensor_ico.get_width())
            overlay_text += "% 4d " % sensor_val

        self.__temp_overlay.props.text = overlay_text
        self.applet.icon.set(context)
        self.update_title(values)

        del gdk_cr
        return True

    def update_title(self, values=None):
        """ update title """
        if not self.applet.tooltip.is_visible(): return
        if (values is None): values = self.values(0)

        self.applet.tooltip.set("\n".join([ 
            (sensor_name + (" \t % 4d"%sensor_val) + sensor_unit ) 
            for name, sensor_name, sensor_val, sensor_ico, sensor_unit in values
        ]))


if __name__ == "__main__":
    awnlib.init_start(
        SensorsApplet, 
        {
            "name": applet_name,
            "short": "sensors",
            "version": __version__,
            "description": applet_description,
            "logo": applet_logo,
            "author": "zhuj",
            "copyright-year": "2011",
            "authors": ["rjabchikov.zhuj@gmail.com"]
        }
    )
