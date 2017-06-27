import os
from time import time
import logging

import pandas as pd

from celery import Celery
from billiard import current_process


app = Celery()

@app.task(autoretry_for=(Exception,), max_retries=2)
def worker(draw_number, component_config, branch_config, logging_directory):
    worker = current_process().index
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', filename=os.path.join(logging_directory, str(worker)+'.log'), level=logging.DEBUG)
    logging.info('Starting job: {}'.format((draw_number, component_config, branch_config)))

    run_configuration = component_config['configuration'].get('run_configuration', {})
    results_directory = run_configuration['results_directory']
    run_configuration['run_id'] = str(worker)+'_'+str(time())
    if branch_config is not None:
        run_configuration['run_key'] = dict(branch_config)
        run_configuration['run_key']['draw'] = draw_number
    component_config['configuration']['run_configuration'] = run_configuration

    try:
        from ceam.framework.engine import configure, run
        from ceam.framework.components import prepare_component_configuration
        from ceam.framework.util import collapse_nested_dict

        configure(draw_number=draw_number, simulation_config=branch_config)
        results = run(prepare_component_configuration(component_config))
        results = pd.DataFrame(results, index=[draw_number]).to_json()

        return results
    except Exception as e:
        logging.exception('Unhandled exception in worker')
        raise
    finally:
        logging.info('Exiting job: {}'.format((draw_number, component_config, branch_config)))
