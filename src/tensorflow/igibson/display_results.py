#!/usr/bin/env python3

import sys
def set_path(path: str):
    try:
        sys.path.index(path)
    except ValueError:
        sys.path.insert(0, path)
from utils import render, datautils, arguments, pfnet_loss
from utils.iGibson_env import iGibsonEnv
import glob

# set programatically the path to 'pfnet' directory (alternately can also set PYTHONPATH)
set_path('/media/suresh/research/awesome-robotics/active-slam/catkin_ws/src/sim-environment/src/tensorflow/pfnet')
# set_path('/home/guttikon/awesome_robotics/sim-environment/src/tensorflow/pfnet')

import os
import cv2
import pfnet
import numpy as np
from tqdm import tqdm
import tensorflow as tf
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg

def store_results(idx, obstacle_map, particle_states, particle_weights, true_states, params):

    trajlen = params.trajlen

    fig = plt.figure(figsize=(7, 7))
    plt_ax = fig.add_subplot(111)
    canvas = FigureCanvasAgg(fig)

    lin_weights = tf.nn.softmax(particle_weights, axis=-1)
    est_states = tf.math.reduce_sum(tf.math.multiply(
                        particle_states[:, :, :, :], lin_weights[:, :, :, None]
                    ), axis=2)

    # normalize between [-pi, +pi]
    part_x, part_y, part_th = tf.unstack(est_states, axis=-1, num=3)   # (k, 3)
    part_th = tf.math.floormod(part_th + np.pi, 2*np.pi) - np.pi
    est_states = tf.stack([part_x, part_y, part_th], axis=-1)

    # plot map
    floor_map = obstacle_map[0].numpy()    # [H, W, 1]
    map_plt = render.draw_floor_map(floor_map, plt_ax, None)

    images = []
    gt_plt = {
        'robot_position': None,
        'robot_heading': None,
    }
    est_plt = {
        'robot_position': None,
        'robot_heading': None,
        'particles': None,
    }
    for traj in range(trajlen):
        true_state = true_states[:, traj, :]
        est_state = est_states[:, traj, :]
        particle_state = particle_states[:, traj, :, :]
        lin_weight = lin_weights[:, traj, :]

        # plot true robot pose
        position_plt, heading_plt = gt_plt['robot_position'], gt_plt['robot_heading']
        gt_plt['robot_position'], gt_plt['robot_heading'] = render.draw_robot_pose(
                        true_state[0], '#7B241C', floor_map.shape, plt_ax,
                        position_plt, heading_plt)

        # plot est robot pose
        position_plt, heading_plt = est_plt['robot_position'], est_plt['robot_heading']
        est_plt['robot_position'], est_plt['robot_heading'] = render.draw_robot_pose(
                        est_state[0], '#515A5A', floor_map.shape, plt_ax,
                        position_plt, heading_plt)

        # plot est pose particles
        particles_plt = est_plt['particles']
        est_plt['particles'] = render.draw_particles_pose(
                            particle_state[0], lin_weight[0],
                            floor_map.shape, particles_plt)

        plt_ax.legend([gt_plt['robot_position'], est_plt['robot_position']], ["gt_pose", "est_pose"])

        canvas.draw()
        img = np.array(canvas.renderer._renderer)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        images.append(img)

    print(f'{idx} True Pose: {true_state[0]}, Estimated Pose: {est_state[0]}')

    size = (images[0].shape[0], images[0].shape[1])
    out = cv2.VideoWriter(params.out_folder + f'result_{idx}.avi', cv2.VideoWriter_fourcc(*'XVID'), 30, size)

    for i in range(len(images)):
        out.write(images[i])
        # cv2.imwrite(params.out_folder + f'result_img_{i}.png', images[i])
    out.release()

