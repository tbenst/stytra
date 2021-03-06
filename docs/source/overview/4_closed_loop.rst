.. raw:: html

     <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>


Closed-loop stimuli design
==========================

Stimuli whose state depends on the behavior of the fish (position and orientation for freely swimming fish, and tail or eye motion for head-restrained fish) are controlled by linking the behavioral state logs to the stimulus display via :class:`~stytra.stimulation.estimators.Estimator` objects (see Fig~\ref{block_diagram}). An :class:`~stytra.stimulation.estimators.Estimator` receives a data stream from a tracking function (such as tail angles), and uses it together with calibration parameters to estimate some quantity online. For example, a good proxy for fish velocity is the standard deviation of the tail curvature over a window of 50~ms :cite:`portugues2011adaptive`, which we refer to as vigor. The figure below shows an example of how vigor can be used in a closed-loop optomotor assay.  When presented with a global motion of the visual field in the caudal-rostral direction, the fish tend to swim in the direction of perceived motion to minimize the visual flow, a reflex known as the optomotor response :cite:`orger2008control` :cite:`portugues2009neural`. The visual feedback during the swimming bout is a crucial cue that the larvae use to control their movements. In this closed-loop experiment, we use the vigor-based estimation of fish forward velocity, together with a gain factor, to dynamically adjust the velocity of the gratings to match the visual flow expected by a forward swimming fish. The gain parameter can be changed to experimentally manipulate the speed of the visual feedback received by the larvae :cite:`portugues2011adaptive`.

.. raw:: html
   :file: ../../figures/closed_loop.html

Closed-loop stimuli may be important for freely swimming fish as well, for example to display patterns or motion which always maintain the same spatial relationship to the swimming fish by matching the stimulus location and orientation to that of the fish. For other examples on how to design closed loop stimuli in Stytra, refer to the :ref:`closedloop-definition` section of the examples.