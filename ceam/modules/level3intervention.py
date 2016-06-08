# ~/ceam/ceam/modules/level3intervention.py

import os.path

import numpy as np
import pandas as pd

from ceam.engine import SimulationModule
from ceam.modules.ihd import IHDModule
from ceam.modules.hemorrhagic_stroke import HemorrhagicStrokeModule


class Level3InterventionModule(SimulationModule):
    DEPENDENCIES = (IHDModule, HemorrhagicStrokeModule,)

    def setup(self):
        self.register_event_listener(self.track_cost, 'time_step')
        self.cummulative_cost = 0

    def track_cost(self, event):
        local_pop = event.affected_population
        self.cummulative_cost += ( 2.0 * np.sum((local_pop.year >= 1995) & (local_pop.age >= 25) & (local_pop.alive == True)) * (self.simulation.last_time_step.days / 365.0) )

    def incidence_rates(self, population, rates, label):
        if label == 'ihd' or label == 'hemorrhagic_stroke':
            rates *= ((population.year >= 1995) & (population.age >= 25)) * 0.5
        return rates

    def reset(self):
        self.cummulative_cost = 0


# End.