def display_results(params):
    """
    display results with the parsed arguments
    """

    old_stdout = sys.stdout
    log_file = open(params.output,'w')
    sys.stdout = log_file

    num_test_batches = 1
    trajlen = params.trajlen
    batch_size = params.batch_size
    num_particles = params.num_particles

    # evaluation data
    filenames = list(glob.glob(params.testfiles[0]))
    test_ds = datautils.get_dataflow(filenames, params.batch_size, is_training=True)
    print(f'test data: {filenames}')

    # create gym env
    env = iGibsonEnv(config_file=params.config_filename, mode=params.mode,
                action_timestep=1 / 10.0, physics_timestep=1 / 240.0,
                device_idx=params.gpu_num, max_step=params.max_step)
    env.reset()

    # create pf model
    pfnet_model = pfnet.pfnet_model(params)

    # load model from checkpoint file
    if params.pfnet_load:
        print("=====> Loading pf model from " + params.pfnet_load)
        pfnet_model.load_weights(params.pfnet_load)

    # get pretrained action model
    if params.agent == 'pretrained' and params.action_load:
        print("=====> Loading action sampler from " + params.action_load)
        action_model = datautils.load_action_model(env, params.gpu_num, params.action_load)
    else:
        action_model = None

    mse_list = []
    success_list = []
    itr = test_ds.as_numpy_iterator()
    # run over all evaluation samples in an epoch
    for idx in tqdm(range(num_test_batches)):
        parsed_record = next(itr)
        batch_sample = datautils.transform_raw_record(env, parsed_record, params)
        # batch_sample = datautils.get_batch_data(env, params, action_model)

        observation = tf.convert_to_tensor(batch_sample['observation'], dtype=tf.float32)
        odometry = tf.convert_to_tensor(batch_sample['odometry'], dtype=tf.float32)
        true_states = tf.convert_to_tensor(batch_sample['true_states'], dtype=tf.float32)
        floor_map = tf.convert_to_tensor(batch_sample['floor_map'], dtype=tf.float32)
        obstacle_map = tf.convert_to_tensor(batch_sample['obstacle_map'], dtype=tf.float32)
        init_particles = tf.convert_to_tensor(batch_sample['init_particles'], dtype=tf.float32)
        init_particle_weights = tf.constant(np.log(1.0/float(num_particles)),
                                    shape=(batch_size, num_particles), dtype=tf.float32)

        # start trajectory with initial particles and weights
        state = [init_particles, init_particle_weights, obstacle_map]

        # if stateful: reset RNN s.t. initial_state is set to initial particles and weights
        # if non-stateful: pass the state explicity every step
        if params.stateful:
            pfnet_model.layers[-1].reset_states(state)    # RNN layer

        input = [observation, odometry]
        model_input = (input, state)

        # forward pass
        output, state = pfnet_model(model_input, training=False)

        # compute loss
        particle_states, particle_weights = output
        loss_dict = pfnet_loss.compute_loss(particle_states, particle_weights, true_states, params.map_pixel_in_meters)

        # we have squared differences along the trajectory
        mse = np.mean(loss_dict['coords'])
        mse_list.append(mse)

        # localization is successfull if the rmse error is below 1m for the last 25% of the trajectory
        successful = np.all(loss_dict['coords'][-trajlen//4:] < 1.0 ** 2)  # below 1 meter
        success_list.append(successful)

        # store results as video
        store_results(idx, obstacle_map, particle_states, particle_weights, true_states, params)

    # report results
    mean_rmse = np.mean(np.sqrt(mse_list)) * 100
    total_rmse = np.sqrt(np.mean(mse_list)) * 100
    mean_success = np.mean(np.array(success_list, 'i')) * 100
    print(f'Mean RMSE (average RMSE per trajectory) = {mean_rmse:03.3f} cm')
    print(f'Overall RMSE (reported value) = {total_rmse:03.3f} cm')
    print(f'Success rate = {mean_success:03.3f} %')

    # close gym env
    env.close()

    sys.stdout = old_stdout
    log_file.close()
    print('testing finished')


if __name__ == '__main__':
    params = arguments.parse_args()

    params.out_folder = './output/'
    Path(params.out_folder).mkdir(parents=True, exist_ok=True)

    params.output = 'display_results.log'

    display_results(params)
