"""
Modules are the basic building blocks of Pyrpl:
  - The internal structure of the FPGA is made of individual modules performing a well defined task. Each of these
    FPGA modules are represented in python by a HardwareModule.
  - Higher-level operations, for instance those that need a coordinated operation of several HardwareModules is
    performed by SoftwareModules.
Both HardwareModules and SoftwareModules inherit BaseModule that give them basic capabilities such as displaying their
attributes in the GUI having their state load and saved in the config file...
"""

from .attributes import BaseAttribute
from .widgets.module_widgets import ModuleWidget
from . import CurveDB

import logging
import numpy as np
from six import with_metaclass
from collections import OrderedDict
from PyQt4 import QtCore


class SignalLauncher(QtCore.QObject):
    """
    A QObject that is connected to the widgets to update their value when
    attributes of a module change. Any timers needed to implement the module
    functionality shoud be implemented here as well
    """
    update_attribute_by_name = QtCore.pyqtSignal(str, list)
    # The name of the property that has changed, the list is [new_value], the new_value of the attribute
    change_options = QtCore.pyqtSignal(str, list) # name of the SelectProperty, list of new options
    change_ownership = QtCore.pyqtSignal() # The owner of the module has changed

    def __init__(self, module):
        super(SignalLauncher, self).__init__()
        self.module = module

    def kill_timers(self):
        """
        kill all timers
        """
        pass

    def connect_widget(self, widget):
        """
        Establishes all connections between the module and the widget by name.
        """
        #self.update_attribute_by_name.connect(widget.update_attribute_by_name)
        for key in dir(self.__class__):
            val = getattr(self, key)
            if isinstance(val, QtCore.pyqtBoundSignal) and hasattr(widget, key):
                val.connect(getattr(widget, key))


class ModuleMetaClass(type):
    """ Generate Module classes with two features:
    - __new__ lets attributes know what name they are referred two in the
    class that contains them
    - __init__ auto-generates the function setup() and its docstring """
    def __new__(cls, classname, bases, classDict):
        """
        Magic to retrieve the name of the attributes in the attributes
        themselves.
        see http://code.activestate.com/recipes/577426-auto-named-decriptors/
        Iterate through the new class' __dict__ and update the .name of all
        recognised BaseAttribute.
        """
        for name, attr in classDict.items():
            if isinstance(attr, BaseAttribute):
                attr.name = name
        return type.__new__(cls, classname, bases, classDict)

    def __init__(self, classname, bases, classDict):
        """
        Takes care of creating 'setup(**kwds)' function of the module.
        The setup function executes set_attributes(**kwds) and then _setup().

        We cannot use normal inheritance because we want a customized
        docstring for each module. The docstring is created here by
        concatenating the module's _setup docstring and individual
        setup_attribute docstrings.
        """
        super(ModuleMetaClass, self).__init__(classname, bases, classDict)
        #if hasattr(self, "setup_attributes"):
        if "setup" not in self.__dict__:
            # 1. generate a setup function
            def setup(self, **kwds):
                self._callback_active = False
                try:
                    # user can redefine any setup_attribute through kwds
                    self.set_setup_attributes(**kwds)
                    # derived class
                    if hasattr(self, '_setup'):
                        self._setup()
                finally:
                    self._callback_active = True
            # 2. place the new setup function in the module class
            setattr(self, "setup", setup)
        # 3. if setup has no docstring, then make one
        # docstring syntax differs between python versions. Python 3:
        if hasattr(self.setup, "__func__"):
            if (self.setup.__func__.__doc__ is None or
                        self.setup.__func__.__doc__ == ""):
                self.setup.__func__.__doc__ = self.make_setup_docstring()
        # ... python 2
        elif (self.setup.__doc__ is None or
                      self.setup.__doc__ == ""):
            setup.__doc__ += self.make_setup_docstring()

    def make_setup_docstring(self):
        """
        Returns a docstring for the function 'setup' that is composed of:
          - the '_setup' docstring
          - the list of all setup_attributes docstrings
        """
        doc = ""
        if hasattr(self, "_setup"):
            doc += self._setup.__doc__ + '\n'
        doc += "attributes\n=========="
        for attr_name in self._setup_attributes:
            attr = getattr(self, attr_name)
            doc += "\n  " + attr_name + ": " + attr.__doc__
        return doc


