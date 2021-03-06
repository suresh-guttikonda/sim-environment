#!/usr/bin/env python3

from gibson2.envs.locomotor_env import NavigateRandomEnv, NavigateEnv
import utils.helpers as helpers
import networks.networks as nets
import numpy as np
import random
import gibson2
import os
import pickle
import time

np.random.seed(42)
random.seed(42)
np.set_printoptions(precision=3)

message = '''
move around
-----------
    w
a       d
    s
'''

def sim_env():
    curr_dir_path = os.path.dirname(os.path.abspath(__file__))
    config_filename = os.path.join(curr_dir_path, 'config/turtlebot_demo.yaml')
    nav_env = NavigateRandomEnv(config_file=config_filename, mode='gui')
    robot = nav_env.robots[0]

    num_epochs = 100
    epoch_len = 100
    for j in range(num_epochs):
        print(message)
        old_state = nav_env.reset()
        old_pose = helpers.get_gt_pose(robot)
        old_vel_cmd = robot.action_list[robot.keys_to_action[()]]
        old_delta_t = 0
        eps_data = []
        for i in range(epoch_len):
            key = input()
            if key in ['w', 's', 'd', 'a']:
                action = robot.keys_to_action[(ord(key),)]
            else:
                action = robot.keys_to_action[()]

            new_vel_cmd = robot.action_list[action]
            start_time = time.time()
            new_state, reward, done, info = nav_env.step(action)
            end_time = time.time()
            new_delta_t = end_time-start_time
            new_pose = helpers.get_gt_pose(robot)

            # store data
            data = {}
            data['id'] = '{:04d}_{:04d}'.format(j, i)
            data['pose'] = old_pose
            data['vel_cmd'] = old_vel_cmd
            data['delta_t'] = old_delta_t
            data['state'] = old_state
            eps_data.append(data)

            old_state = new_state
            old_pose = new_pose
            old_vel_cmd = new_vel_cmd
            old_delta_t = new_delta_t

        file_name = 'rnd_pose_obs_data/data_{:04d}.pkl'.format(j)
        with open(file_name, 'wb') as file:
            pickle.dump(eps_data, file)

def load_data():
    with open('rnd_pose_data/data_0000.pkl','rb') as file:
        data = pickle.load(file)
        for eps_data in data:
            print(eps_data['id'], eps_data['pose'], eps_data['vel_cmd'], eps_data['delta_t'], eps_data['state']['rgb'].shape)

def store_rnd_particles(num_particles=5000):
    curr_dir_path = os.path.dirname(os.path.abspath(__file__))
    config_filename = os.path.join(curr_dir_path, 'config/turtlebot_demo.yaml')
    nav_env = NavigateRandomEnv(config_file=config_filename, mode='gui')

    num_epochs = 100
    for j in range(num_epochs):
        nav_env.reset()
        rnd_particles = []
        for idx in range(num_particles):
            _, initial_pos = nav_env.scene.get_random_point_floor(nav_env.floor_num, nav_env.random_height)
            initial_orn = np.array([0, 0, np.random.uniform(0, np.pi * 2)])
            rnd_particles.append([initial_pos[0], initial_pos[1], initial_orn[2]])
        rnd_particles = np.array(rnd_particles)

        file_name = 'rnd_particles_data/particles_{:04d}.pkl'.format(j)
        with open(file_name, 'wb') as file:
            pickle.dump(rnd_particles, file)

if __name__ == "__main__":
    #sim_env()
    #load_data()
    store_rnd_particles()
