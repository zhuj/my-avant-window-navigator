#!/usr/bin/python
# Copyright (C) 2008 - 2010  onox <denkpadje@gmail.com>
#
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


## sudo chmod u+s /usr/bin/cpufreq-set
## sudo chmod g+s /usr/bin/cpufreq-set

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

try:
    import dbus
    import dbus.service

    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
except ImportError:
    dbus = None
except dbus.DBusException:
    dbus = None

applet_name = _("CPU Frequency Monitor")
applet_description = _("An applet to monitor and control the CPU frequency")

# Themed logo of the applet, used as the applet's icon and shown in the GTK About dialog
applet_logo = os.path.join(os.path.dirname(__file__), "cpufreq.svg")
images_path = os.path.join(os.path.dirname(__file__), "images")

# Interval in seconds between two successive draws of the icon
draw_freq_interval = 1.0

sysfs_dir = "/sys/devices/system/cpu"
proc_cpuinfo_file = "/proc/cpuinfo"

ui_file = os.path.join(os.path.dirname(__file__), "cpufreq.ui")

dbus_bus_name = "org.awnproject.Awn.Applets.CpuFreq"
dbus_object_path = "/org/awnproject/Awn/Applets/CpuFreq"
dbus_object_interface = dbus_bus_name
dbus_object_interface_scaling = dbus_object_interface + ".Scaling"


if dbus is not None:
    class CpuFreqBackendDBusObject(dbus.service.Object):

        """A DBus object to read available frequencies and governors, current
        frequency and governor, and to set new frequency or governor.

        """

        def __init__(self, backend):
            self.__backend = backend

            bus = dbus.SessionBus()
            bus.request_name(dbus_bus_name)

            dbus.service.Object.__init__(self, bus, dbus_object_path + "/" + str(backend.get_cpu_nr()))

        @dbus.service.method(dbus_interface=dbus_object_interface, out_signature="b")
        def SupportsScaling(self):
            return self.__backend.supports_scaling()

        @dbus.service.method(dbus_interface=dbus_object_interface, out_signature="i")
        def GetCurrentFrequency(self):
            return self.__backend.get_current_frequency()

        @dbus.service.method(dbus_interface=dbus_object_interface, out_signature="ai")
        def GetFrequencies(self):
            return self.__backend.get_frequencies()

        @dbus.service.method(dbus_interface=dbus_object_interface_scaling, out_signature="as")
        def GetGovernors(self):
            assert self.__backend.supports_scaling()

            return self.__backend.get_governors()

        @dbus.service.method(dbus_interface=dbus_object_interface_scaling, out_signature="s")
        def GetCurrentGovernor(self):
            assert self.__backend.supports_scaling()

            return self.__backend.get_current_governor()

        @dbus.service.method(dbus_interface=dbus_object_interface_scaling, in_signature="i")
        def SetFrequency(self, frequency):
            assert self.__backend.supports_scaling()

            return self.__backend.set_frequency(int(frequency))

        @dbus.service.method(dbus_interface=dbus_object_interface_scaling, in_signature="s")
        def SetGovernor(self, governor):
            assert self.__backend.supports_scaling()

            return self.__backend.set_governor(str(governor))


class CpuFreqApplet:

    """An applet to monitor and control the CPU frequency.

    """

    __button_press_event_id = None

    def __init__(self, applet):
        self.applet = applet

        self.setup_icon()
        self.backends = [ self.initialize_backend(i) for i in range(0, SysFSBackend.get_number_of_cpus()) ]

        applet.tooltip.connect_becomes_visible(self.update_title)
        applet.connect_size_changed(self.size_changed_cb)

        applet.timing.register(self.draw_freq_cb, draw_freq_interval)
        self.draw_freq_cb()

        if dbus is not None:
            try:
                for b in self.backends: CpuFreqBackendDBusObject(b)
            except dbus.DBusException:
                pass

    def initialize_backend(self, cpu_number):
        backend = None
        for b in backends:
            if b.backend_useable(cpu_number):
                backend = b(cpu_number)
                break

        assert backend is not None

        if backend.supports_scaling():
            """
            Compute the difference, R, (in KHz) between the physical maximum and
            minimum frequency. Then, if there are n images, we can split R into n-1
            frequency ranges and map the frequencies in those ranges to the various images
            """
            backend.freq_range_per_image = (backend.get_phys_max_frequency() - backend.get_phys_min_frequency()) / (len(self.icon_states) - 1)

        return backend

    def size_changed_cb(self):
        """Update the applet's icon to reflect the new height.

        """
        self.setup_icon()
        self.draw_freq_cb()

    def setup_icon(self):
        """Load the images that are going to be used as the applet's icon. """
        self.icon_states = []
        sz = self.applet.get_size()
        for i in range(0,14):
            path = os.path.join(images_path, "cpufreq-%s.svg" % i)
            pixbuf = gdk.pixbuf_new_from_file(path)
            pixbuf = gdk.pixbuf_new_from_file_at_size(path, pixbuf.get_width() * sz / pixbuf.get_height(), sz)
            self.icon_states.append(pixbuf)

    def human_readable_freqency(self, frequency):
        frequency = float(frequency)

        if frequency >= 1e6:
            divisor = 1e6
            unit = _("GHz")
        else:
            divisor = 1e3
            unit = _("MHz")

        if frequency % divisor == 0:
            ffreq = str(int(frequency / divisor))
        else:
            ffreq = "%3.2f" % (frequency / divisor)

        return ffreq + " " + unit

    def frequency_changed_cb(self, widget, backend, frequency):
        """Set the governor to 'userspace' and changes the current frequency.  """
        if widget.get_active():
            backend.set_frequency(frequency)
            self.applet.tooltip.hide()

    def governor_changed_cb(self, widget, backend, governor):
        """Change the current governor. """
        if widget.get_active():
            backend.set_governor(governor)
            self.applet.tooltip.hide()

    def draw_freq_cb(self):
        """Draw the icon and updates the title to keep it synchronized with the drawn frequency. """

        icons = []
        for backend in self.backends:
            if backend.supports_scaling():
                number = float(backend.get_current_frequency() - backend.get_phys_min_frequency()) / backend.freq_range_per_image
                number = int(round(number))
                number = max(min(number, len(self.icon_states)-1), 0)
                icons.append( self.icon_states[ number ] )
            else:
                icons.append( self.icon_states[-1] )

        width = sum([ icon.get_width() for icon in icons ])
        height = max([ icon.get_height() for icon in icons ])
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        gdk_cr = gtk.gdk.CairoContext(context)

        left = 0
        for icon in icons:
            gdk_cr.set_source_pixbuf(icon, left, height-icon.get_height())
            gdk_cr.paint()
            left += icon.get_width()

        self.applet.icon.set(context)
        self.update_title()

        del gdk_cr
        return True

    def update_title(self):
        if not self.applet.tooltip.is_visible():
            return

        tooltip = []
        for backend in self.backends:
            title = self.human_readable_freqency(backend.get_current_frequency())
            if backend.supports_scaling():
                title = backend.get_current_governor() + ", " + title
            tooltip.append("CPU "+str(backend.get_cpu_nr())+": "+title)

        self.applet.tooltip.set("\n".join(tooltip))


