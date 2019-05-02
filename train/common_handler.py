import os
import warnings

from ignite.handlers import ModelCheckpoint, Timer
from ignite.engine import Engine, Events
from ignite.contrib.handlers import ProgressBar

CKPT_PREFIX = 'networks'
LOGS_FNAME = 'logs.tsv'
PLOT_FNAME = 'plot.svg'


def make_handle_handle_exception(checkpoint_handler, save_networks, create_plots=None):
    def handle_exception(engine, e):
        if isinstance(e, KeyboardInterrupt) and (engine.state.iteration > 1):
            engine.terminate()
            warnings.warn('KeyboardInterrupt caught. Exiting gracefully.')

            if create_plots is not None:
                create_plots(engine)

            exception_save_networks = {
                "exception_{}".format(k):save_networks[k]
                for k in save_networks
            }
            checkpoint_handler(engine, exception_save_networks)
        else:
            raise e
    return handle_exception


def make_handle_create_plots(output_dir, logs_path, plot_path):
    def create_plots(engine):
        try:
            import matplotlib as mpl
            mpl.use('agg')

            import numpy as np
            import pandas as pd
            import matplotlib.pyplot as plt

        except ImportError:
            warnings.warn('Loss plots will not be generated -- pandas or matplotlib not found')

        else:
            df = pd.read_csv(os.path.join(output_dir, logs_path), delimiter='\t')
            # x = np.arange(1, engine.state.epoch * engine.state.iteration + 1, PRINT_FREQ)
            _ = df.plot(subplots=True, figsize=(10, 10))
            _ = plt.xlabel('Iteration number')
            fig = plt.gcf()
            path = os.path.join(output_dir, plot_path)

            fig.savefig(path)
            fig.clear()

    return create_plots


def make_handle_make_dirs(output_dir, fnames):
    def make_dirs(engine):
        for fn in fnames:
            save_folder = os.path.join(output_dir, os.path.dirname(fn))
            if not os.path.exists(save_folder):
                print("mkdir {}".format(save_folder))
                os.makedirs(save_folder)
    return make_dirs


def make_handle_print_times(timer, pbar):
    def print_times(engine):
        pbar.log_message('Epoch {} done. Time: {:.3f}[batch/s]*{}[batch] = {:.3f}[s]'.format(
            engine.state.epoch,
            timer.value(),
            engine.state.iteration,
            timer.value() * engine.state.iteration
        ))
        timer.reset()
    return print_times


def make_handle_print_logs(output_dir, epochs, print_freq, pbar, add_message):
    def print_logs(engine):
        if (engine.state.iteration - 1) % print_freq == 0:
            fname = os.path.join(output_dir, LOGS_FNAME)
            columns = sorted(engine.state.metrics.keys())
            values = [str(round(engine.state.metrics[value], 5)) for value in columns]
            with open(fname, 'a') as f:
                if f.tell() == 0:
                    print('\t'.join(columns), file=f)
                print('\t'.join(values), file=f)

            message = '[{epoch}/{max_epoch}][{i}]'.format(
                epoch=engine.state.epoch,
                max_epoch=epochs,
                i=engine.state.iteration
            )
            message += add_message(engine)
            pbar.log_message(message)
    return print_logs


def warp_common_handler(engine, option, networks_to_save, monitoring_metrics, add_message, use_folder_pathes):
    # attach progress bar
    pbar = ProgressBar()
    pbar.attach(engine, metric_names=monitoring_metrics)
    timer = Timer(average=True)
    timer.attach(engine, start=Events.EPOCH_STARTED, resume=Events.ITERATION_STARTED,
                 pause=Events.ITERATION_COMPLETED, step=Events.ITERATION_COMPLETED)
    create_plots = make_handle_create_plots(option.output_dir, LOGS_FNAME, PLOT_FNAME)
    checkpoint_handler = ModelCheckpoint(
        option.output_dir, CKPT_PREFIX,
        save_interval=option.save_interval, n_saved=option.n_saved,
        require_empty=False, create_dir=True, save_as_state_dict=True
    )

    engine.add_event_handler(Events.EPOCH_COMPLETED, checkpoint_handler, to_save=networks_to_save)
    engine.add_event_handler(Events.EPOCH_COMPLETED, create_plots)
    engine.add_event_handler(
        Events.EXCEPTION_RAISED,
        make_handle_handle_exception(checkpoint_handler, networks_to_save, create_plots)
    )
    engine.add_event_handler(
        Events.STARTED,
        make_handle_make_dirs(option.output_dir, use_folder_pathes)
    )
    engine.add_event_handler(
        Events.EPOCH_COMPLETED,
        make_handle_print_times(timer, pbar)
    )
    engine.add_event_handler(
        Events.ITERATION_COMPLETED,
        make_handle_print_logs(option.output_dir, option.epochs, option.print_freq, pbar, add_message)
    )
    return engine