from collections import deque
from datetime import datetime
from queue import Empty
from multiprocessing import Queue

import cv2
import numpy as np
import param as pa
import psutil
from numba import jit

from stytra.hardware.video import FrameProcessor
from stytra.tracking.tail import trace_tail_centroid,\
                                 trace_tail_angular_sweep
from stytra.metadata import HasPyQtGraphParams


class FrameProcessingMethod(HasPyQtGraphParams):
    """ The class for parametrisation fo various tail and fish tracking methods

    """
    def __init__(self):
        super().__init__()
        for child in self.params.children():
            self.params.removeChild(child)

        standard_params_dict = {'rescale': 1,
                                'filter_size': 0}

        for key in standard_params_dict.keys():
            self.set_new_param(key, standard_params_dict[key])

        self.tracked_variables = []

    def process(self, image):
        return tuple()


class TailTrackingMethod(FrameProcessingMethod):
    def __init__(self):
        super().__init__()
        standard_params_dict = dict(n_tail_segments = 10,
                                    color_invert = True,
                                    tail_start_x = 0,
                                    tail_start_y = 0,
                                    tail_length_x = 1,
                                    tail_length_y = 1)

        for key, value in standard_params_dict.items():
            self.set_new_param(key, value)


class CentroidTrackingMethod(TailTrackingMethod):
    def __init__(self):
        super().__init__()
        standard_params_dict = dict(window_size=10)

        for key, value in standard_params_dict.items():
            self.set_new_param(key, value)

    def process(self, image, parameters):
        return trace_tail_centroid(image, **parameters)


class FrameDispatcher(FrameProcessor):
    """ A class which handles taking frames from the camera and processing them,
     as well as dispatching a subset for display

    """
    def __init__(self, in_frame_queue, finished_signal=None, output_queue=None,
                 processing_class=None, processing_parameter_queue=None,
                 gui_framerate=30, **kwargs):
        """
        :param in_frame_queue: queue dispatching frames from camera
        :param gui_queue: queue where to put frames to be displayed on the GUI
        :param finished_signal: signal for the end of the acquisition
        :param output_queue: queue for the output of the function applied on frames
        :param control_queue:
        :param processing_function: function to be applied to each frame
        :param processing_parameter_queue: queue for the parameters to be passed to the function
        :param gui_framerate: framerate of the display GUI
        """

        super().__init__(**kwargs)

        self.frame_queue = in_frame_queue
        self.gui_queue = Queue()
        self.output_queue = Queue()
        self.processing_parameters = dict()

        self.finished_signal = finished_signal
        self.i = 0
        self.gui_framerate = gui_framerate
        self.processing_class = processing_class
        self.processing_parameter_queue = processing_parameter_queue

    def process_internal(self, frame):
        return tuple(self.processing_function(frame,
                                              **self.processing_parameters))

    def run(self):
        every_x = 10
        i_frame = 100
        while not self.finished_signal.is_set():
            try:
                time, frame = self.frame_queue.get()

                # acquire the processing parameters from a separate queue
                if self.processing_parameter_queue is not None:
                    try:
                        self.processing_parameters = \
                            self.processing_parameter_queue.get(timeout=0.0001)
                    except Empty:
                        pass

                if self.processing_function is not None:
                    self.output_queue.put((datetime.now(), self.process_internal(frame)))

                # calculate the frame rate
                self.update_framerate()
                # put the current frame into the GUI queue
                if self.current_framerate:
                    every_x = max(int(self.current_framerate/self.gui_framerate), 1)
                i_frame += 1
                if self.i == 0:
                    self.gui_queue.put((None, frame))
                self.i = (self.i+1) % every_x
            except Empty:
                break
        return


class TailTrackingDispatcher(FrameDispatcher, HasPyQtGraphParams):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.setName('tracking_algorithm_params')

        self.params.addChildren([{'name': 'start_x',
                                  'value': 0},
                                 {'name': 'start_y',
                                  'value': 0},
                                 {'name': 'tail_length_x',
                                  'value': 0},
                                 {'name': 'tail_length_y',
                                  'value': 0},
                                 {'name': 'processing_function',
                                  'type': 'list',
                                'values': ['centroid', 'angular_sweep'],
                                  'value': 'centroid'},
                                 {'name': 'n_segments', 'type': 'int',
                                  'value': 10},
                                 {'name': 'color_invert', 'type': 'bool',
                                  'value': False},
                                 {'name': 'filter_size', 'type': 'int',
                                  'value': 0},
                                 {'name': 'window_size', 'type': 'int',
                                  'value': 7},
                                 {'name': 'image_scale', 'type': 'float',
                                  'value': 0.5},
                                 ])
        self.processing_functions = dict(centroid=trace_tail_centroid,
                                         angular_sweep=trace_tail_angular_sweep)

    def process_internal(self, frame):
        trace_tail_centroid(**self.params.getValues())