class SysFSBackend:

    """Backend using the syfs filesystem. Requires Linux 2.6 and the
    cpufreq-selector program from gnome-applets.

    """

    __selector_binary = "cpufreq-set"

    def __init__(self, cpu_nr):
        self.__cpu_nr = cpu_nr
        self.__supports_scaling = 1
        self.__command = self.__selector_binary

    @staticmethod
    def backend_useable(cpu_nr):
        return os.path.isdir(os.path.join(sysfs_dir, "cpu" + str(cpu_nr), "cpufreq"))

    def supports_scaling(self):
        return self.__supports_scaling

    def get_cpu_nr(self):
        return self.__cpu_nr

    @staticmethod
    def get_number_of_cpus():
        pattern = re.compile("cpu\d")
        return len([i for i in os.listdir(sysfs_dir) if pattern.match(i)])

    def get_governors(self):
        return open(os.path.join(sysfs_dir, "cpu" + str(self.__cpu_nr), "cpufreq/scaling_available_governors")).read().strip().split()

    def get_frequencies(self):
        return map(int, open(os.path.join(sysfs_dir, "cpu" + str(self.__cpu_nr), "cpufreq/scaling_available_frequencies")).read().strip().split())

    def set_governor(self, governor):
        assert governor in self.get_governors(), "Governor '" + governor + "' unknown"

        subprocess.Popen(self.__command + " -c %d -g %s" % (self.__cpu_nr, governor), shell=True)

    def set_frequency(self, frequency):
        assert frequency in self.get_frequencies(), "Frequency " + str(frequency) + " invalid"

        subprocess.Popen(self.__command + " -c %d -f %d " % (self.__cpu_nr, frequency), shell=True)

    def get_current_governor(self):
        return open(os.path.join(sysfs_dir, "cpu" + str(self.__cpu_nr), "cpufreq/scaling_governor")).read().strip()

    def get_current_frequency(self):
        return self.__read_frequency("scaling_cur_freq")

    def get_phys_min_frequency(self):
        return self.__read_frequency("cpuinfo_min_freq")

    def get_phys_max_frequency(self):
        return self.__read_frequency("cpuinfo_max_freq")

    def get_min_frequency(self):
        return self.__read_frequency("scaling_min_freq")

    def get_max_frequency(self):
        return self.__read_frequency("scaling_max_freq")

    def __read_frequency(self, file):
        try: return int(open(os.path.join(sysfs_dir, "cpu" + str(self.__cpu_nr), "cpufreq", file)).read().strip())
        except: return 0


class ProcCPUInfoBackend:

    """Backend using /proc/cpuinfo. Does not provide the ability to scale
    the CPU frequency.

    """

    __cpuinfo_pattern = pattern = re.compile("cpu MHz\s+: (\d+\.\d+)")

    def __init__(self, cpu_nr):
        self.__cpu_nr = cpu_nr

    @staticmethod
    def backend_useable(cpu_nr):
        if not os.path.isfile(proc_cpuinfo_file):
            return False
        return re.compile("processor\s+: %d" % cpu_nr).search(open(proc_cpuinfo_file).read()) is not None

    def supports_scaling(self):
        return False

    def get_cpu_nr(self):
        return self.__cpu_nr

    @staticmethod
    def get_number_of_cpus():
        file = open(proc_cpuinfo_file).read()
        return len(ProcCPUInfoBackend.__cpuinfo_pattern.findall(file))

    def get_frequencies(self):
        return [self.get_current_frequency()]

    def get_current_frequency(self):
        file = open(proc_cpuinfo_file).read()
        # Multiply by 1000 because value is in MHz and should be in KHz
        return int(float(self.__cpuinfo_pattern.findall(file)[self.__cpu_nr])) * 1000


backends = [SysFSBackend, ProcCPUInfoBackend]


if __name__ == "__main__":
    awnlib.init_start(
        CpuFreqApplet, 
        {
            "name": applet_name,
            "short": "cpufreq",
            "version": __version__,
            "description": applet_description,
            "logo": applet_logo,
            "author": "onox",
            "copyright-year": "2008 - 2010",
            "authors": ["onox <denkpadje@gmail.com>"]
        }
    )
