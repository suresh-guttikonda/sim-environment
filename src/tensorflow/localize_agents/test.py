#!/usr/bin/env python3

# coding=utf-8
# Copyright 2018 The TF-Agents Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python2, python3
r"""Train and Eval SAC.

All hyperparameters come from the SAC paper
https://arxiv.org/pdf/1812.05905.pdf

To run:

```bash
tensorboard --logdir $HOME/tmp/sac/gym/HalfCheetah-v2/ --port 2223 &

python tf_agents/agents/sac/examples/v2/train_eval.py \
  --root_dir=$HOME/tmp/sac/gym/HalfCheetah-v2/ \
  --alsologtostderr
```
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import time

from absl import app
from absl import flags
from absl import logging

import gin
from six.moves import range
import tensorflow as tf  # pylint: disable=g-explicit-tensorflow-version-import
import numpy as np

import functools

import sys
def set_path(path: str):
    try:
        sys.path.index(path)
    except ValueError:
        sys.path.insert(0, path)
# path to custom tf_agents
set_path('/media/suresh/research/awesome-robotics/active-slam/catkin_ws/src/sim-environment/src/tensorflow/stanford/agents')
# set_path('/home/guttikon/awesome_robotics/sim-environment/src/tensorflow/stanford/agents')

from tf_agents.system import system_multiprocessing as multiprocessing
from tf_agents.agents.ddpg import critic_network
from tf_agents.agents.sac import sac_agent
from tf_agents.agents.sac import tanh_normal_projection_network
from tf_agents.drivers import dynamic_step_driver
from tf_agents.environments import tf_py_environment
from tf_agents.environments import parallel_py_environment
from tf_agents.eval import metric_utils
from tf_agents.metrics import tf_metrics
from tf_agents.metrics import py_metrics
from tf_agents.metrics import batched_py_metric
from tf_agents.networks import actor_distribution_network
from tf_agents.networks.utils import mlp_layers
from tf_agents.policies import greedy_policy
from tf_agents.policies import random_tf_policy
from tf_agents.replay_buffers import tf_uniform_replay_buffer
from tf_agents.utils import common
import suite_gibson

flags.DEFINE_string('root_dir', os.getenv('TEST_UNDECLARED_OUTPUTS_DIR'),
                    'Root directory for writing logs/summaries/checkpoints.')
flags.DEFINE_multi_string(
    'gin_file', None, 'Path to the trainer config files.')
flags.DEFINE_multi_string('gin_param', None, 'Gin binding to pass through.')

flags.DEFINE_integer('num_iterations', 1000000,
                     'Total number train/eval iterations to perform.')
flags.DEFINE_integer('initial_collect_steps', 1000,
                     'Number of steps to collect at the beginning of training using random policy')
flags.DEFINE_integer('collect_steps_per_iteration', 1,
                     'Number of steps to collect and be added to the replay buffer after every training iteration')
flags.DEFINE_integer('num_parallel_environments', 1,
                     'Number of environments to run in parallel')
flags.DEFINE_integer('num_parallel_environments_eval', 1,
                     'Number of environments to run in parallel for eval')
flags.DEFINE_integer('replay_buffer_capacity', 1000000,
                     'Replay buffer capacity per env.')
flags.DEFINE_integer('train_steps_per_iteration', 1,
                     'Number of training steps in every training iteration')
flags.DEFINE_integer('batch_size', 256,
                     'Batch size for each training step. '
                     'For each training iteration, we first collect collect_steps_per_iteration steps to the '
                     'replay buffer. Then we sample batch_size steps from the replay buffer and train the model'
                     'for train_steps_per_iteration times.')
flags.DEFINE_float('gamma', 0.99,
                   'Discount_factor for the environment')
flags.DEFINE_float('actor_learning_rate', 3e-4,
                   'Actor learning rate')
flags.DEFINE_float('critic_learning_rate', 3e-4,
                   'Critic learning rate')
flags.DEFINE_float('alpha_learning_rate', 3e-4,
                   'Alpha learning rate')

flags.DEFINE_integer('num_eval_episodes', 10,
                     'The number of episodes to run eval on.')
flags.DEFINE_integer('eval_interval', 10000,
                     'Run eval every eval_interval train steps')
flags.DEFINE_boolean('eval_only', False,
                     'Whether to run evaluation only on trained checkpoints')
flags.DEFINE_boolean('eval_deterministic', False,
                     'Whether to run evaluation using a deterministic policy')
flags.DEFINE_integer('gpu_c', 0,
                     'GPU id for compute, e.g. Tensorflow.')

# Added for Gibson
flags.DEFINE_string('config_file', os.path.join('./configs/', 'turtlebot_localize.yaml'),
                    'Config file for the experiment.')
flags.DEFINE_list('model_ids', None,
                  'A comma-separated list of model ids to overwrite config_file.'
                  'len(model_ids) == num_parallel_environments')
flags.DEFINE_list('model_ids_eval', None,
                  'A comma-separated list of model ids to overwrite config_file for eval.'
                  'len(model_ids) == num_parallel_environments_eval')
flags.DEFINE_string('env_mode', 'headless',
                    'Mode for the simulator (gui or headless)')
flags.DEFINE_float('action_timestep', 1.0 / 10.0,
                   'Action timestep for the simulator')
flags.DEFINE_float('physics_timestep', 1.0 / 40.0,
                   'Physics timestep for the simulator')
flags.DEFINE_integer('gpu_g', 0,
                     'GPU id for graphics, e.g. Gibson.')

# pfnet
flags.DEFINE_string('pfnet_load', '', 'Load a previously trained pfnet model from a checkpoint file.')
flags.DEFINE_float('map_pixel_in_meters', 0.1, 'The width (and height) of a pixel of the map in meters. Defaults to 0.1 for iGibson environment [trav_map_resolution].')
flags.DEFINE_integer('num_particles', 30,
                     'Number of particles in Particle Filter.')
flags.DEFINE_boolean('resample', False,
                     'Resample particles in Particle Filter. Possible values: true / false.')
flags.DEFINE_float('alpha_resample_ratio', 1.0,
                    'Trade-off parameter for soft-resampling in PF-net. Only effective if resample == true. Assumes values 0.0 < alpha <= 1.0. Alpha equal to 1.0 corresponds to hard-resampling.')
flags.DEFINE_integer('trajlen', 24, 'Length of trajectories.')
flags.DEFINE_list('transition_std', [0.0, 0.0],
                    'Standard deviations for transition model. Values: translation std (meters), rotation std (radians)')
flags.DEFINE_list('init_particles_std', [15, 0.523599], 'Standard deviations for generated initial particles for tracking distribution. Values: translation std (meters), rotation std (radians)')
flags.DEFINE_string('init_particles_distr', 'gaussian', 'Distribution of initial particles. Possible values: gaussian / uniform.')
# flags.DEFINE_integer('gpu_num', '0', 'use gpu no. to train/test pfnet')
flags.DEFINE_integer('seed', '42', 'Fix the random seed of numpy and tensorflow.')

flags.DEFINE_list('global_map_size', [1000, 1000, 1], '')
flags.DEFINE_float('window_scaler', 8.0, '')
flags.DEFINE_boolean('return_state', True, '')
flags.DEFINE_boolean('stateful', False, '')
flags.DEFINE_boolean('use_plot', True, '')
flags.DEFINE_boolean('store_plot', True, '')
flags.DEFINE_list('init_particles_cov', [], '')

FLAGS = flags.FLAGS

@gin.configurable
def train_eval(
    root_dir,
    gpu=0,
    seed=42,
    env_load_fn=None,
    model_ids=None,
    reload_interval=None,
    eval_env_mode='headless',
    num_iterations=1000000,
    conv_1d_layer_params=None,
    conv_2d_layer_params=None,
    encoder_fc_layers=[256],
    actor_fc_layers=[256, 256],
    critic_obs_fc_layers=None,
    critic_action_fc_layers=None,
    critic_joint_fc_layers=[256, 256],
    # Params for collect
    initial_collect_steps=10000,
    collect_steps_per_iteration=1,
    num_parallel_environments=1,
    replay_buffer_capacity=1000000,
    # Params for target update
    target_update_tau=0.005,
    target_update_period=1,
    # Params for train
    train_steps_per_iteration=1,
    batch_size=256,
    actor_learning_rate=3e-4,
    critic_learning_rate=3e-4,
    alpha_learning_rate=3e-4,
    td_errors_loss_fn=tf.compat.v1.losses.mean_squared_error,
    gamma=0.99,
    reward_scale_factor=1.0,
    gradient_clipping=None,
    use_tf_functions=True,
    # Params for eval
    num_eval_episodes=30,
    eval_interval=10000,
    eval_only=False,
    eval_deterministic=False,
    num_parallel_environments_eval=1,
    model_ids_eval=None,
    # Params for summaries and logging
    train_checkpoint_interval=10000,
    policy_checkpoint_interval=10000,
    rb_checkpoint_interval=50000,
    log_interval=100,
    summary_interval=1000,
    summaries_flush_secs=10,
    debug_summaries=False,
    summarize_grads_and_vars=False,
    eval_metrics_callback=None):

    """A simple train and eval for SAC."""
    root_dir = os.path.expanduser(root_dir)
    train_dir = os.path.join(root_dir, 'train')
    eval_dir = os.path.join(root_dir, 'eval')

    # fix seed
    np.random.seed(seed)
    tf.random.set_seed(seed)

    eval_metrics = [
        tf_metrics.AverageReturnMetric(buffer_size=num_eval_episodes),
        tf_metrics.AverageEpisodeLengthMetric(buffer_size=num_eval_episodes)
    ]

    global_step = tf.compat.v1.train.get_or_create_global_step()
    with tf.compat.v2.summary.record_if(
            lambda: tf.math.equal(global_step % summary_interval, 0)):
        if model_ids is None:
            model_ids = [None] * num_parallel_environments
        else:
            assert len(model_ids) == num_parallel_environments, \
                'model ids provided, but length not equal to num_parallel_environments'

        if model_ids_eval is None:
            model_ids_eval = [None] * num_parallel_environments_eval
        else:
            assert len(model_ids_eval) == num_parallel_environments_eval, \
                'model ids eval provided, but length not equal to num_parallel_environments_eval'

        # tf_py_env = [lambda model_id=model_ids[i]: env_load_fn(model_id, 'headless', gpu)
        #              for i in range(num_parallel_environments)]
        # tf_env = tf_py_environment.TFPyEnvironment(
        #     tf_py_env[0])
        #     # parallel_py_environment.ParallelPyEnvironment(tf_py_env))

        if eval_env_mode == 'gui':
            assert num_parallel_environments_eval == 1, 'only one GUI env is allowed'
        eval_py_env = [lambda model_id=model_ids_eval[i]: env_load_fn(model_id, eval_env_mode, gpu)
                       for i in range(num_parallel_environments_eval)]
        eval_tf_env = tf_py_environment.TFPyEnvironment(
            eval_py_env[0])
            # parallel_py_environment.ParallelPyEnvironment(eval_py_env))

        time_step_spec = eval_tf_env.time_step_spec()
        observation_spec = time_step_spec.observation
        action_spec = eval_tf_env.action_spec()
        print('observation_spec: ', observation_spec)
        print('action_spec: ', action_spec)

        glorot_uniform_initializer = tf.compat.v1.keras.initializers.glorot_uniform()
        preprocessing_layers = {}
        if 'rgb' in observation_spec:
            preprocessing_layers['rgb'] = tf.keras.Sequential(mlp_layers(
                conv_1d_layer_params=None,
                conv_2d_layer_params=conv_2d_layer_params,
                fc_layer_params=encoder_fc_layers,
                kernel_initializer=glorot_uniform_initializer,
            ))

        if 'depth' in observation_spec:
            preprocessing_layers['depth'] = tf.keras.Sequential(mlp_layers(
                conv_1d_layer_params=None,
                conv_2d_layer_params=conv_2d_layer_params,
                fc_layer_params=encoder_fc_layers,
                kernel_initializer=glorot_uniform_initializer,
            ))

        if 'scan' in observation_spec:
            preprocessing_layers['scan'] = tf.keras.Sequential(mlp_layers(
                conv_1d_layer_params=conv_1d_layer_params,
                conv_2d_layer_params=None,
                fc_layer_params=encoder_fc_layers,
                kernel_initializer=glorot_uniform_initializer,
            ))

        if 'task_obs' in observation_spec:
            preprocessing_layers['task_obs'] = tf.keras.Sequential(mlp_layers(
                conv_1d_layer_params=None,
                conv_2d_layer_params=None,
                fc_layer_params=encoder_fc_layers,
                kernel_initializer=glorot_uniform_initializer,
            ))

        if len(preprocessing_layers) <= 1:
            preprocessing_combiner = None
        else:
            preprocessing_combiner = tf.keras.layers.Concatenate(axis=-1)

        actor_net = actor_distribution_network.ActorDistributionNetwork(
            observation_spec,
            action_spec,
            preprocessing_layers=preprocessing_layers,
            preprocessing_combiner=preprocessing_combiner,
            fc_layer_params=actor_fc_layers,
            continuous_projection_net=tanh_normal_projection_network.TanhNormalProjectionNetwork,
            kernel_initializer=glorot_uniform_initializer
            )
        critic_net = critic_network.CriticNetwork(
            (observation_spec, action_spec),
            preprocessing_layers=preprocessing_layers,
            preprocessing_combiner=preprocessing_combiner,
            observation_fc_layer_params=critic_obs_fc_layers,
            action_fc_layer_params=critic_action_fc_layers,
            joint_fc_layer_params=critic_joint_fc_layers,
            kernel_initializer=glorot_uniform_initializer
            )

        tf_agent = sac_agent.SacAgent(
            time_step_spec,
            action_spec,
            actor_network=actor_net,
            critic_network=critic_net,
            actor_optimizer=tf.compat.v1.train.AdamOptimizer(
                learning_rate=actor_learning_rate),
            critic_optimizer=tf.compat.v1.train.AdamOptimizer(
                learning_rate=critic_learning_rate),
            alpha_optimizer=tf.compat.v1.train.AdamOptimizer(
                learning_rate=alpha_learning_rate),
            target_update_tau=target_update_tau,
            target_update_period=target_update_period,
            td_errors_loss_fn=td_errors_loss_fn,
            gamma=gamma,
            reward_scale_factor=reward_scale_factor,
            gradient_clipping=gradient_clipping,
            debug_summaries=debug_summaries,
            summarize_grads_and_vars=summarize_grads_and_vars,
            train_step_counter=global_step)
        tf_agent.initialize()

        # # Make the replay buffer.
        # replay_buffer = tf_uniform_replay_buffer.TFUniformReplayBuffer(
        #     data_spec=tf_agent.collect_data_spec,
        #     batch_size=tf_env.batch_size,
        #     max_length=replay_buffer_capacity)
        # replay_observer = [replay_buffer.add_batch]

        if eval_deterministic:
            eval_policy = greedy_policy.GreedyPolicy(tf_agent.policy)
        else:
            eval_policy = tf_agent.policy

        train_metrics = []
        # train_metrics = [
        #     tf_metrics.NumberOfEpisodes(),
        #     tf_metrics.EnvironmentSteps(),
        #     tf_metrics.AverageReturnMetric(
        #         buffer_size=100, batch_size=tf_env.batch_size),
        #     tf_metrics.AverageEpisodeLengthMetric(
        #         buffer_size=100, batch_size=tf_env.batch_size),
        # ]
        #
        # initial_collect_policy = random_tf_policy.RandomTFPolicy(
        #     tf_env.time_step_spec(), tf_env.action_spec())
        # collect_policy = tf_agent.collect_policy

        train_checkpointer = common.Checkpointer(
            ckpt_dir=train_dir,
            agent=tf_agent,
            global_step=global_step,
            metrics=metric_utils.MetricsGroup(train_metrics, 'train_metrics'))
        policy_checkpointer = common.Checkpointer(
            ckpt_dir=os.path.join(train_dir, 'policy'),
            policy=eval_policy,
            global_step=global_step)
        # rb_checkpointer = common.Checkpointer(
        #     ckpt_dir=os.path.join(train_dir, 'replay_buffer'),
        #     max_to_keep=1,
        #     replay_buffer=replay_buffer)

        train_checkpointer.initialize_or_restore()
        # rb_checkpointer.initialize_or_restore()

    for _ in range(num_eval_episodes):
        time_step = eval_tf_env.reset()
        eval_tf_env.render('human')
        while not time_step.is_last():
            action_step = eval_policy.action(time_step)
            time_step = eval_tf_env.step(action_step.action)
            eval_tf_env.render('human')
        print(time_step.reward)
    eval_tf_env.close()

def main(_):
    tf.compat.v1.enable_v2_behavior()
    logging.set_verbosity(logging.INFO)
    gin.parse_config_files_and_bindings(FLAGS.gin_file, FLAGS.gin_param)

    os.environ['CUDA_VISIBLE_DEVICES'] = str(FLAGS.gpu_c)
    os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'

    conv_1d_layer_params = [(32, 8, 4), (64, 4, 2), (64, 3, 1)]
    conv_2d_layer_params = [(32, (8, 8), 4), (64, (4, 4), 2), (64, (3, 3), 2)]
    encoder_fc_layers = [256]
    actor_fc_layers = [256]
    critic_obs_fc_layers = [256]
    critic_action_fc_layers = [256]
    critic_joint_fc_layers = [256]

    for k, v in FLAGS.flag_values_dict().items():
        print(k, v)
    print('conv_1d_layer_params', conv_1d_layer_params)
    print('conv_2d_layer_params', conv_2d_layer_params)
    print('encoder_fc_layers', encoder_fc_layers)
    print('actor_fc_layers', actor_fc_layers)
    print('critic_obs_fc_layers', critic_obs_fc_layers)
    print('critic_action_fc_layers', critic_action_fc_layers)
    print('critic_joint_fc_layers', critic_joint_fc_layers)

    config_file = FLAGS.config_file
    action_timestep = FLAGS.action_timestep
    physics_timestep = FLAGS.physics_timestep

    train_eval(
        root_dir=FLAGS.root_dir,
        gpu=FLAGS.gpu_g,
        seed=FLAGS.seed,
        env_load_fn=lambda model_id, mode, device_idx: suite_gibson.load(
            config_file=config_file,
            model_id=model_id,
            env_mode=mode,
            action_timestep=action_timestep,
            physics_timestep=physics_timestep,
            device_idx=device_idx,
        ),
        model_ids=FLAGS.model_ids,
        eval_env_mode=FLAGS.env_mode,
        num_iterations=FLAGS.num_iterations,
        conv_1d_layer_params=conv_1d_layer_params,
        conv_2d_layer_params=conv_2d_layer_params,
        encoder_fc_layers=encoder_fc_layers,
        actor_fc_layers=actor_fc_layers,
        critic_obs_fc_layers=critic_obs_fc_layers,
        critic_action_fc_layers=critic_action_fc_layers,
        critic_joint_fc_layers=critic_joint_fc_layers,
        initial_collect_steps=FLAGS.initial_collect_steps,
        collect_steps_per_iteration=FLAGS.collect_steps_per_iteration,
        num_parallel_environments=FLAGS.num_parallel_environments,
        replay_buffer_capacity=FLAGS.replay_buffer_capacity,
        train_steps_per_iteration=FLAGS.train_steps_per_iteration,
        batch_size=FLAGS.batch_size,
        actor_learning_rate=FLAGS.actor_learning_rate,
        critic_learning_rate=FLAGS.critic_learning_rate,
        alpha_learning_rate=FLAGS.alpha_learning_rate,
        gamma=FLAGS.gamma,
        num_eval_episodes=FLAGS.num_eval_episodes,
        eval_interval=FLAGS.eval_interval,
        eval_only=FLAGS.eval_only,
        num_parallel_environments_eval=FLAGS.num_parallel_environments_eval,
        model_ids_eval=FLAGS.model_ids_eval,
    )


if __name__ == '__main__':
    flags.mark_flag_as_required('root_dir')
    flags.mark_flag_as_required('config_file')
    multiprocessing.handle_main(functools.partial(app.run, main))
    app.run(main)