class MovementDetectionParameters(pa.Parameterized):
    fish_threshold = pa.Integer(100, (0, 255))
    motion_threshold = pa.Integer(255*8)
    frame_margin = pa.Integer(10)
    n_previous_save = pa.Integer(400)
    n_next_save = pa.Integer(300)


@jit(nopython=True)
def update_bg(bg, current, alpha):
    am = 1 - alpha
    dif = np.empty_like(current)
    for i in range(current.shape[0]):
        for j in range(current.shape[1]):
            bg[i, j] = bg[i, j] * am + current[i, j] * alpha
            if bg[i, j] > current[i, j]:
                dif[i, j] = bg[i, j] - current[i, j]
            else:
                dif[i, j] = current[i, j] - bg[i, j]
    return dif


class MovingFrameDispatcher(FrameDispatcher):
    def __init__(self, *args, output_queue, control_queue,
                 framestart_queue, signal_start_rec, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_queue = output_queue
        self.control_queue = control_queue
        self.framestart_queue = framestart_queue

        self.signal_start_rec = signal_start_rec
        self.mem_use = 0
        self.processing_parameters = MovementDetectionParameters()

    def run(self):
        i = 0
        every_x = 10

        t, frame_0 = self.frame_queue.get(timeout=5)
        n_previous_compare = 3
        previous_ims = np.zeros((n_previous_compare, ) + frame_0.shape,
                                dtype=np.uint8)

        previous_images = deque()
        record_counter = 0

        i_frame = 0
        recording_state = False

        i_recorded = 0

        while not self.finished_signal.is_set():
            try:
                if self.processing_parameter_queue is not None:
                    try:
                        self.processing_parameters = \
                            self.processing_parameter_queue.get(timeout=0.0001)
                    except Empty:
                        pass

                # process frames as they come, threshold them to roughly find the fish (e.g. eyes)
                current_time, current_frame = self.frame_queue.get()
                _, current_frame_thresh =  \
                    cv2.threshold(cv2.boxFilter(current_frame, -1, (3, 3)),
                                  self.processing_parameters.fish_threshold,
                                  255, cv2.THRESH_BINARY)

                # compare the thresholded frame to the previous ones, if there are enough differences
                # because the fish moves, start recording to file
                difsum = 0
                n_crossed = 0
                image_crop = slice(self.processing_parameters.frame_margin,
                                   -self.processing_parameters.frame_margin)
                if i_frame >= n_previous_compare:
                    for j in range(n_previous_compare):
                        difsum = cv2.sumElems(cv2.absdiff(previous_ims[j, image_crop, image_crop],
                                                          current_frame_thresh[image_crop, image_crop]))[0]

                        if difsum > self.processing_parameters.motion_threshold:
                            n_crossed += 1

                    if n_crossed == n_previous_compare:
                        record_counter = self.processing_parameters.n_next_save

                    if record_counter > 0:
                        if self.signal_start_rec.is_set() and self.mem_use < 0.9:
                            if not recording_state:
                                while previous_images:
                                    time, im = previous_images.popleft()
                                    self.framestart_queue.put(time)
                                    self.output_queue.put(im)
                                    i_recorded += 1
                            self.output_queue.put(current_frame)
                            self.framestart_queue.put(current_time)
                            i_recorded += 1
                        recording_state = True
                        record_counter -= 1
                    else:
                        recording_state = False
                        previous_images.append((current_time, current_frame))
                        if len(previous_images) > self.processing_parameters.n_previous_save:
                            previous_images.popleft()

                i_frame += 1

                previous_ims[i_frame % n_previous_compare, :, :] = current_frame_thresh

                # calculate the framerate
                self.update_framerate()
                if self.current_framerate is not None:
                    every_x = max(int(self.current_framerate / self.gui_framerate), 1)

                if self.i == 0:
                    self.mem_use = psutil.virtual_memory().used/psutil.virtual_memory().total
                    self.gui_queue.put((current_time, current_frame)) # frame

                self.i = (self.i + 1) % every_x
            except Empty:
                break