class BaseModule(with_metaclass(ModuleMetaClass, object)):
    # The Syntax for defining a metaclass changed from Python 2 to 3.
    # with_metaclass is compatible with both versions and roughly does this:
    # def with_metaclass(meta, *bases):
    #     """Create a base class with a metaclass."""
    #     return meta("NewBase", bases, {})
    # Specifically, ModuleMetaClass ensures that attributes have automatically
    # their internal name set properly upon module creation.
    """
    Several fields have to be implemented in child class:
      - setup_attributes: attributes that are touched by setup(**kwds)/saved/restored upon module creation
      - gui_attributes: attributes to be displayed by the widget
      - widget_class: class of the widget to use to represent the module in the gui (a child of ModuleWidget)
      - _setup(): sets the module ready for acquisition/output with the current attribute's values.

    BaseModules implements several functions itself:
      - create_widget: returns a widget according to widget_class
      - get_setup_attributes(): returns a dictionary with the current setup_attribute key value pairs
      - load_setup_attributes(): loads setup_attributes from config file
      - set_setup_attributes(**kwds): sets the provided setup_attributes

    Finally, setup(**kwds) is created by ModuleMetaClass. it combines set_setup_attributes(**kwds) with _setup()
    """

    # Change this to save the curve with a different system
    _curve_class = CurveDB
    # a QOBject used to communicate with the widget
    _signal_launcher = None
    # name that is going to be used for the section in the config file (class-level)
    _section_name = 'basemodule'
    # Change this to provide a custom graphical class
    _widget_class = ModuleWidget
    # attributes listed here will be saved in the config file everytime they are updated.
    _setup_attributes = []
    # class inheriting from ModuleWidget can
    # automatically generate gui from a list of attributes
    _gui_attributes = _setup_attributes
    # Changing these attributes outside setup(
    # **kwds) will trigger self.callback()
    # standard callback defined in BaseModule is to call setup()
    _callback_attributes = _gui_attributes
    # instance-level attribute created in create_widget
    # This flag is used to desactivate callback during setup
    _callback_active = True
    # This flag is used to desactivate saving into file during init
    _autosave_active = True
    # placeholder for widget
    _widget = None
    # internal memory for owner of the module (to avoid conflicts)
    _owner = None
    # pyrpl_config file???
    pyrpl_config = None
    # name of the module, automaticcally assigned one per instance
    name = None
    # the class for the SignalLauncher to be used
    _signal_launcher = SignalLauncher

    def __init__(self, parent, name=None):
        """
        Creates a module with given name. If name is None, cls.name is
        assigned by the metaclass.

        Parent is either
          - a pyrpl instance: config file entry is in
            (self.__class__.name + 's').(self.name)
          - or another SoftwareModule: config file entry is in
            (parent_entry).(self.__class__.name + 's').(self.name)
        """
        if name is not None:
            self.name = name
        self._logger = logging.getLogger(name=__name__)
        # create the signal launcher object from its class
        self._signal_launcher = self._signal_launcher(self)
        self.parent = parent
        self._autosave_active = False
        self._init_module()
        self._autosave_active = True

    def _init_module(self):
        """
        To implement in child class if needed.
        """
        pass

    def get_setup_attributes(self):
        """
        :return: a dict with the current values of the setup attributes
        """
        kwds = OrderedDict()
        for attr in self._setup_attributes:
            kwds[attr] = getattr(self, attr)
        return kwds

    def set_setup_attributes(self, **kwds):
        """
        Sets the values of the setup attributes. Without calling any callbacks
        """
        old_callback_active = self._callback_active
        self._callback_active = False
        try:
            for key in self._setup_attributes:
                if key in kwds:
                    value = kwds.pop(key)
                    setattr(self, key, value)
        finally:
            self._callback_active = old_callback_active
        if len(kwds) > 0:
            raise ValueError("Attribute %s of module %s doesn't exist." % (kwds[0], self.name))
        #    for key, value in kwds.items():
        #        if not key in self.setup_attributes:
        #            raise ValueError("Attribute %s of module %s doesn't exist."%(key, self.name))
        #        setattr(self, key, value)
        #finally:
        #    self._callback_active = old_callback_active

    def load_setup_attributes(self):
        """
         Load and sets all setup attributes from config file
        """
        dic = OrderedDict()
        if self.c is not None:
            for key, value in self.c._dict.items():
                if key in self._setup_attributes:
                    dic[key] = value
            self.set_setup_attributes(**dic)

    @property
    def c_states(self):
        """
        Returns the config file branch corresponding to the "states" section.
        """
        if not "states" in self.c._parent._keys():
            self.c._parent["states"] = dict()
        return self.c._parent.states

    def save_state(self, name, state_branch=None):
        """
        Saves the current state under the name "name" in the config file. If state_section is left unchanged,
        uses the normal class_section.states convention.
        """
        if state_branch is None:
            state_branch = self.c_states
        state_branch[name] = self.get_setup_attributes()

    def load_state(self, name, state_branch=None):
        """
        Loads the state with name "name" from the config file. If state_section is left unchanged, uses the normal
        class_section.states convention.
        """
        if state_branch is None:
            state_branch = self.c_states
        if name not in state_branch._keys():
            raise KeyError("State %s doesn't exist for modules %s"
                           % (name, self.__class__.name))
        self.setup(**state_branch[name])

    def _save_curve(self, x_values, y_values, **attributes):
        """
        Saves a curve in some database system.
        To change the database system, overwrite this function
        or patch Module.curvedb if the interface is identical.

        :param  x_values: numpy array with x values
        :param  y_values: numpy array with y values
        :param  attributes: extra curve parameters (such as relevant module settings)
        """

        c = self._curve_class.create(x_values,
                                     y_values,
                                     **attributes)
        return c

    def free(self):
        """
        Change ownership to None
        """
        self.owner = None

    @property
    def states(self):
        return list(self.c_states._keys())

    def _setup(self):
        """
        Sets the module up for acquisition with the current setup attribute
        values.
        """
        pass

    def help(self, register=''):
        """returns the docstring of the specified register name
           if register is an empty string, all available docstrings are
           returned"""
        if register:
            string = type(self).__dict__[register].__doc__
            return string
        else:
            string = ""
            for key in type(self).__dict__.keys():
                if isinstance(type(self).__dict__[key], BaseAttribute):
                    docstring = self.help(key)
                    # mute internal registers
                    if not docstring.startswith('_'):
                        string += key + ": " + docstring + '\r\n\r\n'
            return string

    def create_widget(self):
        """
        Creates the widget specified in widget_class.
        """
        self._callback_active = False # otherwise, saved values will be overwritten by default gui values
        self._autosave_active = False # otherwise, default gui values will be saved
        try:
            self._widget = self._widget_class(self.name, self)
        finally:
            self._callback_active = True
            self._autosave_active = True
        return self._widget

    @property
    def c(self):
        """
        The config file instance. In practice, writing values in here will
        write the values in the corresponding section of the config file.
        """
        manager_section_name = self._section_name + "s" # for instance, iqs
        try:
            manager_section = getattr(self.parent.c, manager_section_name)
        except KeyError:
            self.parent.c[manager_section_name] = dict()
            manager_section = getattr(self.parent.c, manager_section_name)
        if not self.name in manager_section._keys():
            manager_section[self.name] = dict()
        return getattr(manager_section, self.name)

    def _callback(self):
        """
        This function is called whenever an attribute listed in
        callback_attributes is changed outside setup()
        """
        self.setup()

    @property
    def owner(self):
        return self._owner

    @owner.setter
    def owner(self, val):
        """
        Changing module ownership automagically:
         - changes the visibility of the module_widget in the gui
         - re-setups the module with the module attributes in the config-file
           if new ownership is None
        """
        old = self.owner
        self._owner = val
        if val is None:
            self._autosave_active = True
        else:
            # desactivate autosave for slave modules
            self._autosave_active = False
        self.ownership_changed(old, val)
        if val is None:
            self.setup(**self.c._dict)
        self._signal_launcher.change_ownership.emit()

    def __enter__(self):
        """
        This function is executed in the context manager construct with ... as ... :
        """
        return self

    def __exit__(self, type, val, traceback):
        """
        To make sure the module will be freed afterwards, use the context manager construct:
        with pyrpl.mod_mag.pop('owner') as mod:
            mod.do_something()
        # module automatically freed at this point

        The free operation is performed in this function
        see http://stackoverflow.com/questions/1369526/what-is-the-python-keyword-with-used-for
        """
        self.owner = None

    @property
    def pyrpl(self):
        """
        Recursively looks through patent modules untill pyrpl instance is
        reached.
        """
        from .pyrpl import Pyrpl
        parent = self.parent
        while (not isinstance(parent, Pyrpl)):
            parent = parent.parent
        return parent


