# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
'''
Created on 20 Apr 2010

logging options : INFO, DEBUG
hash_method : content, timestamp

@author: Chris Filo Gorgolewski
'''
from __future__ import print_function, division, unicode_literals, absolute_import
import os
import errno
import atexit
from io import StringIO
from distutils.version import LooseVersion
from simplejson import load, dump
import numpy as np

from builtins import str, object, open
from ..external import portalocker
import configparser

from future import standard_library
standard_library.install_aliases()

NUMPY_MMAP = LooseVersion(np.__version__) >= LooseVersion('1.12.0')

# Get home directory in platform-agnostic way
homedir = os.path.expanduser('~')
default_cfg = """
[logging]
workflow_level = INFO
filemanip_level = INFO
interface_level = INFO
log_to_file = false
log_directory = %s
log_size = 16384000
log_rotate = 4

[execution]
create_report = true
crashdump_dir = %s
hash_method = timestamp
job_finished_timeout = 5
keep_inputs = false
local_hash_check = true
matplotlib_backend = Agg
plugin = Linear
remove_node_directories = false
remove_unnecessary_outputs = true
try_hard_link_datasink = true
single_thread_matlab = true
crashfile_format = pklz
stop_on_first_crash = false
stop_on_first_rerun = false
use_relative_paths = false
stop_on_unknown_version = false
write_provenance = false
parameterize_dirs = true
poll_sleep_duration = 2
xvfb_max_wait = 10
profile_runtime = false

[check]
interval = 1209600
""" % (homedir, os.getcwd())


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


class NipypeConfig(object):
    """
    Base nipype config class
    """

    def __init__(self, *args, **kwargs):
        self._config = configparser.ConfigParser()
        config_dir = os.path.expanduser('~/.nipype')
        config_file = os.path.join(config_dir, 'nipype.cfg')
        self.data_file = os.path.join(config_dir, 'nipype.json')
        self._config.readfp(StringIO(default_cfg))
        self._display = None
        if os.path.exists(config_dir):
            self._config.read([config_file, 'nipype.cfg'])

    def set_default_config(self):
        self._config.readfp(StringIO(default_cfg))

    def enable_debug_mode(self):
        """Enables debug configuration
        """
        self._config.set('execution', 'stop_on_first_crash', 'true')
        self._config.set('execution', 'remove_unnecessary_outputs', 'false')
        self._config.set('execution', 'keep_inputs', 'true')
        self._config.set('logging', 'workflow_level', 'DEBUG')
        self._config.set('logging', 'interface_level', 'DEBUG')

    def set_log_dir(self, log_dir):
        """Sets logging directory

        This should be the first thing that is done before any nipype class
        with logging is imported.
        """
        self._config.set('logging', 'log_directory', log_dir)

    def get(self, section, option):
        return self._config.get(section, option)

    def set(self, section, option, value):
        if isinstance(value, bool):
            value = str(value)

        return self._config.set(section, option, value)

    def getboolean(self, section, option):
        return self._config.getboolean(section, option)

    def has_option(self, section, option):
        return self._config.has_option(section, option)

    @property
    def _sections(self):
        return self._config._sections

    def get_data(self, key):
        if not os.path.exists(self.data_file):
            return None
        with open(self.data_file, 'rt') as file:
            portalocker.lock(file, portalocker.LOCK_EX)
            datadict = load(file)
        if key in datadict:
            return datadict[key]
        return None

    def save_data(self, key, value):
        datadict = {}
        if os.path.exists(self.data_file):
            with open(self.data_file, 'rt') as file:
                portalocker.lock(file, portalocker.LOCK_EX)
                datadict = load(file)
        else:
            dirname = os.path.dirname(self.data_file)
            if not os.path.exists(dirname):
                mkdir_p(dirname)
        with open(self.data_file, 'wt') as file:
            portalocker.lock(file, portalocker.LOCK_EX)
            datadict[key] = value
            dump(datadict, file)

    def update_config(self, config_dict):
        for section in ['execution', 'logging', 'check']:
            if section in config_dict:
                for key, val in list(config_dict[section].items()):
                    if not key.startswith('__'):
                        self._config.set(section, key, str(val))

    def update_matplotlib(self):
        import matplotlib
        matplotlib.use(self.get('execution', 'matplotlib_backend'))

    def enable_provenance(self):
        self._config.set('execution', 'write_provenance', 'true')
        self._config.set('execution', 'hash_method', 'content')

    def get_display(self):
        """Returns the first display available"""

        # Check if an Xorg server is listening
        # import subprocess as sp
        # if not hasattr(sp, 'DEVNULL'):
        #     setattr(sp, 'DEVNULL', os.devnull)
        # x_listening = bool(sp.call('ps au | grep -v grep | grep -i xorg',
        #                    shell=True, stdout=sp.DEVNULL))

        if self._display is not None:
            return ':%d' % self._display.vdisplay_num

        sysdisplay = None
        if self._config.has_option('execution', 'display_variable'):
            sysdisplay = self._config.get('execution', 'display_variable')

        sysdisplay = sysdisplay or os.getenv('DISPLAY')
        if sysdisplay:
            from collections import namedtuple

            def _mock():
                pass

            # Store a fake Xvfb object
            ndisp = int(sysdisplay.split(':')[-1])
            Xvfb = namedtuple('Xvfb', ['vdisplay_num', 'stop'])
            self._display = Xvfb(ndisp, _mock)
            return sysdisplay
        else:
            # If $DISPLAY is empty, it confuses Xvfb so unset
            if sysdisplay == '':
                del os.environ['DISPLAY']
            try:
                from xvfbwrapper import Xvfb
            except ImportError:
                raise RuntimeError(
                    'A display server was required, but $DISPLAY is not defined '
                    'and Xvfb could not be imported.')

            self._display = Xvfb(nolisten='tcp')
            self._display.start()

            # Older versions of Xvfb used vdisplay_num
            if hasattr(self._display, 'vdisplay_num'):
                return ':%d' % self._display.vdisplay_num

            if hasattr(self._display, 'new_display'):
                return ':%d' % self._display.new_display

    def stop_display(self):
        """Closes the display if started"""
        if self._display is not None:
            self._display.stop()


@atexit.register
def free_display():
    from nipype import config
    from nipype import logging
    config.stop_display()
    logging.getLogger('interface').info('Closing display (if virtual)')
