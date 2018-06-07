import datetime
import os
import deepdish as dd
import numpy as np
import pandas as pd
import json

from copy import deepcopy
from pyqtgraph.parametertree import Parameter
from pyqtgraph.pgcollections import OrderedDict

from stytra.utilities import prepare_json


def strip_values(it):
    if isinstance(it, OrderedDict) or isinstance(it, dict):
        new_dict = dict()
        for key, value in it.items():
            if not key == 'value':
                new_dict[key] = strip_values(value)
        return new_dict
    else:
        return it


class Accumulator:
    """
    A general class for accumulating streams of data that
    will be saved or plotted in real time
    """
    def __init__(self, fps_range=10):
        """
        :param fps_range:
        """
        self.stored_data = []
        self.header_list = ['t']
        self.starting_time = None
        self.fps_range = fps_range

    def reset(self, header_list=None):
        """
        Reset accumulator and assign a new header list
        :param header_list:
        """
        if header_list is not None:
            self.header_list = ['t'] + header_list
        self.stored_data = []
        self.starting_time = None
        print('reset')

    def check_start(self):
        if self.starting_time is None:
            self.starting_time = datetime.datetime.now()

    def get_dataframe(self):
        """
        Returns pandas DataFrame with data and headers.
        """
        return pd.DataFrame(self.stored_data,
                            columns=self.header_list)

    def get_fps(self):
        try:
            last_t = self.stored_data[-1][0]
            t_minus_dif = self.stored_data[-self.fps_range][0]
            return self.fps_range/(last_t-t_minus_dif)
        except (IndexError, ValueError):
            return 0.0

    def get_last_n(self, n):
        last_n = min(n, len(self.stored_data))
        if len(self.stored_data) == 0:
            return np.zeros(len(self.header_list)).reshape(1, len(self.header_list))
        else:
            data_list = self.stored_data[-max(last_n, 1):]
            # print('data 0: {}'.format((data_list[0])))
            # print('data -1: {}'.format((data_list[-1])))

            # The length of the tuple in the accumulator may change. Here we
            # make sure we take only the elements that have the same
            # dimension as the last one.
            lenghts = np.array([len(d)==len(data_list[-1]) for d in data_list])
            obar = np.array(data_list[np.where(lenghts)[0][0]:])
            return obar

    def get_last_t(self, t):
        n = int(self.get_fps()*t)
        return self.get_last_n(n)


def metadata_dataframe(metadata_dict, time_step=0.005):
    """
    Function for converting a data_log dictionary into a pandas DataFrame
    for saving.
    :param metadata_dict: data_log dictionary (containing stimulus log!)
    :param time_step: time step (used only if tracking is not present!)
    :return: a pandas DataFrame with a 'stimulus' column for the stimulus
    """

    # Check if tail tracking is present, to use tracking dataframe as template.
    # If there is no tracking, generate a dataframe with time steps specified:
    if 'tail' in metadata_dict['behaviour'].keys():
        final_df = metadata_dict['behaviour']['tail'].copy()
    else:
        t = metadata_dict['stimulus']['log'][-1]['t_stop']
        timearr = np.arange(0, t, time_step)
        final_df = pd.DataFrame(timearr, columns=['t'])

    # Control for delays between tracking and stimulus starting points:
    delta_time = 0
    if 'tail_tracking_start' in metadata_dict['behaviour'].keys():
        stim_start = metadata_dict['stimulus']['log'][0]['started']
        track_start = metadata_dict['behaviour']['tail_tracking_start']
        delta_time = (stim_start - track_start).total_seconds()

    # Assign in a loop a stimulus to each time point
    start_point = None
    for stimulus in metadata_dict['stimulus']['log']:
        if stimulus['name'] == 'start_acquisition':
            start_point = stimulus

        final_df.loc[(final_df['t'] > stimulus['t_start'] + delta_time) &
                     (final_df['t'] < stimulus['t_stop'] + delta_time),
                     'stimulus'] = str(stimulus['name'])

    # Check for the 'start acquisition' which run for a very short time and
    # can be missed:
    if start_point:
        start_idx = np.argmin(abs(final_df['t'] - start_point['t_start']))
        final_df.loc[start_idx, 'stimulus'] = 'start_acquisition'

    return final_df