class HardwareModule(BaseModule):
    """
    Module that directly maps a FPGA module. In addition to BaseModule's r
    equirements, HardwareModule classes have to possess the following class
    attributes
      - addr_base: the base address of the module, such as 0x40300000
    """

    parent = None  # parent will be redpitaya instance

    def __init__(self, parent, name=None):
        """ Creates the prototype of a RedPitaya Module interface

        if no name provided, will use cls.name
        """
        self._client = parent.client
        self._addr_base = self.addr_base
        self._rp = parent
        self.pyrpl_config = parent.c
        super(HardwareModule, self).__init__(parent, name=name)
        self.__doc__ = "Available registers: \r\n\r\n" + self.help()

    def ownership_changed(self, old, new):
        """
        This hook is there to make sure any ongoing measurement is stopped when
        the module gets slaved

        old: name of old owner (eventually None)
        new: name of new owner (eventually None)
        """
        pass

    @property
    def _frequency_correction(self):
        """
        factor to manually compensate 125 MHz oscillator frequency error
        real_frequency = 125 MHz * _frequency_correction
        """
        try:
            return self._rp.frequency_correction
        except AttributeError:
            self._logger.warning("Warning: Parent of %s has no attribute "
                                 "'frequency_correction'. ", self.name)
            return 1.0


    def __setattr__(self, name, value):
        # prevent the user from setting a nonexisting attribute
        # (I am not sure anymore if it's not making everyone's life harder...)
        # if hasattr(self, name) or name.startswith('_') or
        # hasattr(type(self), name):
        if name.startswith("_") \
                or (name in self.__dict__) \
                or hasattr(self.__class__, name):
            # we don't want class.attr
            # to be executed to save one communication time,
            # this was the case with hasattr(self, name)
            super(BaseModule, self).__setattr__(name, value)
        else:
            raise ValueError("New module attributes may not be set at runtime."
                             " Attribute " + name + " is not defined in class "
                             + self.__class__.__name__)

    def _reads(self, addr, length):
        return self._client.reads(self._addr_base + addr, length)

    def _writes(self, addr, values):
        self._client.writes(self._addr_base + addr, values)

    def _read(self, addr):
        return int(self._reads(addr, 1)[0])

    def _write(self, addr, value):
        self._writes(addr, [int(value)])

    def _to_pyint(self, v, bitlength=14):
        v = v & (2 ** bitlength - 1)
        if v >> (bitlength - 1):
            v = v - 2 ** bitlength
        return int(v)

    def _from_pyint(self, v, bitlength=14):
        v = int(v)
        if v < 0:
            v = v + 2 ** bitlength
        v = (v & (2 ** bitlength - 1))
        return np.uint32(v)

    #def get_state(self):
    #    """Returns a dictionaty with all current values of the parameters
    #    listed in parameter_names"""
    #
    #   res = dict()
    #    for par in self.parameter_names:
    #        res[par] = getattr(self, par)
    #    return res

    #def set_state(self, dic):
    #    """Sets all parameters to the values in dic. When necessary,
    #    the function also calls setup()"""
    #
    #    res = dict()
    #    for key, value in dic.iteritems():
    #        setattr(self, key, value)


class SoftwareModule(BaseModule):
    """
    Module that doesn't communicate with the Redpitaya directly.
    Child class needs to implement:
      - init_module(pyrpl): initializes the module (attribute values aren't
        saved during that stage)
      - setup_attributes: see BaseModule
      - gui_attributes: see BaseModule
      - _setup(): see BaseModule, this function is called when the user calls
        setup(**kwds) and should set the module
        ready for acquisition/output with the current setup_attributes' values.
    """
    pass