class DataCollector:
    """ Class for saving all data and data_log produced during an experiment.
    There are two kind of data that are collected:
     - Metadata/parameters: values that should restored from previous
                            sessions.
                            These values don't have to be explicitely added.
                            they are automatically read from all the objects
                            in the stytra Experiment process which are
                            instances of HasPyQtGraphParams.
     - Static data:         (tail tracking, stimulus log...), that should not
                            be restored. Those have to be added one by one
                            via the add_data_source() method.

    Inputs from both types of sources are eventually saved in the .json file
    containing all the information from the experiment.
    In this file data are divided into fixed categories:
     - general:    info about the experiment (date, setup, session...)
     - fish:       info about the fish (line, age, etc.)
     - stimulus:   info about the stimulation (stimuli log, screen
                   dimensions, etc.)
     - imaging:    info about the connected microscope, if present
     - behaviour:  info about fish behaviour (tail log...)
     - camera:     parameters of the camera for behaviour, if one is present
     - tracking:   parameters for tracking
    See documentation of the clean_data_dict() method for a description
    of conventions for dividing the entries among the categories.
    In the future this function may structure its output in other standard
    formats for scientific data (e.g., NWB).

    In addition to the .json file, data_log and parameters from
    HasPyQtGraphParams objects are stored in a config.h5 file (located in the
    experiment directory) which is used for restoring the last configuration
    of the GUI and of the experiment parameters.
    """

    def __init__(self, *data_tuples_list, folder_path='./'):
        """ It accepts static data in a HasPyQtGraph class, which will be
        restored to the last values, or dynamic data like tail tracking or
        stimulus log that will not be restored.
        :param data_tuples_list: tuple of data to be added
        :param folder_path: destination for the final .json file
        """

        # Check validity of directory:
        if os.path.isdir(folder_path):
            if not folder_path.endswith('/'):
                folder_path += '/'
            self.folder_path = folder_path
        else:
            raise ValueError('The specified directory does not exist!')

        # Try to find previously saved data_log:
        self.last_metadata = None
        list_metadata = sorted([fn for fn in os.listdir(folder_path) if
                                fn.endswith('config.h5')])

        if len(list_metadata) > 0:
            self.last_metadata = \
                dd.io.load(folder_path + list_metadata[-1])

        self.log_data_dict = dict()
        self.params_metadata = None
        # Add all the data tuples provided upon instantiation:
        for data_element in data_tuples_list:
            self.add_static_data(*data_element)

    def restore_from_saved(self):
        """ If a config.h5 file is available, use the data there to
        restore the state of the HasPyQtGraph._params tree to last
        session values.
        Before, we make sure that the dictionary that we try to restore
        differs from our parameter structure only in the values.
        Without this control, changing any of the parameters in the code
        could result in bugs and headaches due to the change of the values
        from a config.h5 file from the previous program version.
        """
        if self.last_metadata is not None:
            # Make clean dictionaries without the values:
            current_dict = strip_values(self.params_metadata.saveState())
            prev_dict = strip_values(self.last_metadata)

            # Restore only if equal:
            if current_dict == prev_dict:
                self.params_metadata.restoreState(self.last_metadata,
                                                  blockSignals=True)
                # Here using the restoreState of the _params for some reason
                #  does not block signals coming from restoring the values
                # of its params children.
                # This means that functions connected to the treeStateChange
                # of the params of HasPyQtGraphParams instances may be called
                # multiple times.

    def add_param_tree(self, params_tree):
        """ Add the params tree that will be used for reading and restoring
        the parameters from the previous session.
        It should be the HasPyQtGraph._params tree for it to
        contain all the params branches in all the different experiment objects.
        """
        if isinstance(params_tree, Parameter):
            self.params_metadata = params_tree;
            #self.restore_from_saved()  # restoring is called by experiment
            # at a different time!
        else:
            print('Invalid params source passed!')

    def add_static_data(self, entry, name='unspecified_entry'):
        """ Add new data to the dictionary.
        :param entry: data that will be stored;
        :param name: name in the dictionary. It should start with "category_",
                     where "category" should be one of the possible keys
                     of the dictionary produced in get_clean_dict().
        """
        self.log_data_dict[name] = entry

    def get_clean_dict(self, paramstree=True, eliminate_df=False,
                       convert_datetime=False):
        """ Collect data from all sources and put them together in
        the final hierarchical dictionary that will be saved in the .json file.
        The first level in the dictionary is fixed and defined by the keys
        of the clean_data_dict that will be returned. data from all sources
        are divided in these categories according to the key preceding the
        underscore in their name (e.g., value of general_db_idx will be put in
        ['general']['db_idx']).
        :param paramstree: see sanitize_item docs;
        :param eliminate_df: see sanitize_item docs;
        :param convert_datetime: see sanitize_item docs;
        :return: dictionary with the sorted data.
        """
        clean_data_dict = dict(animal={}, stimulus={}, imaging={},
                               behaviour={}, general={}, camera={},
                               tracking={}, unassigned={})

        # Params data_log:
        value_dict = deepcopy(self.params_metadata.getValues())

        # Static data dictionary:
        value_dict.update(deepcopy(self.log_data_dict))

        for key in value_dict.keys():
            category = key.split('_')[0]
            value = prepare_json(value_dict[key], paramstree=paramstree,
                                 convert_datetime=convert_datetime,
                                 eliminate_df=eliminate_df)
            if category in clean_data_dict.keys():
                split_name = key.split('_')
                if split_name[1] == 'metadata':
                    clean_data_dict[category] = value
                else:
                    clean_data_dict[category]['_'.join(split_name[1:])] = value
            else:
                clean_data_dict['unassigned'][key] = value

        return clean_data_dict

    def get_last_value(self, class_param_key):
        """
        Get the last saved value for a specific class_param_key.
        """
        if self.last_metadata is not None:
            # This syntax is ugly but apparently necessary to scan through
            # the dictionary saved by pyqtgraph.Parameter.saveState().
            return self.last_metadata['children'][
                class_param_key]['children']['name']['value']
        else:
            return None

    def save_config_file(self):
        """
        Save the config.h5 file with the current state of the params
        data_log.
        """
        dd.io.save(self.folder_path + 'config.h5',
                   self.params_metadata.saveState())

    def save_json_log(self, timestamp=None):
        """ Save the .json file with all the data from both static sources
        and the updated params.
        :param timestamp:
        """
        clean_dict = self.get_clean_dict(convert_datetime=True)
        if timestamp is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save clean json file as timestamped Ymd_HMS_metadata.h5 files:
        fish_name = datetime.datetime.now().strftime("%y%m%d") + '_f' + \
                    str(clean_dict['animal']['id'])
        dirname = '/'.join([self.folder_path,
                   clean_dict['stimulus']['protocol_params']['name'],
                             fish_name,
                             str(clean_dict['general']['session_id'])])
        # dd.io.save(filename, self.get_clean_dict(convert_datetime=True))
        if not os.path.isdir(dirname):
            os.makedirs(dirname)
        with open(dirname+'/'+timestamp+'_metadata.json', 'w') as outfile:
            json.dump(clean_dict,
                      outfile, sort_keys=True)

    def save(self, timestamp=None):
        """ Save both the data_log.json log and the config.h5 file
        """

        self.save_json_log(timestamp)
        self.save_config_file()